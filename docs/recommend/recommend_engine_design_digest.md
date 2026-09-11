# 추천 코어 엔진 구현 명세 요약 (에이전트용)

**정하는 것**: 파트 B 구현 명세 `recommend_engine_design.md`(2.0.0) 의 압축본. 구현·검증에 필요한 계약, 식, 상수, 배치, 상태만

**적용 대상**: 파트 B 를 이어서 구현하는 AI 코딩 에이전트. 원본과 어긋나면 **원본이 이기고**, 원본과 코드가 어긋나면 코드가 이깁니다. 원본이 바뀌면 이 파일을 같은 커밋에서 갱신합니다

**버전**: 3.0.0 · **최종 수정**: 2026-09-12 · **작성자**: 유재현

---

## 1. 흐름과 원칙

| 키 | 값 |
|---|---|
| 흐름 | ① Retrieval(A 의 DB 함수 `retrieve_for_user`, 최대 500) → ② Ranking(17 피처 가중합 × 감점) → ③ Re-ranking(MMR + 혼합 탐색) → 응답 + 로그. 취향 페르소나가 ② 의 사용자 6축을 공급 |
| 경계 | 계약·DDL·SQL·로그 적재·`rank`·`reason`·`explore`·`serendipity` 는 A. ②③·페르소나·저장소·완화 계획·조립·`policy` 는 B. C 는 로그를 읽음 |
| 원칙 | Zero-Drop(모름은 분자·분모 동시 제외, 0 과 다름) · 감점은 곱셈 · 알레르기는 ① SQL 하드컷, 제외는 ① 에서만 · 맛은 코퍼스 평균 차감 후 코사인 · 취향은 묻지 않고 계산 · 같은 입력 같은 결과(시각·시드는 호출자) · 서빙 순간 값은 그 순간 기록 · 실패는 세고 범위 밖은 거부 |
| 상태 | Layer 1(Mock 단위) 완료. Layer 2·3 은 실 DB 대기. 라우터 8경로 전부 목업(`engine/mock.py`) |

---

## 2. 계약

| 모델 | 필드 |
|---|---|
| `Candidate` (①→②) | `recipe_id`, `missing_count`, `missing_ids`, `coverage` 0~1, `cluster_id` (None = 배치 전) |
| `ScoredCandidate` (②→③) | + `features` 17종 원값(None 허용, 전부 필수), `score` 0~1, `penalty` |
| `RankedItem` (③→응답) | + `final_rank`, `reason`, `reason_features`, `mmr_penalty`, `is_exploration`, `propensity` 0<p<=1, `explore_source` uniform·thompson·None, `team` |
| `StageInfo` / `StageTrace` | `name`, `in_count`, `out_count`, `latency_ms`, `strategy`, `filters`, `dropped`, `params`, `score_stats`, `exploration_items` / `stages`, `totals(latency_ms, degraded, user_mode)` |
| 엔진 입력 | `RecipeFeature`(6축·인기·품질·조리시간·요리군·군집…), `UserHistory`(선호·기피 재료, 최근 7일 노출, 14일 조리, 조리 재료 집합, 군집 관측), `CorpusStats`(`flavor_mean`, `ingredient_idf`), `Persona`(`vec`, `prior_source`, `mode`, `prior_weight`, `behavior_weight`, `n_events`), `UserContext`(위 합 + pantry·expiring·상한·선호). `build_context(persona=필수)` |
| HTTP (A) | `POST /v1/recommend` `RecommendRequest{user_id, session_id ^[cgd]-, top_k 1~100, max_missing 0~10, max_minutes, model_version, weight_override, interleave_with, include_trace, context}` → `RecommendResponse{request_id, user_id, model_version, weights, items, trace, served_at}` · `POST /v1/events` `EventBatchIn{events 1~200 of EventIn{user_id, event_type, recipe_id, value, request_id, position, session_id, context}}` (시각 필드 없음 → 수신 시각) → `EventAck` · `POST /v1/onboarding/{user_id}` `OnboardingIn{picks 인덱스 1~20, scales 3축 0~4, allergy_groups, allergy_ingredient_ids, avoid_ingredient_ids<=3, household_size}` → `OnboardingOut{taste_vec 6, n_blocked_ingredients}` · pantry GET/PUT · 검색 2 · `/health` |
| 제시 목록 | `seeds/onboarding_recipes.yaml` `presented` 20개(`reserve` 4). 축 순서 (매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐) 검사. `picks` 는 이 배열의 인덱스 |

---

## 3. 식과 상수

### 3.1 ① Retrieval 과 완화

