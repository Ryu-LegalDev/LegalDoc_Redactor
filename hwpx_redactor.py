"""HWPX 법률 서면 개인정보 제거기 — 하이브리드 프로토타입 (정규식 + Ollama LLM + OCR 이미지 처리)"""

import zipfile
import json
import re
import io
import tempfile
import shutil
from pathlib import Path
from lxml import etree
import ollama
import easyocr
from PIL import Image, ImageDraw, ImageFont

NAMESPACES = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
}

OLLAMA_MODEL = "gemma3:4b"

# ── 1단계: 정규식 패턴 ──────────────────────────────────────────

REGEX_PATTERNS = {
    "주민등록번호": re.compile(r"\d{6}\s*-\s*[1-4]\d{6}"),
    "전화번호": re.compile(
        r"(?:0(?:2|31|32|33|41|42|43|44|51|52|53|54|55|61|62|63|64)"
        r"[-.\s]?\d{3,4}[-.\s]?\d{4}"
        r"|01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}"
        r"|(?:전화|팩스|TEL|FAX)[:\s]*\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4})"
    ),
    "계좌번호": re.compile(r"(?<!전화[:\s])(?<!팩스[:\s])\d{3,6}-\d{2,6}-\d{2,6}"),
    "법인명": re.compile(r"법무법인\s+\S+"),
    "주소": re.compile(
        r"(?:[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|시|군)|서울|부산|대구|인천|광주|대전|울산|세종)"
        r"(?:\s+[가-힣]+(?:시|구|군))*"
        r"\s+[가-힣]+(?:대로|로|길)"
        r"(?:\d+번?(?:안?길)?)?"
        r"(?:[\s,]*\d+(?:[-]\d+)*)?"
        r"(?:[\s,]*제?\d+층)?"
        r"(?:[\s,]*제?\d+호)?"
        r"(?:\s*[가-힣0-9]+(?:동|차)\s*[가-힣0-9\s]*)?"
        r"(?:\s*\([가-힣0-9A-Za-z\s,]+\))?"
    ),
    "주소_동리": re.compile(
        r"(?:[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|시|군)|서울|부산|대구|인천|광주|대전|울산|세종)"
        r"(?:\s+[가-힣]+(?:시|구|군))*"
        r"\s+[가-힣]+(?:동|읍|면|리)"
        r"\s+\d+(?:[-]\d+)+"
    ),
}


KOREAN_FONT_PATH = "C:/Windows/Fonts/malgun.ttf"

# ── 엔티티 접두사/접미사 (고유명과 업종/유형 분리용) ────────────

ENTITY_PREFIXES = [
    "법무법인", "법률사무소", "회계법인", "세무법인", "특허법인",
    "주식회사", "유한회사", "유한책임회사",
]

ENTITY_SUFFIXES = [
    # 장소 — 긴 것 우선
    "특별자치시", "특별자치도", "광역시", "특별시",
    "나이트클럽", "노래방", "오피스텔", "아파트", "빌라", "맨션",
    "PC방", "카페", "식당", "주점", "호텔", "모텔", "펜션",
    "대로", "시", "군", "구", "동", "읍", "면", "리", "로", "길",
    # 조직/사업체 — 긴 것 우선
    "임대주택조합", "재건축조합", "주택조합",
    "자산운용사", "신탁회사", "증권회사", "보험회사", "투자회사",
    "대학교", "고등학교", "중학교", "초등학교", "학교", "유치원", "어린이집",
    "종합병원", "대학병원", "병원", "의원", "한의원", "치과", "약국",
    "은행", "증권", "보험", "캐피탈",
    "조합", "협회", "재단", "공단", "공사", "위원회", "연구소", "연구원",
    "회사", "상사", "건설", "물산", "그룹",
]


def make_masked_label(value: str, type_label: str) -> str:
    """고유명은 마스킹하되 업종/유형 접미사는 보존.
    예: '한국관나이트클럽' → '[장소]나이트클럽', '법무법인 율재' → '법무법인 [법인명]'"""
    # 사람이름은 전체 마스킹
    if type_label == "사람이름":
        return f"[{type_label}]"

    # 접두사 체크 (법무법인 X, 주식회사 X)
    for prefix in ENTITY_PREFIXES:
        if value.startswith(prefix):
            return f"{prefix} [{type_label}]"

    # 접미사 체크 (X나이트클럽, X신탁회사, X시)
    for suffix in ENTITY_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return f"[{type_label}]{suffix}"

    return f"[{type_label}]"


