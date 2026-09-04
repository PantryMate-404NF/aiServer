# 02 · DIRECTORY_STRUCTURE

**정하는 것**: 파일을 어디에 두는가. 폴더 배치, 네이밍, 분리 기준, import, 문서 형식.

**적용 대상**: aiServer 소스 트리(`src/`)와 저장소의 모든 문서

**버전**: 1.0.0 · **최종 수정**: 2026-09-04 · **작성자**: 김민경

---

## 1. 설계 원칙

### 1.1 기능 중심 구조를 택합니다

**기능 하나를 고칠 때 여는 폴더가 하나여야 하기 때문입니다.** 계층으로 자르면 엔드포인트 하나를 추가할 때 폴더 다섯 개를 오갑니다.

그래서 최상위 경계는 `features/` 이고, 기술 스택으로 자르지 않습니다. 기능 밖에 남는 것은 **여러 기능이 실제로 공유하는 자원(`infra/`)과 도메인 지식이 없는 잡무(`utils/`)** 뿐입니다. 이 둘은 계층이 아니라 **잔여물**입니다.

### 1.2 배제한 것

| 배제 | 이유 |
|---|---|
| 수직 레이어 전용 구조 (`routers/`, `services/`, `repositories/` 를 최상위에) | 기능 하나에 폴더 다섯 개를 오갑니다 |
| 별도 `domain/` 계층 | 도메인 모델은 그 기능이 소유합니다. 기능 밖으로 빼면 기능 중심이 아닙니다 |
| DDD 7계층 | 도메인이 둘인 프로젝트에서 유지 비용이 이득을 넘습니다 |
| 모든 폴더의 `__init__.py` 배럴 | 순환 참조를 만들고 추적을 불가능하게 합니다 |

### 1.3 불변 규칙

1. **의존은 한 방향으로만 흐릅니다.** `features → infra → utils → config`. 역방향과 기능 간 상호 참조를 금지합니다.
2. **무거운 자원의 생성 위치는 한 곳뿐입니다.** OCR 모델, DB 풀, LLM 클라이언트를 여러 곳에서 만들면 메모리와 지연시간으로 즉시 드러납니다.
3. **공통 승격은 두 번째 사용처가 생긴 뒤에 합니다.** "언젠가 쓸 것 같아서"는 근거가 아닙니다.
4. **디렉토리 깊이는 `src/` 기준 4단계를 넘지 않습니다** (예시값, 실제 데이터로 대체 필요).

---

## 2. 디렉토리 트리

### 2.1 저장소 루트

```text
aiServer/
├── src/                      # 애플리케이션 소스. 유일한 소스 루트
├── tests/                    # src 구조를 그대로 반영
├── scripts/                  # 백필·벤치마크·일회성 작업
├── docs/                     # 규칙 문서와 설계 결정 기록
├── data/                     # 로컬 샘플·산출물     [폴더만 커밋, 내용 제외]
├── models/                   # 모델 가중치 캐시     [폴더만 커밋, 내용 제외]
├── pyproject.toml            # 의존성·도구 설정 단일 파일
├── uv.lock                   # 반드시 커밋
├── .env.example              # 키 이름만. 값은 빈 문자열
├── .gitignore                # data/ · models/ · 비밀값 제외
├── README.md                 # 진입점. 문서 목록은 여기 하나뿐입니다
└── CLAUDE.md                 # AI 에이전트 진입점
```

- **src 레이아웃을 씁니다.** 설치되지 않은 코드를 실수로 import하는 사고를 막습니다.
- 루트에 `.py` 를 두지 않습니다. `conftest.py` 도 `tests/` 안입니다.
- `requirements.txt`, `setup.py`, `.flake8`, `mypy.ini` 를 만들지 않습니다. 설정은 `pyproject.toml` 하나입니다.

### 2.2 `src` — 애플리케이션 소스

괄호 안 숫자는 `src/` 기준 깊이입니다.

