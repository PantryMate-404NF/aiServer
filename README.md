# aiServer

**정하는 것**: 프로젝트 개요와 실행 절차. 규칙 본문은 없습니다.

**적용 대상**: 이 저장소를 처음 여는 모든 사람

**버전**: 1.1.0 · **최종 수정**: 2026-09-16 · **작성자**: 김민경

---

## 1. 무엇을 하는 서버인가

Python 3.12 기반 AI 서버입니다. 기능은 두 개입니다.

| 기능 | 내용 |
|---|---|
| 영수증 OCR | 영수증 사진 1장에서 구매일과 식재료 품목명을 추출합니다. 금액·개수·상호명은 뽑지 않습니다 |
| 추천 | 소비 데이터로 후보를 만들고 점수를 매겨 상위 N개와 설명을 반환합니다 |

**스택**: Python 3.12 · uv · PostgreSQL 16.15 · PaddleOCR PP-OCRv5(`lang='korean'`) · Gemini 3.5 Flash Lite
**소스 루트**: `src/` 입니다. 그 아래에 중간 계층을 두지 않으므로 import는 `from features.receipt import ...` 형태입니다.

---

## 2. 시작하기

```bash
git clone <repository-url> && cd aiServer
uv sync                          # 의존성 설치. pip 을 직접 쓰지 않습니다
cp .env.example .env             # 값은 팀 비밀 저장소에서 개별 수령합니다
uv run pytest tests/unit         # 환경 정상 여부 확인
uv run uvicorn main:create_app --factory --reload   # 개발 서버 실행
```

완료를 선언하기 전에 아래를 실행하고, 통과한 출력을 근거로만 보고합니다.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit
```

통합 테스트는 실제 OCR 모델을 올려 느리므로 기본 실행에서 빠집니다. 파이프라인 배선을 바꿨다면 아래를 함께 돌립니다.

```bash
uv run pytest -m integration --no-cov
```

**추천 데이터**: 레시피가 늘거나 식재료 사전(`seeds/`)이 바뀌면 `make renormalize` 한 번이면 됩니다.
정규화·피처·맛 벡터·클러스터를 순서대로 다시 만들고 회귀 게이트까지 돌립니다 (약 11분).
명령 목록은 `make help` 에 있습니다.

---

## 3. 컨테이너로 실행

서비스가 하나라 이미지도 하나입니다. Dockerfile 은 저장소 루트에 있고 빌드도 루트에서 합니다.

```bash
docker build -t reco-ai-server:local .
docker run --rm -p 8000:8000 --env-file .env reco-ai-server:local
```

로컬에서 DB 와 함께 띄우려면 compose 를 씁니다. `deploy/.env` 에 `INTERNAL_API_KEY` 와 `GEMINI_API_KEY` 가 비어 있으면 **앱이 기동 시점에 멈춥니다** — 값이 없으면 없다고 말하는 것이 `config.py` 의 의도된 동작입니다.

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
| 실행 | `uvicorn main:create_app --factory --workers 1`. 앱이 OCR 프로세스 풀을 따로 띄우므로 워커를 늘리면 메모리가 배로 듭니다 — 확장은 레플리카로 합니다 |
| 필수 환경변수 | `DB_HOST` · `DB_NAME` · `DB_USER` · `DB_PASSWORD` · `INTERNAL_API_KEY` · `GEMINI_API_KEY` (나머지는 기본값. 목록은 `.env.example`) |
| 이미지 크기 | 2.62GB (paddle 719MB · opencv 182MB · OCR 모델 98MB). OCR 모델은 빌드 때 구워 넣어 기동 즉시 `ready` 이고 외부 통신이 없어도 돕니다 |
| 사용자 | 비 root (`uid 10001`) |

배포를 맡는 쪽에 필요한 것(런타임 계약·외부 의존·실측 자원·확인하지 못한 것)은 [docs/container_handover.md](docs/container_handover.md) 에 있습니다.

---

## 4. 개발 규칙

규칙 문서의 목록과 읽는 순서는 [docs/README.md](docs/README.md) 에 있습니다. 첫 작업 전에 그 문서부터 엽니다.
AI 코딩 에이전트로 작업한다면 [CLAUDE.md](CLAUDE.md) 가 시작점입니다.
