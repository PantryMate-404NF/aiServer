# 냉장고 기반 개인화 레시피 추천 시스템

> 작성자: 박재우 · 작성일: 2026-09-03

> 사용자가 **보유한 재료**로 만들 수 있는 레시피 중, **개인 선호**에 맞는 것을 추천하는
> 추천 엔진과 그 관측 체계.

| | |
|---|---|
| 성격 | 부트캠프 최종 프로젝트 · 8주 |
| **AI 파트 개발** | ~~2명~~ **3명** *(2026-09-02 전환)* (+ 데이터 정제는 팀원 도움) |
| 스택 | Python 3.12 · PostgreSQL 16(pgvector) · FastAPI · Streamlit · LightGBM |
| 실유저 | 50~100명 목표 → **CF 불가**, 콘텐츠 + 지식 기반 하이브리드 |

---

## 지금 어디까지 왔나

| 산출물 | 상태 | 검증 |
|---|---|---|
| **A** 시드 데이터 | ✅ | 재료 525 · alias 245 · 단위환산 193 |
| **B** DB 부트스트랩 | ✅ | 29테이블 · **스모크 13/13 · p95 30.2ms** |
| **D** API 계약 + Mock | ✅ | 경로 8개 · 오퍼레이션 9개 · `make contract` 통과 (출력이 SoT) |
| 크롤러 어댑터 | ✅ | 3종 형태 대응 |
| **C** 정규화 **P1·P2** | ✅ | **fixture 74건** |
| **C** 정규화 P3~P5 | ⬜ | ← **다음** |
| ⑤ Ranking · 대시보드 | ⬜ | |

**실행에서만 드러난 버그 5건**을 이미 잡았다 (설계 04-1). 전부 정적 검토를 통과한 뒤
실행에서 나왔다 — 그래서 **설계 → 즉시 구현 → 측정** 순서를 유지한다.

---

## 빠른 시작

```bash
./setup.sh --track B      # A 데이터 · B 엔진 · C 관측 중 자기 트랙
```

`.venv` 생성 → 트랙별 의존성 → 컨테이너 기동 → 시드 → 검증까지 한 번에 한다.
**여러 번 돌려도 안전하다** — 이미 있는 것은 건드리지 않는다.
먼저 `brew install uv` 와 `brew install --cask orbstack` 이 필요하고, 없으면
스크립트가 설치 명령을 알려준다.

```bash
./setup.sh --check                # 진단만 (아무것도 안 바꾼다)
./setup.sh --track A --no-db      # 컨테이너 없이 파이썬 환경만
./setup.sh --track B --extra rank-v1
```

> 🔴 `.env` 의 `REVIEW_SALT` 는 **스크립트가 만들지 않는다.** 후기 624,422건의 작성자
> 해시를 만든 값이라 새로 만들면 과거와 어긋난다. 팀 채널에서 받아 넣으세요.
> 없어도 시드·계약·Retrieval 은 된다 — 막히는 것은 크롤 데이터 적재뿐이다.

<details><summary>손으로 하려면</summary>

```bash
uv venv
make install TRACK=B      # uv 가 없으면: pip install -r requirements/B.txt
make bootstrap            # DB + 시드 + 검증
```
</details>

```bash
# DB 없이 되는 것들
make normalize-test    # P1·P2 fixture 74건
make contract          # API·스테이지 계약 (출력의 건수가 SoT)
make probe-all         # 크롤러 어댑터
```

---

## 문서 — **전체를 읽어야 하는 사람은 없다**

