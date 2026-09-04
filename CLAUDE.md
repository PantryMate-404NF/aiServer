# aiServer · 개발 시작점

**정하는 것**: 신규 합류자와 AI 에이전트가 첫 작업을 시작하는 데 필요한 최소 정보.

**적용 대상**: 이 저장소에서 코드를 작성하는 모든 인원 및 AI 코딩 에이전트

**버전**: 1.0.0 · **최종 수정**: 2026-09-04 · **작성자**: 김민경

---

## 1. 이 프로젝트

Python 3.12 기반 AI 서버입니다. 기능은 영수증 OCR과 추천 두 개입니다.

**스택**: Python 3.12 · uv · PostgreSQL 16.15 · PaddleOCR PP-OCRv5(`lang='korean'`) · Gemini 3.5 Flash Lite
**소스 루트**: `src/` 입니다. 그 아래에 중간 계층이 없으므로 import는 `from features.receipt import ...` 형태입니다.

```text
src/
├── main.py, config.py, deps.py
├── features/<도메인>/     기능 단위 캡슐화. 최상위 경계
├── infra/                 기능 둘 이상이 실제로 쓰는 외부 자원
└── utils/                 도메인 지식이 없는 잡무
```

---

## 2. 처음 30분

```bash
git clone <repository-url> && cd aiServer
uv sync                          # 의존성 설치. pip 을 직접 쓰지 않습니다
cp .env.example .env             # 값은 팀 비밀 저장소에서 개별 수령합니다
uv run pytest tests/unit         # 환경 정상 여부 확인
uv run python -m main            # 실행
```

이 블록의 기준은 [README.md](README.md) 2절입니다. 명령이 바뀌면 그쪽을 먼저 고치고 여기를 맞춥니다.
그다음 [docs/README.md](docs/README.md) 를 엽니다. 문서 목록과 읽는 순서가 거기 있습니다.

---

## 3. 작업 규칙 요약

**이 절은 요약입니다.** 원문은 `docs/convention/` 에 있으며, 요약과 원문이 어긋나면 **원문이 이깁니다.**

| 항목 | 규칙 | 원문 |
|---|---|---|
| 브랜치 | `main` 직접 push 금지. `<type>/<영문-소문자-하이픈>` | 01의 2.1 |
| 커밋 | Conventional Commits. 헤더는 영어, body는 한국어 허용. 1 커밋 = 1 논리적 변경 | 01의 2.2 |
| 의존성 | `uv add` 만 씁니다. 추가 사유를 커밋 body에 한 줄 남깁니다 | 01의 1절과 2.2 |
| PR | 300줄 이하, 승인 1명, 통과 출력 첨부 | 01의 5.1 |
| 배치 | 기능 중심. 새 도메인은 파일 5개 이하 | 02의 6절 |
| import | 절대 경로만. 상대 import 금지. 배럴은 도메인 진입점 하나뿐 | 02의 7절 |
| 네이밍 | 모듈·함수는 `snake_case`, 클래스는 `PascalCase`, 상수는 `UPPER_SNAKE_CASE` | 02의 4절 |
| 분리 | 파일 300줄에서 검토, 500줄에서 필수 분리 | 02의 5.1 |
| 설정 | `config.py` 밖에서 `os.environ` 을 호출하지 않습니다 | 03의 2절 |
| 예외 | `except Exception: pass` 금지. 실패 지점을 구분한 예외를 씁니다 | 03의 3절 |

---

## 4. 완료 선언 전

아래를 실행하고 **통과한 출력을 근거로만** 보고합니다. 추정으로 완료를 선언하지 않습니다.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit
```

실패하면 실패했다고 출력과 함께 보고합니다.

---

## 5. 절대 하지 않는 것

- `.env`, API 키, 토큰, 인증서를 코드·로그·에러 메시지·커밋에 남기기
- 개인정보가 포함된 샘플 데이터, 모델 가중치, 빌드 산출물 커밋
- 요청하지 않은 파일·추상화·설정 옵션·의존성 생성
- 검증 없이 그대로 저장되는 외부 API 응답
- 단위 테스트에서 외부 네트워크·실제 DB·유료 API 호출
- AI가 생성한 코드를 한 줄씩 읽지 않은 채 PR에 포함하기
