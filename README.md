# aiServer

**정하는 것**: 프로젝트 개요와 실행 절차. 규칙 본문은 없습니다.

**적용 대상**: 이 저장소를 처음 여는 모든 사람

**버전**: 1.0.0 · **최종 수정**: 2026-09-04 · **작성자**: 김민경

---

## 1. 무엇을 하는 서버인가

Python 3.12 기반 AI 서버입니다. 기능은 두 개입니다.

| 기능 | 내용 |
|---|---|
| 영수증 OCR | 영수증 이미지에서 품목·금액·날짜를 추출하고 상호명·품목명을 정규화합니다 |
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

---

## 3. 개발 규칙

규칙 문서의 목록과 읽는 순서는 [docs/README.md](docs/README.md) 에 있습니다. 첫 작업 전에 그 문서부터 엽니다.
AI 코딩 에이전트로 작업한다면 [CLAUDE.md](CLAUDE.md) 가 시작점입니다.