| 문서 | 무엇 |
|---|---|
| ⭐ [`docs/reco/design/01_ARCHITECTURE.md`](docs/reco/design/01_ARCHITECTURE.md) | **여기부터.** 왜 이렇게 만들었는지 — 서술형 30분 |
| [`docs/reco/design/02_RECOMMENDER_DESIGN.md`](docs/reco/design/02_RECOMMENDER_DESIGN.md) | 전체 설계 ①~⑦ **v2.7** · 📊 **0-5 데이터 플로우** · **5-0-1 모델 아키텍처** |
| [`docs/reco/design/03_OPEN_ISSUES.md`](docs/reco/design/03_OPEN_ISSUES.md) | 🔴 **혼자 결정할 수 없는 것** |
| [`docs/reco/decisions/2026-09-04_model_selection.md`](docs/reco/decisions/2026-09-04_model_selection.md) | 모델 6종 정량 비교 · 발표 QA |
| [`docs/reco/design/04_EXECUTION_PLAN.md`](docs/reco/design/04_EXECUTION_PLAN.md) | 🔴 **3명 · 남은 3주 기준 범위 · 주차별 · 컷라인** |
| [`docs/reco/design/05_API_SPEC.md`](docs/reco/design/05_API_SPEC.md) | 경로 8개 · 오퍼레이션 9개 (자동 생성) |
| [`docs/reco/design/06_INFRA_SPEC.md`](docs/reco/design/06_INFRA_SPEC.md) | **서버 사양** · 실측 기반 산출 · 요청서 템플릿 |
| [`docs/reco/design/07_EVALUATION_ROADMAP.md`](docs/reco/design/07_EVALUATION_ROADMAP.md) | **평가 방법론** · 딥러닝 전환 트리거 · 🔴 **소급 불가 항목 10종** |
| ⭐ [`docs/reco/decisions/2026-09-04_frozen_design.md`](docs/reco/decisions/2026-09-04_frozen_design.md) | **결정이 끝난 것만** 모은 참조본 · 미결·컷 목록 포함 |
| [`docs/reco/design/08_CHANGELOG.md`](docs/reco/design/08_CHANGELOG.md) | 버전별 변경 이력 (01 헤더에는 최신 1개만) |

> ⚠️ **`01` 은 20명 전제로 쓰였다.** 실제로 무엇을 만드는지는 **`04` 가 정한다.**
> 충돌하면 `04` 가 우선한다. `01` 은 "어떻게 만드는 것이 옳은가",
> `04` 는 "그중 3명이 남은 3주에 무엇을".

### 역할별 읽을 곳

| 역할 | 읽을 것 | 분량 |
|---|---|---|
| **크롤링 담당** | `02` C절 · `src/features/recommend/ingest/README.md` | ~100줄 |
| **정규화 (개발자 A)** | `01` ④ · `seeds/00_README.md` | ~450줄 |
| **엔진 (개발자 A)** | `01` ⑤⑥ · `05` | ~900줄 |
| **대시보드 (개발자 B)** | `01` ③ · `05` · `01` 0-5 판단4 | ~400줄 |
| **DB 관리자** | `01` 1-7 · `infra/00_README.md` · **`06` 8-C절** | ~400줄 |
| **서버 담당** | **`06` 전체** (요청서 템플릿 10절) | ~320줄 |
| **처음 합류** | 이 README → **`00`** → `04` | ~600줄 |

---

## 코드

폴더 구조는 [`docs/rules/00_aiserver_rules.md`](docs/rules/00_aiserver_rules.md) 4절을 따른다.

```
app/
├── main.py           FastAPI 앱 (현재 Mock)                    make mock
├── schemas/          API·스테이지 계약 (SoT)                    make contract
├── db/               커넥션 풀 · Retrieval 래퍼                 make smoke-py
├── core/             로그 라이터 · 카운터                       make log-test
└── services/
    ├── recommends/   탐색 · 우연성 · 이유 생성
    ├── normalize/    P1 전처리 · P2 분해 · P3 매칭 · P4 역할     make normalize-test
    ├── ingest/       크롤러 출력 어댑터 (YAML 매핑)              make probe
    └── eval/         임계값 계산

infra/                DDL 29테이블 · 함수 6 · 뷰 2 · Compose     make bootstrap
seeds/                재료 536종 · alias · 단위환산 · 대체 · 소비기한  make validate
scripts/              배치 · 검사 (load_recipes · migrate · doc_check)
scripts/reco/bench/        설계 근거 시뮬레이션 (문서가 인용하는 수치)
tests/                계약 · 라이터 · 스모크 · DDL
docs/reco/design/api/             OpenAPI · 실호출 예시 (자동 생성)          make api-docs
```

