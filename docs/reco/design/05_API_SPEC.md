# API 명세

> 작성자: 박재우 · 작성일: 2026-09-03

| 항목 | 내용 |
|---|---|
| 생성 | 2026-09-07 17:48 — `make api-docs` 캡처 시각 |
| 계약 버전 | `v1` |
| **SoT** | **[`src/features/recommend/schema.py` · `stage.py`](../../../src/features/recommend/)** — 이 문서는 거기서 파생된다 |
| 검증 | `make contract` — **출력의 통과 건수가 SoT** · Mock 실호출 캡처 |
| 기계 판독용 | [`api/openapi.json`](api/openapi.json) · [`api/examples.json`](api/examples.json) |

> **아래 예시는 손으로 쓴 것이 아니라 Mock 서버를 실제로 호출해 캡처한 것이다.**
> 코드와 어긋날 수 없다. 재생성 방법은 문서 맨 아래에 있다.

```bash
make mock        # 서버 기동 → http://localhost:8000/docs
make contract    # 계약 검증
make api-docs    # 이 문서 재생성
```

> 🔴 **인증이 아직 없다.** 라우트 어디에도 인증 의존성이 없어 **누구나 임의 `user_id` 로
> 호출할 수 있다** — `GET /v1/users/99999/pantry` 가 200 을 준다.
> `PUT /v1/users/{id}/pantry` 는 **전체 교체**라 남의 냉장고를 통째로 치환할 수 있다.
> **브라우저에서 직접 부르지 말고 백엔드를 경유한다는 전제다.** 내부 API 키 인증은
> 채택됐지만 미구현이다 (COMMON-005 · `docs/reco/design/03_OPEN_ISSUES.md` I-17①).
> 붙는 시점에 요청 헤더가 추가되고 그 전 호출은 401 이 된다.

---

## 엔드포인트 목록

| 메서드 | 경로 | 용도 |
|---|---|---|
| `POST` | `/v1/recommend` | 추천 실행 |
| `POST` | `/v1/events` | 행동 로그 기록 |
| `POST` | `/v1/onboarding/{user_id}` | 🔑 온보딩 5문항 저장 |
| `GET` | `/v1/recipes/search` | **자연어 레시피 검색** |
| `GET` | `/v1/ingredients/search` | 재료 자동완성 |
| `GET` · `PUT` | `/v1/users/{user_id}/pantry` | 냉장고 조회 · 갱신 |
| `GET` | `/v1/recommendations/{request_id}` | trace 재조회 |
| `GET` | `/health` | 상태 |
| `GET` | `/health/live` | ⚠️ **용도 미기재** — scripts/reco/api/render.py 의 `_PURPOSE` 에 추가하세요 |
| `GET` | `/health/ready` | ⚠️ **용도 미기재** — scripts/reco/api/render.py 의 `_PURPOSE` 에 추가하세요 |
| `POST` | `/v1/ocr/receipt` | ⚠️ **용도 미기재** — scripts/reco/api/render.py 의 `_PURPOSE` 에 추가하세요 |

**경로 11개 · 오퍼레이션 12개다** (pantry 가 GET·PUT 두 개).
소수 인원이 유지할 수 있는 최소 표면으로 잘랐다. AI 파트가 3명으로 늘었지만 표면은
그대로 둔다 — 남은 기간이 3주다.

## 이 API 에 없는 것

화면을 그리기 전에 읽어야 한다. **여기 있는 것들은 이 표면으로 만들 수 없다.**

| 무엇 | 지금 상태 |
|---|---|
| **오늘의 추천 (MAIN-000)** | 🔴 **조회 API 도 배치도 없다.** 사전 계산 결과를 담을 `daily_recommendation` 테이블만 준비돼 있다. 배치 구현은 3주 계획 밖이다 (결정 D-19). **메인화면을 이 기능 전제로 그리지 말 것** — 지금 붙일 수 있는 것은 `POST /v1/recommend` 뿐이고 그것은 실시간이며 `reason_source` 구분도 stale 판정도 없다 |
| **레시피 상세 (RECIPE-021)** | 🔴 `items[]` 는 `recipe_id` 만 준다. 제목·이미지·조리시간·난이도·인분·원본 링크가 응답에 **없다.** `GET /v1/recipes/{id}` 신설이냐 백엔드가 `recipe` 테이블을 직접 읽느냐가 **미정** |
| **온보딩 제시 20종 조회** | 제시 목록을 내려주는 라우트가 없다. `seeds/onboarding_recipes.yaml` 의 `presented` 순서가 곧 `picks` 의 인덱스다 |
| **재료 ID → 이름 역조회** | `missing_ids` 는 정수 배열인데 이름으로 바꿀 경로가 없다. `/v1/ingredients/search` 는 이름 질의만 받는다 |
| **인증 · 권한** | 위 경고 참조 |
| **페이지네이션** | 목록 API 가 `offset`·`cursor`·`total` 을 주지 않는다. `limit` 로 자를 뿐이다 |

---

# POST /v1/recommend

추천을 실행하고 **파이프라인 trace 를 함께 반환**한다.

## 요청

```json
{
  "user_id": 7,
  "session_id": "c-7-a1b2c3d4e5f6",
  "top_k": 8,
  "max_missing": 2
}
```

| 필드 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `user_id` | int | — | 필수 |
| `session_id` | str? | null | 🔴 **소급 불가.** `^[cgd]-` · 형식 `c-{user_id}-{uuid4hex12}` · 30분 무활동 시 갱신. **반드시 보낸다** — impression 은 이 요청에서 서버가 자동 기록하므로, 안 보내면 서버가 `g-…` 로 채워 **실사용자 트래픽이 게스트로 기록된다** |
| `top_k` | int | 20 | 1~100 |
| `max_missing` | int | 2 | 부족 허용 재료 수 (0~10) |
| `max_minutes` | int? | null | 조리시간 상한 *(⚠️ Mock 미구현 — 보내도 무시된다)* |
| `model_version` | str? | null | **디버거 전용** — 모델을 골라 비교 |
| `weight_override` | dict? | null | **디버거 전용** — ablation(R9) 실행 |
| `interleave_with` | str? | null | **Team-Draft Interleaving** — 비교 모델 지정 시 `items[].team` 이 채워진다 (설계 5-7-2) |
| `include_trace` | bool | true | false 여도 DB 에는 그대로 남는다 |
| `context` | dict | `{}` | `hour` `weekday` `device` `source_screen`. **값은 문자열·정수·null 만** — 실수·배열·중첩 객체는 400 |

