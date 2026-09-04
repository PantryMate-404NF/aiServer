# 설계 변경 이력 (CHANGELOG)

> 작성자: 박재우 · 작성일: 2026-09-03

[`01_추천시스템_설계.md`](01_추천시스템_설계.md) 헤더에는 **최신 버전 1개만** 남기고,
이전 버전의 변경 요약은 여기에 보존한다. 규칙:

- 폐기된 문장은 본문 **그 자리에서 ~~취소선~~** 처리하고 후계 절 링크를 단다.
- "왜 뒤집혔나" 서사는 본문이 아니라 이 파일에 쓴다.

> ⚠️ **v2.1~v2.8 항목이 이 파일에 없다.** 08-28~09-01 사이 변경은
> [`01_추천시스템_설계.md`](01_추천시스템_설계.md) 헤더의 절별 요약과
> [`04_실행계획.md`](04_실행계획.md) §8 에만 남아 있다. 없는 이력을 지금 지어내지 않고
> 빈칸으로 남긴다 — 아래 v2.9·v3.0 은 실제 코드·DB 로 확인한 것만 적었다.

---

## v3.0 주요 변경 (2026-09-02~03) — 크롤 실측 반영 · 소급 불가 항목 착지

**이번 판의 성격은 "설계를 바꿨다"가 아니라 "실제 데이터와 코드에 맞췄다"이다.**
크롤 46,353건이 도착하면서 전제 3개가 깨졌고, 소급 불가 컬럼들이 DDL 에 실제로 착지했다.

### 스키마 — 소급 불가라 지금 넣은 것

| 항목 | 무엇 | 없으면 무엇을 잃나 |
|---|---|---|
| **`event_log.source`** *(NOT NULL · DEFAULT 없음)* | `served` / `viewport` / `client` | 지금은 서버 응답을 `served` 로 쓰지만 나중에 `viewport` 로 바꾸면 **두 시대의 impression 은 뜻이 다르다.** 열이 없으면 한 테이블에서 섞여 **둘 다 못 쓴다.** 사후 백필 불가. 전환기에 둘을 같이 쓰면 `r(p) = viewport(p)/served(p)` 로 환산계수가 나온다 |
| 〃 **DEFAULT 를 두지 않은 이유** | — | 기본값이 있으면 새 삽입 경로가 **조용히** 오분류된다. NOT NULL + DEFAULT 없음이라 새 라이터는 반드시 값을 정해야 한다 |
| **`ux_ev_impression(request_id, recipe_id, source)`** 부분 유니크 | 재시도·리플레이 멱등 | `source` 를 키에서 빼면 `served`+`viewport` 이중 기록이 공존하지 못해 환산계수 창이 닫힌다. viewport 는 스크롤 in/out 으로 여러 번 발화하므로 dedup 이 없으면 `r(p) > 1` 이 된다 |
| **impression 은 `position` 필수** (CHECK) | `event_type <> 'impression' OR position IS NOT NULL` | position 이 비면 그 시대 데이터는 통째로 못 쓴다. '상위 k 만 잘라 보기'조차 안 되고 position bias 보정이 불가능하다. **DDL 이 막지 않으면 라이터 버그 하나로 조용히 비어간다** |
| **`session_id` 접두어에 `d-` 추가** | `c-` 실사용자 · `g-` 게스트 · **`d-` 개발·디버거·시딩** | `is_simulated` 는 **가상 유저만** 거른다. 개발자가 **실유저 ID 로** 디버거를 눌러본 것은 그대로 통과해 실유저 지표에 섞인다. `v_real_events`·`v_real_recommendations` 가 `d-` 와 `model_version LIKE 'mock-%'` 를 함께 거른다 |
| **`user_vector.onboarding_picks` · `onboarding_scales`** | 온보딩 원본 보존 | `taste_vec` 은 선택한 레시피들의 **평균**이라, 원본이 없으면 **시드가 바뀔 때 재계산할 수 없다.** 평균은 되돌릴 수 없다 |
| **`pantry_item.purchased_at`** | 소비기한 추정의 기준일 | `added_at`(앱 등록 시각)으로 추정하면 마트에서 사고 사흘 뒤에 넣은 재료가 **사흘을 공짜로 번다.** 임박 판정이 그만큼 늦어져 재료가 상한다. 미래 구매를 막는 CHECK 를 함께 걸었다 |
| **`daily_recommendation` 테이블 [26]** | 트랙1 '오늘의 추천' 사전계산 (D-19) | `user_id`·`rank` PK · `reason` · `reason_source`(llm/template/fallback) · **`pantry_fingerprint`** · `config_hash` · `stats_version` · `batch_run_id` |
| **`recipe_ingredient_raw UNIQUE(recipe_id, position)`** · **`recipe_review UNIQUE NULLS NOT DISTINCT`** | 로더 멱등성 | 🔴 제약 이전에는 **적재를 2회 돌리면 전량이 2배**가 됐다 (`recipe_ingredient_raw` 451,862 → 903,724). 조용히 늘어나므로 아무도 눈치채지 못하고, 그 위에서 계산한 인기도·커버리지가 전부 틀린다 |