> 🔴 `src/features/recommend/repository.py` 는 규칙 3절의 "DB 에 직접 붙지 않는다" 에 대한 **예외**다 —
> ① Retrieval 이 46,353건에서 집합 연산으로 거르는 SQL 함수라 요청 본문으로
> 받을 수 없다. 규칙 문서 갱신이 필요하다 (미합의).

### SoT 가 어디인가

| 대상 | SoT |
|---|---|
| **범위 · 일정** | `docs/reco/design/04_EXECUTION_PLAN.md` |
| **API·스테이지 계약** | **`src/features/recommend/schema.py` · `stage.py`** (문서가 아니라 코드) |
| **이유 생성 · exploration · interleaving · 우연성** | `src/features/recommend/engine/` |
| **임계값 캘리브레이션** | `src/features/recommend/evaluation/threshold.py` |
| **설계 판단의 근거 시뮬레이션** | `scripts/reco/bench/` — 문서에 인용된 숫자를 재현한다 |
| **다이어그램 렌더 검증** | `scripts/check_mermaid.mjs` — `make diagrams` |
| **재료 사전** | `seeds/*.csv` `*.yaml` (DB 아님) |
| **DB 스키마** | `deploy/init/02_schema.sql` ← `01` ② 와 동기화 |
| **크롤러 매핑** | `seeds/sources/*.yaml` |

---

## 다음 단계

### 즉시 (개발)

| | 작업 | 필요한 것 |
|---|---|---|
| **A** | **P3 매칭 캐스케이드 L0~L2** | DB (시드 적재됨) |
| **A** | P4 역할 판정 · P5 수량 환산 | P3 |
| **B** | 디버거 화면 v1 | `make mock` |

### 🔴 사람에게 물어야 하는 것

| # | 대상 | 내용 |
|---|---|---|
| **C-2** | 크롤러 담당 | **`raw_json` 에 원본 전체를 담고 있는가** — 없으면 재크롤링 |
| **I-6** | 팀 | 실유저 **100명** 모집 — 50명이면 LightGBM 자체가 불가 |
| **I-8** | 팀 | 온보딩 설문 문항 — 모집 후 바꾸면 데이터 반쯤 폐기 |
| **B-1** | 백엔드팀 | `public` 스키마 쓰지 않기 |

상세는 [`docs/reco/design/03_OPEN_ISSUES.md`](docs/reco/design/03_OPEN_ISSUES.md).

### 주차별

```
W2  P1·P2 ✅  →  P3~P5 · 디버거 화면
W3  🔴 크롤링 도착 전제 · 커버리지 1차 측정      목표 0.55
W4  검수 스프린트 (팀원 20명 반나절) · Ranking v0  목표 0.75
W5  임베딩 파이프라인 · k-means(S-A) · 평가 하네스   목표 0.85
W6  🔴 유저 100명 모집 · Re-ranking
W7  LightGBM v1 · MLflow 실험 · 리포트 초안
W8  안정화 · 발표    ← 새 기능 넣지 않는다
```

**컷라인**(언제 무엇을 포기할지)은 [`docs/reco/design/04_EXECUTION_PLAN.md`](docs/reco/design/04_EXECUTION_PLAN.md) 5절에 있다.

---

## 명령

```
make bootstrap        기동 → 시드 → 검증
make up / down / psql / logs

make validate         시드 정합성        (DB 불필요)
make contract         API·스테이지 계약  (DB 불필요)
make normalize-test   P1·P2 fixture      (DB 불필요)
make probe-all        크롤러 어댑터      (DB 불필요)

make seed             시드 적재
make smoke            Retrieval 정확성·지연시간
make mock             Mock 추천 API
make api-docs         API 명세 재생성

make normalize-demo T="대파 1대(흰 부분만)"
make probe SAMPLE=크롤링샘플.json
```

`make help` 로 전체 목록.