- 보유 = pantry(removed_at IS NULL) ∪ staple. 알레르기 = 직접 ∪ 카테고리 하위 ∪ 그룹 컬럼 ∪ 직접 재료의 그룹 확산(severity='allergy' 만).
- 조건: `n_total > 0` · `feature_version NOT LIKE 'test-%'`(include_test 제외) · `n_essential > 0 OR 미매칭 비율 <= 0.3` · `essential && pantry OR |essential| = 0` · `NOT (all_ids && allergy)` · `icount(essential − pantry) <= k` · `cook_minutes IS NULL OR <= max_minutes`. 정렬 `missing ASC, popularity DESC`, `LIMIT 500`. 반환 `coverage = 1 − missing/n_essential`(0개면 1.0), `cluster_id`.
- 완화(`engine/candidate.py`): `needed = max(20, top_k + 2·exploration_min_pool_ratio·round(top_k·ratio))` = 36(보통) / 52(cold). k 2→3→4(`relax_missing`) → 인기순(`popularity`, 부족 무시·조리시간 유지). `next_plan(current, found, policy, top_k, exploration_ratio)`.

### 3.2 페르소나 (`engine/persona.py`)

```text
prior: picks 평균(k=12) > scales/4 앞 3축(k=6) > 없음(k=0). picks 있으면 scales 저장만
w = kind × 0.5^(age/90d) × Π cycle(1 − s·(1−cos 2πΔ)/2; s 연 0.5·주 0·일 0)
kind: cook 1.0 · save 0.6 · click 0.3 · rating clamp((v−3)/2, 0, 1), v∉[1,5] 는 invalid · 나머지 0
vec[i] = (k·p + S_i·b_i)/(k + S_i); 한쪽만 있으면 그것, 둘 다 없으면 None. 전부 None 인 flavor 는 미집계
mode: n_events=0 → onboarding · S >= (k or 6.0) → behavior · else blended
cold(vec 전부 None) → exploration_ratio 0.4, f_taste None
```

- 저장 `profile_store.JsonProfileStore`: `root/<id%256:02x>/<id>.json`, schema 1, 원본(picks·pick_flavors·scales·events·updated_at), `mkstemp` + `os.replace`, `allow_nan=False`, 사용자 대조, 읽기 실패 = ValueError 한 종류 → 서빙은 cold + `persona_profile_unreadable`. 저장 전 prune(730일 · 2000건, 동률은 뒤쪽 유지). 서비스 잠금으로 RMW 직렬화.
- `service.onboarding_profile`: 인덱스 범위 밖 거부, 중복 하나로(+카운트), <3 카운트(`MIN_PICKS`), 척도 0~`SCALE_MAX`(4) 밖 거부. `record_events`: 배치 내 (user, recipe, kind, request_id) 중복 카운트, `at = now`(aware 필수), flavor None/전부 None → unknown.

### 3.3 ② Ranking (`engine/feature.py`·`score.py`·`taste.py`)

- `raw = Σ w·f / Σ w` (f None 또는 w 0 제외), `score = raw × p_recent × p_cooked × (1 − min(0.8, 2.0·기피비율))`, 정렬 `(−score, recipe_id)`.
- 피처: coverage(①값) · missing `1 − m/(k+1)` · expiring `|E∩임박|/|임박|` · pantry_use `|A∩P|/|P|` · taste 중심화 코사인 `(cos+1)/2` 후 `0.5 + (sim−0.5)·min(1, |u'|/0.25)` · ing_pref `|A∩liked|/|A|` · cuisine/dish_type 1/0 · cooccur max IDF-자카드 · popularity/quality 원값 · time_fit `1 − max(0,(T−Tmax)/Tmax)` · season 원값 · skill_fit `1 − |diff−skill|` · ing_cf/group_pref/content 항상 None.
- 가중치(A `DEFAULT_WEIGHTS`): coverage .24 taste .16 expiring .15 ing_pref .11 cooccur .10 popularity .10 missing .05 cuisine .04 time_fit .03 season .02, 나머지 0.

### 3.4 ③ Re-ranking (`engine/rerank.py`)

- MMR: 풀 = 점수 상위 `max(mmr_pool_size 200, total)`, `argmax λ·score − (1−λ)·max_sim`, λ 0.7, sim = IDF 자카드(all_ids). `total` 개 뽑은 뒤 `total − n_explore` 개만 개인화.
- 탐색: `n = round(total × exploration_ratio(ctx))`, 풀 = rest 중 `score >= median`[:200], `n = min(n, |풀| // exploration_min_pool_ratio)`. `serendipity.mixed_exploration(k=n, uniform_share=effective, pool_size=200, mc=200)` — `effective_uniform_share` = 군집 하나라도 있으면 0.5, 없으면 1.0(계약의 균등 폴백을 B 가 구현). propensity = 균등 `n_uniform/|pool|` + Thompson MC 확률(묶음의 최고 후보에 귀속), 하한 1e-6. 위치 `explore.exploration_slots` 무작위.
- 이유: `rank.top_reasons`(z-salience `w(f−μ)/σ`, σ 하한 0.05, 상위 2) → `reason.build_reason` 템플릿(종결형·연결형, 조사 자동). 탐색은 "새로운 시도는 어떠세요".

