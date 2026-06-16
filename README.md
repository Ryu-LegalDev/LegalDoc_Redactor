# LegalDoc Redactor

한글(HWPX) 법률 서면에서 개인정보를 자동으로 탐지하고 제거하는 로컬 AI 도구입니다.

클라우드 서비스 없이 **Ollama 로컬 LLM + 정규식**을 조합한 하이브리드 방식으로, 민감한 법률 문서를 외부에 전송하지 않고 안전하게 처리합니다.

## 두 가지 모드

| 모드 | 입력 | 출력 | 특징 |
|------|------|------|------|
| **HWPX Redactor** | `.hwpx` | `_제거완.hwpx` | 원본 형식 보존, 이미지 OCR, 브랜딩 제거 |
| **HWPX → MD Redactor** | `.hwpx` | `_제거완.md` | [kordoc](https://github.com/nicejean/kordoc)으로 MD 변환 후 개인정보 제거, 빠른 처리 |

## 주요 기능

### 하이브리드 개인정보 탐지
- **정규식 기반** (우선순위별 처리): 주민등록번호, 전화번호(지역번호 포함), 계좌번호, 법인명(주식회사/은행/증권 등), 주소(도로명/동리)
- **LLM 기반** (Ollama gemma3:4b): 사람 이름, 장소, 상호명 등 문맥 의존적 개인정보
- **당사자 매핑**: 원고/피고/소외인을 헤더에서 자동 인식하여 역할명으로 치환
- **오탐 필터링**: 법률 용어, 법원명, 소송 역할 호칭, 법률명 등을 개인정보로 오인하지 않음
- **금액 보존**: 위자료, 손해배상액, 분양대금 등 금액 정보는 마스킹하지 않음

### HWPX Redactor 전용 기능
- **이미지 내 개인정보 제거**: EasyOCR 기반 이미지 텍스트 탐지, 당사자 역할명으로 대체
- **법무법인 브랜딩 제거**: 마스터페이지 장식 요소, 로고, 직인/도장 이미지 제거
- **HWPX 구조 보존**: 한글에서 정상적으로 열리는 형식 유지

### HWPX → MD Redactor
- **[kordoc](https://github.com/nicejean/kordoc)** (npm 패키지)를 활용하여 HWPX를 Markdown으로 변환
- 변환된 MD에서 개인정보를 탐지/제거하여 `_제거완.md` 출력
- 이미지 처리가 없어 HWPX Redactor 대비 처리 속도가 빠름
- Obsidian, Claude 등 외부 AI 도구에 바로 활용 가능한 형식

## 처리 파이프라인

### HWPX Redactor
```
HWPX 파일
  ├─ 1단계: 정규식 → 주민번호, 전화번호, 계좌번호, 회사명, 주소 탐지
  ├─ 2단계: Ollama LLM → 사람 이름, 장소, 법인명 등 문맥 의존적 탐지
  ├─ 3단계: 당사자 매핑 → 원고/피고/소외인 이름 자동 매핑
  ├─ 4단계: 텍스트 치환 → 본문 XML + 미리보기 텍스트 마스킹
  ├─ 5단계: 이미지 OCR → 이미지 속 이름을 역할명으로 대체
  └─ 6단계: 브랜딩 제거 → 로고, 직인, 페이지 장식 제거
       ↓
  (파일명)_제거완.hwpx
```

### HWPX → MD Redactor
```
HWPX 파일
  ├─ 1단계: kordoc → HWPX를 Markdown으로 변환
  ├─ 2단계: 정규식 → 구조화된 개인정보 패턴 탐지
  ├─ 3단계: Ollama LLM → 문맥 의존적 개인정보 탐지
  ├─ 4단계: 당사자 매핑 → 원고/피고/소외인 역할 매핑
  └─ 5단계: 텍스트 치환 → 이미지 참조 제거 + 개인정보 마스킹
       ↓
  (파일명)_제거완.md
```

## 설치

### 사전 요구사항

- **Windows 10/11**
- **Python 3.10+** — https://www.python.org/downloads/ (설치 시 "Add Python to PATH" 체크)
- **Ollama** — https://ollama.com/download
- **Node.js** — https://nodejs.org/ (HWPX → MD 모드 사용 시)

### 설치 순서

```bash
# 1. Ollama 설치 후 모델 다운로드 (~2.5GB)
ollama pull gemma3:4b

# 2. 저장소 클론
git clone https://github.com/Ryu-LegalDev/LegalDoc_Redactor.git
cd LegalDoc_Redactor

# 3. Python 패키지 설치
pip install -r requirements.txt

# 4. kordoc 설치 (HWPX → MD 모드 사용 시)
npm install -g kordoc
```

> EasyOCR 첫 실행 시 한국어 OCR 모델이 자동 다운로드됩니다 (~100MB).

## 사용법

### HWPX Redactor (GUI)

`redactor_gui.pyw`를 더블클릭하면 파일 선택 GUI가 열립니다.

1. **"파일 선택 후 실행"** 버튼 클릭
2. HWPX 파일 선택 (여러 개 동시 선택 가능)
3. 콘솔 창에서 처리 진행 상황 확인
4. 완료 알림 → 같은 폴더에 `_제거완.hwpx` 파일 생성

### HWPX → MD Redactor (GUI)

`hwpx_to_md_redactor_gui.pyw`를 더블클릭

1. **"HWPX 파일 선택 후 실행"** 버튼 클릭
2. HWPX 파일 선택
3. kordoc 변환 → 개인정보 제거 자동 진행
4. 완료 알림 → 같은 폴더에 `_제거완.md` 파일 생성

### CLI

```bash
# HWPX Redactor
python -X utf8 hwpx_redactor.py "준비서면.hwpx"

# HWPX → MD Redactor
python -X utf8 hwpx_to_md_redactor.py "준비서면.hwpx"

# 여러 파일 동시 처리
python -X utf8 hwpx_to_md_redactor.py "서면1.hwpx" "서면2.hwpx"
```

## 탐지 대상

| 유형 | 탐지 방식 | 예시 |
|------|-----------|------|
| 주민등록번호 | 정규식 | `880101-1234567` |
| 전화번호 | 정규식 (지역번호 포함) | `02-1234-5678`, `010-1234-5678` |
| 계좌번호 | 정규식 | `110-123-456789` |
| 회사명 | 정규식 | `OO주식회사`, `OO은행`, `OO증권` |
| 법무법인명 | 정규식 | `법무법인 OO` |
| 주소 (도로명) | 정규식 | `OO시 OO로 123` |
| 주소 (동리) | 정규식 | `OO시 OO동 123-45` |
| 사람 이름 | LLM | 2~3글자 한국인 이름 (띄어쓰기 포함) |
| 장소/상호 | LLM | 아파트 단지명, 고유 지명 등 |
| 이미지 속 이름 | OCR + LLM 매핑 | 녹취록 캡처 내 이름 → 역할명 대체 (HWPX 모드 전용) |

## 프로젝트 구조

```
LegalDoc_Redactor/
  ├─ hwpx_redactor.py              # HWPX Redactor (원본 형식 보존)
  ├─ redactor_gui.pyw              # HWPX Redactor GUI 런처
  ├─ hwpx_to_md_redactor.py        # HWPX → MD Redactor (kordoc 활용)
  ├─ hwpx_to_md_redactor_gui.pyw   # HWPX → MD Redactor GUI 런처
  ├─ requirements.txt              # Python 패키지 의존성
  └─ LICENSE                       # MIT License
```

## 개인정보 보호 설계

이 도구는 변호사의 의뢰인 비밀유지 의무를 고려하여 설계되었습니다.

- **모든 처리는 로컬에서 수행**: 문서 데이터가 외부 서버에 전송되지 않음
- **Ollama는 stateless**: LLM 호출 시 데이터가 저장되지 않으며, 모델 가중치만 로컬에 보관
- **kordoc도 로컬 처리**: HWPX → MD 변환이 로컬에서 수행됨
- **처리 결과만 외부 활용**: 제거 완료된 `_제거완` 파일을 Obsidian, Claude 등에 안전하게 사용 가능

## 시스템 요구사항

| 항목 | 최소 사양 | 권장 사양 |
|------|-----------|-----------|
| OS | Windows 10 | Windows 11 |
| RAM | 8GB | 16GB+ |
| 디스크 | 5GB (모델+패키지) | 10GB |
| GPU | 불필요 (CPU 처리) | CUDA GPU (처리 속도 향상) |
| 처리 시간 (HWPX) | 1~3분/파일 (CPU) | 30초 이내 (GPU) |
| 처리 시간 (MD) | 30초~1분/파일 (CPU) | 10초 이내 (GPU) |

## 주의사항

- **Ollama가 백그라운드에서 실행 중**이어야 합니다 (시스템 트레이 아이콘 확인)
- HWPX → MD 모드 사용 시 **kordoc이 설치**되어 있어야 합니다 (`npm install -g kordoc`)
- 처리할 파일이 **한글(Hwp)에서 열려있으면** PermissionError가 발생합니다
- LLM은 비결정적이므로 **실행할 때마다 결과가 약간 다를 수 있습니다**
- PowerShell에서 한글 출력 깨짐 방지를 위해 `-X utf8` 플래그를 사용합니다

## 기술 스택

- **Python 3.10+**
- **Ollama** + **gemma3:4b** — 로컬 LLM (한국어 NER)
- **[kordoc](https://github.com/nicejean/kordoc)** — HWPX → Markdown 변환 (npm 패키지)
- **EasyOCR** — 이미지 내 한국어 텍스트 좌표 탐지 (HWPX 모드)
- **Pillow** — 이미지 마스킹 및 텍스트 렌더링 (HWPX 모드)
- **lxml** — HWPX XML 파싱 및 수정 (HWPX 모드)

## 라이선스

MIT License

## 크레딧

- [kordoc](https://github.com/nicejean/kordoc) — HWPX를 Markdown으로 변환하는 CLI 도구. HWPX → MD Redactor 모드의 문서 변환에 활용됩니다.
- [Ollama](https://ollama.com/) — 로컬 LLM 실행 환경
- [gemma3:4b](https://ollama.com/library/gemma3) — Google의 경량 LLM 모델
