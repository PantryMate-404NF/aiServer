# 추천 코어 엔진 구현 명세 (파트 B)

**정하는 것**: 팬트리메이트 추천 엔진(파트 B)이 지금 어떻게 만들어져 있고 어떻게 움직이는가. 전체 흐름, 데이터 계약, 알고리즘과 수식, 저장소와 로그, 소스 배치, 구현 상태, 손잡이 값, 검증 결과, 아직 정하지 않은 것

**적용 대상**: 팀 전원. 파트 A(데이터)와 C(평가), 백엔드는 2절(계약)·4절(저장소와 로그)·9절(열린 결정)을 먼저 봅니다. 파트 B 구현자와 AI 코딩 에이전트는 전체를 봅니다

**버전**: 2.0.0 · **최종 수정**: 2026-09-12 · **작성자**: 유재현

---

## 1. 시스템 아키텍처와 설계 원칙

이 문서는 2026-09-09 의 개발 착수본(1.x)을 대체하는 두 번째 판입니다. 착수본은 세 파트 회의 이전의 계획이었고, 그 뒤 두 번의 회의와 열두 번의 작업 세션을 거치며 계약·피처 체계·취향 모델이 바뀌었습니다. 이 판은 **현재 저장소의 코드가 실제로 하는 일**을 적습니다. 다른 문서를 열지 않고도 읽을 수 있도록 필요한 내용은 여기 다 옮겼고, 아직 실제 데이터로 확인하지 않은 숫자는 `(예시값, 실제 데이터로 대체 필요)` 로 표시했습니다.

착수본과 달라진 것을 먼저 한눈에 보면 다음과 같습니다.

| 항목 | 착수본(1.x) | 현재(2.0.0) |
|---|---|---|
| 계약의 정본 | 파트 B 가 요청·응답 모델을 정의 | 파트 A 의 계약(`schema.py`·`stage.py`·`enums.py`)과 DDL 을 그대로 씁니다 |
| 맛 벡터 | 3축(매움, 단맛, 짠맛) | **6축**(매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐). 값이 없는 축은 계산에서 빠집니다 |
| 점수 | 5블록 가중합 | **17 피처** 가중합. 못 재는 피처는 0 이 아니라 "모름" 으로 두고 분자·분모에서 함께 뺍니다 |
| 사용자 취향 | 온보딩 3축 척도 + 행동의 지수이동평균 | 온보딩에서 **고른 음식**의 6축 평균이 먼저, 없으면 3축 척도, 둘 다 없으면 취향 없음. 행동은 시각과 함께 저장하고 반감기 감쇠와 계절 주기로 가중 평균해 합칩니다 |
| 노출 확률 | 확률의 역수 | **확률**(0 < p <= 1). 새로운 시도 슬롯만 1 미만입니다 |
| 취향 저장 | DB 의 `user_vector` | 실 DB 전까지 사용자당 JSON 파일. DB 가 붙으면 옮깁니다 |
| 후보 조회 | 파트 B 가 SQL 작성 | 파트 A 의 DB 함수 `retrieve_for_user()`. 파트 B 는 완화 계획(몇 번 더 조회할지)만 냅니다 |

### 1.1 한눈에 보는 흐름

```text
 [클라이언트] ── POST /v1/recommend ──▶ [라우터] ── (지금은 목업 응답. DB 가 붙으면 아래 경로로 연결)
                                            │
   ┌────────────────────────────────────────┴──────────────────────────────────────────┐
   │                                                                                    │
   │  ① 후보 고르기 (Retrieval) — DB 함수 retrieve_for_user()                            │
   │     냉장고 + 기본 양념으로 만들 수 있고, 알레르기 재료가 없고, 부족 재료가 k개 이하인      │
   │     레시피 최대 500건. 모자라면 k 를 2 → 3 → 4 로 풀고, 그래도 모자라면 인기순으로 채움  │
   │                                       │                                            │
   │                                       ▼                                            │
   │  ② 점수 매기기 (Ranking) — engine/feature.py · score.py                             │
   │     후보마다 17 피처를 재고 가중 평균. 모르는 피처는 빼고 셈. 최근 본 것·만든 것·        │
   │     기피 재료는 곱으로 감점. 맛 피처는 사용자의 취향 페르소나 6축과 비교                  │
   │                                       │                                            │
   │                                       ▼                                            │
   │  ③ 목록 다듬기 (Re-ranking) — engine/rerank.py                                       │
   │     MMR 로 비슷한 것이 몰리지 않게 개인화 슬롯을 채우고, 20%(취향 없으면 40%)는          │
   │     새로운 시도 슬롯(균등 + Thompson). 노출 확률과 이유 문구를 붙여 무작위 위치에 섞음     │
   │                                       │                                            │
   │                                       ▼                                            │
   │  [응답] items(순위·점수·17 피처·확률·이유) + trace(단계별 기록)                          │
   │  [로그] recommendation_log 1행 + impression N행 — 실패해도 응답은 나가고 실패를 셈        │
   └────────────────────────────────────────────────────────────────────────────────────┘

   취향 페르소나 (②가 쓰는 사용자 6축)
   [온보딩: 고른 음식 3개 이상 + 3축 척도] ─┐
   [행동: 클릭·저장·조리·별점 이벤트]      ─┴─▶ [사용자당 JSON 원본] ─▶ derive_persona() ─▶ 6축 취향
```

세 단계는 각각 독립된 부품이고 순서대로만 호출됩니다. ① 은 데이터베이스가 하고 ②③ 은 파이썬 순수 함수입니다. 같은 입력(후보, 문맥, 정책, 난수 시드)을 넣으면 같은 결과가 나오도록 만들어서, 로그에 남은 값으로 그때의 추천을 그대로 다시 만들 수 있습니다.

### 1.2 세 파트의 경계

| 파트 | 소유하는 것 | 이 엔진과의 접점 |
|---|---|---|
| A 데이터 | 레시피·재료 정규화, `recipe_feature`(6축 맛·인기·재료 집합), DDL, 후보 조회 SQL 함수, 로그 적재 함수, 계약 모델, 검사 도구(`Makefile`) | 엔진은 A 의 계약 모델만 보고 DB 행 형식을 모릅니다. 후보는 A 함수가 내고, 로그는 A 함수가 씁니다 |
| B 추천 엔진 | ② 점수, ③ 재정렬, 취향 페르소나와 그 저장소, 완화 계획, 흐름 조립, 추적 파라미터 | 이 문서의 대부분입니다 |
| C 평가 | 추천 로그를 읽어 지표를 냅니다 | 로그의 `stage_trace.params`(정책·노출 확률의 정의·취향 출처)와 `candidates`(17 피처 원값·노출 확률)를 읽습니다. 3.5절 |

### 1.3 세 개의 레이어와 현재 위치

```text
[Layer 1 단위] Mock 픽스처 위에서 ②③·취향 페르소나·완화 계획을 순수 함수로 구현하고 단위 검사로 못 박음   ── 완료
[Layer 2 통합] 라우터를 실엔진에 연결, DB 후보 조회·사용자 이력·코퍼스 통계 로드, 로그 적재 연결          ── 대기 (DB)
[Layer 3 종단] 실데이터 계약 검증, 부하 검증(p95 < 58ms), 온보딩 → 추천 → 이벤트 → 취향 갱신 루프         ── 대기 (DB)
```

Layer 1 은 끝났고 검사 276건과 가상 사용자 12명의 종단 실행으로 확인했습니다(8절). Layer 2 는 실 데이터베이스가 준비되어야 시작할 수 있으며, 그때 빠뜨리면 안 되는 항목 열다섯 개를 점검표로 두고 검사가 그것을 강제합니다(6.8절).

### 1.4 불변 원칙

