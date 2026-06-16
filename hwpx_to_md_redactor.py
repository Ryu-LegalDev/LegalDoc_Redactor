"""HWPX → MD 변환 + 개인정보 제거 통합 도구
kordoc로 HWPX를 MD로 변환한 뒤, 로컬 LLM(Ollama)으로 개인정보를 제거합니다."""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import ollama

OLLAMA_MODEL = "gemma3:4b"
CHUNK_SIZE = 1500

# ── 정규식 패턴 ──────────────────────────────────────────────────

REGEX_PATTERNS = {
    "주민등록번호": re.compile(r"\d{6}\s*-\s*[1-4]\d{6}"),
    "전화번호": re.compile(
        r"(?:0(?:2|31|32|33|41|42|43|44|51|52|53|54|55|61|62|63|64)"
        r"[-.\s]?\d{3,4}[-.\s]?\d{4}"
        r"|01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}"
        r"|(?:전화|팩스|TEL|FAX)[:\s]*\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4})"
    ),
    "계좌번호": re.compile(r"(?<!전화[:\s])(?<!팩스[:\s])\d{3,6}-\d{2,6}-\d{2,6}"),
    "법인명": re.compile(
        r"(?:법무법인\s+\S+"
        r"|[가-힣A-Za-z]+주식회사"
        r"|주식회사[가-힣A-Za-z]+"
        r"|[가-힣A-Za-z]+\(주\)"
        r"|\(주\)[가-힣A-Za-z]+"
        r"|㈜[가-힣A-Za-z]+"
        r"|[가-힣A-Za-z]+㈜"
        r"|[가-힣]+은행"
        r"|[가-힣]+증권"
        r"|[가-힣]+보험"
        r"|[가-힣]+캐피탈"
        r"|[가-힣]+저축은행"
        r"|[가-힣]+신탁)"
    ),
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
    "법인등록번호": re.compile(r"법인등록번호[:\s]*\d{6}-\d{7}"),
}

REGEX_PRIORITY = ["주민등록번호", "법인등록번호", "전화번호", "계좌번호", "법인명", "주소", "주소_동리"]

# ── 엔티티 접두사/접미사 ────────────────────────────────────────

ENTITY_PREFIXES = [
    "법무법인", "법률사무소", "회계법인", "세무법인", "특허법인",
    "주식회사", "유한회사", "유한책임회사",
]

ENTITY_SUFFIXES = [
    "특별자치시", "특별자치도", "광역시", "특별시",
    "나이트클럽", "노래방", "오피스텔", "아파트", "빌라", "맨션",
    "PC방", "카페", "식당", "주점", "호텔", "모텔", "펜션",
    "대로", "시", "군", "구", "동", "읍", "면", "리", "로", "길",
    "임대주택조합", "재건축조합", "주택조합",
    "자산운용사", "신탁회사", "증권회사", "보험회사", "투자회사",
    "대학교", "고등학교", "중학교", "초등학교", "학교", "유치원", "어린이집",
    "종합병원", "대학병원", "병원", "의원", "한의원", "치과", "약국",
    "은행", "증권", "보험", "캐피탈",
    "조합", "협회", "재단", "공단", "공사", "위원회", "연구소", "연구원",
    "회사", "상사", "건설", "물산", "그룹",
]


def make_masked_label(value: str, type_label: str) -> str:
    if type_label == "사람이름":
        return f"[{type_label}]"
    for prefix in ENTITY_PREFIXES:
        if value.startswith(prefix):
            return f"{prefix} [{type_label}]"
    for suffix in ENTITY_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return f"[{type_label}]{suffix}"
    return f"[{type_label}]"


# ── 당사자 매핑 ─────────────────────────────────────────────────

def extract_party_roles(text: str, detected_names: list[str] | None = None) -> dict[str, str]:
    role_map = {}
    header_pattern = re.compile(
        r"^(원 *고|피 *고) +(?:\d+\.\s*)?([가-힣][가-힣 ]*[가-힣])",
        re.MULTILINE,
    )
    role_counters = {}
    for m in header_pattern.finditer(text):
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

    sooe_pattern = re.compile(r"소외\s+([가-힣]{2,3})(?=[은는이가와의을를에과\s(])")
    sooe_counter = 0
    for m in sooe_pattern.finditer(text):
        name = m.group(1)
        if name in role_map or name in {"사람", "상대", "관계"}:
            continue
        sooe_counter += 1
        label = "소외인" if sooe_counter == 1 else f"소외인{sooe_counter}"
        role_map[name] = label

    if detected_names:
        for name in detected_names:
            compact = name.replace(" ", "")
            if compact not in role_map:
                role_map[compact] = "관계인"
    return role_map


