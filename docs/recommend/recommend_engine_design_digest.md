# PM-ENG-RECO-B-001 요약 (에이전트용)

**정하는 것**: 파트 B 설계 명세 `recommend_engine_design.md` 의 압축본. 구현·검증에 필요한 계약, 수식, 상수, 배치, 검증 항목만

**적용 대상**: 파트 B 를 구현하는 AI 코딩 에이전트. 원본과 어긋나면 **원본이 이깁니다**. 원본이 바뀌면 이 파일을 같은 커밋에서 갱신합니다

**버전**: 2.0.0 · **최종 수정**: 2026-09-10 · **작성자**: 유재현

---

## 1. 목적과 제약

> **이 문서는 1차 3자 회의 이전의 계획입니다.** 회의 결정(2026-09-10)으로 맛 축 수, 피처 체계, 노출확률의 의미, 그리고 계약 파일의 정본이 전부 데이터 파트 쪽으로 바뀌었습니다. **현재 계약은 `src/features/recommend/{enums,stage,schema}.py` 와 `deploy/init/02_schema.sql` 이 정합니다.** 바뀐 내용과 근거는 `recommend_engine_work_log.md` 3.2 의 D-19~D-26 과 `../decisions/2026-09-10_recommend_engine_follows_data_track_contract.md` 에 있습니다. 이 문서는 그때의 판단 근거를 남기기 위해 그대로 둡니다.

| 키 | 값 |
|---|---|
| 목표 | 추천 코어 서빙 파이프라인(Track B). 3단계: Retrieval → 5블록 스코어링·감점 → Re-ranking |
| 환경 제약 | 데이터 희소성 > 99.99%, 표본 라벨 600쌍. 외부 상용 API 없이 로컬 CPU 에서 완결 |
| 레이어 | L1 Mock 기반 순수 함수 · L2 Schema Adapter + PostgreSQL 16 intarray + 로그 적재 · L3 계약 42건, Locust p95 < 58ms, 피드백 루프 |
| 불변 원칙 | (1) Zero-Drop: 측정 불가 피처는 분자·분모 동시 제외 (2) 감점은 곱연산 (3) 알레르기는 Stage 1 SQL 에서 완전 제외 (4) 맛 코사인 전 `feature_stats` 코퍼스 평균을 양쪽에서 차감 |
| 실현 상태 | `recommend_engine_work_log.md` 1절 |

---

## 2. 계약 (회의 이전 계획)

**아래 모델은 더 이상 존재하지 않습니다.** B 의 `schema.py` 는 D-20 으로 폐기됐고 같은 경로에 데이터 파트의 계약이 들어와 있습니다(`RecommendRequest`, `RecommendResponse`, `EventIn`, `OnboardingIn` 등). `TastePreference`·`RecommendedItem`·`FeedbackEventRequest`·`FeedbackEventResponse` 는 현재 코드에 없습니다. D-07·D-08·D-13 도 D-20 으로 대체됐습니다. 아래는 그때의 계획입니다.

| 모델 | 필드 (기본값) |
|---|---|
| `TastePreference` | `spicy_level`, `sweet_level`, `salty_level`: int 0~4 (2) |
| `RecommendRequest` | `user_id: int` · `pantry_ingredient_ids: list[int]` · `expiring_ingredient_ids: list[int]` ([]) · `taste_preference` · `household_size: int ≥1` (1, "4인 가구" 파싱) · `preferred_cuisines: list[str]` ([], 문자열 `[,/ ]` 분리) · `max_cook_minutes: int|None` · `allergy_group_codes: list[str]` (식약처 19종) · `top_k: int 1~50` (20) |
| `RecommendedItem` | `rank`, `recipe_id`, `recipe_title`, `match_score 0~1`, `cook_minutes|None`, `missing_ingredient_ids`, `missing_count`, `reason`, `matched_product_ids`, `is_exploration` (False) |
| `RecommendResponse` | `request_id: UUID`, `recommendations: list[RecommendedItem]`, `meta` (원본 dict; 구현 `RecommendMeta`) |
| `FeedbackEventRequest` | `user_id`, `request_id: UUID`, `recipe_id`, `position 1~50`, `event_type ∈ {click, cook, dismiss}`, `timestamp` ISO-8601 |
| `FeedbackEventResponse` | `success: bool`, `updated_taste_vector: list[float]` |

---

## 3. 알고리즘

### 3.1 Stage 1 Retrieval (PostgreSQL intarray)