def extract_party_roles(preview_text: str, detected_names: list[str] | None = None) -> dict[str, str]:
    """Preview 텍스트 또는 본문에서 당사자 이름 → 역할(원고/피고/소외인) 매핑을 추출.
    복수 당사자 시 원고2, 피고3 등으로 번호 부여."""
    role_map = {}

    # Preview 텍스트 헤더에서 "원 고 [이름]", "피 고 [이름]" 추출
    # 형식: "원    고 박대영(주민번호)" 또는 "원 고 전 송 희" 또는 "피    고 1. 회사명"
    header_pattern = re.compile(
        r"^(원 *고|피 *고) +(?:\d+\.\s*)?([가-힣][가-힣 ]*[가-힣])",
        re.MULTILINE,
    )
    role_counters = {}
    for m in header_pattern.finditer(preview_text):
        role_raw = m.group(1).replace(" ", "")
        name_raw = m.group(2).strip()
        name_compact = name_raw.replace(" ", "")

        if name_compact in {"소송대리인", "담당변호사", "변호사"}:
            continue

        role_counters.setdefault(role_raw, 0)
        role_counters[role_raw] += 1
        count = role_counters[role_raw]
        label = role_raw if count == 1 else f"{role_raw}{count}"

        role_map[name_compact] = label
        if name_raw != name_compact:
            role_map[name_raw] = label

    # 본문에서 "소외 [이름]" 패턴 추출 (이름 2~3글자, 뒤에 조사/괄호/공백)
    sooe_pattern = re.compile(r"소외\s+([가-힣]{2,3})(?=[은는이가와의을를에과\s(])")
    sooe_counter = 0
    for m in sooe_pattern.finditer(preview_text):
        name = m.group(1)
        if name in role_map or name in {"사람", "상대", "관계"}:
            continue
        sooe_counter += 1
        label = "소외인" if sooe_counter == 1 else f"소외인{sooe_counter}"
        role_map[name] = label

    # LLM이 탐지한 이름 중 매핑 안 된 것이 있으면, 본문 문맥에서 역할 추정
    if detected_names:
        for name in detected_names:
            compact = name.replace(" ", "")
            if compact not in role_map:
                role_map[compact] = "관계인"

    return role_map


REGEX_PRIORITY = ["주민등록번호", "전화번호", "계좌번호", "법인명", "주소", "주소_동리"]

def regex_redact(text: str) -> tuple[str, list[dict]]:
    findings = []
    covered = set()
    for label in REGEX_PRIORITY:
        pattern = REGEX_PATTERNS.get(label)
        if not pattern:
            continue
        for m in pattern.finditer(text):
            s, e = m.span()
            if any(not (e <= cs or s >= ce) for cs, ce in covered):
                continue
            covered.add((s, e))
            display_type = "주소" if label.startswith("주소") else label
            findings.append({"type": display_type, "value": m.group(), "span": (s, e)})

    for f in sorted(findings, key=lambda x: x["span"][0], reverse=True):
        s, e = f["span"]
        mask = f"[{f['type']}]"
        text = text[:s] + mask + text[e:]

    return text, findings


# ── 2단계: LLM 기반 개인정보 탐지 ───────────────────────────────