1. **Zero-Drop.** 잴 수 없는 피처는 0 점이 아니라 "모름" 입니다. 가중합의 분자와 분모에서 함께 빼서, 남은 피처의 비중이 자동으로 커집니다. 0 은 "계산했더니 0", 모름은 "계산할 수 없음" 이며 학습에서도 다른 뜻입니다.
2. **감점은 곱셈.** 최근 노출·최근 조리·기피 재료의 감점은 뺄셈이 아니라 곱셈입니다. 뺄셈이면 고득점 레시피가 감점을 흡수해 계속 1위를 차지합니다.
3. **알레르기는 ① 에서 완전 제외.** 감점 대상이 아니라 SQL 조건으로 후보에서 뺍니다. 제외는 ① 에서만 하고 ②③ 은 후보를 빼지 않습니다 — 그래야 "왜 빠졌는가" 를 한 곳에서 찾습니다.
4. **맛 비교는 코퍼스 평균을 뺀 뒤에.** 모든 맛 값이 0~1 양수라 그냥 코사인을 재면 무엇을 넣어도 0.77 근처로 몰립니다. 전체 레시피의 평균을 사용자와 레시피 양쪽에서 뺀 뒤 비교합니다.
5. **취향은 묻지 않고 계산합니다.** 사용자가 고른 음식과 실제 행동에서 6축 취향을 만들고, 직접 적은 척도는 고른 음식이 있으면 저장만 합니다.
6. **같은 입력이면 같은 결과.** 엔진은 시계·DB·전역 난수를 보지 않습니다. 시각과 난수 시드는 호출자가 넘기고 로그에 남습니다.
7. **서빙 순간의 값은 그 순간에 기록합니다.** 노출 확률, 17 피처 원값, 그때의 냉장고, 정책 지문은 나중에 어떤 방법으로도 복원되지 않습니다.
8. **실패는 조용히 지나가지 않습니다.** 로그 쓰기 실패는 추천을 실패시키지 않되 반드시 셉니다. 범위 밖 입력은 잘라 넣지 않고 거부하며, 거부와 무시는 이름을 달리해 셉니다.

---

## 2. 데이터 계약

### 2.1 스테이지 사이의 계약

세 단계는 아래 모델로만 대화합니다. 사전(dict)을 넘기지 않고, 단계끼리 직접 부르지 않으며, `service.py` 가 순서대로 호출합니다.

| 모델 | 누가 만들고 누가 받나 | 필드 |
|---|---|---|
| `Candidate` | ① 이 만들고 ② 가 받음 | `recipe_id`, `missing_count`(부족 필수 재료 수), `missing_ids`, `coverage`(0~1, 필수 재료 충족률), `cluster_id`(레시피 묶음. 배치 전이면 None) |
| `ScoredCandidate` | ② 가 만들고 ③ 이 받음 | `Candidate` 의 전부 + `features`(17 피처 원값, None 허용, 전부 있어야 함), `score`(0~1), `penalty`(곱한 감점 계수) |
| `RankedItem` | ③ 이 만들고 응답에 그대로 실림 | `ScoredCandidate` 의 전부 + `final_rank`(1부터), `reason`, `reason_features`, `mmr_penalty`, `is_exploration`, `propensity`(0 < p <= 1), `explore_source`(`uniform` · `thompson` · None), `team` |
| `StageInfo` | 단계마다 하나 | `name`(retrieval · ranking · rerank), `in_count`, `out_count`, `latency_ms`, `strategy`, `filters`, `dropped`, `params`, `score_stats`, `exploration_items` |
| `StageTrace` | 응답과 로그에 실림 | `trace_version`, `stages`, `totals`(`latency_ms`, `degraded`, `user_mode`) |

`features` 에는 17 피처가 전부 있어야 하고(모르면 None), 가중치를 곱한 값이 아니라 **원값**을 저장합니다. 가중치가 바뀌어도 과거 로그를 다시 해석할 수 있고, 지금 가중치 0 인 피처도 나중에 학습에 쓸 수 있기 때문입니다.

### 2.2 엔진이 받는 입력

엔진은 HTTP 요청 모델을 보지 않습니다. 요청·DB·코퍼스에서 온 값을 한 자리에 모은 문맥을 받습니다.

| 모델 | 출처 | 필드 |
|---|---|---|
| `RecipeFeature` | `recipe_feature` 한 행 | `recipe_id`, `title`, `essential_ids`(필수 재료 집합), `all_ids`(전체 재료 집합), `flavor_vec`(6축), `popularity_score`, `quality_score`, `cook_minutes`, `cuisine`, `dish_type`, `season_score`, `difficulty`, `product_ids`. 데이터가 없는 칸은 None |
| `UserHistory` | `user_ingredient_pref`·`event_log`·`user_cluster_stat` | `liked_ingredient_ids`, `avoid_ingredient_ids`, `recent_recipe_ids`(최근 7일 노출), `cooked_recipe_ids`(최근 14일 조리), `cooked_ingredient_sets`, `cluster_seen`, `cluster_hits` |
| `CorpusStats` | `feature_stats`·`ingredient.freq_count` | `flavor_mean`(코퍼스 6축 평균), `ingredient_idf`, `ingredient_names` |
| `Persona` | 취향 페르소나 계산 결과(3.2절) | `vec`(6축, 모르는 축 None), `prior_source`(picks · scales · none), `mode`(onboarding · blended · behavior), `prior_weight`, `behavior_weight`, `n_events`, `axis_weights` |
| `UserContext` | 위 넷을 모은 것 | `user_id`, `pantry_ids`, `expiring_ids`(임박 재료), `taste_vec`(= persona.vec), `max_cook_minutes`, `preferred_cuisines`, `preferred_dish_types`, `skill_level`, `history`, `persona` |

`UserContext` 를 만드는 `build_context()` 는 `persona` 를 **필수 인자**로 받습니다. 기본값을 두면 빠뜨린 호출자가 조용히 취향 없는 사용자가 되어 목록이 통째로 달라지는데 에러는 나지 않기 때문입니다. 취향이 없으면 `cold_persona()` 를 명시적으로 넘깁니다.

### 2.3 HTTP 계약

경로는 파트 A 의 계약이며 여덟 개입니다. **지금은 전부 목업 모듈이 응답합니다.** 실 DB 가 붙을 때 `/v1/recommend` 를 `service.rank_candidates` 에, `/v1/onboarding` 과 `/v1/events` 를 `service.PersonaService` 에 잇습니다(6.8절). 그 전까지 이 경로들이 내는 응답은 실제 엔진의 결과가 아니므로 평가에 쓰지 않습니다.

| 메서드 · 경로 | 하는 일 | 요청 | 응답 |
|---|---|---|---|
| `POST /v1/recommend` | 추천 목록 | `RecommendRequest` | `RecommendResponse` |
| `GET /v1/recommendations/{request_id}` | 추천 로그 한 건 조회(디버거) | | `RecommendationLogOut` |
| `POST /v1/events` | 행동 이벤트 수집 | `EventBatchIn`(1~200건) | `EventAck` |
| `POST /v1/onboarding/{user_id}` | 온보딩 5문항 저장 | `OnboardingIn` | `OnboardingOut` |
| `GET` · `PUT /v1/users/{user_id}/pantry` | 냉장고 조회·전체 교체 | `PantryIn` | `PantryOut` |
| `GET /v1/ingredients/search` · `GET /v1/recipes/search` | 재료·레시피 검색 | `q`, `limit` | 검색 결과 |
| `GET /health` | 상태 | | `HealthOut`(`db` 는 실제 확인값, `redis` 는 근거 없는 참 — 9절) |

**`RecommendRequest`**: `user_id`, `session_id`(`c-` 실사용자 · `g-` 게스트 · `d-` 개발), `top_k`(1~100, 기본 20), `max_missing`(0~10, 기본 2), `max_minutes`, `model_version`, `weight_override`(디버거 전용 가중치), `interleave_with`(두 랭커 비교), `include_trace`, `context`. 냉장고와 알레르기는 요청에 없습니다 — DB 함수가 사용자 ID 로 유도합니다.

**`RecommendResponse`**: `contract_version`, `request_id`(이벤트를 보낼 때 함께 보내는 열쇠), `user_id`, `model_version`, `weights`(이 응답을 만든 실효 가중치), `items`(`RankedItem` 목록), `trace`(`StageTrace`), `served_at`.