### 🔴 DB 시간대를 `Asia/Seoul` 로 고정했다

`deploy/init/01_extensions.sql` 의 `ALTER DATABASE recodb SET timezone = 'Asia/Seoul'`.

UTC 로 두면 **한국 자정~오전 9시 사이에 `current_date` 가 하루 전**이 되어
`f_expiring` 의 임박 판정이 하루씩 어긋난다. 새벽 배치가 정확히 그 창에서 돈다.

> **컨테이너 `TZ`·`PGTZ` 만으로는 부족하다.** 그건 컨테이너 안에서만 유효하고,
> 밖에서 TCP 로 붙는 `psycopg2` 세션에는 적용되지 않는다. `ALTER DATABASE` 여야 한다.

### 함수

| 변경 | 내용 |
|---|---|
| **`retrieve_candidates` · `retrieve_for_user` 에 `p_include_test`** (기본 `FALSE`) | `feature_version` 이 `test-` 로 시작하면 조회에서 제외 — B·C 가 만드는 합성 피처가 실서빙에 섞이지 않는다 (D-17). 🔴 **시그니처가 바뀌어 `DROP FUNCTION IF EXISTS` 가 앞에 붙었다** — 안 붙이면 구 시그니처와 오버로드로 공존해 어느 쪽이 불릴지 알 수 없다 |
| **`effective_expiry` 가 구매일 기준으로** | `COALESCE(p.purchased_at, p.added_at::date) + shelf_life_days` |

### 계약 (`src/features/recommend/schema.py` · `stage.py`)

- **`OnboardingIn` / `OnboardingOut` 신설** — `POST /v1/onboarding/{user_id}`.
  `picks`(레시피 인덱스) · `scales`(3축 0~4) · `allergy_groups` · `allergy_ingredient_ids` ·
  `avoid_ingredient_ids` · `household_size`.
  🔴 **알러지는 `severity='allergy'` 로 저장해야 한다.** 값을 생략하면 DDL 기본값이
  `avoid` 라 **하드 컷이 페널티로 강등**된다 — 알러젠이 점수만 낮은 채 노출된다.
- **`PantryItemIn.purchased_at` 추가.** `expires_at` 은 **소비기한(use-by)** 이지
  유통기한(sell-by)이 아니다 — 둘을 섞으면 추정 시드 ~~525~~ 536종의 의미가 흔들린다.
- `PantryIn.removed` 를 mock 이 더 이상 버리지 않는다 (제거 사유 1비트가 E-3 ③ 이다).
- `PENDING_DATA_FEATURES = {f_cuisine, f_season}` · `ACTIVE_WEIGHT_TODAY = 0.94` 신설 —
  **17종 중 실제로 값이 붙는 것은 8종**이고 그 합이 0.94 다.

### 구현