```text
src/                                  (0)  소스 루트. 이 아래가 곧 최상위 import 이름
├── main.py                           # create_app() 팩토리. uvicorn 이 호출
├── config.py                         # 환경변수 → 설정 객체. 단일 진실 공급원
├── deps.py                           # 의존성 주입 조립부
│
├── features/                         (1)  기능 단위 캡슐화. 최상위 경계
│   ├── receipt/                      (2)  영수증 OCR
│   │   ├── __init__.py               #    공개 API. 이 도메인의 유일한 배럴
│   │   ├── router.py                 #    입출력 경계. 로직 금지
│   │   ├── schema.py                 #    요청·응답·도메인 모델 (pydantic)
│   │   ├── service.py                #    흐름 조립. 구현 없음
│   │   ├── repository.py             #    이 도메인 테이블에 대한 SQL
│   │   ├── tables.py                 #    이 도메인이 소유하는 테이블 정의
│   │   ├── pipeline/                 (3)  처리 단계
│   │   │   ├── preprocess.py         #      회전 보정·이진화·크롭
│   │   │   ├── ocr.py                #      OCR 엔진 인스턴스. 이 파일이 유일
│   │   │   ├── parse.py              #      규칙 기반 1차 추출
│   │   │   └── normalize.py          #      LLM 정규화
│   │   └── prompts/                  (3)
│   │       └── normalize-receipt.md
│   └── recommend/                    (2)  추천
│       ├── __init__.py
│       ├── router.py
│       ├── schema.py
│       ├── service.py
│       ├── repository.py
│       ├── tables.py
│       ├── ingest/                   (3)  1. 데이터 수집·정제
│       ├── engine/                   (3)  2. 추천 엔진
│       │   ├── candidate.py          #      후보 생성
│       │   ├── rank.py               #      점수 계산. 순수 함수
│       │   └── explain.py            #      LLM 설명 생성
│       ├── evaluation/               (3)  3. 평가
│       └── prompts/                  (3)
│           └── explain-recommendation.md
│
├── infra/                            (1)  기능 둘 이상이 실제로 쓰는 외부 자원만
│   ├── db.py                         #    Engine·세션. 생성은 여기만
│   ├── gemini.py                     #    LLM 클라이언트·재시도·타임아웃
│   └── metadata.py                   #    테이블 메타데이터 수집 단일 지점
│
└── utils/                            (1)  도메인 지식이 없는 잡무
    ├── logging.py
    ├── errors.py                     #    AppError 및 하위 예외 계층
    └── time.py
```

**모든 도메인의 루트가 같은 6개 파일을 가집니다.** 다른 도메인으로 옮겨가도 파일 이름이 같아 탐색 비용이 없습니다.

| 위치 | 개수 | 내용 |
|---|---|---|
| 도메인 루트 | `.py` 6개 | `__init__`, `router`, `schema`, `service`, `repository`, `tables` |
| 처리 단계 폴더 | 도메인마다 다름 | 이름은 아래 규칙이 정합니다 |
| `prompts/` | LLM을 쓰는 도메인만 | `.md` 파일만 |

도메인 루트를 6개로 고정한 이유는 5.1의 "디렉토리당 8개" 기준을 넘지 않으면서, 처리 단계가 늘어도 루트가 붐비지 않게 하기 위함입니다.

**처리 단계 폴더 이름은 축의 개수가 정합니다.**

| 축 | 폴더 | 예 |
|---|---|---|
| 하나 | `pipeline/` | `receipt` — 전처리부터 정규화까지 한 흐름입니다 |
| 여럿 | 축 이름 폴더 | `recommend` — `ingest/`, `engine/`, `evaluation/` |

축이 여럿일 때 `pipeline/` 아래에 다시 나누지 않습니다(MUST NOT). 깊이가 4단계가 되고 `pipeline` 층이 아무 의미도 갖지 못합니다.

**축 사이 의존은 한 방향으로만 흐릅니다**(MUST). `recommend` 는 `ingest` 에서 `engine` 으로, `engine` 에서 `evaluation` 으로 흐릅니다. 평가가 엔진을 호출하거나 엔진이 수집을 호출하지 않습니다(MUST NOT). 축을 잇는 흐름은 `service.py` 에만 둡니다. 이 방향이 깨지면 축을 나눈 의미가 사라집니다.