**`EventIn`**: `user_id`, `event_type`(impression · click · save · unsave · cook · rating · dismiss · search), `recipe_id`, `value`(별점 또는 체류 시간), `request_id`, `position`(1부터, `final_rank` 와 같은 기준), `session_id`, `context`. 발생 시각 필드는 없어서 **서버 수신 시각**이 이벤트 시각입니다. `impression` 은 클라이언트가 보내지 않고 서버가 추천 응답 시 기록합니다.

**`OnboardingIn`**: `picks`(제시 음식 20개 가운데 고른 것의 인덱스 — 레시피 ID 가 아닙니다, 1~20개), `scales`(3축 [매움, 짠맛, 단맛] 각 0~4), `allergy_groups`, `allergy_ingredient_ids`, `avoid_ingredient_ids`(최대 3), `household_size`. 고른 개수는 서버가 강제하지 않고(프론트의 일) 3개 미만이면 셉니다. **`OnboardingOut`**: `user_id`, `taste_vec`(6축), `n_blocked_ingredients`.

### 2.4 온보딩 제시 음식 목록

취향의 측정 도구입니다. 데이터 파트가 `seeds/onboarding_recipes.yaml` 에 음식 20개와 각각의 6축 맛을 둡니다(예비 4개는 따로). 엔진은 이 파일을 읽을 때 축 순서가 (매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐)인지, 항목마다 축이 여섯 개인지 검사합니다 — 값이 전부 0~1 이라 순서가 어긋나도 다른 어떤 검사에도 걸리지 않기 때문입니다.

| 요리군 | 음식 (6축 예 — 매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐) |
|---|---|
| 한식 | 오징어볶음 (0.33, 0.33, 0.25, 0.00, 0.45, 0.03) · 오징어초무침 · 불고기 · 김치찌개 (0.47, 0.12, 0.03, 0.07, 0.25, 0.08) · 된장찌개 · 계란말이 · 해물파전 |
| 양식 | 크림파스타 (0.01, 0.19, 0.08, 0.01, 0.31, 0.62) · 스테이크 · 팬케이크 · 카프레제 |
| 중식 | 깐풍기 · 탕수육 · 짬뽕 |
| 일식 | 연어덮밥 · 유부초밥 · 돈까스 |
| 기타 | 쌀국수 · 후라이드치킨 · 카레라이스 |

사용자가 김치찌개와 짬뽕과 깐풍기를 골랐다면 첫 취향은 세 음식의 축별 평균 — 매움 0.41, 짠맛 0.17, 단맛 0.03, 신맛 0.02, 감칠맛 0.26, 기름짐 0.04 — 입니다. 시드의 맛 값이 고쳐지면 원본 인덱스로 다시 계산합니다.

---

## 3. 알고리즘과 수식

### 3.1 ① 후보 고르기 (Retrieval)

데이터 파트의 DB 함수 `retrieve_for_user(user_id, max_missing, max_minutes, limit, include_test)` 한 번으로 끝납니다. 요청당 데이터베이스 왕복이 한 번이어야 원격 DB 로 옮겨도 지연이 늘지 않습니다. 함수 안에서 사용자 ID 로 두 집합을 유도합니다.

- **보유 재료** = 냉장고에 있고 버리지 않은 재료 + 기본 양념(`is_staple`) 전부. 기본 양념을 더하지 않으면 한식 레시피의 95% 가 재료 부족으로 걸러집니다.
- **알레르기 재료** = 직접 지정한 재료 + 지정한 카테고리의 하위 전부 + 알레르기 그룹 컬럼이 같은 재료 + 직접 지정한 재료와 같은 그룹의 재료(단순 기피는 확산하지 않음). 안전 관련이라 네 경로의 합집합입니다.

후보 조건은 순서대로 다음과 같습니다.

```text
⓪  재료가 하나도 붙지 않은 레시피 제외 (n_total > 0)
⓪' 합성 시험 피처 제외 (feature_version 이 'test-' 로 시작하면 제외. 스모크·단위 검사만 켤 수 있음)
⓪'' 필수 재료가 0개인 레시피는 미매칭 비율이 30% 이하일 때만 (정규화 실패 레시피가 만점을 받는 것을 막음)
①  필수 재료가 보유 재료와 하나라도 겹침, 또는 필수 재료가 전부 기본 양념
②  전체 재료가 알레르기 재료와 겹치지 않음                    ← 하드컷
③  부족한 필수 재료 수 <= max_missing (기본 2)
④  조리 시간 <= max_minutes (상한을 준 경우. 조리 시간을 모르는 레시피는 통과)
정렬  부족 재료 수 오름차순, 인기 점수 내림차순.  LIMIT 500 (예시값, 실제 데이터로 대체 필요)
반환  recipe_id, missing_count, missing_ids, coverage = 1 - 부족 수 / 필수 수 (필수 0개면 1.0), cluster_id
```

**완화 계획.** 한 번 조회로 후보가 충분하지 않으면 다시 조회합니다. 조회 결과 안에서 조건을 풀어 봐도 새 후보는 나오지 않으므로 완화는 재조회여야 합니다. 파트 B 의 `engine/candidate.py` 가 "다음 조회 인자" 만 내고 조회 자체는 하지 않습니다.

```text
needed = max(20, top_k + 2 × exploration_min_pool_ratio × 탐색 슬롯 수)
       = 36  (top_k 20, 탐색 4칸)     /  52 (취향 없는 사용자, 탐색 8칸)

1차   k = 2                                            stage = none
      후보 < needed 이면 → k = 3 → k = 4                stage = relax_missing
      그래도 < needed 이면 → 인기순 (부족 재료 무시,
                              조리 시간 상한은 유지)      stage = popularity
```

탐색 슬롯은 개인화에 들지 못한 잔여 후보의 상위 절반에서만 뽑고, 그 절반이 슬롯 수의 2배는 있어야 슬롯을 줄이지 않습니다(3.4절). 그래서 슬롯 수만 더한 24건으로는 4칸 중 1칸만 채워졌고, 지금은 36건을 요구합니다. 대가는 완화 빈도입니다 — 가상 카탈로그 120건에서 인기순 폴백이 12명 중 7명이었고 실데이터에서 다시 잽니다. 어느 단계까지 풀었는지는 `trace.totals.degraded` 와 단계의 `fallback` 에 남습니다.

### 3.2 취향 페르소나

사용자의 6축 취향입니다. ② 의 맛 피처가 이것을 레시피의 6축과 비교합니다.

**사전 취향(prior)의 우선순위**

```text
(1) 온보딩에서 고른 음식이 있으면  → 고른 음식들의 축별 평균.            출처 = picks,  무게 k = 12
(2) 없고 3축 척도가 있으면          → [매움, 짠맛, 단맛] = 척도 / 4, 뒤 3축 = 모름.  출처 = scales, 무게 k = 6
(3) 둘 다 없으면                    → 6축 전부 모름.                       출처 = none,   무게 k = 0
```

고른 음식이 있으면 3축 척도는 **계산에 쓰지 않고 저장만** 합니다. 둘이 어긋나면 실제로 고른 것이 자기 보고보다 믿을 만하고, 척도는 나중에 말로 요청하는 추천의 보정값으로 쓸 계획이기 때문입니다. 같은 음식을 두 번 고른 것은 한 번으로 셉니다. 척도가 0~4 를 벗어나면 잘라 넣지 않고 거부합니다(잘라 넣으면 5 가 4 와 같아지는데 에러가 없습니다).

**행동 이벤트의 무게**

레시피를 클릭·저장·조리하거나 별점을 주면 백엔드가 `EventIn` 으로 보냅니다. 엔진은 그 레시피의 6축 맛과 수신 시각을 이벤트로 저장하고, 취향을 계산할 때마다 이벤트마다 무게를 매깁니다.