### `session_id` 접두어

| 접두어 | 뜻 |
|---|---|
| `c-` | 실사용자 |
| `g-` | 게스트 |
| `d-` | 개발·디버거·시딩 |

접두어는 DB CHECK 로도 강제된다. 위반하면 **400** 이다.
`d-` 는 지표 뷰에서 통째로 제외되므로, 디버거·시딩 트래픽은 반드시 `d-` 를 쓴다.

## 응답 200 — 봉투

```json
{
  "contract_version": "v1",
  "request_id": "382f0284-4e6d-4d96-8656-19bf6df6a44d",
  "user_id": 7,
  "model_version": "mock-linear-v0",
  "weights": {
    "f_coverage": 0.24,
    "f_taste": 0.16,
    "f_expiring": 0.15,
    "f_ing_pref": 0.11,
    "f_cooccur": 0.1,
    "f_popularity": 0.1,
    "f_missing": 0.05,
    "f_cuisine": 0.04,
    "f_time_fit": 0.03,
    "f_season": 0.02,
    "f_quality": 0.0,
    "f_pantry_use": 0.0,
    "f_dish_type": 0.0,
    "f_skill_fit": 0.0,
    "f_content": 0.0,
    "f_ing_cf": 0.0,
    "f_group_pref": 0.0
  },
  "served_at": "2026-09-07T08:48:21.867841Z"
}
```

`request_id` 를 그대로 들고 있다가 `POST /v1/events` 의 각 이벤트에 싣는다 —
**이 연결이 없으면 학습 라벨을 이을 수 없다.**
`weights` 가 이 응답의 **실효 가중치**이며, 아래 `features` 의 기여도 되계산은 이 값으로 한다.

## 응답 200 — items[] (일반 슬롯)

```json
{
  "recipe_id": 10000,
  "missing_count": 0,
  "missing_ids": [],
  "coverage": 1.0,
  "cluster_id": 1,
  "features": {
    "f_coverage": 1.0,
    "f_missing": 1.0,
    "f_expiring": 0.1222,
    "f_pantry_use": 0.6245,
    "f_taste": 0.6394,
    "f_ing_pref": 0.4805,
    "f_cuisine": 0.302,
    "f_dish_type": 0.5917,
    "f_cooccur": 0.6278,
    "f_group_pref": null,
    "f_ing_cf": null,
    "f_content": null,
    "f_popularity": 0.6787,
    "f_quality": 0.4644,
    "f_time_fit": 0.1383,
    "f_season": 0.2541,
    "f_skill_fit": 0.3957
  },
  "score": 0.9444,
  "penalty": 1.0,
  "final_rank": 1,
  "reason": "가진 재료로 바로 만들 수 있고, 좋아하시는 달걀이 들어가요",
  "reason_features": [
    "f_coverage",
    "f_ing_pref"
  ],
  "mmr_penalty": 0.0,
  "is_exploration": false,
  "propensity": 1.0,
  "explore_source": null,
  "team": null
}
```

| 필드 | 의미 |
|---|---|
| `recipe_id` | 레시피 식별자. **이것 말고 제목·이미지는 응답에 없다** (위 “이 API 에 없는 것”) |
| `missing_count` · `missing_ids` | ① Retrieval 산출. `missing_ids` 는 **재료 ID 정수 배열**이다 — 이름이 아니고, 이름으로 바꿀 경로가 이 API 에 없다 |
| `coverage` | `n_essential = 0` 이면 **1.0**, 아니면 `1 - missing_count / n_essential` |
| `score` | ② Ranking 최종 점수 |
| **`features`** | **피처 원값 17종 전부** *(v1.9 — `contrib` 저장 폐기)*. `null` 과 `0.0` 의 뜻이 다르다 — 아래 표 참조 |
| `penalty` | `p_recent × p_cooked × (1 - p_avoid)` |
| `reason` · `reason_features` | **z-salience 상위 2개**로 만든 문구와 그 근거 피처 (설계 5-5). `contrib` argmax 는 이유가 1종으로 붕괴해 폐기됐다 |
| `cluster_id` | 우연성·다양성 축 (설계 5-3-5). 배치 미실행이면 `null` |
| `final_rank` | 최종 순위. **1-base** — `/v1/events` 의 `position` 과 **같은 기준**이다 |
| `mmr_penalty` | 다양성(MMR) 감점. 기본 `0.0` |
| `is_exploration` · `explore_source` | 무작위 삽입 슬롯과 채운 경로 — `uniform`(support 보장) / `thompson`(우연성) |
| **`propensity`** | 🔴 이 아이템이 Top-K **어딘가에** 노출될 **주변확률**이다 — (아이템, 위치) 결합확률이 **아니다**(`propensity_semantics="item"` 로 동결). **off-policy 평가(IPS)의 분모** — 소급 불가. 위치 효과를 곱해 넣으면 `P(examine\|position) × P(relevant\|item)` 이 한 칸에 섞여 **다시 뺄 수 없다** |
| `team` | interleaving 시 이 자리를 가져간 랭커 (`A`/`B`) |

> 필수 재료가 전부 기본양념인 레시피(간장계란밥류)는 `essential_ids` 가 빈 배열이라
> 겹침 검사가 아니라 별도 경로로 후보에 들어오고, `coverage` 는 **항상 1.0** 이다.
> 냉장고에 아무것도 안 맞아도 상위에 올 수 있다.

## 응답 200 — items[] (탐색 슬롯)

```json
{
  "recipe_id": 10001,
  "missing_count": 0,
  "missing_ids": [],
  "coverage": 1.0,
  "cluster_id": 2,
  "features": {
    "f_coverage": 1.0,
    "f_missing": 1.0,
    "f_expiring": 0.4049,
    "f_pantry_use": 0.0623,
    "f_taste": 0.6863,
    "f_ing_pref": 0.3544,
    "f_cuisine": 0.5691,
    "f_dish_type": 0.5521,
    "f_cooccur": 0.3984,
    "f_group_pref": null,
    "f_ing_cf": null,
    "f_content": null,
    "f_popularity": 0.5733,
    "f_quality": 0.6464,
    "f_time_fit": 0.0225,
    "f_season": 0.0723,
    "f_skill_fit": 0.685
  },
  "score": 0.8895,
  "penalty": 1.0,
  "final_rank": 2,
  "reason": "새로운 시도는 어떠세요",
  "reason_features": [],
  "mmr_penalty": 0.0,
  "is_exploration": true,
  "propensity": 0.215,
  "explore_source": "uniform",
  "team": null
}
```