**실행 경로가 다른 축은 진입점도 다릅니다.** 요청을 받아 도는 축만 `router.py` 에 노출하고, 배치로 도는 축의 진입점은 `scripts/` 에 둡니다.

축을 최상위 기능으로 올리는 것은 **셋 중 하나가 만족될 때**입니다. 별도 배포 단위가 되거나, 다른 팀이 소유하거나, 다른 기능이 그 축을 직접 사용하게 될 때입니다. 그전에는 폴더로만 나눠 둡니다(8.1).

**최상위 이름 주의.** `src/` 바로 아래 이름이 곧 최상위 import 이름이 됩니다. `config`, `utils` 같은 흔한 이름이 설치된 패키지와 충돌할 수 있습니다. 새 최상위 모듈을 만들기 전에 확인합니다.

```bash
uv run python -c "import <새이름>"     # ModuleNotFoundError 가 나와야 안전합니다
```

최상위 이름은 6개(`main`, `config`, `deps`, `features`, `infra`, `utils`)로 고정하며, 7번째를 추가하려면 PR 본문에 근거를 씁니다.

### 2.3 `tests`

`src` 구조를 그대로 따라갑니다. 테스트 파일을 찾을 때 경로를 추측하지 않게 하기 위함입니다.

```text
tests/
├── conftest.py
├── unit/
│   ├── receipt/
│   └── recommend/
├── integration/           # 실제 DB, 실제 OCR 엔진
└── fixtures/
    ├── images/            # 샘플 영수증
    └── responses/         # 저장된 LLM 응답
```

### 2.4 `docs` — 문서 배치

```text
docs/
├── README.md                         # docs 폴더 진입점. 목록과 읽는 순서
├── convention/                       # 규칙 문서. 읽는 순서대로 두 자리 번호
│   ├── 01_DEVELOPMENT_RULES.md
│   ├── 02_DIRECTORY_STRUCTURE.md
│   └── 03_IMPLEMENTATION_RULES.md
└── decisions/                        # 설계 결정 기록
    └── 2026-09-04_receipt_schema_boundary.md
```

**파일명 규칙**

| 규칙 | 내용 |
|---|---|
| 구분자 | **언더바(`_`)로만 구분합니다.** 하이픈을 쓰지 않습니다(MUST NOT) |
| 규칙 문서 | `NN_UPPER_SNAKE_CASE.md`. 번호는 **두 자리 고정**입니다. 한 자리를 쓰면 10번대에서 정렬이 깨집니다 |
| 순서 | 번호는 읽는 순서입니다. 중요도 순이 아닙니다 |
| 추가 | 새 규칙 문서는 다음 번호를 이어 붙입니다. 중간에 끼워넣지 않습니다 |
| 변경 | **기존 번호를 바꾸지 않습니다.** 링크와 북마크가 깨집니다 |
| 폴더 진입점 | **`README.md`**. 번호도 접두어도 붙이지 않습니다 |
| 설계 결정 | `docs/decisions/YYYY-MM-DD_제목.md`. 번호를 붙이지 않습니다 |

**폴더 진입점의 파일명은 언제나 `README.md` 입니다**(MUST). 언더바 규칙의 유일한 예외이며, **GitHub가 폴더를 열 때 이 이름만 자동으로 렌더링하기 때문입니다.** `docs_README.md` 처럼 폴더명을 접두어로 붙이면 그 폴더를 열어도 아무것도 보이지 않습니다.

진입점은 그 폴더만 열어도 무엇이 들어 있는지 알아야 할 때 만듭니다. 파일이 서너 개뿐인 폴더에는 만들지 않습니다. 현재는 `docs/README.md` 하나뿐입니다.

**문서 목록은 `docs/README.md` 한 곳에만 둡니다.** 문서를 추가하거나 제거할 때 고칠 곳을 하나로 만들기 위함입니다. **다른 파일에 목록을 복사하지 않습니다**(MUST NOT). 저장소 루트의 `README.md` 와 `CLAUDE.md` 는 목록 대신 `docs/README.md` 링크 하나만 둡니다.