```text
w = 종류 무게 × 시간 감쇠 × 연 주기 친화도 × 주 주기 친화도 × 일 주기 친화도

종류 무게   조리 1.0 · 저장 0.6 · 클릭 0.3 · 별점 (별점 - 3) / 2 를 0~1 로 자름 (5점 = 1.0, 3점 이하 = 0)
            무시·저장 취소·노출·검색 = 0 (취향은 좋아한 것으로만 만듭니다)
            1~5 밖의 별점은 무게가 아니라 잘못된 이벤트로 세고 저장하지 않습니다
시간 감쇠   0.5 ^ (경과일 / 반감기),  반감기 90일 (예시값, 실제 데이터로 대체 필요). 0 이하면 감쇠 끔
주기 친화도 1 - s × (1 - cos(2π × 위상 차이)) / 2
            위상 = 연중 경과 비율(연) · 요일+시각(주) · 하루 경과 비율(일)
            세기 s: 연 0.5 · 주 0 · 일 0 (예시값, 실제 데이터로 대체 필요). 0 이면 그 주기를 보지 않음
```

반감기 90일이면 석 달 전 조리는 0.5, 1년 전 조리는 0.06 의 무게입니다. 연 주기 세기 0.5 이면 정반대 계절의 이벤트는 절반이 됩니다. 세기 1 은 권하지 않습니다 — 반대 계절의 이벤트가 전부 사라져 취향이 갑자기 사전 취향으로 돌아갑니다. 맛을 하나도 모르는 이벤트(레시피의 6축이 전부 비어 있음)는 세지 않습니다.

**합치는 식** — 축마다 따로 계산합니다.

```text
취향[축] = (k × prior[축] + S[축] × behavior[축]) / (k + S[축])

behavior[축] = 그 축에 값이 있는 이벤트들의 무게 가중 평균
S[축]        = 그 이벤트들의 무게 합
prior 가 없는 축은 behavior 만, behavior 가 없는 축은 prior 만, 둘 다 없으면 모름
```

이벤트가 없으면 S = 0 이라 사전 취향 그대로이고, 쌓일수록 행동 쪽으로 옮겨 갑니다. 오래된 이벤트가 감쇠해 S 가 줄면 다시 사전 취향으로 돌아옵니다 — 잊는 것이 따로 없이 식에서 나옵니다. 상태는 셋입니다: 세어진 이벤트가 없으면 `onboarding`, 무게 합이 기준(사전 취향의 k, 사전 취향이 없으면 6.0) 이상이면 `behavior`, 그 사이면 `blended`. 이 상태와 출처는 추천 로그에 실려 평가가 사용자 집단을 나누는 데 씁니다.

**취향이 없는 사용자.** 6축이 전부 모름이면 맛 피처가 계산에서 빠지고(Zero-Drop) 새로운 시도 비율이 20% 에서 40% 로 올라갑니다. 임의의 취향을 넣지 않습니다 — 넣으면 일관되게 엉뚱한 추천이 되고, 비우면 다양성 장치가 대신 일합니다.

**저장.** 실 DB 전까지 사용자당 JSON 파일 하나에 **원본**을 둡니다 — 고른 음식의 인덱스와 그때의 6축 스냅숏, 3축 척도 원본, 이벤트 목록(레시피, 종류, 시각, 6축 스냅숏, 값). 결과가 아니라 원본을 두어 시드나 계산식이 바뀌어도 다시 계산할 수 있습니다. 파일은 사용자 ID 로 256개 폴더에 나누고, 호출마다 다른 임시 파일에 쓴 뒤 이름을 바꾸며(쓰다가 죽어도 이전 파일이 남음), 파일 안의 사용자와 요청한 사용자가 다르면 읽기를 거부하고, 같은 사용자의 읽고-합치고-쓰기는 프로세스 안에서 잠금으로 직렬화합니다. 저장 전에 730일보다 오래되거나 2,000건을 넘는 이벤트를 잘라냅니다(예시값, 실제 데이터로 대체 필요) — 기본값에서는 잘리는 이벤트의 무게가 0.4% 이하입니다. JSON 으로는 멀쩡해도 축이 모자라거나 NaN 이거나 범위 밖인 파일은 읽기 실패 한 종류로 다루고, 서빙은 그것을 받아 취향 없는 사용자로 응답하며 실패를 셉니다. DB 가 붙으면 같은 내용을 `user_vector` 와 `event_log` 에서 읽습니다(6.8절).

### 3.3 ② 점수 매기기 (Ranking)

후보마다 17 피처를 재고 가중 평균한 뒤 감점을 곱합니다. 전부 0~1 입니다.

```text
raw    = Σ w_i × f_i / Σ w_i        (f_i 가 모름이거나 w_i = 0 인 피처는 분자·분모에서 함께 제외)
score  = raw × P_recent × P_cooked × (1 - P_avoid)
```

| 군 | 피처 | 계산 | 모름이 되는 조건 | 가중치 (예시값, 실제 데이터로 대체 필요) |
|---|---|---|---|---|
| 재료 | `f_coverage` | ① 이 준 coverage = 1 − 부족 수 / 필수 수 | 없음 | 0.24 |
| 재료 | `f_missing` | 1 − 부족 수 / (max_missing + 1) | 없음 | 0.05 |
| 재료 | `f_expiring` | 임박 재료 가운데 필수 재료에 쓰이는 비율 | 임박 재료가 없음 | 0.15 |
| 재료 | `f_pantry_use` | 보유 재료 가운데 레시피에 쓰이는 비율 | 보유 재료가 없음 | 0 |
| 취향 | `f_taste` | 중심화 코사인(아래) | 코퍼스 평균 없음 · 공통 축 없음 · 방향 없음 | 0.16 |
| 취향 | `f_ing_pref` | 레시피 재료 가운데 선호 재료 비율 | 선호 재료 이력이 없음 | 0.11 |
| 취향 | `f_cuisine` | 선호 요리군이면 1, 아니면 0 | 레시피 요리군 없음 · 선호 없음 | 0.04 |
| 취향 | `f_dish_type` | 선호 요리 종류면 1, 아니면 0 | 같음 | 0 |
| 취향 | `f_cooccur` | 최근 조리한 레시피들과의 IDF 가중 자카드 최대값 | 조리 이력이 없음 | 0.10 |
| 취향 | `f_ing_cf` · `f_group_pref` · `f_content` | 계산 수단 없음 | 항상 모름 | 0 |
| 품질 | `f_popularity` · `f_quality` | `recipe_feature` 값 그대로 | 값 없음 | 0.10 · 0 |
| 상황 | `f_time_fit` | 1 − max(0, (조리 시간 − 상한) / 상한) | 상한 또는 조리 시간 없음 | 0.03 |
| 상황 | `f_season` | 제철 점수 그대로 | 값 없음 | 0.02 |
| 상황 | `f_skill_fit` | 1 − abs(난이도 − 실력) | 둘 중 하나 없음 | 0 |

가중치 0 인 피처는 재기만 하고 순위에 쓰지 않습니다. 가중치의 합은 1.00 이고, 그 가운데 이력이 있어야 재는 `f_ing_pref`·`f_cooccur`(0.21)와 데이터가 아직 없는 `f_cuisine`·`f_season`(0.06)은 지금 Mock 에서 대부분 모름이라 순위에 관여하지 않습니다. 데이터가 오면 코드를 고치지 않아도 켜집니다. 가중치는 실제 사용자 데이터로 쌍대비교 학습을 해 다시 정합니다.

**맛 피처(중심화 코사인)**

```text
u' = 사용자 6축 - 코퍼스 평균,  r' = 레시피 6축 - 코퍼스 평균   (세 벡터 모두 값이 있는 축만)
cos = u'·r' / (|u'| |r'|)
similarity = (cos + 1) / 2                                    (0~1 로 옮김)
confidence = min(1, |u'| / 0.25)                              (평균에서 가까운 사용자는 방향만으로 전폭 반영하지 않음)
f_taste    = 0.5 + (similarity - 0.5) × confidence
```

`0.25` 는 온보딩 한 단계에 해당하는 거리입니다(예시값, 실제 데이터로 대체 필요). 어느 한쪽이 평균과 같아 방향이 없으면 모름입니다. 사유 문구용으로는 기여가 가장 큰 축과 그 축에서 사용자가 평균보다 높은 쪽인지를 함께 구해, 매운맛을 싫어하는 사람에게 "선호하시는 매운맛" 이라고 쓰지 않게 합니다.

**감점 계수**

