# aiServer

**정하는 것**: 프로젝트 개요, 실행 절차, 열려 있는 API, 운영에 필요한 사실. 규칙 본문은 없습니다.

**적용 대상**: 이 저장소를 처음 여는 모든 사람

**버전**: 2.0.0 · **최종 수정**: 2026-09-30 · **작성자**: 김민경

---

## 1. 무엇을 하는 서버인가

영수증 사진에서 식재료를 읽고, 사용자의 식재료와 취향으로 레시피를 추천하는 AI 서버입니다. 호출자는 백엔드 서버 하나이며, 앱이 직접 부르지 않습니다.

| 기능 | 내용 |
|---|---|
| 영수증 OCR | 영수증 사진 1장에서 구매일과 식재료 품목명을 추출합니다. 금액·개수·상호명은 뽑지 않습니다. 흐름은 전처리, PaddleOCR 인식, Gemini 후처리, 개인정보 마스킹, 품목 정규화 순서입니다 |
| 추천 | 보유 식재료와 취향으로 후보를 찾고(Retrieval), 17개 피처로 점수를 매기고(Ranking), 다양성과 탐색을 섞어(Re-ranking) 상위 N개와 이유를 반환합니다. 백엔드 주소가 없으면 목업이 답합니다 |

**스택**: Python 3.12 · FastAPI · uv · PostgreSQL 16 · PaddleOCR PP-OCRv5(`lang='korean'`) · Gemini 3.5 Flash Lite · Prometheus · Grafana

---

## 2. 시작하기

```bash
git clone <repository-url> && cd aiServer
uv sync                          # 의존성 설치. pip 을 직접 쓰지 않습니다
cp .env.example .env             # 값은 팀 비밀 저장소에서 개별 수령합니다
uv run pytest tests/unit         # 환경 정상 여부 확인
uv run uvicorn main:create_app --factory --reload   # 개발 서버 실행. 포트 8000
```

필수 환경변수는 여섯 개입니다. 비어 있으면 앱이 기동 시점에 멈춥니다. 값이 없으면 없다고 말하는 것이 `config.py` 의 의도된 동작입니다.

| 변수 | 받는 곳 |
|---|---|
| `DB_HOST` · `DB_NAME` · `DB_USER` · `DB_PASSWORD` | 로컬은 `make up` 이 띄우는 compose 값. 운영은 클라우드 팀 |
| `INTERNAL_API_KEY` | 백엔드와 합의한 값. 팀 비밀 저장소 |
| `GEMINI_API_KEY` | 김민경. 팀 비밀 저장소 |

나머지는 기본값이 있습니다. 전체 목록과 각 값의 뜻은 [docs/env_variables.md](docs/env_variables.md) 에 있습니다. `BACKEND_BASE_URL` 을 비우면 추천은 목업으로 답하므로 백엔드 없이도 개발할 수 있습니다.

---

## 3. API

`/health/*` 와 관리자 페이지 껍데기를 빼면 모든 경로가 `X-Internal-Api-Key` 헤더를 요구합니다. 값은 `INTERNAL_API_KEY` 와 같아야 하고 틀리면 401 입니다. 요청·응답 모양과 오류 코드는 [docs/backend_api_spec.md](docs/backend_api_spec.md) 가 정본이며, 문서와 코드가 어긋나면 코드가 맞습니다.

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| POST | `/v1/ocr/receipt` | 영수증 이미지 1장(`multipart`, 10MB 이하)을 받아 구매일·품목·신뢰도를 돌려줍니다 |
| POST | `/v1/recommend` | 사용자 한 명에게 레시피 상위 N개와 이유를 돌려줍니다 |
| GET | `/v1/recommendations/{request_id}` | 추천 한 건의 기록을 다시 읽습니다 |
| POST | `/v1/events` | 조회·조리·저장 같은 행동 로그를 받습니다. 노출은 서버가 자동 기록합니다 |
| GET | `/v1/ingredients/search` | 식재료 이름 검색 |
| GET | `/v1/recipes/search` | 레시피 이름 검색 |
| GET · PUT | `/v1/users/{user_id}/pantry` | 사용자 보유 식재료 조회·갱신 |
| GET | `/v1/onboarding/presented` | 온보딩에 보여줄 음식 목록 |
| GET | `/v1/onboarding/taste-axes` | 온보딩에서 묻는 맛 척도 |
| POST | `/v1/onboarding/{user_id}` | 온보딩 응답을 받아 취향 벡터를 만듭니다 |
| GET | `/v1/admin/monitoring/summary` | 추천 엔진 진단 요약 |
| GET | `/health` | 추천 쪽 상태. DB 연결과 모델 이름 |
| GET | `/health/live` | 프로세스 생존. 인증 없음 |
| GET | `/health/ready` | OCR 모델 적재 완료. 그 전에는 503. 인증 없음 |
| GET | `/metrics` | Prometheus 수집 지점. 내부 키 또는 Bearer 토큰 |
| GET | `/admin/monitoring` | 관리자 페이지. 페이지는 열려 있고 숫자는 키를 넣어야 나옵니다 |