**`docs/` 밖에 두는 파일 2개**

| 파일 | 역할 | 이유 |
|---|---|---|
| `README.md` | 프로젝트 개요와 실행 절차 | GitHub가 저장소 첫 화면에 렌더링합니다 |
| `CLAUDE.md` | 개발 시작점. 규칙 요약과 금지 목록 | Claude Code가 이 경로에서 자동으로 읽습니다 |

**규칙 원문은 `docs/convention/` 에만 둡니다**(MUST). `CLAUDE.md` 는 첫 작업에 필요한 최소 요약을 담을 수 있으나, 각 항목에 **원문 위치를 함께 적어야 하며**(MUST) 요약과 원문이 어긋나면 원문이 이깁니다.

### 2.5 문서 작성 형식

**모든 `.md` 파일이 같은 헤더를 씁니다.** 규칙 문서와 진입점을 구분하지 않습니다. H1 다음에 항목 3개를 이 순서로 두고, **항목 사이를 빈 줄로 띄웁니다**(MUST).

```markdown
# 문서 이름

**정하는 것**: 이 문서가 정하는 범위. 한 문장

**적용 대상**: 누구 또는 무엇에 적용되는가

**버전**: X.Y.Z · **최종 수정**: YYYY-MM-DD · **작성자**: 이름

---
```

- **빈 줄 없이 세 항목을 붙여 쓰지 않습니다**(MUST NOT). 마크다운은 연속된 줄을 한 문단으로 이어 붙여 렌더링하므로, 빈 줄이 없으면 세 항목이 한 줄로 나옵니다.
- 세 항목을 한 줄로 합치지 않습니다(MUST NOT). 항목을 빼거나 순서를 바꾸지 않습니다(MUST NOT). 인용 기호(`>`)를 붙이지 않습니다(MUST NOT).
- **작성자**에는 역할명이 아니라 사람 이름을 씁니다. 물어볼 대상이 분명해야 합니다.
- `docs/convention/` 의 문서는 H1을 `NN · FILENAME_STEM` 형식으로 씁니다. 파일명과 1:1로 대응해 검색으로 찾을 수 있게 합니다.
- **헤더에 다른 문서 링크를 넣지 않습니다**(MUST NOT). 문서가 추가되거나 제거될 때마다 모든 헤더를 고쳐야 하기 때문입니다.

**본문 규칙**

| # | 규칙 | 이유 |
|---|---|---|
| 1 | 헤딩은 `###` 까지만 씁니다. `####` 를 금지합니다 | 4단계가 필요하면 절 구성이 틀린 것입니다 |
| 2 | 절 번호는 `## N.` 과 `### N.M` 두 단계입니다. 본문 참조는 `N.M` 형식입니다 | 참조 형식이 섞이면 검색이 안 됩니다 |
| 3 | `---` 는 `##` 절 사이에만 넣습니다 | 구분선이 흔해지면 구분 기능을 잃습니다 |
| 4 | 코드 블록에 **언어 태그를 반드시 붙입니다**. 트리와 메시지 예시는 `text` 입니다 | 태그가 없으면 강조가 안 되고 복사 시 형식이 깨집니다 |
| 5 | **이모지를 쓰지 않습니다.** 트리와 표에서도 금지합니다 | 폰트·터미널·에디터마다 폭이 달라 정렬이 깨지고, 검색과 diff를 방해합니다 |
| 6 | 체크리스트는 `- [ ]` 만 쓰고 **항목은 명사구로** 씁니다. 완료 표시를 남기지 않습니다 | 문서의 체크박스는 템플릿입니다. 명사구가 짧고 어조에 휘둘리지 않습니다 |
| 7 | 굵은 글씨는 조항의 핵심 한 구절에만 씁니다. 한 문단에 2개 이하입니다 | 전부 강조하면 아무것도 강조되지 않습니다 |
| 8 | 기울임을 쓰지 않습니다 | 한글에서 기울임은 가독성만 떨어뜨립니다 |
| 9 | 파일·경로·명령·코드 심볼은 백틱으로 감쌉니다 | 백틱이 붙은 것은 그대로 복사해 쓸 수 있어야 합니다 |
| 10 | 내부 링크는 상대 경로를 씁니다 | 클론하거나 포크해도 링크가 살아 있어야 합니다 |
| 11 | 검증되지 않은 정량 기준 뒤에 `(예시값, 실제 데이터로 대체 필요)` 를 붙입니다 | 근거 없는 숫자가 근거처럼 굳는 것을 막습니다 |
| 12 | "상황에 따라", "가급적", "적절히", "유연하게" 를 쓰지 않습니다 | 해석이 갈리는 표현은 규칙이 아닙니다 |
| 13 | 모든 문서를 `~합니다`체로 씁니다. 인용된 산출물(커밋 메시지, 로그)은 예외입니다 | 어조가 섞이면 규칙과 설명의 경계가 흐려집니다 |
| 14 | 규칙 문서는 `## 구성 근거` 로 끝냅니다. 2~3문단 | 근거가 없으면 다음 사람이 같은 논의를 반복합니다 |