### 3.5 추적과 로그 (`service.rank_candidates` · A `repository.write_recommendation`)

- `params` 동결 10키: `policy_id, propensity_semantics(item), explore_pool_size, uniform_share, propensity_mc, rng_seed, max_missing_final, top_k, n_explore, serving_mode`. 덧붙임(`with_trace_extra`, 동결 키 덮으면 ValueError): `persona_source, persona_mode, persona_events, persona_behavior_weight, explore_fallback(uniform)`.
- rerank `dropped = {mmr_or_cap, explore_shortfall}`, ranking `score_stats = {min, p25, p50, p75, max}`.
- 로그: `recommendation_log` 1행(`candidates` = 상위 50 ∪ served, `not_reproducible` 표시) + `impression` N행. 300ms 타임아웃, 실패는 `failed` 카운트 + 묘비. 카운터 `service.bump/counters`.

---

## 4. 배치와 상태

```text
[A] enums · stage · schema · repository · repository_ingest · router · ingest/ · evaluation/ · engine/{rank,reason,explore,serendipity}
[B] policy · profile_store · service · engine/{candidate,context,taste,persona,feature,score,rerank,mock}
scripts/generate_mock_fixtures.py(12명·120·60·18) · scripts/eval_recommend_mock.py · seeds/onboarding_recipes.yaml · tests/unit/recommend/ (B 136건)
```

| Step | 상태 |
|---|---|
| 0 계약·픽스처 | 완료 (A 계약 채택, B 옛 모델 삭제) |
| 1 로그·관측 | 부분 — A `write_recommendation` 있음, 미연결. 카운터 노출 미착수 |
| 2 Retrieval | 부분 — A 함수 + B 완화 계획 완료, 서빙 미연결 |
| 3 Ranking | 완료 |
| 4 Re-ranking | 완료 (군집 폴백은 B 우회) |
| 5 페르소나·조립 | 부분 — 엔진 완료, 라우터 3경로 목업 |
| 6 종단·부하 | 대기 — 계약 98건 통과, 실데이터·Locust 미실행 |

DB 전환 15항목은 `test_db_cutover.py` 가 못을 박음: 라우터 실연결 · 후보 조회 · 이력 적재 · 코퍼스 통계 · 로그 적재(재현 3값) · 시드 실사용 · 카운터 노출 · 손잡이 정본 · 합성 격리 · `f_time_fit` · `redis` · A 검사 4종 · 커버리지 `omit` · 취향 JSON→DB(중복 사용자 분리) · 온보딩·이벤트 라우트.

---

## 5. 손잡이 (`policy.RankingPolicy`, 전부 예시값)

`penalty_recent .7 · penalty_cooked .5 · avoid_multiplier 2 · avoid_cap .8 · taste_min_norm .25 · max_missing 2 · max_missing_relaxed 4 · min_candidates 20 · candidate_limit 500 · mmr_lambda .7 · mmr_pool_size 200 · exploration_ratio .2 · cold_exploration_ratio .4 · explore_pool_size 200 · exploration_min_pool_ratio 2 · uniform_share .5 · propensity_mc 200 · picks_prior_weight 12 · scales_prior_weight 6 · persona_half_life_days 90 · season/weekly/daily_cycle_strength .5/0/0 · persona_max_events 2000 · persona_max_event_age_days 730`. `__post_init__` 가 범위를 검증. `fingerprint(weights)` 가 지문. `POLICY_ID = reco-b-linear-v0`.

---

## 6. 검증 (2026-09-12, `b7b25b4`)

`ruff check · ruff format --check(142) · python -m mypy src(62) · pytest tests/unit` 전부 종료코드 0 — **276 passed, coverage 90.23%**(2,098문). `seeds/validate.py` 0. `test_contract` 98건 0(설정값 20종 환경변수). Mock 종단: 출처 picks 10·scales 1·none 1, 탐색 [4,4,4,4,4,4,4,4,2,4,10,8], 피드백 매움 0.22→0.57(behavior 15.07)/1년 전 0.26(blended 0.91), 계절 반년 전 무게 .246/.123/.000, p95 17.6ms. 한계: Mock 맛 분포 ≠ 시드, `needed` 36 으로 인기순 폴백 7/12.

열린 결정: 척도 0~4 vs 1~5 · 월별 주기 해석 · `EventIn` 발생 시각 · A 함수의 균등 폴백 · 손잡이 정본 · `redis` · 예시값 전부.