| 계수 | 조건 | 값 (예시값, 실제 데이터로 대체 필요) |
|---|---|---|
| `P_recent` | 최근 7일 안에 노출한 레시피 | × 0.7 |
| `P_cooked` | 최근 14일 안에 조리한 레시피 | × 0.5 |
| `P_avoid` | 기피 재료 비율 × 2.0, 상한 0.8 | × (1 − P_avoid), 최저 × 0.2 |

곱한 계수는 `penalty` 로 남겨 로그에서 되계산됩니다. 후보는 점수 내림차순, 같은 점수는 `recipe_id` 순으로 고정합니다.

### 3.4 ③ 목록 다듬기 (Re-ranking)

**개인화 슬롯 — MMR.** 점수 상위 200건(예시값, 실제 데이터로 대체 필요)을 풀로 두고 한 자리씩 고릅니다.

```text
다음 아이템 = argmax [ λ × score(r) − (1 − λ) × max_{s ∈ 이미 뽑은 것} sim(r, s) ],  λ = 0.7
sim(r, s)   = Σ_{공통 재료} IDF / Σ_{합집합 재료} IDF          (소금·물처럼 흔한 재료는 가볍게)
```

먼저 `top_k` 개를 전부 개인화로 고른 뒤, 새로운 시도 슬롯 수만큼 뒤에서 잘라 냅니다. 개인화를 먼저 정해야 결과가 난수에 흔들리지 않고, 개인화가 어차피 보여 줄 것을 탐색이 다시 고르는 일이 없습니다.

**새로운 시도 슬롯 — 혼합 탐색.**

```text
슬롯 수 n     = round(top_k × 비율),  비율 0.2 (취향 없으면 0.4)
후보 풀       = 개인화에 들지 못한 잔여 후보 가운데 점수가 중위수 이상인 것, 최대 200건
슬롯 조정     = min(n, |후보 풀| // 2)     → 채우지 못한 칸 수는 dropped.explore_shortfall 에 남김
균등 몫       = max(1, round(n × 0.5))   → 풀에서 균등 무작위. 모든 후보에 최소 노출 확률을 보장
Thompson 몫   = 나머지                   → 레시피 묶음(cluster)별 Beta 사후분포를 뽑아 상위 묶음에서 점수 최고 후보
군집이 없으면 → 전부 균등 (계약대로), 추적에 explore_fallback = uniform
```

균등 몫이 없으면 Thompson 이 외면한 묶음의 레시피는 노출 확률이 0 이 되어 나중에 어떤 방법으로도 평가할 수 없습니다. 반대로 Thompson 이 없으면 우연한 발견이 없습니다. 그래서 반씩입니다(예시값, 실제 데이터로 대체 필요).

**노출 확률.** 아이템 단위의 확률이고, 서빙 순간에 계산한 값이 유일본입니다.

```text
균등으로 뽑힐 확률     = 균등 몫 / |후보 풀|
Thompson 으로 뽑힐 확률 = 몬테카를로 200회로 추정한 "그 묶음이 뽑힐 확률" 을 묶음의 점수 최고 후보에 부여
아이템 확률           = 두 확률의 합 (하한 1e-6, 상한 1)
개인화 슬롯의 확률     = 1.0   ← "편향이 없다" 가 아니라 "이 로그로는 보정할 수 없다" 는 뜻
```

**위치와 이유.** 새로운 시도 아이템은 1~top_k 가운데 무작위 위치에 끼웁니다. 위치를 고정하면 위치별 검사확률 곡선을 구할 수 없습니다. 이유 문구는 그 요청의 후보 집합을 기준으로 표준화한 두드러짐(`w × (f − 평균) / 표준편차`, 표준편차 하한 0.05)이 큰 피처 두 개를 템플릿으로 이어 만듭니다 — "두부(D-2)를 소진할 수 있고, 가진 재료로 바로 만들 수 있어요" 처럼. 점수가 높은 이유(항상 `f_coverage`)가 아니라 다른 후보와 달라서 뽑힌 이유를 말하기 위해서입니다. 조사는 앞 글자의 받침으로 고르고, 채울 값이 없는 템플릿은 건너뜁니다. 새로운 시도 아이템은 "새로운 시도는 어떠세요" 입니다.

### 3.5 추적 파라미터와 로그

추천 한 번의 `trace.stages` 에는 ranking 과 rerank 두 단계가 실립니다. 두 단계의 `params` 는 같고, 아래 열 개의 **동결 키**를 반드시 갖습니다. 값이 아니라 정의가 소급 불가라, 이 키가 없는 로그로는 노출 확률을 재구성할 수 없습니다.

| 키 | 뜻 |
|---|---|
| `policy_id` | 어느 정책이었나 (`reco-b-linear-v0`) |
| `propensity_semantics` | 무엇의 확률인가 (`item`) |
| `explore_pool_size` · `uniform_share` · `propensity_mc` | 탐색 풀 크기 200 · 균등 비율 0.5 · 몬테카를로 200 |
| `rng_seed` | 난수 시드. 같은 시드면 같은 탐색 슬롯 |
| `max_missing_final` | 완화 뒤 실제로 쓴 부족 재료 상한 |
| `top_k` · `n_explore` | 노출 개수 · 실제 탐색 슬롯 수 |
| `serving_mode` | `real` · `sim` · `load_test`. 저장하는 후보 수가 다름(50 · 10 · 0) |

여기에 요청별 값을 덧붙입니다. 동결 키를 덮어쓰는 것은 코드가 거부합니다.

| 키 | 뜻 |
|---|---|
| `persona_source` · `persona_mode` | 취향 출처(picks · scales · none) · 상태(onboarding · blended · behavior) |
| `persona_events` · `persona_behavior_weight` | 세어진 이벤트 수 · 행동 무게 합 |
| `explore_fallback` | 군집이 없어 탐색을 전부 균등으로 채웠으면 `uniform`. 있으면 그 요청의 실효 균등 비율은 1.0 |

rerank 단계의 `dropped` 에는 `mmr_or_cap`(다양성·상한으로 빠진 수)과 `explore_shortfall`(채우지 못한 탐색 칸 수)이, ranking 단계의 `score_stats` 에는 점수의 min·p25·p50·p75·max 가 실립니다. 점수 분포가 납작해지는 것을 이 값으로 알아챕니다.

**로그 적재**(파트 A 의 `write_recommendation`). 응답 직후 `recommendation_log` 1행과 노출 아이템마다 `event_log` 의 `impression` 1행을 씁니다. 저장하는 후보는 점수 상위 50건에 **실제 노출분을 합집합**한 것입니다 — 새로운 시도 아이템은 상위 200 풀에서 뽑혀 50 밖으로 떨어질 수 있는데, 그것이 노출 확률 1 미만인 유일한 행이라 잘리면 평가에 필요한 것만 정확히 사라집니다. 점수 재현에 필요한 세 값(`config_hash`, `warm_alpha`, `stats_version`)이 없으면 행에 `not_reproducible` 표시를 남깁니다. 쓰기는 300ms 안에 끝나야 하고, 실패하면 예외를 올리지 않고 `failed` 를 세며 최소 행(묘비)만이라도 남깁니다. 세는 값은 `written` · `promoted` · `duplicate` · `failed` · `impressions` · `tombstoned` · `contract_violation` 입니다.

---

## 4. 데이터베이스와 저장소, 관측

### 4.1 이 엔진이 읽고 쓰는 테이블

DDL 은 데이터 파트가 `deploy/init/02_schema.sql` 로 소유합니다. 여기서는 엔진이 어느 칸을 어떻게 쓰는지만 적습니다.