**형식 점검**

```bash
FILES=$(find . -name "*.md" -not -path "./.git/*" -not -path "./.venv/*")

awk '/^```/{n++} /^```$/ && n%2==1 {print FILENAME": "FNR}' $FILES   # 언어 태그 누락
grep -n "^#### " $FILES                                              # 4단계 헤딩
grep -nE "상황에 따라|가급적|적절히|유연하게" $FILES                  # 모호 표현
grep -nP "[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]" $FILES             # 이모지
```

---

## 3. 디렉토리별 역할과 금지

### 3.1 최상위

| 위치 | 역할 | 포함 | 절대 포함 금지 |
|---|---|---|---|
| `config.py` | 환경변수를 읽는 단일 진입점 | 설정 클래스, 튜닝 상수(임계값·가중치·타임아웃), 모델명 | 비즈니스 로직, 비밀값 리터럴 |
| `deps.py` | 요청 단위 자원 조립·주입 | 세션·클라이언트 주입 함수 | 도메인 로직, SQL, 자원의 최초 생성 |
| `features/` | 기능 단위 캡슐화 | 도메인 폴더만 | 도메인에 속하지 않는 느슨한 `.py` |
| `infra/` | **둘 이상의 기능이 실제로 쓰는** 외부 자원 | DB 엔진, LLM 클라이언트, 테이블 메타데이터 수집 | `features/` import, 한 기능만 쓰는 자원 |
| `utils/` | 도메인 지식이 없는 잡무 | 로깅 설정, 예외 계층, 시간·경로 헬퍼 | 도메인 지식, DB 접근, 외부 API 호출 |

### 3.2 기능 내부

| 파일 | 역할 | 포함 | 절대 포함 금지 |
|---|---|---|---|
| `__init__.py` | 이 도메인의 공개 API | re-export, `__all__` | 로직, 조건문, 부수효과 |
| `router.py` | 입출력 경계 | 요청 파싱, 응답 직렬화, 주입 선언 | 비즈니스 로직, SQL, 외부 API 직접 호출 |
| `schema.py` | 경계를 넘는 데이터 구조와 도메인 모델 | pydantic 모델, 검증 규칙 | DB 테이블 정의, 비즈니스 로직 |
| `service.py` | 흐름만 조립 | 단계 호출 순서, 트랜잭션 경계 | 구현 상세, SQL, 파싱, 프롬프트 문자열 |
| `repository.py` | 이 도메인 테이블에 대한 SQL | 쿼리, 매핑 | 다른 도메인 테이블 접근, 비즈니스 판단 |
| `tables.py` | 이 도메인이 소유하는 테이블 | SQLAlchemy 테이블 | 다른 도메인 테이블 정의 |
| `pipeline/` 또는 축 폴더 | 이 도메인의 처리 단계 | 단계별 모듈 | 다른 도메인 import, 축 간 역방향 호출, 흐름 조립 |
| `prompts/` | LLM 프롬프트 원문 | `.md` 파일만 | `.py` 파일, 하드코딩된 값 |

### 3.3 지원 디렉토리