| | |
|---|---|
| 🔴 **탐색 슬롯을 실제로 꽂는다** | 이전에는 `exploration_slots()` 결과를 **버려서** 탐색 아이템이 점수 순서 그대로 남았다 — 즉 항상 비슷한 위치에 왔다. 위치별 CTR 이 검사확률 곡선이 되지 않아 **IPS 보정이 성립하지 않는 상태**였다. 실측: 200요청 × 2칸 = 400 노출이 1~20위에 17~33건씩 퍼진다 |
| **`make seed-reset` 가드** | `TRUNCATE ... CASCADE` 가 `recipe_ingredient`·`pantry_item` 까지 함께 비운다. 행이 있으면 멈추고 `FORCE=1` 로만 강행한다 |
| 시드 결함 2건 | 식용유 기름짐 **0.60 → 1.00** · 멸치 3종 분리(국물용/잔멸치/총칭) |

### 환경 — `pyproject.toml` + `uv.lock`

`db/` · `reco/` 아래 있던 requirements 파일 2개를 **폐지**했다. 설치는 트랙별로:

```
make install TRACK=A   # 데이터  ml
make install TRACK=B   # 엔진    api + ml
make install TRACK=C   # 관측    dash + ml + obs
```

> 🔴 **맨손 `uv sync` 금지.** 락에 없는 패키지를 지운다.
> 09-02 에 실제로 `fastapi`·`numpy` 가 사라져 mock 서버가 죽었다.
> extras: `api`(fastapi·uvicorn) `dash`(streamlit) `obs`(mlflow) `ml`(numpy·scikit-learn)
> `rank-v1`(lightgbm) `embed`(sentence-transformers)

**09-03 추가.** `EXTRA=` 로 묶음을 얹을 수 있게 했다 — 문서는 이미
`make install TRACK=B EXTRA=rank-v1` 을 안내하고 있었는데 **Makefile 에 그 인자가 없었다.**
🔴 `EXTRA` 는 한 번 붙이고 끝이 아니다. 다음 `make install` 에서 빼먹으면 `uv sync` 가
도로 지운다.

`embed`(sentence-transformers) 를 정의만 해 두고 **어디에도 깔지 않는다.** A-12 가
임베딩 없는 TF-IDF→SVD 판으로 확정돼 부를 코드가 없는데, torch 를 포함해 2GB 를 끌고 온다.

**`requirements/` 신설** — uv 를 못 쓰는 환경(순정 pip · 코랩 · 채점 서버)용 사본이다.
`base`(7) · `A`(14) · `B`(22) · `C`(105) · `all`(106).

```
make requirements         # pyproject 를 고친 뒤 다시 뽑는다
make requirements-check   # 락과 어긋나면 실패 (어느 파일인지 알려준다)
```

> 🔴 **사본을 손으로 고치지 않는다.** 고치면 락과 조용히 갈라지고, 갈라진 걸 아무도 모른다.
> 옛 `db/requirements.txt`·`reco/requirements.txt` 2벌이 딱 그렇게 갈라져서 폐지됐다.

### 결정 (`docs/reco/draft/05_작업분담_결정사항.md` D-1~D-19)

| | 내용 |
|---|---|
| **D-9 정정** | 인기도는 `percent_rank` 가 아니라 **`row_number` + `recipe_id` tie-break**. `percent_rank` 는 3분위가 비고 **8,470건이 동점**이라 후보 500 컷이 실행마다 달라진다 → **propensity 가 재현되지 않는다** |
| D-11 | `flavor_vec` 6축 유지. 실제 사용 축은 W3 에서 결정 |
| D-12 | 클러스터는 A-4 직후 **한 번만** — 로그에 `cluster_version` 칸이 없다 |
| D-13 | 맛 코퍼스 평균 μ 는 **A-5·A-13 두 번만** |
| D-14 | D-10 판정(필수재료 0개)은 SQL 함수에서 |
| D-17 | 합성 피처는 `feature_version='test-*'` |
| D-18 | 쌍대비교 **블록별 120쌍** + 구글폼 |
| **D-19** | **트랙1은 사전 계산으로 만든다** (실시간 아님). 사유 10건을 300ms 에 내려면 833 tok/s 가 필요한데 가장 빠른 로컬 모델이 90.9 tok/s 다 |

**이슈 I-8·I-13·I-15 닫힘. 남은 미결: I-6 · I-11 · I-14 · I-16 · I-17.**

### API 명세 재생성 + 문서 정합 *(2026-09-03 오후)*