서버를 띄운 뒤 `http://localhost:8000/docs` 에서 OpenAPI 문서를 볼 수 있습니다.

---

## 4. 실행과 배포

서비스가 하나라 이미지도 하나입니다. Dockerfile 은 저장소 루트에 있고 빌드도 루트에서 합니다.

```bash
docker build -t reco-ai-server:local .
docker run --rm -p 8000:8000 --env-file .env reco-ai-server:local
```

로컬에서 DB 와 함께 띄우려면 compose 를 씁니다. 값은 `deploy/.env` 에서 읽습니다.

```bash
make up-app                         # postgres · redis · reco-api
curl -s localhost:8000/health/live  # 프로세스 생존
curl -s localhost:8000/health/ready # OCR 모델 로딩 완료 (그 전에는 503)
```

| 항목 | 값 |
|---|---|
| 베이스 | `python:3.12-slim`. 3.11 에서는 설치가 거부됩니다 |
| 의존성 | `uv.lock` 을 `uv sync --frozen --no-dev` 로 설치합니다. `requirements.txt` 는 없습니다 |
| 포트 | 8000 |
| 실행 | `uvicorn main:create_app --factory --workers 1`. 워커를 늘리면 OCR 프로세스 풀과 레시피 사전이 워커 수만큼 생겨 메모리가 배로 들고 지표가 널뜁니다. 확장은 레플리카로 합니다 |
| 이미지 크기 | 2.62GB (paddle 719MB · opencv 182MB · OCR 모델 98MB). OCR 모델은 빌드 때 구워 넣어 외부 통신이 없어도 기동합니다 |
| 기동 시간 | `/health/ready` 200 까지 8~14초 |
| 메모리 | 유휴 456MiB, 영수증 처리 중 536MiB, 레시피 사전 적재 뒤 약 600MiB (`OCR_WORKERS=1`). 요청 512Mi, 상한 1Gi 를 시작점으로 권합니다 |
| 볼륨 | `/app/var` 에 지속 볼륨을 붙입니다. 취향 원본과 추천 로그가 거기 놓이며, 없으면 배포마다 사라집니다 |
| 사용자 | 비 root (`uid 10001`) |
| GPU | 쓰지 않습니다. CPU 전용 구성입니다 |

배포를 맡는 쪽에 필요한 것(런타임 계약·외부 의존·실측 자원·확인하지 못한 것)은 [docs/container_handover.md](docs/container_handover.md) 에, 클라우드 DB 에 스키마와 사전을 넣는 절차는 [deploy/SETUP_CLOUD.md](deploy/SETUP_CLOUD.md) 에 있습니다.

---

## 5. 구조

`src/` 가 소스 루트입니다. 그 아래에 중간 계층을 두지 않으므로 import 는 `from features.receipt import ...` 형태입니다.

```text
aiServer/
├── src/
│   ├── main.py, config.py, deps.py   앱 조립 · 설정 · 인증 의존성
│   ├── features/
│   │   ├── receipt/                   영수증 OCR. pipeline/ 에 s1~s5 단계, prompts/ 에 LLM 프롬프트
│   │   └── recommend/                 추천. engine/ 점수·재정렬, ingest/ 피처 생성, evaluation/ 모니터링
│   ├── infra/                         둘 이상의 기능이 쓰는 외부 자원. DB · Gemini · 메타데이터
│   └── utils/                         도메인 지식이 없는 잡무. 에러 · 로깅 · 지표 · 추적
├── tests/
│   ├── unit/                          외부 네트워크 · 실제 DB 없이 도는 테스트
│   └── integration/                   실제 OCR 모델을 올리는 느린 테스트
├── deploy/                            compose · DB 초기화 SQL · Prometheus · Grafana 설정
├── seeds/                             식재료 사전과 분류 체계. 추천의 정본 데이터
├── scripts/                           평가 · 벤치마크 · 시뮬레이션 · 데이터 보정 스크립트
├── raw_data/, data/, models/          git 에 올리지 않는 원본 · 산출물 · 가중치 자리
├── review/                            사람이 손으로 채우는 검수 산출물 자리
├── docs/                              규칙 · 설계 · 기록 문서
├── Dockerfile, Makefile, Jenkinsfile
└── CLAUDE.md                          AI 코딩 에이전트의 시작점
```