| 위치 | 역할 | 금지 |
|---|---|---|
| `tests/` | `src` 구조를 반영한 테스트 | 실제 외부 API를 호출하는 단위 테스트 |
| `scripts/` | 백필·벤치마크·일회성 작업 | 애플리케이션이 import하는 코드 |
| `docs/decisions/` | 되돌리기 어려운 설계 결정 1건당 1파일 | 회의록, 임시 메모 |
| `data/`, `models/` | 로컬 산출물과 모델 가중치 캐시 | **안의 파일을 커밋하는 것을 금지합니다.** 폴더 자체는 `.gitkeep` 으로 유지해 클론 직후에도 존재합니다 |

---

## 4. 네이밍

| 대상 | 규칙 | 예시 | 도구 검증 |
|---|---|---|---|
| 패키지·모듈 | `snake_case`, 단수형 | `receipt`, `preprocess.py` | ruff `N999` |
| 클래스 | `PascalCase` | `ReceiptItem`, `RankedCandidate` | ruff `N801` |
| 함수·메서드·변수 | `snake_case` | `extract_items`, `min_confidence` | ruff `N802`, `N806` |
| 상수·환경변수 | `UPPER_SNAKE_CASE` | `DEFAULT_TIMEOUT_SEC`, `GEMINI_API_KEY` | 리뷰 |
| 내부 전용 | 앞에 `_` | `_build_payload` | 리뷰 |
| 테스트 파일 | `test_` + 대상 모듈명 | `test_preprocess.py` | pytest |
| 프롬프트·정적 자산 | `kebab-case` + 확장자 | `normalize-receipt.md` | 리뷰 |

**camelCase를 쓰지 않습니다.** PEP 8 위반이며 ruff `N` 규칙이 실패시킵니다. 웹 프론트엔드 관례를 그대로 가져오지 않습니다.

```python
# src/features/receipt/pipeline/parse.py
"""OCR 텍스트에서 품목·금액·날짜를 규칙 기반으로 추출합니다."""

from __future__ import annotations

from config import settings  # 절대 경로
from features.receipt.schema import OcrLine  # 같은 도메인도 직접 경로

MIN_LINE_HEIGHT_PX = 12  # UPPER_SNAKE_CASE 상수


class ReceiptParser:  # PascalCase
    def extract_items(self, lines: list[OcrLine]) -> list[ParsedItem]:  # snake_case
        ...

    def _merge_wrapped_lines(self, lines: list[OcrLine]) -> list[OcrLine]:  # 내부 전용
        ...
```

```python
# 금지 예시
from .schema import OcrLine       # 상대 import 금지 (7절)
def extractItems(...): ...        # camelCase — ruff N802 실패
MIN = 12                          # 단위와 대상이 없는 이름
class receipt_parser: ...         # 클래스는 PascalCase — ruff N801 실패
```

**이름 짓기 3원칙**

1. **패키지 이름을 모듈 이름에 반복하지 않습니다.** `receipt/receipt_parser.py` 가 아니라 `receipt/parse.py` 입니다.
2. **`utils.py`, `helpers.py`, `common.py`, `misc.py`, `manager.py` 라는 파일을 만들지 않습니다.** 최상위 `utils/` 폴더는 예외이며, 그 안의 파일은 `logging.py`, `errors.py` 처럼 내용을 드러내야 합니다.
3. **단위를 이름에 넣습니다.** `timeout` 대신 `timeout_sec`, `amount` 대신 `amount_krw`. 불리언은 `is_`, `has_`, `should_` 로 시작합니다.

---

## 5. 파일 분리 기준

### 5.1 나눠야 할 때

| 지표 | 임계값 | 조치 |
|---|---|---|
| 단일 파일 라인 수 | 300줄 초과 (예시값, 실제 데이터로 대체 필요) | 분리 지점을 찾습니다 |
| 단일 파일 라인 수 | 500줄 초과 (예시값, 실제 데이터로 대체 필요) | **반드시 분리합니다** |
| 디렉토리 내 파일 수 | 8개 초과 (예시값, 실제 데이터로 대체 필요) | 하위 폴더를 만듭니다. 깊이 4단계를 넘길 수는 없습니다 |
| 모듈의 공개 심볼 수 | 7개 초과 (예시값, 실제 데이터로 대체 필요) | 책임이 둘 이상입니다 |
| 단일 함수 라인 수 | 50줄 초과 (예시값, 실제 데이터로 대체 필요) | 분리 지점을 찾습니다 |
| 함수 인자 수 | 5개 초과 | dataclass나 pydantic 모델로 묶습니다 |