| 테이블 | 엔진이 읽는 것 | 엔진이 쓰는 것 |
|---|---|---|
| `recipe_feature` | `essential_ids`, `all_ids`, `n_essential`, `n_total`, `n_unmatched`, `flavor_vec`(6축), `popularity_score`, `quality_score`, `cook_minutes`, `difficulty`, `cuisine`, `dish_type`, `season_vec`, `cluster_id`, `feature_version` | 없음 |
| `feature_stats` | `flavor_mu`(코퍼스 6축 평균), `n_recipes` → 로그의 `stats_version` | 없음 |
| `ingredient` | `freq_count`(IDF 의 원천), `is_staple`, `name` | 없음 |
| `user_ingredient_pref` | `score`(−1~1)로 선호·기피 재료 | 없음 (온보딩의 기피 재료는 A 함수가 −0.8 로 저장) |
| `user_cluster_stat` | `n_impress`, `n_positive` → Thompson 의 Beta 사후분포 | 없음 (배치가 채움) |
| `user_vector` | `taste_vec`, `n_events`, `computed_from`, `onboarding_picks`, `onboarding_scales` | 취향 원본을 DB 로 옮길 때 이 칸에 씁니다(6.8절) |
| `event_log` | 최근 노출·조리 이력, 조리 레시피의 재료 집합 | `impression` 행(A 함수) |
| `recommendation_log` | 디버거 조회 | 서빙 1행 — `stage_trace`, `candidates`, `served`, `config_hash`, `warm_alpha`, `stats_version`, `pantry_snapshot`, `policies`(A 함수) |
| `scoring_config` | `config_hash` 로 기준 가중치 복원 | 가중치·감점 계수 등록 |

### 4.2 취향 원본 JSON 저장소 (실 DB 전까지)

```text
data/<user_id % 256 을 16진수 두 자리>/<user_id>.json
{
  "schema": 1,
  "user_id": 1001,
  "picks": [3, 11, 13],                      온보딩 제시 목록의 인덱스
  "pick_flavors": [[0.47, 0.12, ...], ...],   그때의 6축 스냅숏
  "scales": [0.75, 0.25, 0.0],               3축 척도를 0~1 로 옮긴 값 (없으면 null)
  "events": [{"recipe_id": 42, "kind": "cook", "at": "2026-09-11T12:00:00+09:00",
              "flavor": [0.5, 0.4, 0.3, 0.2, 0.1, 0.0], "value": null}, ...],
  "updated_at": "2026-09-11T12:00:00+09:00"
}
```

시각은 반드시 시간대가 있어야 합니다(없으면 거부). `data/` 는 개인의 행동 이력이 들어 있어 저장소에 커밋되지 않습니다.

### 4.3 관측

엔진은 프로세스 메모리 카운터로 "에러 없이 틀리는 자리" 를 드러냅니다. 아직 `/health` 나 대시보드가 이 값을 싣지 않아 밖에서 볼 수 없으며, 그것을 잇는 일은 DB 전환 점검표에 있습니다.

| 카운터 | 언제 늘어나나 |
|---|---|
| `persona_onboarded` · `persona_events_stored` | 온보딩 저장 · 이벤트 저장 |
| `persona_pick_out_of_range` · `persona_scale_out_of_range` | 범위 밖 인덱스 · 척도(거부) |
| `persona_pick_duplicate` · `persona_picks_under_min` | 같은 음식 중복 · 3개 미만 선택 |
| `persona_event_ignored` · `persona_event_invalid` · `persona_event_duplicate` | 무게 0 인 종류 · 범위 밖·값 없는 별점 · 한 배치 안의 같은 이벤트 |
| `persona_recipe_unknown` | 맛을 모르는 레시피의 이벤트 |
| `persona_missing` · `persona_profile_unreadable` | 원본 없음 · 원본 파일을 읽을 수 없음(둘 다 취향 없는 사용자로 응답) |
| `explore_shortfall` · `explore_uniform_fallback` | 채우지 못한 탐색 칸 수 · 군집 없이 균등으로 채운 요청 수 |
| `written` · `promoted` · `duplicate` · `failed` · `impressions` · `tombstoned` · `contract_violation` | 로그 적재 결과 |

`/health` 의 `db` 는 실제 연결 확인값입니다. `redis` 는 저장소에 클라이언트가 없는데 기본값 참이라 근거가 없습니다(9절).

---

## 5. 소스 코드 배치와 모듈 책임

```text
src/features/recommend/
├── enums.py            [A] 피처 17종·가중치·이벤트 종류·동결 키·저장 후보 수
├── stage.py            [A] 스테이지 계약 (Candidate · ScoredCandidate · RankedItem · StageTrace)
├── schema.py           [A] HTTP 계약 (RecommendRequest/Response · EventIn · OnboardingIn …)
├── policy.py           [B] 절차 손잡이 RankingPolicy (7절). 범위 검증. 추적 파라미터. 정책 지문
├── profile_store.py    [B] 취향 원본 JSON 저장소. 제시 음식 목록 로더
├── service.py          [B] 흐름 조립 — rank_candidates(②③), PersonaService(온보딩·이벤트·조회), 카운터
├── repository.py       [A] 후보 조회 retrieve(), 로그 적재 write_recommendation()
├── repository_ingest.py[A] 배치용 SQL
├── router.py           [A] 경로 8개. 지금은 engine/mock.py 를 부름
├── ingest/, evaluation/, prompts/   [A] 정규화 파이프라인, 평가 유틸
└── engine/
    ├── candidate.py    [B] 완화 계획 — needed(), first_plan(), next_plan(), dedupe()
    ├── context.py      [B] 엔진 입력 모델 — RecipeFeature · UserHistory · CorpusStats · UserContext · build_context()
    ├── taste.py        [B] 6축 맛 — centered_cosine(), dominant_axis(), 축 결측 처리
    ├── persona.py      [B] 취향 페르소나 — 우선순위 · 이벤트 무게 · 감쇠 · 주기 · 합치기 · 잘라내기
    ├── feature.py      [B] 17 피처 원값
    ├── score.py        [B] 가중합과 감점 → ScoredCandidate
    ├── rerank.py       [B] MMR · 혼합 탐색 · 균등 폴백 · 위치 · 이유 → RankedItem
    ├── rank.py         [A] 두드러짐(z-salience) · 저장 후보 선택 · 동결 키 검사
    ├── reason.py       [A] 이유 템플릿과 조사 처리
    ├── explore.py      [A] 무작위 위치 · Team-Draft Interleaving
    ├── serendipity.py  [A] 균등 + Thompson 혼합 탐색과 노출 확률
    └── mock.py         [B] 실 DB 전의 목업 응답

scripts/generate_mock_fixtures.py   가상 사용자 12명 · 레시피 120 · 재료 60 · 알레르기 그룹 18 (tests/fixtures/recommend/)
scripts/eval_recommend_mock.py      12명 종단 실행과 판정 지표, 피드백·계절·지연시간 시나리오
seeds/onboarding_recipes.yaml       온보딩 제시 음식 20개의 6축
tests/unit/recommend/               파트 B 검사 136건 + 파트 A 검사 도구(계약 98건 등)
```

`[A]` 는 데이터 파트가 만든 파일을 그대로 쓰는 것이고, 파트 B 는 표기·타입 수정과 인자 하나(`mixed_exploration(mc=)`) 외에는 손대지 않았습니다. `engine/` 은 외부 입출력이 없는 순수 함수만 둡니다 — DB 도, 시계도, 전역 난수도 보지 않습니다.

---

## 6. 구현 단계와 현재 상태

착수본의 Step 0~6 을 그대로 두고 상태를 적습니다. 검증 항목은 실제로 있는 검사로 바꿨습니다.

### 6.1 Step 0 — 데이터 계약과 가상 환경 · 완료

| 항목 | 상태 |
|---|---|
| 계약 모델 | 파트 A 의 `schema.py`·`stage.py`·`enums.py` 를 정본으로 채택. 파트 B 의 옛 모델은 삭제 |
| 가상 인벤토리 | 12명(취향·냉장고·알레르기·조리 시간 상한이 다름), 레시피 120, 재료 60, 알레르기 그룹 18. 생성기는 해시로 결정론을 확보하고 페르소나마다 척도에 가장 가까운 제시 음식 4개를 고른 것으로 둡니다(척도만 있는 사람 1, 취향 없는 사람 1 포함) |
| 라우터 골격 | 경로 8개가 목업으로 응답 |

### 6.2 Step 1 — 로그와 관측 · 부분