# ── 정규식 탐지 ─────────────────────────────────────────────────

def regex_redact(text: str) -> list[dict]:
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
            if label == "법인등록번호":
                display_type = "주민등록번호"
            findings.append({"type": display_type, "value": m.group(), "span": (s, e)})
    return findings


# ── LLM 탐지 ────────────────────────────────────────────────────

LLM_SYSTEM_PROMPT = """당신은 법률 서면에서 개인정보를 탐지하는 전문가입니다.
주어진 텍스트에서 다음 유형의 개인정보를 찾아 JSON 배열로 반환하세요.

## 탐지 대상
- 사람이름: 실제 사람의 고유한 이름 (예: 김철수, 이영희)
  주의: "원고", "피고", "소외인", "소송대리인", "담당변호사", "피고들" 등 소송 역할 호칭은 제외하세요.
  주의: "시행사", "시공사", "매도인", "매수인", "채권자", "채무자" 등 거래상 지위도 제외하세요.
  주의: "신의칙", "과실상계" 등 법률 용어는 제외하세요.
  주의: 법률 서면에서 이름에 띄어쓰기가 있을 수 있습니다 (예: "김 현 정"). 이것도 사람이름입니다.
- 장소: 구체적인 고유 지명, 주소, 상호명, 아파트 단지명 (예: 의정부시, 힐스테이트 회룡역파크뷰)
  주의: "집 주소", "피고의 직장", "거주지" 등 일반 표현은 제외하세요.
  주의: 법원명(의정부지방법원 등)은 제외하세요.
- 법인명: 법무법인, 회사 등 조직의 고유 이름 (예: 법무법인 율재, 신한자산신탁주식회사)
  주의: "법원", "대법원" 등 사법기관은 제외하세요.
  주의: "민법", "상법", "주택법" 등 법률 이름은 법인명이 아닙니다. 제외하세요.

## 탐지하지 않는 것 (절대 포함하지 마세요)
- 금액 (위자료, 손해배상액, 분양대금 등)
- 사건번호 (예: "2024가소123456")
- 날짜 (예: "2026. 2. 6.")
- 법률 용어 및 법률명

## 출력 형식
반드시 JSON 배열만 반환하세요. 설명 없이:
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


# ── 오탐 필터 ────────────────────────────────────────────────────

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
    "부동산개발업", "분양계약", "수분양권", "당사자", "상대방",
    "분양가", "분양 입지", "분양대금", "계약금", "중도금", "잔금",
    "혜택", "할인", "옵션", "프리미엄", "인센티브",
    "입주", "입주자", "세입자", "거주자", "소유자", "명의자",
    "감정평가", "공시지가", "실거래가", "시가", "시세",
    "등기부등본", "등기사항", "말소등기", "이전등기", "보존등기",
    "근저당", "가압류", "가처분", "압류", "경매", "공매",
    "법무법인", "법률사무소",
}

EXCLUDE_LAW_NAMES = {
    "민법", "상법", "형법", "민사소송법", "형사소송법", "행정소송법",
    "부동산실명법", "주택법", "건축법", "도시정비법", "신탁법",
    "집합건물법", "주택임대차보호법", "상가임대차보호법",
    "민사집행법", "가사소송법", "국세기본법", "지방세법",
}

EXCLUDE_WELL_KNOWN = {
    "카카오톡", "카카오", "네이버", "구글", "인스타그램", "페이스북",
    "GTX", "KTX", "SRT",
    "대한민국", "한국", "서울", "수도권",
}

ALLOWED_TYPES = {"사람이름", "장소", "법인명"}


def filter_llm_findings(findings_raw: list[dict], full_text: str) -> list[dict]:
    seen = set()
    filtered = []
    for item in findings_raw:
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
        if re.match(r"^\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.$", val):
            continue
        if "법률" in val or "에 관한" in val:
            continue
        if item["type"] == "사람이름" and not re.match(r"^[가-힣\s]{2,5}$", val):
            continue
        if item["type"] == "장소" and re.match(
            r"^(분양|입지|부지|현장|단지|공사|사업|택지|토지|대지|건물|아파트|주택|상가|오피스텔|빌라)$",
            val.replace(" ", ""),
        ):
            continue
        if item["type"] == "법인명" and val == val.rstrip("주식회사(주)"):
            if len(val) <= 3:
                continue
        seen.add(key)
        filtered.append(item)
    return filtered


# ── kordoc 변환 ──────────────────────────────────────────────────

KORDOC_CMD = r"C:\Users\User\AppData\Roaming\npm\kordoc.cmd"


def convert_hwpx_to_md(hwpx_path: Path, md_path: Path) -> bool:
    try:
        result = subprocess.run(
            [KORDOC_CMD, str(hwpx_path), "-o", str(md_path), "--silent"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        if result.returncode != 0:
            print(f"  [kordoc 오류] {result.stderr.strip()}")
            return False
        return md_path.exists()
    except FileNotFoundError:
        print("  [오류] kordoc가 설치되어 있지 않습니다. npm install -g kordoc")
        return False
    except subprocess.TimeoutExpired:
        print("  [오류] kordoc 변환 시간 초과 (120초)")
        return False


# ── 메인 파이프라인 ──────────────────────────────────────────────

def process_hwpx(input_path: str, output_dir: str | None = None):
    input_path = Path(input_path)
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = input_path.parent

    final_output = out_dir / f"{input_path.stem}_제거완.md"

    # ── Step 1: HWPX → MD (kordoc) ──
    print(f"\n[Step 1] HWPX → MD 변환 (kordoc)")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_md = Path(tmp_dir) / f"{input_path.stem}.md"
        if not convert_hwpx_to_md(input_path, tmp_md):
            print("  변환 실패. 중단합니다.")
            return None
        full_text = tmp_md.read_text(encoding="utf-8")
        # kordoc가 이미지 폴더도 생성할 수 있으므로 텍스트만 가져옴
    print(f"  변환 완료 ({len(full_text)}자)")

    # ── Step 2: 정규식 탐지 ──
    print(f"\n[Step 2] 정규식 탐지")
    regex_findings = regex_redact(full_text)
    print(f"  {len(regex_findings)}건 탐지:")
    for f in regex_findings:
        print(f"    [{f['type']}] {f['value']}")

    # ── Step 3: LLM 탐지 ──
    print(f"\n[Step 3] LLM 탐지 ({OLLAMA_MODEL})")
    llm_findings_raw = []
    paragraphs = [p.strip() for p in full_text.split("\n") if p.strip()]
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) > CHUNK_SIZE and current_chunk:
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

    llm_findings = filter_llm_findings(llm_findings_raw, full_text)
    print(f"  {len(llm_findings)}건 탐지:")
    for f in llm_findings:
        print(f"    [{f['type']}] {f['value']}")

    # ── Step 4: 당사자 매핑 ──
    detected_names = [f["value"].replace(" ", "") for f in llm_findings if f["type"] == "사람이름"]
    party_roles = extract_party_roles(full_text, detected_names)
    if party_roles:
        print(f"\n[당사자 매핑]")
        for name, role in party_roles.items():
            if " " not in name:
                print(f"  {name} → {role}")

    # ── Step 5: 치환 ──
    replacements = {}
    for f in regex_findings:
        replacements[f["value"]] = make_masked_label(f["value"], f["type"])
    for f in llm_findings:
        replacements[f["value"]] = make_masked_label(f["value"], f["type"])
    replacements = dict(sorted(replacements.items(), key=lambda x: len(x[0]), reverse=True))

    result = full_text
    for original, masked in replacements.items():
        result = result.replace(original, masked)

    # 이미지 참조 제거
    result = re.sub(r"!\[.*?\]\(.*?\)\n?", "", result)

    final_output.write_text(result, encoding="utf-8")
    print(f"\n완료: {final_output}")
    print(f"총 {len(replacements)}개 고유 항목 마스킹")
    return final_output


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -X utf8 hwpx_to_md_redactor.py <파일.hwpx> [<파일2.hwpx> ...]")
        print("  HWPX → MD 변환 후 개인정보를 제거하여 _제거완.md를 생성합니다.")
        sys.exit(1)

    for src in sys.argv[1:]:
        src_path = Path(src)
        if not src_path.exists():
            print(f"파일 없음: {src}")
            continue
        if src_path.suffix.lower() not in (".hwpx", ".hwp"):
            print(f"지원하지 않는 형식: {src}")
            continue
        print(f"\n{'='*60}")
        print(f"처리 시작: {src_path.name}")
        print(f"{'='*60}")
        result = process_hwpx(str(src_path))
        if result:
            print(f"\n출력 파일: {result}")