- 조건: `(essential && pantry OR |essential| = 0) AND NOT (all_ids && allergy) AND icount(essential - pantry) <= k AND (max_minutes IS NULL OR cook_minutes <= max_minutes)`
- 정렬 `missing_count ASC, popularity_score DESC`, `LIMIT 500`, k ≤ 2. 목표 15ms.
- SELECT: `recipe_id, icount(essential_ids - :pantry) AS missing_count, cardinality(essential_ids), flavor_vec, popularity_score, quality_score, cook_minutes` (구현은 +`title, all_ids, cuisine, product_ids`)
- 폴백(후보 < 20): k 2→3→4 → 대체재 확장 → 전체 인기순. `meta.degraded = True`, 200 OK.

### 3.2 Stage 2 스코어

`Score = (Σ_{b∈측정가능} w_b·S_b / Σ_{b∈측정가능} w_b) × P_recent × P_cooked × (1 − P_avoid)`

| 블록 | w | S_b | 측정 불가 조건 |
|---|---|---|---|
| match | 0.29 | `1 − |E∖P| / (|E| + ε)` | 없음 |
| expiring | 0.15 | `|E ∩ P_D3| / (|P_D3| + ε)` | `|P_D3| = 0` |
| taste | 0.31 | 중심화 코사인 `(u−μ)·(r−μ) / (‖u−μ‖‖r−μ‖)`, μ = `feature_stats` 3축 평균 | μ 없음, 영벡터 (구현 추가) |
| quality | 0.15 | `0.6·popularity + 0.4·quality` | 둘 다 결측 (구현 추가) |
| ctx | 0.10 | `1 − max(0, (T_r − T_max) / T_max)` | `T_max` 또는 `T_r` 없음 |

| 감점 | 조건 | 값 |
|---|---|---|
| P_recent | 최근 7일 추천 노출 | ×0.7 |
| P_cooked | 최근 14일 조리 완료 | ×0.5 |
| P_avoid | 기피 재료 포함 비율 × 2.0 | 최대 0.8 |

### 3.3 Stage 3 Re-ranking

- Top-20 = 개인화 16 (80%) + 탐색 4 (20%). 구현은 `round(top_k × 0.2)`.
- 개인화 MMR: `argmax_{r∈C∖S} [λ·Score(r) − (1−λ)·max_{s∈S} Sim(r,s)]`, λ = 0.7, `Sim = Σ_{i∈A_r∩A_s} IDF(i) / Σ_{j∈A_r∪A_s} IDF(j)`.
- 탐색: 비선호 요리군(`preferred_cuisines` 외)·미경험 맛 중 `quality_score` 상위 풀. Thompson `Beta(α_c, β_c)` 2건 + 균등 2건. 노출 확률 역수를 `propensity_scores` 로 로깅. 위치 `random.sample(range(top_k), 4)`, `is_exploration = True`.

### 3.4 피드백 루프

- `α = min(1, n_events / 20)`
- `u_behavior(t) = (1−γ)·u_behavior(t−1) + γ·r_flavor`, γ = 0.2
- `u_effective = (1−α)·u_onboarding + α·u_behavior(t)`

---

## 4. DDL (회의 이전 계획)

`src/features/recommend/tables.py` 는 만들지 않았습니다. DDL 은 데이터 파트가 `deploy/init/02_schema.sql` 과 `04_functions.sql` 로 소유합니다.

| 테이블 | 컬럼 |
|---|---|
| `recommendation_log` | `request_id UUID PK`, `user_id BIGINT`, `config_fingerprint VARCHAR(64)`, `pantry_snapshot JSONB`, `served_recipe_ids JSONB`, `candidates_features JSONB`, `propensity_scores JSONB`, `latency_ms INT`, `created_at TIMESTAMPTZ`. 인덱스 `(user_id, created_at DESC)` |
| `event_log` | `event_id BIGSERIAL PK`, `request_id UUID FK`, `user_id`, `recipe_id`, `position INT`, `event_type VARCHAR(16)`, `created_at`. 인덱스 `(request_id)` |
| `user_vector` | `user_id BIGINT PK`, `taste_vec REAL[] DEFAULT '{0.5,0.5,0.5}'`, `events_count INT DEFAULT 0`, `updated_at` |

관측: `config_fingerprint` = 가중치·계수 조합 해시. `/health` 카운터 `reco_served_total`, `reco_failed_total`, `reco_degraded_total`. `evaluation/feature_report.py` 17개 특성 min/median/max/null 비율.

---

## 5. 파일 배치 (원본 5절)

```text
src/features/recommend/
├── __init__.py  router.py  schema.py  service.py  repository.py  tables.py
├── engine/   candidate.py  rank.py  penalty.py  rerank.py  explain.py   (구현 추가: context.py, feedback.py)
└── evaluation/feature_report.py
```

1차 통합 회의가 계약을 데이터 파트에 맞추기로 정해 구현이 이 명세와 여러 곳에서 달라졌습니다.
근거는 `../decisions/2026-09-10_recommend_engine_follows_data_track_contract.md` 이고 상세는
작업 기록 D-16~D-22 입니다. 코드를 고칠 때는 아래를 기준으로 삼습니다.