LLM_SYSTEM_PROMPT = """당신은 법률 서면에서 개인정보를 탐지하는 전문가입니다.
주어진 텍스트에서 다음 유형의 개인정보를 찾아 JSON 배열로 반환하세요.

## 탐지 대상
- 사람이름: 실제 사람의 고유한 이름 (예: 김철수, 이영희)
  주의: "원고", "피고", "소외인", "소송대리인", "담당변호사", "피고들" 등 소송 역할 호칭은 제외하세요.
  주의: "시행사", "시공사", "매도인", "매수인", "채권자", "채무자" 등 거래상 지위도 제외하세요.
  주의: "신의칙", "과실상계" 등 법률 용어는 제외하세요.
  주의: 법률 서면에서 이름에 띄어쓰기가 있을 수 있습니다 (예: "김 현 정", "전 송 희"). 이것도 사람이름입니다.
- 장소: 구체적인 고유 지명, 주소, 상호명 (예: 의정부시, 한국관나이트클럽)
  주의: "집 주소", "피고의 직장", "거주지" 등 일반 표현은 제외하세요.
  주의: 법원명(의정부지방법원 등)은 제외하세요.
- 법인명: 법무법인, 회사 등 조직의 고유 이름 (예: 법무법인 율재)
  주의: "법원", "대법원" 등 사법기관은 제외하세요.
  주의: "민법", "상법", "주택법" 등 법률 이름은 법인명이 아닙니다. 제외하세요.

## 탐지하지 않는 것 (절대 포함하지 마세요)
- 금액 (위자료, 손해배상액, 분양대금 등 — 예: "37,155,000원")
- 사건번호 (예: "2024가소123456")
- 날짜
- 법률 용어 및 법률명

## 예시
입력: "원고는 소외 김대오와 2009. 12. 17. 혼인신고를 마쳤습니다."
출력: [{"type": "사람이름", "value": "김대오"}]
(원고, 소외는 역할이므로 제외. 김대오는 실제 이름이므로 포함.)

입력: "피고는 의정부시 소재 한국관나이트클럽에서 만났습니다."
출력: [{"type": "장소", "value": "의정부시"}, {"type": "장소", "value": "한국관나이트클럽"}]

## 출력 형식
반드시 아래 형식의 JSON 배열만 반환하세요. 설명이나 다른 텍스트는 절대 포함하지 마세요:
[{"type": "사람이름", "value": "홍길동"}, ...]
탐지 항목이 없으면 빈 배열 []을 반환하세요."""


def parse_llm_json(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    bracket_start = raw.find("[")
    bracket_end = raw.rfind("]")
    if bracket_start != -1 and bracket_end != -1:
        raw = raw[bracket_start:bracket_end + 1]
    return json.loads(raw)


def llm_detect_pii(text: str, retries: int = 2) -> list[dict]:
    if len(text.strip()) < 5:
        return []

    for attempt in range(retries + 1):
        try:
            resp = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": f"텍스트:\n{text}"},
                ],
                options={"temperature": 0.0, "num_ctx": 4096},
            )
            return parse_llm_json(resp.message.content)
        except (json.JSONDecodeError, Exception) as e:
            if attempt < retries:
                print(f"  [LLM 재시도 {attempt + 1}] {e}")
            else:
                print(f"  [LLM 파싱 실패] {e}")
                return []


# ── 3단계: 이미지 OCR + 마스킹 ───────────────────────────────────

_ocr_reader = None

def get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        _ocr_reader = easyocr.Reader(["ko"], gpu=False, verbose=False)
    return _ocr_reader