| 항목 | 상태 |
|---|---|
| DDL | 파트 A 소유(`deploy/init/02_schema.sql`). 파트 B 의 `tables.py` 는 만들지 않음 |
| 로그 적재 | 파트 A 의 `write_recommendation()` 있음. 서빙 경로가 아직 부르지 않음 |
| 카운터 | 프로세스 메모리 카운터 있음(4.3절). `/health` 에 노출은 미착수 |

### 6.3 Step 2 — 후보 고르기와 완화 · 부분

| 항목 | 상태 |
|---|---|
| DB 함수 | 파트 A 의 `retrieve_for_user()` 있음(3.1절) |
| 완화 계획 | 파트 B 의 `engine/candidate.py` 완료. `needed()` 가 탐색 비율을 반영 |
| 연결 | 서빙 경로가 함수를 부르는 것은 미착수 |
| 검사 | `test_candidate.py` 6건 — 첫 계획 k=2, 충분하면 종료, 2→3→4→인기순, `needed(20)=36`·취향 없으면 52, 중복 제거 |

### 6.4 Step 3 — 점수 매기기 · 완료

| 항목 | 상태 |
|---|---|
| 17 피처 · Zero-Drop · 감점 | 완료(`engine/feature.py`·`score.py`) |
| 맛 중심화 | 완료(`engine/taste.py`). 축 결측 처리, 신뢰도 |
| 검사 | `test_feature.py` 12 · `test_score.py` 10 · `test_taste.py` 10 — 결측이 분모에서 빠짐, 중심화 없이는 실패, 조리 14일 감점 정확히 0.5, 공통 축 없으면 모름 |

### 6.5 Step 4 — 목록 다듬기 · 완료

| 항목 | 상태 |
|---|---|
| MMR · 혼합 탐색 · 위치 · 노출 확률 · 이유 | 완료(`engine/rerank.py` + 파트 A 의 `serendipity`·`explore`·`reason`·`rank`) |
| 군집 없을 때 균등 폴백 | 완료(파트 B 우회. 파트 A 함수 안에 넣는 것은 9절) |
| 검사 | `test_rerank.py` 11 — 탐색 4칸·취향 없으면 8칸, 확률이 (0,1], 위치가 매번 다름, 같은 시드면 같은 결과, 이유가 비지 않음 |

### 6.6 Step 5 — 취향 페르소나와 조립 · 부분

| 항목 | 상태 |
|---|---|
| 취향 페르소나 | 완료(`engine/persona.py`·`profile_store.py`·`service.PersonaService`) |
| ②③ 조립 | 완료(`service.rank_candidates`). 추적 파라미터 동결 키 10종 + 취향·탐색 키 |
| 라우터 연결 | 미착수. `/v1/recommend`·`/v1/onboarding`·`/v1/events` 가 목업 |
| 검사 | `test_persona.py` 33 · `test_profile_store.py` 11 · `test_service.py` 27 · `test_wiring.py` 6 — 우선순위, 척도 미사용, 합치는 식(1/13, 120/132), 반감기 정확히 절반, 1년 전 이벤트로 되돌아옴, 계절 친화도, 시간대 없는 시각 거부, 저장소 왕복·샤딩·원자성·사용자 대조·깨진 파일, 동시 배치 무손실, 별점 상한, 탐색 부족 추적 |

### 6.7 Step 6 — 종단과 부하 · 대기

| 항목 | 상태 |
|---|---|
| 계약 검증 | 파트 A 의 98건이 통과(설정값을 채운 환경). 착수본의 "42건" 은 이것으로 대체 |
| 실데이터 무결성 · 부하(p95 < 58ms) | 실 DB 와 Locust 가 없어 미실행. Mock 후보 500건에서 엔진 p95 17.6ms |

### 6.8 실 DB 를 붙일 때 반드시 함께 처리할 것

라우터가 목업으로 200 을 돌려주므로 아래를 하나 빠뜨려도 에러가 나지 않습니다. 그래서 `tests/unit/recommend/test_db_cutover.py` 가 각 항목에 못을 박아 두었고, 항목 하나를 건드리면 검사가 깨지며 실패 메시지가 번호를 알려 줍니다.

| 항목 | 해야 할 일 | 확인 근거 |
|---|---|---|
| 라우터 실연결 | `/v1/recommend` → `service.rank_candidates`. 아래 넷과 같은 변경에서 | 응답의 `model_version` 이 정책 이름과 같음 |
| 후보 조회 연결 | `repository.retrieve()` 산출을 엔진에 넣기. 완화는 `engine/candidate.py` 계획대로 재조회 | 요청당 DB 왕복 1회 |
| 사용자 이력 적재 | 선호·기피 재료, 최근 노출·조리, 군집 관측을 DB 에서 읽어 `UserHistory` 로 | `f_ing_pref`·`f_cooccur` 가 모름이 아님 |
| 코퍼스 통계 로드 | `feature_stats.flavor_mu` 와 IDF. `stats_version` 을 로그에 | 맛 코사인이 실제 평균을 차감 |
| 로그 적재 연결 | 응답 직후 `write_recommendation`. `config_hash`·`warm_alpha`·`stats_version` 을 반드시 넘김 | 저장된 행의 `params` 에 동결 키 10종 |
| 난수 시드 실사용 | 서빙이 `random.Random(rng_seed)` 를 만들어 넘김 | 같은 시드로 두 번 호출해 탐색 슬롯이 같음 |
| 실패 카운터 노출 | `counters()` 를 `/health` 나 대시보드에 | 적재를 일부러 실패시키고 밖에서 보임 |
| 손잡이 정본 결정 | `Settings`(환경변수)와 `RankingPolicy` 가운데 하나로 | `.env` 값을 바꾸면 추적 파라미터가 따라 바뀜 |
| 합성 피처 격리 확인 | `feature_version LIKE 'test-%'` 필터 실측 | 합성 행 0건 |
| `f_time_fit` 재정의 | ① 이 조리 시간을 이미 걸러 살아남은 후보가 전부 1.0. 가중치 학습과 함께 | 값 종류가 2 이상 |
| `/health` 의 `redis` | 붙여서 확인하거나 필드를 뺌 | 근거 없는 참이 없음 |
| DB 필요한 A 검사 4종 | `make smoke`·`log-test`·`ddl-test`·`feature-test` | 네 명령 종료 코드 0 |
| 커버리지 범위 되돌리기 | 단독 스크립트를 pytest 로 옮기는 만큼 `omit` 제거 | 80% 이상 유지 |
| 취향 원본을 DB 로 | `ProfileStore` 의 DB 구현, JSON 이벤트를 `event_log` 로. 두 저장소의 페르소나 대조(재시도 중복이 있는 사용자는 따로) | 축마다 1e-6 안에서 같음 |
| 온보딩·이벤트 라우트 실연결 | `PersonaService.save_onboarding`·`record_events` 연결. 이벤트 시각은 수신 시각 | 범위 밖 인덱스가 거부되고 `persona_events_stored` 가 늘어남 |

---

## 7. 손잡이 값과 가중치, 목표 지표

### 7.1 절차 손잡이 (`policy.RankingPolicy`)

전부 `(예시값, 실제 데이터로 대체 필요)` 입니다. 하나라도 바꾸면 정책 지문(`fingerprint`)이 달라져 이전 로그와 섞이지 않습니다. 범위를 벗어난 값(주기 세기 > 1, 사전 무게 <= 0, 상한 0 등)은 정책을 만들 때 거부합니다.