| 이 명세 | 실제 구현 |
|---|---|
| 3축 맛 벡터 (매움, 단맛, 짠맛) | **6축** (매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐). 값이 없는 축은 분자·분모에서 함께 빠집니다 |
| 5블록 가중합 | `enums.FEATURE_KEYS` 의 **17 피처**. 못 재는 것은 None |
| `schema.RecommendRequest`/`Response` 를 B 가 정의 | A 의 `schema.py`. B 엔진은 요청 모델을 보지 않고 `engine/context.UserContext` 를 받습니다 |
| `RecommendedItem` | A 의 `stage.RankedItem`. `propensity` 는 **확률**(0 < p <= 1) |
| `engine/{candidate,rank,penalty,rerank,explain}.py` | A 의 `enums`·`stage`·`rank`·`reason`·`explore`·`serendipity` + B 의 `policy`·`taste`·`context`·`feature`·`score`·`rerank`·`candidate` |
| 가중치 w_match 0.29 등 | A 의 `enums.DEFAULT_WEIGHTS`. 절차 손잡이는 `policy.RankingPolicy` |

---

## 6. 단계와 검증 항목

| Step | 구현 | TC | 통과 기준 | 명령 |
|---|---|---|---|---|
| 0 | schema.py, `scripts/generate_mock_fixtures.py`, 더미 라우터 | TC-0-1 | "4인 가구" → `household_size=4` | `uv run pytest tests/unit/recommend/test_schema.py -k test_persona` |
| | | TC-0-2 | 임의 필드 무시 | `... -k test_extra` |
| | | TC-0-3 | Mock JSON 12개 파싱 | `... -k test_fixtures` |
| 1 | tables.py, `repository.log_serving_result`, `/health` 카운터 | TC-1-1 | 서빙 1회 → 로그 1건 | `make log-test` |
| | | TC-1-2 | DB 차단 시에도 200 | `uv run pytest tests/unit/recommend/test_logging.py` |
| | | TC-1-3 | `reco_served_total` +1 | `curl -s :8000/health \| grep reco_served` |
| 2 | intarray 바인딩, 알레르기 하드컷, 폴백 | TC-2-1 | 알레르기 레시피 0건 | `... test_candidate.py -k test_allergy` |
| | | TC-2-2 | `degraded=True`, 20건 | `... -k test_fallback` |
| | | TC-2-3 | p95 < 15ms | `make smoke` |
| 3 | rank.py, 중심화, penalty.py | TC-3-1 | 임박 없을 때 w2 분모 제외 | `... test_rank.py -k test_zero_drop` |
| | | TC-3-2 | 중심화 미적용 시 실패 (0.767 쏠림) | `... -k test_centering` |
| | | TC-3-3 | 14일 내 조리 = 정확히 50% | `... test_penalty.py` |
| 4 | rerank.py MMR·탐색·위치 무작위화·IPS, explain.py | TC-4-1 | 20 중 `is_exploration` 4 | `... test_rerank.py -k test_exploration_count` |
| | | TC-4-2 | 100회 위치 분산 | `... -k test_random_positions` |
| | | TC-4-3 | 결측 없는 한국어 조사 | `... test_explain.py` |
| 5 | service/router 연결(난수 제거), `/v1/events` EMA, feature_report | TC-5-1 | 결정론적 점수 | `... test_serve.py -k test_deterministic` (구현: `test_service.py`) |
| | | TC-5-2 | 20회 누적 시 행동 반영 100% | `... test_events.py` (구현: `test_feedback.py`) |
| | | TC-5-3 | 17개 특성 통계 | `uv run python src/features/recommend/evaluation/feature_report.py` |
| 6 | Track A 실데이터, 계약 42건, Locust | TC-6-1 | 42건 통과 | `make contract` |
| | | TC-6-2 | 결측·스키마 오류 0 | `make feature-test` |
| | | TC-6-3 | p95 < 58ms (SLA 300ms) | `locust -f tests/load/locustfile.py --headless -u 10 -r 2 --run-time 1m` |

---

## 7. 목표 지표와 착수 파라미터

| 지표 | 최저 | 목표 |
|---|---|---|
| 서빙 p95 | < 300ms | < 58ms |
| NDCG@10 | ≥ 0.85 | 0.926 |
| Recall@20 | ≥ 0.60 | ≥ 0.60 |
| 카탈로그 커버리지 | ≥ 15% | ≥ 15% |
| ILD | ≥ 0.95 | ≥ 0.95 |

착수 가중치 `w_match 0.29 · w_expiring 0.15 · w_taste 0.31 · w_quality 0.15 · w_ctx 0.10`. Bradley-Terry 학습 후 실측치로 교체, `config_fingerprint` 기록.

최종 품질 명령: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit/recommend`