폴더 진입점은 각 폴더의 `README.md` 입니다. `deploy/` `seeds/` `raw_data/` `review/` 에 있습니다.

---

## 6. 검증

변경을 마쳤다고 선언하기 전에 아래를 실행합니다. 네 명령이 모두 종료 코드 0 이면 통과입니다. 화면 마지막 줄이 아니라 종료 코드로 판정합니다.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit
```

| 명령 | 보는 것 |
|---|---|
| `ruff check` | 린트 |
| `ruff format --check` | 포맷. 고치려면 `--check` 를 뺍니다 |
| `mypy src` | 타입 |
| `pytest tests/unit` | 단위 테스트. 외부 네트워크 · 실제 DB · 유료 API 를 부르지 않습니다 |

통합 테스트는 실제 OCR 모델을 올려 느리므로 기본 실행에서 빠집니다. 파이프라인 배선을 바꿨다면 함께 돌립니다.

```bash
uv run pytest -m integration --no-cov
```

**추천 데이터**: 레시피가 늘거나 식재료 사전(`seeds/`)이 바뀌면 `make renormalize` 한 번으로 정규화·피처·맛 벡터·클러스터를 다시 만들고 회귀 게이트까지 돌립니다(약 11분). 명령 목록은 `make help` 에 있습니다.

---

## 7. 운영 정보

**로그**는 표준 출력으로 나갑니다. 요청마다 메서드·경로·상태·소요 시간과 `X-Request-Id` 를 남기고 본문은 남기지 않습니다. 업로드 이미지와 OCR 원문에 개인정보가 섞이기 때문입니다. 추천 로그와 취향 원본은 파일로 `/app/var` (로컬은 `var/`) 아래에 쌓입니다. 추천 한 건에 약 19KB 이며 지우는 장치가 없습니다.

**모니터링**은 세 층입니다.

```bash
make up-obs        # prometheus · grafana · mlflow
```

| 진입점 | 보는 것 |
|---|---|
| `http://localhost:8000/admin/monitoring` | 관리자 페이지. 내부 API 키를 넣으면 지금 고칠 것을 뽑아 줍니다 |
| `http://localhost:9090/targets` · `/alerts` | Prometheus 수집 대상과 경보 규칙 상태 |
| `http://localhost:3000` | Grafana. `추천시스템` 폴더의 "추천 엔진 — 평가와 모니터링" |

무엇을 재고 무엇을 경고하는지는 [docs/recommend/recommend_monitoring.md](docs/recommend/recommend_monitoring.md) 에 있습니다.

**알려진 제약**

- 영수증 OCR 정확도는 정답 셋 48장에서 96.7% 입니다. 정답 셋과 키는 저장소에 없습니다.
- 영수증 처리는 한 장에 8~11초입니다. `OCR_WORKERS=1` 에서 업로드가 겹치면 뒤 요청은 자리를 기다리고, `OCR_QUEUE_TIMEOUT_SEC` 을 넘기면 실패합니다.
- 추천 응답의 `confidence` 임계치는 서버가 정하지 않습니다. 어디부터 낮은 값인지는 호출자 몫입니다.
- 레플리카를 둘 이상 두면 `/app/var` 의 취향 파일이 레플리카마다 갈라집니다. 늘리기 전에 볼륨을 어떻게 나눌지 먼저 정합니다.
- 경보 임계값은 착수 추정치입니다(예시값, 실제 데이터로 대체 필요).
- `main` 에 무엇이 언제 들어갔고 되돌리려면 무엇을 하는지는 [docs/release_notes.md](docs/release_notes.md) 에 있습니다.

---

## 8. 문서 지도

문서 목록과 읽는 순서는 [docs/README.md](docs/README.md) 한 곳에만 둡니다. 여기에는 목록을 복사하지 않습니다.

- 규칙 원문은 `docs/convention/` 에 있습니다. 첫 작업 전에 01 을 통독합니다.
- AI 코딩 에이전트로 작업한다면 [CLAUDE.md](CLAUDE.md) 가 시작점입니다.
- 설계 결정의 근거는 `docs/decisions/` 에 날짜별로 있습니다.

---

## 9. 담당과 라이선스

| 영역 | 담당 |
|---|---|
| 영수증 OCR, 컨테이너, 문서 규칙 | 김민경 |
| 추천 엔진, 평가와 모니터링 (파트 B · C) | 유재현 |
| 데이터 파이프라인, 식재료 사전, DB 부트스트랩 (파트 A) | 박재우 |

전체 기여자는 `git log` 로 확인합니다. 저장소에 라이선스 파일은 없으며 팀 내부 프로젝트로 다룹니다.