| 묶음 | 손잡이 | 값 | 뜻 |
|---|---|---|---|
| 감점 | `penalty_recent` · `penalty_cooked` | 0.7 · 0.5 | 최근 7일 노출 · 14일 조리 |
| 감점 | `avoid_multiplier` · `avoid_cap` | 2.0 · 0.8 | 기피 재료 비율의 배수 · 상한 |
| 맛 | `taste_min_norm` | 0.25 | 평균에서 이만큼 떨어져야 맛을 전폭 반영 |
| ① 완화 | `max_missing` · `max_missing_relaxed` | 2 · 4 | 부족 재료 상한과 완화 상한 |
| ① 완화 | `min_candidates` · `candidate_limit` | 20 · 500 | 최소 후보 · 조회 상한 |
| ③ 재정렬 | `mmr_lambda` · `mmr_pool_size` | 0.7 · 200 | 점수 대 다양성 · MMR 풀 |
| ③ 탐색 | `exploration_ratio` · `cold_exploration_ratio` | 0.2 · 0.4 | 새로운 시도 비율 · 취향 없을 때 |
| ③ 탐색 | `explore_pool_size` · `exploration_min_pool_ratio` | 200 · 2 | 탐색 풀 · 슬롯당 필요한 후보 배수 |
| ③ 탐색 | `uniform_share` · `propensity_mc` | 0.5 · 200 | 균등 몫 · 몬테카를로 반복 |
| 취향 | `picks_prior_weight` · `scales_prior_weight` | 12 · 6 | 사전 취향을 조리 몇 건과 같은 무게로 볼지 |
| 취향 | `persona_half_life_days` | 90 | 이벤트 무게가 절반이 되는 일수. 0 이하면 감쇠 끔 |
| 취향 | `season_cycle_strength` · `weekly_cycle_strength` · `daily_cycle_strength` | 0.5 · 0 · 0 | 연 · 주 · 일 주기 세기 |
| 취향 | `persona_max_events` · `persona_max_event_age_days` | 2000 · 730 | 저장 상한 |

### 7.2 피처 가중치 (`enums.DEFAULT_WEIGHTS`, 파트 A 소유)

`f_coverage` 0.24 · `f_taste` 0.16 · `f_expiring` 0.15 · `f_ing_pref` 0.11 · `f_cooccur` 0.10 · `f_popularity` 0.10 · `f_missing` 0.05 · `f_cuisine` 0.04 · `f_time_fit` 0.03 · `f_season` 0.02, 나머지 7종 0. 합 1.00. 실제 사용자 데이터에서 쌍대비교(Bradley-Terry) 학습으로 다시 정하고, 정한 값은 `scoring_config` 에 `config_hash` 로 등록합니다.

### 7.3 목표 지표

착수본의 목표치를 그대로 두되 실측은 8절에 적습니다. 목표치는 검증 전 값입니다(예시값, 실제 데이터로 대체 필요).

| 지표 | 최저 기준 | 목표 | 지금 잴 수 있나 |
|---|---|---|---|
| 서빙 지연시간 p95 | < 300ms | < 58ms | Mock 후보 500건에서 엔진 17.6ms. DB 왕복은 미포함 |
| NDCG@10 | >= 0.85 | 0.926 | 라벨 600쌍이 없어 미측정 |
| Recall@20 | >= 0.60 | >= 0.60 | 미측정 |
| 카탈로그 커버리지 | >= 15% | >= 15% | Mock 120건 기준만 |
| 목록 다양성 ILD | >= 0.95 | >= 0.95 | Mock 0.903 (재료 자카드 기준) |

---

## 8. 검증 명령과 현재 결과

판정은 화면의 마지막 줄이 아니라 **종료 코드**로 합니다. 검사 스크립트는 중간에서 죽어도 그때까지의 통과 줄을 남기므로, 마지막 줄이 통과 표시인 것과 전부 통과한 것은 다릅니다.

```bash
uv run ruff check . && uv run ruff format --check . && uv run python -m mypy src && uv run pytest tests/unit
```

2026-09-12 결과(커밋 `b7b25b4` 트리):

```text
ruff check                → 0   All checks passed!
ruff format --check       → 0   142 files already formatted
mypy src                  → 0   Success: no issues found in 62 source files
pytest tests/unit         → 0   276 passed, coverage 90.23% (기준 80%, 측정 2,098문)
python seeds/validate.py  → 0   시드 정합 통과 (경고 13건)
python -m tests.unit.recommend.test_contract → 0   계약 98건 전부 통과 (설정값 20종을 환경변수로 채운 환경)
python scripts/eval_recommend_mock.py        → 0   가상 사용자 12명 종단
```

가상 사용자 12명 종단 실행에서 확인한 것입니다.

| 확인한 것 | 결과 |
|---|---|
| 알레르기 재료가 든 레시피 | 0건 (해당 사용자 4명 전원) |
| 조리 시간 상한을 넘긴 레시피 | 0건 (해당 사용자 9명 전원) |
| 취향 출처 | 고른 음식 10명 · 척도 1명 · 없음 1명 |
| 새로운 시도 칸 | 20개 목록은 4칸, 10개 목록은 2칸, 50개는 10칸, 취향 없는 사용자는 8칸 — 전원 설계값 |
| 노출 확률이 (0, 1] · 이유 문구가 비지 않음 | 전건 |
| 매운 레시피를 최근 16번 만든 뒤 | 취향의 매움 0.22 → 0.57, 상태 behavior(행동 무게 15.07), 상위 5개 매움 평균 0.40 → 0.53 |
| 같은 16번을 1년 전으로 두면 | 매움 0.26, 상태 blended(행동 무게 0.91) — 잊는 것이 동작 |
| 계절 세기별 반년 전 이벤트 무게 | 세기 0: 0.246 · 0.5: 0.123 · 1.0: 0.000 (같은 계절 1년 전은 0.060 으로 세기와 무관) |
| 후보 500건 기준 지연 | p50 15.4ms · p95 17.6ms · 최대 19.5ms |

한계도 둘 있습니다. 가상 레시피 120건의 맛 값은 시험용 분포라 고른 음식(실제 시드)으로 만든 취향이 가상 평균 아래에 놓여 맛 정합 효과가 실제보다 작게 나옵니다. 그리고 `needed` 를 36건으로 올린 뒤 가상 카탈로그에서는 인기순 폴백이 12명 중 7명입니다. 둘 다 실데이터에서 다시 잽니다.

---

## 9. 아직 정하지 않은 것

엔진 동작에 닿는 것만 적습니다. 결정자와 진행 상태는 회의 안건 문서가 관리합니다.

| 무엇 | 지금 | 정해야 할 것 |
|---|---|---|
| 3축 척도의 범위 | 계약은 0~4, 회의 기록은 1~5. 엔진은 0~1 로 옮긴 값만 받고 변환은 한 곳(`service.SCALE_MAX`) | 어느 쪽이 맞는지. 어긋나면 척도 사용자의 취향이 한 단계씩 밀리는데 에러는 나지 않습니다 |
| "월별 주기" 의 뜻 | 연 주기의 위상(달이 다르면 위상이 다름)으로 구현. 월초·월말 같은 월내 주기는 없음 | 해석이 회의 뜻과 맞는지. 틀렸으면 손잡이 하나를 더합니다 |
| 이벤트 발생 시각 | `EventIn` 에 시각이 없어 서버 수신 시각을 씀. 오프라인 동기화 배치는 전부 같은 시각 | 선택 항목으로 발생 시각을 계약에 더할지. 주·일 주기를 켤 조건입니다 |
| 군집 없을 때의 균등 폴백 | 파트 A 의 `mixed_exploration` 이 계약의 폴백을 구현하지 않아 파트 B 가 밖에서 우회 | 파트 A 함수 안으로 옮길지 |
| 손잡이의 정본 | `Settings`(환경변수)와 `RankingPolicy` 에 같은 값이 두 곳. 엔진은 `RankingPolicy` 만 읽음 | 어느 쪽을 정본으로 할지 |
| `/health` 의 `redis` | 클라이언트가 없는데 기본값 참 | 필드를 뺄지 붙일지 |
| 모든 예시값 | 이 문서의 `(예시값, 실제 데이터로 대체 필요)` 전부 | 실제 사용자 데이터에서 학습·재측정 |

진행 상태·결정 이력·검증 수치의 원본은 `docs/recommend/` 의 작업 기록·검증 기록·회의 안건·DB 전환 점검표에 있고, 개발자가 아닌 독자를 위한 설명은 `recommend_engine_how_it_works.md` 에 있습니다. 이 문서는 그 넷을 읽지 않아도 엔진을 이해할 수 있게 쓴 것이며, 코드와 이 문서가 어긋나면 코드가 맞고 이 문서를 고칩니다.