### 5.2 나누지 말아야 할 때

- 20줄짜리 함수를 위해 새 모듈을 만들지 않습니다.
- 한 곳에서만 쓰는 dataclass·상수는 쓰는 파일 안에 둡니다.
- "재사용될지도 모르니까"는 분리 사유가 아닙니다.

### 5.3 판단 순서

임계값은 신호이지 명령이 아닙니다. 300줄을 넘었다면 이렇게 봅니다.

1. 이 파일이 하는 일을 **한 문장으로** 말할 수 있는가. 가능하면 그대로 둡니다.
2. 두 문장이 필요한가. 문장 단위로 자릅니다.
3. 자른 조각의 이름을 한 단어로 지을 수 있는가. 못 지으면 자르는 위치가 틀렸습니다.

---

## 6. 신규 기능 추가 체크리스트

1. **위치를 정합니다.** 기존 도메인인지 새 도메인인지 판단하고, 새 도메인이면 근거를 PR 본문에 한 문장으로 씁니다.
2. **파일을 만듭니다.** 아래 표의 필수 파일만 만듭니다. 미리 만들지 않습니다.
3. **의존 방향을 확인합니다.** 다른 도메인을 import하지 않는지, `infra` 가 `features` 를 import하지 않는지 봅니다.
4. **공개 범위를 정합니다.** 다른 도메인이 써야 하는 것만 `__init__.py` 에 re-export합니다.
5. **검증하고 PR을 올립니다.** 9절과 `01_DEVELOPMENT_RULES.md` 3.2를 실행하고 통과 출력을 첨부합니다.

| 상황 | 새로 만드는 파일 | 개수 |
|---|---|---|
| 기존 도메인에 엔드포인트 1개 추가 | 없음 (`router`, `service`, `schema` 수정) | 0개 |
| 기존 도메인에 외부 연동 1개 추가 | 연동 모듈 1개 | 1개 |
| 새 도메인 (HTTP 노출 없음) | `__init__`, `schema`, `service`, `repository` | 4개 |
| 새 도메인 (HTTP 노출 + 테이블 소유) | 위 4개 + `router` 또는 `tables` | 5개 |

**새 도메인 하나에 만드는 파일은 5개를 넘지 않습니다.** 6개째가 필요하다고 느껴지면 도메인 경계를 먼저 의심합니다.

---

## 7. import와 배럴

### 7.1 절대 경로

파이썬에는 번들러 별칭(`@/components`)이 없습니다. **`src/` 가 소스 루트** 역할을 하며, 어느 파일에서든 경로가 같습니다.

```python
from config import settings
from features.receipt.schema import OcrLine
from utils.errors import OcrError
```

```toml
[tool.hatch.build.targets.wheel]
only-include = ["src"]
sources = ["src"]            # src/ 내용을 최상위로 매핑

[tool.ruff.lint.flake8-tidy-imports]
ban-relative-imports = "all" # 상대 import 전면 금지 (TID252)
```

`uv sync` 가 패키지를 editable로 설치하므로 별도 `PYTHONPATH` 설정이 필요 없습니다. IDE 자동완성은 `.vscode/settings.json` 에 `"python.analysis.extraPaths": ["src"]` 를 넣습니다.

### 7.2 import 3규칙

1. **절대 경로만 씁니다.** 상대 import를 ruff `TID252` 가 차단합니다.
2. **같은 도메인 내부에서는 배럴을 경유하지 않습니다.** `features/receipt/service.py` 는 `features.receipt.pipeline.parse` 를 직접 import합니다. 배럴 경유가 순환 참조의 첫 번째 원인입니다.
3. **다른 도메인은 배럴만 봅니다.** `recommend` 는 `features.receipt` 의 공개 API만 호출하고 `features.receipt.repository` 를 직접 import하지 않습니다.