def redact_image(
    image_bytes: bytes,
    target_names: list[str],
    name_role_map: dict[str, str] | None = None,
    padding: int = 3,
) -> bytes | None:
    """이미지에서 target_names에 해당하는 텍스트를 역할명으로 대체 마스킹.
    name_role_map이 있으면 해당 역할명(원고, 피고 등)으로 표시, 없으면 검은 박스."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        reader = get_ocr_reader()
        results = reader.readtext(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    matches = []
    for bbox, text, conf in results:
        for name in target_names:
            if name in text:
                matches.append((bbox, name))
                break

    if not matches:
        return None

    img = Image.open(io.BytesIO(image_bytes))
    draw = ImageDraw.Draw(img)

    for bbox, matched_name in matches:
        xs = [int(p[0]) for p in bbox]
        ys = [int(p[1]) for p in bbox]
        x0, y0 = min(xs) - padding, min(ys) - padding
        x1, y1 = max(xs) + padding, max(ys) + padding

        role_label = None
        if name_role_map:
            compact = matched_name.replace(" ", "")
            role_label = name_role_map.get(compact) or name_role_map.get(matched_name)

        if role_label:
            box_w = x1 - x0
            box_h = y1 - y0
            font_size = max(int(box_h * 0.8), 12)
            try:
                font = ImageFont.truetype(KOREAN_FONT_PATH, font_size)
            except OSError:
                font = ImageFont.load_default()

            label_bbox = font.getbbox(role_label)
            label_w = label_bbox[2] - label_bbox[0]
            while label_w > box_w and font_size > 8:
                font_size -= 1
                try:
                    font = ImageFont.truetype(KOREAN_FONT_PATH, font_size)
                except OSError:
                    font = ImageFont.load_default()
                    break
                label_bbox = font.getbbox(role_label)
                label_w = label_bbox[2] - label_bbox[0]

            if role_label.startswith("원고"):
                text_color = "#CC0000"
            elif role_label.startswith("피고"):
                text_color = "#0044CC"
            else:
                text_color = "black"

            draw.rectangle([x0, y0, x1, y1], fill="white")

            label_bbox = font.getbbox(role_label)
            label_w = label_bbox[2] - label_bbox[0]
            label_h = label_bbox[3] - label_bbox[1]
            tx = x0 + ((box_w - label_w) // 2)
            ty = y0 + ((box_h - label_h) // 2)
            draw.text((tx, ty), role_label, fill=text_color, font=font)
        else:
            draw.rectangle([x0, y0, x1, y1], fill="black")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── HWPX 파싱 / 쓰기 ────────────────────────────────────────────

def extract_full_text(xml_bytes: bytes) -> str:
    root = etree.fromstring(xml_bytes)
    texts = []
    for elem in root.iter(f"{{{NAMESPACES['hp']}}}t"):
        if elem.text:
            texts.append(elem.text)
        for child in elem:
            if child.tail:
                texts.append(child.tail)
    return "\n".join(texts)


def redact_section_xml(xml_bytes: bytes, replacements: dict[str, str]) -> bytes:
    root = etree.fromstring(xml_bytes)

    def apply_replacements(text: str) -> str:
        for original, masked in replacements.items():
            text = text.replace(original, masked)
        return text

    for elem in root.iter(f"{{{NAMESPACES['hp']}}}t"):
        if elem.text:
            elem.text = apply_replacements(elem.text)
        for child in elem:
            if child.tail:
                child.tail = apply_replacements(child.tail)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def clear_masterpage(xml_bytes: bytes) -> bytes:
    """마스터페이지에서 도형/이미지(법무법인 로고, 파란색 장식 등)를 제거."""
    root = etree.fromstring(xml_bytes)
    ns_hp = NAMESPACES["hp"]

    # rect, line, pic 등 도형 요소를 모두 제거
    for tag_name in ["rect", "line", "pic", "container", "ole"]:
        for elem in root.iter(f"{{{ns_hp}}}{tag_name}"):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def clear_stamp_from_section(xml_bytes: bytes) -> bytes:
    """본문에서 직인/도장 이미지(image4 등) 참조를 제거."""
    root = etree.fromstring(xml_bytes)
    ns_hp = NAMESPACES["hp"]

    for pic_elem in list(root.iter(f"{{{ns_hp}}}pic")):
        pic_xml = etree.tostring(pic_elem, encoding="unicode")
        if "image3" in pic_xml or "image4" in pic_xml:
            parent = pic_elem.getparent()
            if parent is not None:
                parent.remove(pic_elem)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def redact_hwpx(input_path: str, output_path: str | None = None):
    """HWPX 파일을 열어 하이브리드 마스킹을 수행한 새 HWPX를 생성."""
    input_path = Path(input_path)

    if output_path is None:
        stem = input_path.stem
        output_path = input_path.parent / f"{stem}_제거완.hwpx"
    output_path = Path(output_path)

    # 1) 전체 텍스트 추출
    with zipfile.ZipFile(input_path, "r") as zin:
        section_files = [
            f for f in zin.namelist()
            if f.startswith("Contents/section") and f.endswith(".xml")
        ]
        full_text = ""
        for sf in section_files:
            full_text += extract_full_text(zin.read(sf)) + "\n"

        # Preview 텍스트도 읽기 (헤더 형식이 더 깨끗함)
        preview_text = ""
        if "Preview/PrvText.txt" in zin.namelist():
            preview_text = zin.read("Preview/PrvText.txt").decode("utf-8", errors="replace")

    print(f"본문 길이: {len(full_text)}자")

    # 2) 정규식 1차 탐지
    _, regex_findings = regex_redact(full_text)
    print(f"\n[1단계: 정규식] {len(regex_findings)}건 탐지:")
    for f in regex_findings:
        print(f"  [{f['type']}] {f['value']}")

    # 3) LLM 2차 탐지 — 문단 단위로 처리
    print(f"\n[2단계: LLM ({OLLAMA_MODEL})] 분석 중...")
    llm_findings_raw = []

    paragraphs = [p.strip() for p in full_text.split("\n") if p.strip()]
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) > 800 and current_chunk:
            chunks.append(current_chunk)
            current_chunk = para
        else:
            current_chunk += "\n" + para if current_chunk else para
    if current_chunk:
        chunks.append(current_chunk)

    for i, chunk in enumerate(chunks):
        print(f"  청크 {i + 1}/{len(chunks)} 처리 중...")
        items = llm_detect_pii(chunk)
        llm_findings_raw.extend(items)

    # 오탐 필터링 + 중복 제거
    EXCLUDE_VALUES = {
        "원고", "피고", "소외인", "소외", "소송대리인", "담당변호사", "변호사",
        "원고들", "피고들", "소외인들",
        "대법원", "지방법원", "고등법원", "가정법원",
        "집 주소", "거주지", "직장", "피고의 직장", "도어락 비밀번호",
        "집", "직장 주소", "피고의 거주지", "소외인의 인스타그램",
        "인스타그램", "나이트클럽",
    }
    EXCLUDE_LEGAL_TERMS = {
        "신의칙", "신의성실", "권리남용", "불법행위", "채무불이행", "손해배상",
        "위자료", "부당이득", "사해행위", "상계", "과실상계", "기판력",
        "시행사", "시행자", "시공사", "수분양자", "수탁자", "위탁자",
        "매도인", "매수인", "임대인", "임차인", "채권자", "채무자",
        "보증인", "연대보증인", "대리인", "신탁사", "대통령령", "시행령",
        "부동산개발업", "분양계약", "수분양권",
    }
    EXCLUDE_LAW_NAMES = {
        "민법", "상법", "형법", "민사소송법", "형사소송법", "행정소송법",
        "부동산실명법", "주택법", "건축법", "도시정비법", "신탁법",
        "집합건물법", "주택임대차보호법", "상가임대차보호법",
        "민사집행법", "가사소송법", "국세기본법", "지방세법",
    }
    EXCLUDE_WELL_KNOWN = {
        "카카오톡", "카카오", "네이버", "구글", "인스타그램", "페이스북",
        "하나은행", "국민은행", "신한은행", "우리은행", "농협은행", "기업은행",
        "SC은행", "씨티은행", "케이뱅크", "카카오뱅크", "토스뱅크",
        "GTX", "KTX", "SRT", "이음",
    }
    ALLOWED_TYPES = {"사람이름", "장소", "법인명"}
    seen = set()
    llm_findings = []
    for item in llm_findings_raw:
        if not item.get("value") or not item.get("type"):
            continue
        if item["type"] not in ALLOWED_TYPES:
            continue
        val = item["value"].strip()
        item["value"] = val
        key = (item["type"], val)
        if key in seen or val not in full_text:
            continue
        if val in EXCLUDE_VALUES or val in EXCLUDE_LEGAL_TERMS or val in EXCLUDE_LAW_NAMES:
            continue
        if val in EXCLUDE_WELL_KNOWN:
            continue
        if item["type"] == "장소" and "법원" in val:
            continue
        if len(val.replace(" ", "")) <= 1:
            continue
        if item["type"] == "장소" and len(val.replace(" ", "")) <= 2:
            continue
        if val.startswith("이 사건") or val.startswith("피고 ") or val.startswith("원고 "):
            continue
        seen.add(key)
        llm_findings.append(item)

    print(f"\n[2단계: LLM] {len(llm_findings)}건 탐지:")
    for f in llm_findings:
        print(f"  [{f['type']}] {f['value']}")

    # 3-b) 당사자 역할 매핑 추출 (LLM 탐지 후 이름 목록으로)
    detected_name_values = [f["value"].replace(" ", "") for f in llm_findings if f["type"] == "사람이름"]
    source_text = preview_text or full_text
    party_roles = extract_party_roles(source_text, detected_name_values)
    if party_roles:
        print(f"\n[당사자 매핑]")
        for name, role in party_roles.items():
            if " " not in name:
                print(f"  {name} → {role}")

    # 4) 치환 맵 생성 (고유명만 마스킹, 업종/유형은 보존)
    replacements = {}
    for f in regex_findings:
        replacements[f["value"]] = make_masked_label(f["value"], f["type"])
    for f in llm_findings:
        replacements[f["value"]] = make_masked_label(f["value"], f["type"])

    replacements = dict(sorted(replacements.items(), key=lambda x: len(x[0]), reverse=True))

    # 이미지 마스킹 대상 이름 목록
    name_values = [f["value"] for f in llm_findings if f["type"] == "사람이름"]
    # 띄어쓰기 제거 버전도 추가
    name_values_compact = list(set(name_values + [n.replace(" ", "") for n in name_values]))

    # 5) HWPX 재패키징
    print(f"\n[3단계: 파일 처리]")
    with zipfile.ZipFile(input_path, "r") as zin:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                # 본문 XML 마스킹
                if item.filename.startswith("Contents/section") and item.filename.endswith(".xml"):
                    data = redact_section_xml(data, replacements)
                    data = clear_stamp_from_section(data)
                    print(f"  텍스트 마스킹 + 직인 제거: {item.filename}")

                # 마스터페이지 정리 (파란색 장식, 로고 제거)
                elif item.filename.startswith("Contents/masterpage") and item.filename.endswith(".xml"):
                    data = clear_masterpage(data)
                    print(f"  마스터페이지 정리: {item.filename}")

                # 미리보기 텍스트 마스킹
                elif item.filename == "Preview/PrvText.txt":
                    text = data.decode("utf-8", errors="replace")
                    for orig, masked in replacements.items():
                        text = text.replace(orig, masked)
                    data = text.encode("utf-8")
                    print(f"  미리보기 마스킹: {item.filename}")

                # 이미지 OCR + 마스킹
                elif item.filename.startswith("BinData/") and item.filename.endswith(".png"):
                    img_name = Path(item.filename).stem
                    if img_name in ("image3",):
                        # 로고 → 1x1 투명 PNG로 교체
                        img = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        data = buf.getvalue()
                        print(f"  로고 제거: {item.filename}")
                    elif img_name in ("image4",):
                        # 직인 → 1x1 투명 PNG로 교체
                        img = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        data = buf.getvalue()
                        print(f"  직인 제거: {item.filename}")
                    else:
                        # 녹취록 등 이미지에서 이름 OCR → 역할명 대체 마스킹
                        redacted = redact_image(data, name_values_compact, name_role_map=party_roles)
                        if redacted:
                            data = redacted
                            print(f"  이미지 OCR 마스킹: {item.filename}")
                        else:
                            print(f"  이미지 변경 없음: {item.filename}")

                zout.writestr(item, data)

    print(f"\n완료: {output_path}")
    print(f"총 {len(replacements)}개 고유 텍스트 항목 마스킹")
    return output_path


# ── 실행 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("사용법: python hwpx_redactor.py <파일경로.hwpx> [<파일2.hwpx> ...]")
        print("  결과물은 같은 폴더에 (원본이름)_제거완.hwpx 로 저장됩니다.")
        sys.exit(1)

    for src in sys.argv[1:]:
        src_path = Path(src)
        if not src_path.exists():
            print(f"파일 없음: {src}")
            continue
        if not src_path.suffix.lower() == ".hwpx":
            print(f"HWPX 파일이 아님: {src}")
            continue

        print(f"\n{'='*60}")
        print(f"처리 시작: {src_path.name}")
        print(f"{'='*60}")
        result = redact_hwpx(str(src_path))
        print(f"\n출력 파일: {result}")