`docs/reco/design/05_API_SPEC.md` 를 707줄 → 1238줄로 다시 생성했다. **생성 파일이므로 손대지 않고**
`docs/reco/design/api/capture.py`·`render.py` 를 고쳤다.

가장 컸던 것: **`/v1/events` 의 "정상 응답" 예시가 실제로는 422 였다.** 캡처가
`session_id: "s-7-a1b2"` 를 보내는데 계약이 `^[cgd]-` 로 바뀌어 있었고, 문서는 그 에러 본문을
`## 응답` 자리에 실었다. 프론트가 복사하면 즉시 막히는데 **문서만 봐서는 알 수 없었다.**
→ `grab()` 에 기대 상태코드를 강제하고 다르면 원인을 말하고 멈춘다.

`session_id` 는 문서에 **한 줄도 없었다.** 안 보내면 서버가 `g-`(게스트)로 채우고,
impression 은 그 요청에서 서버가 자동 기록하므로 **이벤트의 95% 가 게스트로 쌓인다.**
소급 불가다. 필드표·접두어 표·소급 불가 표에 넣었고, `작업분담_B_엔진`·`00_공통` 의
BE→AI 계약 3줄과 기능명세 COMMON-022 에도 넣었다 (이벤트 쪽에만 있고 요청 쪽에 없었다).

문서가 침묵하던 것: 인증 · 페이지네이션 · 시간대 · 오늘의 추천 부재 · 레시피 상세 부재.
"이 API 에 없는 것" 절을 만들어 6가지를 명시했다.

**계약 상수는 `capture.py` 가 `_const` 로 실어 보낸다.** `render.py` 는 `reco` 를 import
할 수 없어서(Makefile 이 `docs/reco/design/api/` 에서 돌린다) 손으로 적을 수밖에 없었는데, 그게 낡음의
근원이었다. 엔드포인트 표도 `openapi.json` 에서 생성한다 — 새 라우트에 설명이 없으면 ⚠️ 가 뜬다.

### 문서 간 불일치 49건 정리 *(2026-09-03 오후)*

05 를 고친 뒤 **다른 문서가 따라왔는지** 대조했다. 68건 판정 · 49건 확정 · 19건 기각.
개발을 틀리게 만드는 6건:

| 어디 | 무엇이 틀렸나 |
|---|---|
| `작업분담_A_데이터` | "B 는 가중치를 **재분배해야** 한다" — D-4 는 **나눗셈, 재분배 아님** |
| `작업분담_00_공통`·`B_엔진` (+읽기쉬운판 2) | `request_id` 없는 이벤트가 "정상적으로 쌓인다" — 실제로는 **rejected 로 저장 안 된다** |
| `작업분담_B_엔진`·`00_공통` | BE→AI 요청 필드에 `session_id` 없음 |
| `01` ⑦절 3곳 | 폐기된 `contrib` 을 저장 필드로 서술 (실제는 `features`) |
| `09_기능명세_대조표` | "`daily_recommendation` 테이블이 없다" — 09-02~03 에 착지했다 |
| `09_기능명세_대조표` | 알러지 그룹을 ~~9칩~~ 이라 적었다 — 실제 10종, **메밀이 빠진다** |

나머지 43건은 수치 잔재였다 — 엔드포인트 ~~7~~(5곳) · 테이블 ~~27~~·~~28~~(9곳) ·
기본양념 ~~28~~·~~40~~(4곳) · 재료 ~~525~~(9곳) · 알러지 ~~9그룹~~(3곳) · 인원 ~~2명~~(11곳).
전부 실측값으로 맞췄다.

**계약 두 곳도 함께 채웠다** (문서가 이미 그렇다고 적고 있던 것):
`UNAVAILABLE_FEATURES` 에 `f_content`(content_emb 생산 코드 없음),
`PENDING_DATA_FEATURES` 에 `f_dish_type`(분류축 전수 0건). 둘 다 `w=0` 이라
`ACTIVE_WEIGHT_TODAY = 0.94` 는 그대로다.