**탐색 슬롯은 점수로 뽑힌 것이 아니다.** 무작위로 꽂은 자리이고, 그래서
`propensity` 가 1.0 이 아니다 — 이 값이 off-policy 평가의 분모가 된다.
`explore_source` 가 `uniform` 이면 support 보장용, `thompson` 이면 우연성용이다.

### `features` — `null` 은 두 종류다

| 값 | 뜻 |
|---|---|
| `null` | 계산 결과가 아니라 **계산하지 못했다.** 이유가 둘이다 (아래 표) |
| `0.0` | 계산해서 나온 0 |

| 피처 | `w` | 오늘의 상태 | 위 예시에서 |
|---|---|---|---|
| `f_coverage` | 0.24 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_missing` | 0.05 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_expiring` | 0.15 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_pantry_use` | 0.0 | ⬜ **점수에 안 쓴다** — `w=0`. 계산해도 순위를 못 바꾼다 | 값 있음 |
| `f_taste` | 0.16 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_ing_pref` | 0.11 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_cuisine` | 0.04 | ⏳ **데이터 없음** — 수단은 있으나 원천이 비었다. `w` 는 유지 | 값 있음 |
| `f_dish_type` | 0.0 | ⏳ **데이터 없음** — 수단은 있으나 원천이 비었다. `w` 는 유지 | 값 있음 |
| `f_cooccur` | 0.1 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_group_pref` | 0.0 | 🔴 **수단 없음** — 계산할 코드가 없다 | `null` |
| `f_ing_cf` | 0.0 | 🔴 **수단 없음** — 계산할 코드가 없다 | `null` |
| `f_content` | 0.0 | 🔴 **수단 없음** — 계산할 코드가 없다 | `null` |
| `f_popularity` | 0.1 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_quality` | 0.0 | ⬜ **점수에 안 쓴다** — `w=0`. 계산해도 순위를 못 바꾼다 | 값 있음 |
| `f_time_fit` | 0.03 | ✅ 점수에 쓰인다 | 값 있음 |
| `f_season` | 0.02 | ⏳ **데이터 없음** — 수단은 있으나 원천이 비었다. `w` 는 유지 | 값 있음 |
| `f_skill_fit` | 0.0 | ⬜ **점수에 안 쓴다** — `w=0`. 계산해도 순위를 못 바꾼다 | 값 있음 |

> 🔴 **17종 중 점수에 실제로 기여하는 것은 8종이고,
> 그 가중치 합은 0.94 다** (설계 의도는 1.00).
> 나머지 9종 중 7종은 `w=0` 이라 계산돼도
> 순위를 못 바꾸고, 3종은 `w` 를 가진 채 값이 안 온다.
> 점수 재현은 `Σwᵢfᵢ / Σwᵢ` 이며 — **`fᵢ` 가 `null` 인 피처는 분자·분모에서 함께 빠진다.**
> 분모를 1.00 으로 잡으면 값이 틀린다. 재분배가 아니라 나눗셈이다.

> ⚠️ **위 예시가 `f_cuisine`·`f_season` 에 값을 보이는 것은 Mock 한정이다.**
> 실서빙에서는 원천 데이터가 없어 둘 다 `null` 이다. 반대로 `f_content`·`f_ing_cf`·
> `f_group_pref` 는 Mock·실서빙 **양쪽 모두** `null` 이다 — 계산할 코드가 없다.

> `features` 는 **17개 키 전부**가 있어야 한다. 빠뜨리거나 모르는 키가
> 있으면 **`ScoredCandidate` 생성 자체가 거부된다** — 이것은 요청으로 보내는 필드가 아니라
> 서버 내부 계약이라, 어기면 400 이 아니라 **응답 조립 중 500** 이다.
> `w=0` 피처가 로그에서 소실되어 소급 학습이 불가능해지는 것을 계약이 막는 장치다.

## 응답 200 — trace

```json
{
  "trace_version": "v1",
  "stages": [
    {
      "name": "retrieval",
      "in_count": 231042,
      "out_count": 487,
      "latency_ms": 15,
      "strategy": "pantry_coverage",
      "model": null,
      "fallback": null,
      "filters": {
        "no_overlap": 227999,
        "allergy_cut": 12,
        "missing_gt_k": 1502,
        "cooktime_cut": 1042
      },
      "dropped": {},
      "params": {
        "max_missing": 2,
        "staple_added": 28
      },
      "score_stats": {},
      "exploration_items": []
    },
    {
      "name": "ranking",
      "in_count": 487,
      "out_count": 487,
      "latency_ms": 8,
      "strategy": null,
      "model": "mock-linear-v0",
      "fallback": null,
      "filters": {},
      "dropped": {},
      "params": {},
      "score_stats": {
        "min": 0.02,
        "p25": 0.19,
        "p50": 0.41,
        "p75": 0.63,
        "max": 0.93
      },
      "exploration_items": []
    },
    {
      "name": "rerank",
      "in_count": 487,
      "out_count": 8,
      "latency_ms": 3,
      "strategy": null,
      "model": null,
      "fallback": null,
      "filters": {},
      "dropped": {
        "diversity": 31,
        "recent_seen": 8,
        "category_cap": 5
      },
      "params": {
        "mmr_lambda": 0.7,
        "explore_slots_random": 1,
        "explore_pool_size": 200,
        "uniform_share": 0.5,
        "propensity_semantics": "item",
        "top_k": 8,
        "n_explore": 2,
        "serving_mode": "sim",
        "propensity_mc": 200,
        "policy_id": "mock-linear-v0+explore2-randpos",
        "rng_seed": 20260834,
        "max_missing_final": 2
      },
      "score_stats": {},
      "exploration_items": [
  …
```

**`filters` 블록이 디버깅에서 가장 유용하다.** “결과가 3개밖에 안 나왔다”는 신고에
`missing_gt_k` 를 보면 즉시 “k 를 2에서 3으로 올려야 한다”는 결론이 나온다.
탈락 사유를 집계하지 않으면 이 진단에 몇 시간이 걸린다.

### `rerank.params` 동결 키 10종

**값이 아니라 정의가 소급 불가다.** 없으면 로그가 있어도 propensity 를 재구성할 수 없다.

| 키 | 의미 |
|---|---|
| `policy_id` | 어느 정책이었나 |
| `propensity_semantics` | 무엇의 확률인가. **`item` 으로 동결** — Top-K 어딘가에 노출될 **주변확률**이고 (아이템,위치) 결합확률이 아니다 |
| `explore_pool_size` | 탐색 풀 크기 |
| `uniform_share` | 혼합 정책의 균등 비율 |
| `propensity_mc` | MC 반복 수 |
| `rng_seed` | 재현용 난수 시드 |
| `max_missing_final` | 폴백 완화 후 **실제로 적용된** 값 (요청값과 다를 수 있다) |
| `top_k` | 몇 개를 노출했나. propensity 재계산의 분모 |
| `n_explore` | 탐색 슬롯이 몇 칸이었나 |
| `serving_mode` | `real`·`sim`·`load_test` — 없으면 candidates 가 “잘려서 없는 것”인지 “원래 없던 것”인지 구분되지 않는다 |

라이터가 누락을 검사해 행에 `missing_trace_params` 플래그를 남긴다 (쓰기는 막지 않는다).

### 결과가 비는 다른 이유

① `recipe_feature.feature_version` 이 `test-` 로 시작하면 **조회에서 자동 제외**된다.
스위치는 SQL 함수 인자일 뿐 **API 로 노출하지 않는다** — 기본값이 꺼짐이라 실서빙에서 켤 수 없다.
② `feature_version` 은 `^(v[0-9]|test-)` 를 만족해야 **저장된다.** `exp1` 같은 값은 INSERT 자체가 실패한다.
③ `n_total = 0` 인 레시피(정규화 실패)는 후보에서 빠진다.
④ Retrieval 은 `missing_count` 오름 → 인기도 내림 순 **상위 500건**까지만 본다.

## 예시 — ablation (R9 검증)

```json
{
  "user_id": 7,
  "session_id": "d-7-debug00000001",
  "top_k": 2,
  "weight_override": {
    "f_expiring": 0.0
  }
}
```

특정 피처의 가중치를 0으로 두고 순위 변화를 본다. **순위가 안 바뀌면 그 피처는 무의미하다.**
설계 5-2-2 의 가중치표가 검증되지 않은 추정치이므로, 이 필드가 UI 에서 바로
ablation 을 돌릴 수 있게 한다. 디버거 경로이므로 `d-` 세션을 쓴다.

## 예시 — Interleaving (v0 vs v1 비교)

```json
{
  "user_id": 7,
  "top_k": 6,
  "interleave_with": "ranker-lgbm-v1"
}
```

유저 100명에서 A/B 는 검정력이 없다(설계 5-7-2). 두 랭커의 결과를 한 목록에 섞고
클릭이 어느 `team` 것인지로 승패를 센다.

## 예시 — 후보 부족 (degraded)

`max_missing=0` 으로 좁히면 후보가 줄어든다. **에러가 아니라 200 + 플래그다.**

```json
{
  "latency_ms": 28,
  "cache_hit": false,
  "degraded": true,
  "user_mode": "onboarding"
}
```

**`degraded` 비율이 조용히 올라가는 것이 가장 위험한 실패 양상이다.** Grafana 로 추적한다.

---

# POST /v1/events

행동 로그를 기록한다. 한 번에 **1~200건**. 0건이나 201건은 배치 전체가 **400** 이다.

> 🔴 **`impression` 은 클라이언트가 보내지 않는다.** `/v1/recommend` 가 응답을 반환하는
> 순간 **서버측에서 자동 기록**한다. 클라이언트에 맡기면 새로고침·세션 만료로 누락되고,
> 그러면 랭킹 학습의 negative 샘플이 사라져 **모델 학습 자체가 불가능해진다.**

## 요청

```json
{
  "events": [
    {
      "user_id": 7,
      "event_type": "click",
      "recipe_id": 10001,
      "request_id": "382f0284-4e6d-4d96-8656-19bf6df6a44d",
      "position": 1,
      "session_id": "c-7-a1b2c3d4e5f6",
      "context": {
        "hour": 19
      }
    }
  ]
}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `user_id` | int | 필수 |
| `event_type` | EventType | 아래 8종 |
| `recipe_id` | int? | `search` 등 아이템이 없는 이벤트는 생략 |
| `value` | float? | `rating` 은 **원점수 1~5**, dwell time 은 초(sec). 한 칸을 두 뜻으로 쓴다. 🔴 **서버가 범위를 검증하지 않는다** — 100 을 보내도 200 이고 라벨이 48.5 가 된다 |
| `request_id` | UUID? | 🔴 없으면 학습 라벨과 추천 로그를 이을 수 없다 |
| `position` | int (1~100)? | 🔴 **1-base** — `items[].final_rank` 와 같은 기준이다. 배열 인덱스(0-base)를 그대로 보내면 **400** |
| `session_id` | str? | 🔴 `^[cgd]-` — 아래 접두어 표 참조 |
| `context` | dict | 값은 **문자열·정수·null 만**. 실수(37.5)·배열·중첩 객체는 400 |

| `event_type` | 학습 라벨 | 비고 |
|---|---|---|
| `impression` | **0.0** | negative 후보. 서버가 자동 기록 |
| `click` | 0.3 | |
| `save` | 0.6 | |
| `unsave` | -0.3 | |
| **`cook`** | **1.0** | 최강 신호 |
| `rating` | `(value-3)/2` | 1~5 → -1.0~+1.0. **서버는 범위를 검증하지 않는다** |
| `dismiss` | -0.5 | |
| `search` | — | 분석 전용 |

**8종은 추가만 가능하고 변경·삭제는 불가능하다.** 이미 쌓인 로그를 소급 수정할 방법이 없다.

## `source` — 클라이언트는 보내지 않는다

`event_log.source` 는 **NOT NULL 이고 기본값이 없다.** 채우는 것은 서버다.
`EventIn` 에 이 필드가 없으므로 `{"source": "client"}` 를 보내면 **400** 이다.

| 값 | 뜻 | 누가 쓰나 |
|---|---|---|
| `served` | 서버가 응답에 담았다 — **본 것이 아니라 보낸 것** | `/v1/recommend` 자동 impression |
| `viewport` | 클라이언트가 실제 화면 노출을 관측 | 아직 미사용 |
| `client` | 사용자 행위 보고 (click·cook·save…) | `/v1/events` 수신분 |

> 🔴 **지금은 impression 을 `served` 로 쓴다** (S2 결정 — 프론트 합의가 필요해서).
> **스크롤해서 안 본 것도 포함된다.** CTR 분모를 읽을 때 이 뜻을 전제해야 한다.
> 나중에 `viewport` 로 바꾸면 **두 시대의 impression 은 뜻이 달라지고 사후 백필은 불가능하다.**

## 응답 200

```json
{
  "accepted": 1,
  "rejected": 0,
  "errors": []
}
```

## 예시 — 별점 (`value`)

```json
{
  "events": [
    {
      "user_id": 7,
      "event_type": "rating",
      "recipe_id": 10001,
      "value": 5,
      "request_id": "382f0284-4e6d-4d96-8656-19bf6df6a44d",
      "position": 2,
      "session_id": "c-7-a1b2c3d4e5f6"
    }
  ]
}
```

## 400 — 세션 접두어를 안 지키면

가장 흔히 맞는 400 이다. 접두어 3종 이외는 **입력에서** 거부된다.

```json
{
  "events": [
    {
      "user_id": 7,
      "event_type": "click",
      "recipe_id": 10001,
      "request_id": "382f0284-4e6d-4d96-8656-19bf6df6a44d",
      "position": 1,
      "session_id": "s-7-a1b2"
    }
  ]
}
```
```json
null
```

## 🔴 소급 불가 필드

`request_id` 없이 보내면 — **400 이 아니라 200 + `rejected` 집계다.**
상태코드만 보고 성공 처리하면 안 된다.

```json
{
  "events": [
    {
      "user_id": 7,
      "event_type": "cook",
      "recipe_id": 10001
    }
  ]
}
```
```json
{
  "accepted": 0,
  "rejected": 1,
  "errors": [
    "cook: request_id 없음 — 학습 라벨과 이을 수 없다"
  ]
}
```

| 필드 | 없으면 |
|---|---|
| `request_id` | 학습 라벨과 추천 로그를 이을 수 없다 |
| `position` | position bias 보정(IPW)이 **영구 불가** |
| `session_id` | 시퀀스 모델(SASRec·BERT4Rec) 학습 단위가 영구 소실 · 개발 트래픽(`d-`)을 실유저 지표에서 못 걷어낸다 |

**조용히 받으면 안 된다.** 라벨을 못 잇는 데이터가 쌓이는 것을 몇 주 뒤에 발견하게 된다.

## 재전송 · 재시도

| 대상 | 중복 처리 |
|---|---|
| `impression` + `request_id` 있음 | DB 가 `(request_id, recipe_id, source)` 로 **흡수한다** |
| `impression` + `request_id` 없음 | 부분 인덱스 조건 밖이라 **멱등이 아니다** |
| 그 외 7종 (`click`·`save`·`cook`·`rating`·`dismiss`…) | **중복 방지 장치가 없다.** 재시도는 클라이언트 책임 — 중복 적재는 에러 없이 학습 라벨만 부풀린다 |

`accepted` 는 **계약 검사를 통과해 받아들인 개수**다 — 보낸 건수에서 `rejected` 를 뺀 값이고,
**저장에 성공한 개수가 아니다.** DB 가 흡수한 중복도 여기 반영되지 않으므로 실제 적재 건수와 다르다.

---

# GET /v1/recipes/search

**자연어 레시피 검색.** 임베딩이 아니면 불가능한 유일한 기능이다 (설계 6-4-3).

```
GET /v1/recipes/search?q=김치&limit=5&user_id=7
```

> ⏳ **남은 3주 동안 이 경로에는 임베딩이 없다.** A-12 가 임베딩 없는 TF-IDF→SVD 판으로
> 확정돼 `content_emb` 를 만드는 코드가 저장소에 없고 `sentence-transformers` 도 깔지 않는다.
> 아래 예시가 그럴듯한 점수를 내는 것은 **Mock 이 제목 부분일치로 흉내내기 때문이다.**
> 실서빙에서는 이 3주 동안 `degraded=true` 가 상시 경로다.

| 파라미터 | |
|---|---|
| `q` | str · **1~100자** · 필수 |
| `limit` | int · 기본 20 · **1~100** |
| `user_id` | int? — 주면 `missing_count`·`missing_names` 가 채워진다 |
| `max_missing` | int? — 주면 **만들 수 있는 것만** 남긴다 |

> 🔴 **Mock 은 이 상한을 걸지 않는다** — 라우트가 계약 모델(`RecipeSearchIn`)을 쓰지 않고
> 평문 쿼리 인자를 받기 때문이다. `limit=500` 이 Mock 에서는 200 이지만 SoT 는 400 이다.
> **이 엔드포인트에 한해 예시와 `openapi.json` 을 믿으면 안 된다.**

```json
{
  "query": "김치",
  "hits": [
    {
      "recipe_id": 10000,
      "title": "김치찌개",
      "score": 0.896,
      "cuisine": "korean",
      "cook_minutes": 10,
      "missing_count": 0,
      "missing_names": []
    }
  ],
  "model_version": "ko-sbert-mock",
  "latency_ms": 8,
  "degraded": false
}
```

| 필드 | 의미 |
|---|---|
| `score` | 임베딩 구축 후에는 쿼리와의 **코사인 유사도**. 그전(=남은 3주)에는 제목 일치 점수다 |
| `missing_count` · `missing_names` | `user_id` 를 준 경우에만 채워진다. **여기는 이름이 온다** (`/v1/recommend` 의 `missing_ids` 와 다르다) |
| `degraded` | 계약상 뜻은 “임베딩 인덱스 미구축 시 제목 검색으로 폴백했음”. ⚠️ **Mock 은 `hits` 가 0건일 때 켠다** — 실제 구현의 정의와 다르다. 이 값에 “검색 품질 저하” 배너를 걸면 Mock 에서는 **빈 결과일 때만** 뜬다 |

`hits` 는 `score` 내림차순이다.

## 결과가 없을 때

```json
{
  "query": "ratatouille",
  "hits": [],
  "model_version": "ko-sbert-mock",
  "latency_ms": 8,
  "degraded": true
}
```

---

# GET /v1/ingredients/search

```
GET /v1/ingredients/search?q=대파&limit=5
```

```json
{
  "query": "대파",
  "hits": [
    {
      "ingredient_id": 1042,
      "name": "대파",
      "score": 1.0,
      "method": "exact",
      "category": "농산물.채소.경채류"
    }
  ],
  "not_found_message": null
}
```

| `method` | 의미 |
|---|---|
| `exact` | 정규 재료명 완전일치 |
| `alias` | 표기 변형 사전 일치 |
| `jamo_trgm` | **자모 분해 후 trgm 유사도** (설계 4-4-1) |

`hits` 의 **정렬은 보장되지 않는다** — exact 일치가 위로 온다는 보장이 없다.
페이지네이션도 없다.

> **온라인 경로에서 임베딩 추론은 하지 않는다** (설계 1-4). L4 는 배치 전용이다.

## 못 찾았을 때 — 조용히 무시하지 않는다

```json
{
  "query": "zzzz",
  "hits": [],
  "not_found_message": "'zzzz'을(를) 찾지 못했어요. 직접 등록을 요청할까요?"
}
```

`not_found_message` 를 그대로 보여주고, `직접 등록 요청` 을 누르면 `normalization_queue` 에
`source='user'` 로 들어간다. **유저 입력이 사전을 키우는 피드백 루프**가 된다.

---

# GET /v1/users/{id}/pantry

```json
{
  "user_id": 7,
  "items": [
    {
      "ingredient_id": 1042,
      "quantity": 1.0,
      "unit": "개",
      "purchased_at": null,
      "expires_at": "2026-09-01",
      "name": "대파",
      "days_left": 5,
      "is_staple": false
    },
    {
      "ingredient_id": 1050,
      "quantity": 1.0,
      "unit": "개",
      "purchased_at": null,
      "expires_at": "2026-09-02",
      "name": "양파",
      "days_left": 6,
      "is_staple": false
    },
    {
      "ingredient_id": 1101,
      "quantity": 1.0,
      "unit": "개",
      "purchased_at": null,
      "expires_at": "2026-09-03",
      "name": "애호박",
      "days_left": 7,
      "is_staple": false
    },
    {
      "ingredient_id": 1200,
      "quantity": 1.0,
      "unit": "개",
      "purchased_at": null,
      "expires_at": "2026-09-04",
      "name": "돼지고기",
      "days_left": 8,
      "is_staple": false
    }
  ],
  "staple_count": 28
}
```

| 필드 | 의미 |
|---|---|
| `name` | 정규 재료명 |
| `expires_at` | **소비기한**(use-by) |
| `days_left` | `expires_at - 오늘(KST)`. **이미 지난 항목은 음수다** |
| `is_staple` | 기본양념 여부 — 유저가 등록하지 않아도 보유로 친다 |
| `staple_count` | 자동 가산된 기본양념 수 |

> **`staple_count` 는 유저가 등록한 것이 아니다.** 소금·간장 등은 “모든 유저가 항상
> 보유”로 자동 가산된다 (설계 결정 2). 이것이 없으면 간장을 등록하지 않은 유저에게
> **한식 레시피의 95%가 재료 부족으로 걸러진다.**

> ⚠️ 위 예시의 `staple_count`·`days_left` 는 **Mock 의 고정 스텁**이라 실제 값도
> 아니고 `expires_at` 과 산술이 맞지도 않는다. 실제 기본양념 수는 `ingredient.is_staple`
> 행 수이며 현재 시드 기준 **39종**이다.
> `PUT` 응답의 `days_left` 가 항상 `null` 인 것도 Mock 의 한계지 버그가 아니다.

`expires_at` · `days_left` 가 `f_expiring` 피처의 원천이며 이 서비스의 **최대 차별화 포인트**다.

## 시간대

DB 시간대는 **Asia/Seoul** 이다. 날짜와 시각의 기준이 다르므로 섞지 않는다.

| 필드 | 기준 |
|---|---|
| `expires_at` · `purchased_at` | **KST 날짜**로 보내고 읽는다 |
| `days_left` | 서버가 `expires_at - current_date(KST)` 로 계산해 내려준다 |
| `served_at` · `created_at` | **UTC** (`…Z`). 여기서 “오늘” 을 유도하지 말 것 |

> 🔴 `served_at` 에서 오늘을 유도하면 한국시간 00:00~09:00 에 하루가 어긋난다.
> DB 시간대를 UTC 로 두었을 때 실제로 겪은 오차다.

# PUT /v1/users/{id}/pantry

```json
{
  "items": [
    {
      "ingredient_id": 1042,
      "quantity": 1,
      "unit": "대",
      "purchased_at": "2026-09-01"
    },
    {
      "ingredient_id": 1300,
      "quantity": 1,
      "unit": "모",
      "expires_at": "2026-09-06"
    }
  ],
  "removed": [
    {
      "ingredient_id": 1101,
      "reason": "consumed"
    }
  ]
}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `items[].ingredient_id` | int | **필수** |
| `items[].quantity` | float? | |
| `items[].unit` | str? | |
| `items[].purchased_at` | date? | 소비기한 추정의 **기준일** |
| `items[].expires_at` | date? | 주면 추정을 이긴다 |
| `removed[].ingredient_id` | int | **필수** |
| `removed[].reason` | enum | 아래 3종 |

```json
{
  "user_id": 7,
  "items": [
    {
      "ingredient_id": 1042,
      "quantity": 1.0,
      "unit": "대",
      "purchased_at": "2026-09-01",
      "expires_at": null,
      "name": "대파",
      "days_left": null,
      "is_staple": false
    },
    {
      "ingredient_id": 1300,
      "quantity": 1.0,
      "unit": "모",
      "purchased_at": null,
      "expires_at": "2026-09-06",
      "name": "두부",
      "days_left": null,
      "is_staple": false
    }
  ],
  "staple_count": 28
}
```

**전체 교체(replace) 방식**이다. 부분 갱신은 제공하지 않는다 — 소수 인원이 유지할
API 표면을 좁게 두기 위한 선택이다.

🔴 **한 유저·한 재료의 활성 행은 1개다.** `items` 안에 같은 `ingredient_id` 를 두 번 넣으면
저장이 실패한다 — 같은 재료를 소비기한별로 나눠 담을 수 없다. 수량을 합치거나
**소비기한이 이른 쪽**을 쓴다. 계약은 이것을 검사하지 않아 Mock 은 200 이고,
실 DB 에서야 터진다.

🔴 **`purchased_at` 이 소비기한 추정의 기준일이다.** 앱 등록 시각이 아니다 —
마트에서 사고 사흘 뒤에 넣으면 **사흘을 공짜로 벌어준다.** 유저가 `expires_at` 을
직접 주면 그것이 추정을 이긴다.

```
expires_at = COALESCE(purchased_at, 등록일) + 재료별 소비기한 일수
```

> ⚠️ **Mock 은 이 추정을 하지 않는다.** 위 응답에서 `purchased_at` 을 준 항목의
> `expires_at` 이 `null` 로 남는 것은 그 때문이다 — 계약이 틀린 게 아니라 Mock 이
> 아직 계산하지 않는다. `days_left` 가 항상 `null` 인 것도 같은 이유다.

미래 날짜는 받지 않는다 — `purchased_at <= 등록일 + 1일` 이 DB CHECK 로 강제된다.
**예약 구매 등록은 불가**이고, 계약은 이것을 검사하지 않아 Mock 은 통과시킨다.

**소비기한**(use-by)이지 유통기한(sell-by)이 아니다. 유통기한은 판매 가능 기한이라
지나도 먹을 수 있고, 우리가 알고 싶은 것은 “언제까지 먹을 수 있나” 다.

추정인지 유저 입력인지는 DB 의 `expires_at_source`(`user` · `estimated` · `unknown`)로
구분하고 로그 계약에도 있지만, **GET 응답에는 아직 이 필드가 없다.**
화면에 “추정치” 배지가 필요하면 계약에 필드 추가가 선행이다.

## `removed[].reason`

🔴 **`removed` 를 버리지 않는다.** 소진·폐기를 안 물어보면 나중에 물을 대상이 없다.
행을 지우지 않고 tombstone 으로 남긴다 — 지우면 “무엇을 얼마나 버렸나” 를 영원히 못 센다.

| 값 | 뜻 |
|---|---|
| `consumed` | 다 썼다 |
| `discarded` | 상해서 버렸다 |
| `unknown` | **물었는데 유저가 건너뛰었다** |

> 🔴 **아예 묻지 않았다면 그 항목을 `removed` 에 넣지 않는다.** DB 의 NULL(안 물어봄)과
> `unknown`(물었는데 스킵)은 다른 뜻이고, “건너뛰기” 를 “안 보냄” 으로 구현하면 둘이 섞인다.
> 스킵은 폐기 쪽에 몰리므로(버린 걸 밝히기 싫어서) — 섞이면 낭비율의 **상한·하한조차** 못 낸다.
> 소급 불가다.

---

# POST /v1/onboarding/{user_id}

**가입 직후 1회.** 5문항 응답을 받는다. 이 계약이 없어서 **가중치 0.27
(`f_taste` 0.16 + `f_ing_pref` 0.11)을 저장할 곳이 없었다** *(09-03 신설)*.

```json
{
  "picks": [
    3,
    7,
    12
  ],
  "scales": [
    2,
    3,
    1
  ],
  "allergy_groups": [
    "nut",
    "shellfish"
  ],
  "allergy_ingredient_ids": [
    170
  ],
  "avoid_ingredient_ids": [
    55
  ],
  "household_size": 2
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `picks` | int[] | 1~20개. 🔴 **`seeds/onboarding_recipes.yaml` 의 `presented` 배열 인덱스다 — `recipe_id` 가 아니다.** 서버가 값 범위를 검증하지 않아 `recipe_id` 를 넣어도 200 이 떨어지고 `taste_vec` 이 조용히 틀어진다 |
| `scales` | int[3] | **정확히 3칸**, 각 0~4. 🔴 **순서가 계약이다** — `[매움, 짠맛, 단맛]` |
| `allergy_groups` | str[] | 아래 10종 |
| `allergy_ingredient_ids` | int[] | 재료 ID 직접 지정 (그룹과 **둘 다** 받는다 — 안전 이중화) |
| `avoid_ingredient_ids` | int[] | 기피(알러지 아님). **최대 3개** |
| `household_size` | int? | 1~10. 선택 |

> 🔴 `scales` 순서를 바꿔 보내도 범위만 맞으면 **400 이 나지 않는다** — `taste_vec` 이
> 조용히 뒤집힌다. 원본이 그대로 저장되므로 재계산으로도 못 되돌린다.

```json
{
  "user_id": 7,
  "taste_vec": [
    0.5,
    0.75,
    0.25,
    0.5,
    0.5,
    0.5
  ],
  "n_blocked_ingredients": 25
}
```

| 필드 | 설명 |
|---|---|
| `taste_vec` | **정확히 6칸.** 축 순서 `[매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐]` |
| `n_blocked_ingredients` | 그룹 전개 후 차단될 재료 수 |

🔴 **원본을 그대로 받는다.** `taste_vec` 은 고른 레시피들의 평균이라
결과만 저장하면 **시드가 바뀔 때 다시 계산할 수 없다** — 실제로 09-02 에
맛 시드 2건을 고쳤다. `picks`·`scales` 를 `user_vector` 에 남긴다.

🔴 **알러지는 `severity='allergy'` 로 저장해야 한다.** DB 기본값 `'avoid'` 에
맡기면 **그룹 확산이 조용히 꺼진다** — 아몬드만 등록한 사람에게 호두·잣이
그대로 추천된다. 에러가 안 나서 더 위험하다.

## `allergy_groups` — 아래 10종만 받는다

| 코드 | |
|---|---|
| `nut` | 견과 |
| `sesame` | 참깨 |
| `soy` | 대두 |
| `gluten` | 밀 |
| `egg` | 달걀 |
| `dairy` | 유제품 |
| `fish` | 어류 |
| `shellfish` | 갑각·패류 |
| `peach` | 복숭아 |
| `buckwheat` | 메밀 |

> ⚠️ **서버는 이 값을 검증하지 않는다** — 계약에 enum 이 없어 목록 밖 값도 **200 이 떨어진다**
> (`["견과류"]` 를 보내도 그럴듯한 `n_blocked_ingredients` 가 돌아온다).
> 그러나 실 DB 는 CHECK 로 거부한다. **오타 하나가 알러지 차단을 통째로 끈다.**

## 400 — 범위를 벗어난 척도

```json
{
  "picks": [
    1
  ],
  "scales": [
    9,
    0,
    0
  ]
}
```
```json
null
```

---

# GET /v1/recommendations/{request_id}

**로그 탐색기용.** “3주 전 이 추천이 왜 나왔나”를 복원한다.

```json
{
  "request_id": "382f0284-4e6d-4d96-8656-19bf6df6a44d",
  "user_id": 7,
  "session_id": "c-7-a1b2c3d4e5f6",
  "model_version": "mock-linear-v0",
  "mlflow_run_id": null,
  "config_hash": "00bcdd199797bd654baae7d5307c6a2c",
  "warm_alpha": 0.0,
  "stats_version": null,
  "pantry_snapshot": [
    1042,
    1050,
    1101,
    1200,
    1300
  ],
  "pantry_detail": null,
  "allergy_snapshot": [],
  "policies": null,
  "served": [
    10000,
    10001,
    10006,
    10002,
    10003,
    10004,
    10005,
    10007
  …
```

| 필드 | 의미 |
|---|---|
| `config_hash` | 기준 가중치를 되찾는 열쇠. `scoring_config` 레지스트리에 행이 있어야 복원된다 (해시는 단방향) |
| `warm_alpha` | 웜 전환 계수 `α = min(1, n_events/n_warm)`. **0.0~1.0** |
| `stats_version` | 어느 코퍼스 평균 μ 로 `f_taste` 를 계산했나 |
| `session_id` | `^[cgd]-`. 요청에 안 실으면 서버가 **`g-`(게스트)로 채운다** — 로그인 유저는 반드시 `c-` 를 실어야 한다 |
| `pantry_snapshot` · `pantry_detail` | 요청 시점 냉장고 (ID 배열 · 상세) |
| `allergy_snapshot` · `served` | 요청 시점 알러지 · 실제 노출된 `recipe_id` 순서 |
| `policies` | interleaving 승패 귀속. 단일 정책이면 `null` |

🔴 **응답에 `weights` 가 없는 것은 누락이 아니다.** v2.9 에서 뺐고, 실효 가중치는
`config_hash`(기준 w) × `warm_alpha`(α) 와 `features` 의 `null` 패턴으로 **유도된다.**

## 응답 안의 JSONB 속 형식

`pantry_detail` 과 `policies` 는 `dict` 배열이라 스키마에 모양이 안 드러난다.
속 모양의 SoT 는 [`src/features/recommend/schema.py`](../../../src/features/recommend/schema.py) 다.

| 칸 | 속 모양 |
|---|---|
| `pantry_detail` | `[{ingredient_id, quantity?, unit?, expires_at?, expires_at_source}]` — `expires_at_source` 는 `user` · `estimated` · `unknown` |
| `policies` | `[{team, model_version, mlflow_run_id?, recipe_ids}]` — 🔴 **`recipe_ids` 가 없으면 interleaving 승패를 귀속할 수 없다** |

> 🔴 **`pantry_snapshot` 이 재현성의 핵심이다.** 냉장고는 계속 바뀌므로 요청 시점 상태를
> 남기지 않으면 나중에 어떤 조건이었는지 알 수 없다. `mlflow_run_id` 는 오프라인 실험과
> 온라인 결과를 잇는다 (설계 3-3). `created_at` 은 UTC 다.

## 404 — 없는 `request_id`

```json
{
  "detail": "request_id 를 찾을 수 없습니다"
}
```

---

# GET /health

```json
{
  "status": "ok",
  "contract_version": "v1",
  "model_version": "mock-linear-v0",
  "db": true,
  "redis": true
}
```

⚠️ `db`·`redis` 는 **하드코딩된 `true`** 다 — 계약의 기본값이 그대로 나가고 실제 연결
상태를 확인하는 코드가 없다. **상태 판정·재시도 로직에 쓰지 말 것.**

---

# 에러 규약

| 상황 | 코드 | 비고 |
|---|---|---|
| 계약 위반 (오타 · 범위 · 세션 접두어) | **400** | 🔴 **본문 없음** — 사유는 서버 로그에만 |
| 없는 `request_id` | 404 | |
| **후보 부족** | **200** | `trace.totals.degraded=true` |
| **모델 로드 실패** | **200** | `stage_trace.ranking.fallback` |
| **`user_preference` 없음** | **200** | 중립값(0.5) → 사실상 인기순 |
| **`request_id` 누락 이벤트** | **200** | `rejected` + `errors[]` — 상태코드만 보고 성공 처리하면 안 된다 |

## 추천 경로에서 5xx 가 나오면 안 된다

최악의 경우에도 **인기순 Top-N** 을 돌려주고 `degraded` 로 표시한다 (설계 5-6).
빈 목록은 유저에게 장애로 보이고 **디버깅 정보도 남지 않는다.**

## 400 예시 — 필드 오타

```json
{
  "user_id": 7,
  "topk": 20
}
```
```json
null
```

전 모델이 `extra="forbid"` 이므로 `topk` 같은 오타가 **조용히 무시되지 않고 즉시 터진다.**
3명이 각자 짜다 필드명을 다르게 쓰는 사고를 막는 장치다.

> 🔴 **본문이 비어 있다 (09-07 변경).** 영수증 파트가 앱 전체에
> `RequestValidationError → 400` 핸들러를 걸면서, 이전의 `422 {"detail": [...]}`
> 가 **빈 400** 으로 바뀌었다. 어느 필드가 왜 틀렸는지는 **서버 로그에만** 남는다.
>
> 대시보드는 사유를 화면에 띄울 수 없다. 필요해지면 그때 핸들러가 본문을 싣도록
> 영수증 파트와 협의한다 — 상태코드 통일을 먼저 맞춘 상태다.
>
> 404 는 여전히 `{"detail": "문자열"}` 이다. 계약의 `ErrorOut` 은
> **아직 어느 라우트에도 붙어 있지 않으니** 그 모델로 파싱하지 말 것.

---

# 버전 관리

```
CONTRACT_VERSION         = "v1"    # API 전체
StageTrace.trace_version = "v1"    # trace 구조
```

| 변경 | 버전 | 예 |
|---|---|---|
| 필드 **추가** (optional) | 유지 | 새 피처를 `FEATURE_KEYS`·`features` 에 추가 |
| 필드 **삭제 · 의미 변경** | 올림 | `EventType` 값 변경 |
| `stage_trace` 구조 변경 | `trace_version` 올림 | 단계 추가 |

---

# 이 문서를 손으로 고치지 않는다

```bash
make api-docs
#  = python scripts/reco/api/capture.py   Mock 서버 실호출 → examples.json · openapi.json
#  + python scripts/reco/api/render.py    → docs/reco/design/05_API_SPEC.md
```

계약이 바뀌면 **`src/features/recommend/schema.py` · `stage.py` 를 고치고 재생성**한다. 문서를 직접 수정하면
다음 재생성에서 사라진다.

`capture.py` 는 각 호출의 **기대 상태코드**를 명시하고 다르면 그 자리에서 멈춘다.
09-03 까지 `/v1/events` 의 “정상 예시” 가 실제로는 에러였고 이 문서가 그 에러 본문을
`## 응답` 으로 싣고 있었다 — 가드가 없어서 아무도 몰랐다.