### 7.3 배럴 남용 방지

| 위치 | `__init__.py` 내용 |
|---|---|
| `features/<도메인>/__init__.py` | 공개 API re-export + `__all__`. **이 프로젝트의 유일한 배럴입니다** |
| 그 외 모든 폴더 | 비워 둡니다. re-export 금지 |

```python
# src/features/receipt/__init__.py
from features.receipt.service import process_receipt
from features.receipt.schema import ReceiptResult

__all__ = ["ReceiptResult", "process_receipt"]
```

**금지** — 모든 폴더에 배럴 두기, 배럴 안의 로직·조건문·부수효과, `__all__` 없는 re-export, 순환 참조를 `TYPE_CHECKING` 으로 우회하기. 순환 참조는 우회하지 않고 **구조를 고칩니다**.

---

## 8. 예외

### 8.1 공통 승격 유예

**두 개 이상의 독립된 도메인에서 실제 사용이 확인되기 전까지는 `utils/` 나 `infra/` 로 승격하지 않습니다.**

| 상황 | 위치 |
|---|---|
| `receipt` 만 사용 | `features/receipt/pipeline/ocr.py` |
| `receipt` 와 `recommend` 가 모두 사용 | `infra/` 또는 `utils/` 로 이동 |
| "언젠가 쓸 것 같음" | 이동하지 않습니다 |

이 프로젝트가 무너지는 가장 흔한 경로가 성급한 공통화입니다. 되돌리는 비용이 옮기는 비용보다 큽니다.

### 8.2 점진적 마이그레이션

1. **신규 규칙은 `src/features/` 에만 적용합니다.**
2. 기존 파일은 **그 파일을 수정할 일이 생겼을 때만** 1:1로 옮깁니다.
3. 이전 작업과 기능 변경을 같은 PR에 섞지 않습니다.
4. 옮기지 못한 파일은 `docs/decisions/` 에 목록으로 남기고, 항목마다 막고 있는 이유를 한 줄씩 씁니다.

---

## 9. 검증

```bash
# 깊이 4단계 이하 (macOS)
find src -type d | awk -F/ '{print NF-1, $0}' | sort -rn | head -5

# 순환 참조. ImportError 로 즉시 실패합니다
uv run python -c "import features.receipt, features.recommend"

# 의존 방향. 결과가 있으면 위반입니다
grep -rn "from features.recommend" src/features/receipt/
grep -rn "from features" src/infra/

# 디렉토리당 파일 수 8개 초과 (예시값, 실제 데이터로 대체 필요)
find src -type d -exec sh -c 'echo "$(ls -1 "$1"/*.py 2>/dev/null | wc -l) $1"' _ {} \; | sort -rn | head -5
```

의존 방향을 도구로 강제하려면 `import-linter` 도입이 필요하며, `01_DEVELOPMENT_RULES.md` 1절의 신규 도구 승인 절차를 거칩니다.

---

## 구성 근거

최상위를 기능으로 자른 이유는 2~5인 팀에서 폴더 간 이동 시간이 실제 구현 시간을 넘기 때문입니다. 계층 구조에서는 엔드포인트 하나에 폴더 다섯 개를 오갑니다. 같은 이유로 `domain/` 계층도 두지 않았습니다. 도메인 모델을 기능 밖으로 빼는 순간 기능 중심이 아니게 됩니다.

`infra/` 와 `utils/` 를 남긴 것은 계층을 만들기 위해서가 아니라 기능에 넣을 수 없는 것이 실제로 남기 때문입니다. 그래서 승격에 "두 번째 사용처" 조건을 붙였습니다. 사용처가 하나인 것과 둘인 것을 같은 층에 두면 몇 달 뒤 아무도 구분하지 못합니다.

배럴을 도메인 진입점 하나로 제한한 것은 순환 참조와 탐색 비용 때문입니다. 모든 폴더에 re-export를 두면 IDE의 정의로 이동이 배럴에서 멈춰 실제 구현까지 두 번 이상 점프해야 합니다.