> 🔴 **원인은 아무도 안 보고 있었다는 것이다.** `doc-check` 가 파일 하나만 봤고,
> 수치는 등록돼 있지 않았다. 이제 **전 문서**를 훑고, 05 의 단언 22건을 **실제로 호출해**
> 대조하며, 테이블 수·엔드포인트·기본양념·재료·알러지 그룹·캡처 건수를 등록했다
> (53건 → **94건**).

### 실측 검증 건수 *(2026-09-03)*

```
make contract   98건      make ddl-test    47건      make normalize-test 113건 (74+23+16)
make smoke      13건      make smoke-py    19건      make log-test        48건
make doc-check  94건      ← 09-03 재측정. 지시서의 47건은 그 사이 검사가 늘어 낡았다
```

> ⚠️ **`doc-check` 건수는 유동적이다.** 문서를 고칠 때마다 검사를 더 넣기 때문이다.
> 그래서 `doc_check.py` 자신이 **다른 명령들의 건수**를 문서와 대조한다 —
> 자기 건수는 대조하지 않는다.

**09-03 정정.** 이 대조가 **`03_작업분담_공통.md` 한 파일만** 보고 있었다. 그래서
`contract` 84→98 · `ddl-test` 41→47 로 늘었을 때 **docs/ 8개 파일 21곳이 낡았는데
하나도 못 잡았다.** 이제 `docs/**/*.md` 전부를 본다.

건수를 적는 규칙 두 가지 (검사기가 이 모양을 읽는다):

| 무엇 | 어떻게 쓰나 |
|---|---|
| **전체** 건수 | 명령을 먼저, 수를 뒤에 — `` `make contract` 98건 `` · `` `make ddl-test` **47건** `` |
| **부분** 건수 (그 항목이 더한 몫) | 수를 먼저, 명령을 뒤에 — `` 8건 (`make contract` 중) `` |

취소선(`~~84~~`)과 `65건 → 98건` 의 앞쪽은 "일부러 남긴 옛 값"으로 보고 건너뛴다.

---

## v2.9 주요 변경 (2026-09-01) — 레시피 46,353 정정

크롤이 도착하면서 **23만 건 전제로 쓰인 산정치가 전부 무효**가 됐다.

| 절 | 내용 |
|---|---|
| 전 문서 | 레시피 수 **23만 → 46,353**. `docs/06` 의 벡터 지배 서술이 뒤집혔다 (임베딩+HNSW 가 DB 의 24%, 1위는 로그) |
| `01` 5-2-2-1 | 결측 피처는 **재분배가 아니라 나눗셈** — `(Σ wᵢ·fᵢ) / Σ wᵢ`. 재분배는 남은 피처의 의미를 바꾼다 |
| `01` 2-5-1 ⑤ | `f_taste` **중심화** — 맛 코퍼스 평균 μ 를 빼고 계산한다. μ 가 바뀌면 과거 `f_taste` 가 달라지므로 **μ 를 로그에 남긴다** |
| DDL 개정 | `scoring_config`(27) · `pantry_item` tombstone · `pantry_detail` · `policies` · `warm_alpha` · `stats_version` — 07 E-3 의 소급 불가 항목 착지 |

---

## v2.0 주요 변경 — 우연성(serendipity)

**"이 재료가 들어간 비슷한 메뉴만 보인다"는 문제는 MMR 로 해결되지 않는다.**
MMR 은 이번 목록을 흩뜨릴 뿐, 유저가 가본 적 없는 곳으로 데려가지 않는다.

| 절 | 내용 |
|---|---|
| **5-3-5 신설** | 탐색 슬롯을 **클러스터 Thompson** 으로 채운다 — 숨은 취향 발견 **+39%**, 조리 수도 +5.3건 |
| **5-3-5** | 🔴 그런데 Thompson 단독은 propensity=0 인 클러스터를 32/50 만든다 → **혼합 정책** `½ 균등 + ½ Thompson` |
| **5-3-2 보강** | 분류축 캡은 점수를 **10.7%** 버리는데 클러스터 쿼터는 **1%** — 측정으로 확인 |
| 스키마 | `recipe_feature.cluster_id`·`cluster_version` · `user_cluster_stat` 테이블 |
| 계약 | `Candidate.cluster_id` · `RankedItem.explore_source ∈ {uniform, thompson}` |
| 코드 | `src/features/recommend/engine/serendipity.py` · 계약 테스트 42건 |

> ⚠️ **k-means 배치는 아직 없다.** `cluster_id` 가 NULL 이면 균등 탐색으로 폴백하므로
> 서비스는 동작한다. 배치는 W5 임베딩 파이프라인과 묶는다 (04).

---

## v1.9 주요 변경 — 설계 검토에서 나온 결함 수정

**측정으로 확인된 결함 3건과, 그 결함이 재발하지 않게 하는 장치들이다.**

| # | 결함 | 측정된 증상 | 수정 |
|---|---|---|---|
| **A-1** | 이유를 `contrib=w·f` 의 argmax 로 골랐다 | 후보 500건 시뮬레이션에서 Top-20 이유가 **100% `f_coverage`** | **z-salience** 로 교체 (5-5) |
| **A-2** | `contrib` 만 로깅했다 | `w=0` 인 피처 **6종이 로그에서 소실** → LightGBM v1 학습 불가 | **피처 원값 17종 전부 로깅** (3-1) |
| **A-3** | 계산 수단이 없는 피처에 가중치가 있었다 | `f_cooccur`·`f_group_pref` 에 **0.16** — Σw=1.00 을 통과하며 서빙은 0.84 | 재정의·재분배 + **계약 검증에 못박음** (5-2-2) |

| # | 추가 장치 | 왜 |
|---|---|---|
| **B-2** | exploration 슬롯 **위치 무작위화** | 6·14 고정으로는 위치별 검사확률 곡선을 못 구해 IPS 가 불가능 (5-3-3) |
| **B-3** | **Team-Draft Interleaving** | 유저 100명에서 A/B 는 검정력이 없다 (5-7-2) |
| **B-4** | **캘리브레이션 임계값** | "0.6 이 적당해 보여서"가 재현율 0% 를 낳았다. 같은 숫자가 아직 5개 남아 있다 (5-7-3) |
| **B-5** | **핵심어 구조 매칭** | 퍼지 매칭 자리를 대체. confusable 42쌍 **차단 100%**, 수식어 변형 **통과 100%**, 임계값 없음 (4-4-2) |
| **C** | **소비기한 시드 ~~525~~ 536종** | `f_expiring`(w=0.15)이 유저 입력에만 의존해 사실상 죽어 있었다 (5-2-1) |

> 🔴 **`propensity` 와 `session_id` 는 소급이 불가능하다.** 지금 로그에 안 남기면
> 나중에 off-policy 평가와 시퀀스 모델을 시도할 데이터가 영원히 없다. 그래서 계약에 넣었다.

---

## v0.3 주요 변경

| 변경 | 이전 (v0.2) | 현재 (v0.3) |
|---|---|---|
| 레시피 분류 | `category VARCHAR(32)` 단일 문자열 `'한식/국·탕'` | **4축 분리** — `dish_type` · `situation` · `main_ing_cat` · `method` |
| 요리 계열 | 없음 | **2계층 파생 축** `cuisine_family`(7종) + `cuisine`(16종). **분류 구현은 보류, 컬럼만 확보** |
| 신규 테이블 | — | `cuisine_taxonomy` (23번) |
| 크롤러 요구사항 | `group_name` 수집 | **+ 카테고리 4축 원문을 `raw_json` 에 포함** |

## v0.2 주요 변경

| 변경 | 이전 (v0.1) | 현재 (v0.2) |
|---|---|---|
| 백엔드 | Spring Boot BFF + Python Reco (2서비스) | **Python 단일 서비스 (FastAPI)** |
| 프론트 | React 유저 앱 + 관리자 대시보드 | **유저 앱 제외.** Streamlit + Grafana + MLflow UI |
| 로그 | event_log / recommendation_log 2종 | **4층 관측성 체계** (③ 신규 섹션) |
| 인증 | JWT | 제거. 대시보드 내 유저 선택 |
| 실유저 로그 유입구 | 유저 웹앱 | **대시보드 내 추천 디버거** |

