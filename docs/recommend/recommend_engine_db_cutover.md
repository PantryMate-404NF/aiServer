# 추천 엔진 DB 전환 점검표 (에이전트용)

**정하는 것**: 실 데이터베이스를 붙이는 시점에 반드시 처리해야 하는 항목과 그 확인 근거. 지금은 목업·기본값·빈 이력으로 채워 둔 자리들이며 전부 에러를 내지 않습니다

**적용 대상**: `src/features/recommend/` 를 DB 에 연결하는 인원과 AI 코딩 에이전트. 사람은 `human/` 의 서술본을 읽습니다

**버전**: 1.10.0 · **최종 수정**: 2026-09-22 · **작성자**: 유재현

---

## 1. 이 문서를 언제 여는가

**DB 와 닿는 변경을 하나라도 시작할 때 엽니다.** 라우터 연결, 저장소 함수 추가,
로그 적재, 배치 결과 사용, 취향 원본 이전 가운데 무엇을 하든 여기부터 봅니다.

여기 적힌 것은 전부 **에러가 나지 않는 자리**입니다. 목업이 200 을 돌려주고,
기본값이 조용히 쓰이고, 빈 이력이 None 으로 흘러갑니다. 그래서 하나를 빠뜨려도
검사도 화면도 아무 말을 하지 않습니다. 실제로 그런 일이 이미 세 번 있었습니다 —
`/health` 가 DB 를 안 보고 참을 냈고(F-24), 추적에 적는 값이 계산에 안 쓰였고(F-25),
같은 손잡이가 두 곳에 있었습니다(F-26).

## 2. 건너뛸 수 없게 하는 장치

`tests/unit/recommend/test_db_cutover.py` 가 아래 항목 가운데 열한 항목(M-01 · M-03 · M-05 ·
M-06 · M-07 · M-08 · M-11 · M-13 · M-14 · M-15 · M-16)이 **아직 전환 전 상태임을 못 박고 있습니다.**
그중 하나를 건드리면 검사가 깨지고 실패 메시지가 이 문서의 항목 번호를 가리킵니다. 나머지
다섯(M-02 · M-04 · M-09 · M-10 · M-12)은 실 DB 가 있어야 확인되는 것이라 못이 없고, 이 문서의
확인 근거로만 남습니다.

```bash
uv run pytest tests/unit/recommend/test_db_cutover.py --no-cov
```

`--no-cov` 가 없으면 이 파일만 돌려도 저장소 전체 커버리지 문턱(80%)에 걸려 종료 코드가 1 이 됩니다 —
검사가 실패한 것이 아닌데 01의 3.4 대로 읽으면 실패입니다.

검사가 깨지면 순서는 이렇습니다.

1. 이 문서에서 해당 항목을 엽니다.
2. "확인 근거" 를 실제로 돌려 통과시킵니다. 추정으로 넘어가지 않습니다(01의 3.2).
3. 이 문서의 상태를 바꾸고, 못을 그 항목의 완료 조건을 재는 검사로 **바꿉니다.**

**못을 지우기만 하지 않습니다.** 지우면 다음 사람이 같은 자리를 다시 밟습니다.

## 3. 항목

상태는 `대기`(아직 못이 박혀 있음) · `진행`(전환 중) · `완료`(검사가 완료 조건을 잼) · `대체됨`(전환할 자리 자체가 없어짐)입니다.

| ID | 항목 | 지금 상태 | 전환할 때 하는 일 | 확인 근거 | 상태 |
|---|---|---|---|---|---|
| M-01 | 라우터 실연결 | `router.py` 의 모든 엔드포인트가 `engine/mock.py` 를 부릅니다. 응답은 200 이고 피처·추적·사유가 다 채워져 실엔진과 구분되지 않습니다 (F-29) | `/v1/recommend` 를 `service.rank_candidates` 로 바꿉니다. M-02·M-05·M-06 을 같은 변경에서 함께 처리합니다. 09-21 부터 냉장고와 알레르기는 요청에 실려 옵니다(D-57) — `req.pantry` 를 `build_context(own_pantry_ids=...)` 로, `req.allergies` 를 `allergy.resolve()` 와 `blocks()` 로 잇습니다. 목업은 받은 값을 남기기만 하므로, 잇지 않고 목업만 떼면 **알레르기를 받고도 거르지 않는 서버**가 됩니다 | 실호출 응답의 `model_version` 이 `policy.POLICY_ID` 와 같고, `trace.stages` 가 `service` 가 만든 것과 일치 | 완료(09-22) — `serving.LiveServing` 이 요청의 `pantry` · `allergies` 로 ① → ② → ③ 을 잇고 라우터가 그것을 부릅니다. **백엔드 주소(`BACKEND_BASE_URL`)가 있을 때만**이고 없으면 목업입니다. 사전을 받기 전에는 503 입니다. 응답의 `model_version` 이 `POLICY_ID` 이고 추적의 단계가 셋인 것을 검사합니다(`test_serving.py`) |
| M-02 | 후보 조회 연결 | 후보를 Mock 카탈로그와 픽스처가 만듭니다. `repository.retrieve()` 는 있으나 서빙 경로가 부르지 않습니다 | `retrieve(user_id, max_missing, max_minutes, limit)` 산출을 `rank_candidates` 에 넣습니다. 완화 계획은 `engine/candidate.py` 가 그대로 냅니다 | `make smoke-py` 통과. 요청당 DB 왕복이 1회인지 확인 | 대체됨(09-22) — 레시피의 정본이 백엔드 API 로 가면서 후보 조회가 DB 함수에서 메모리(`engine/retrieval.py`)로 옮겨졌습니다. 규칙은 같고(부족 허용 사다리 · 조리시간 상한 · 알레르기 하드컷) 표본 21,491건에서 추천 한 건 전체가 p50 28~83ms 였습니다(개발 PC 에서 두 번 잰 값). `repository.retrieve()` · `load_recipe_features()` 는 데이터 파트의 배치 검증에만 남습니다 |
| M-03 | 사용자 이력 적재 | `UserHistory` 가 항상 비어 있어 `f_ing_pref`(0.11)·`f_cooccur`(0.10)가 **전건 None** 입니다. 클러스터 관측도 비어 Thompson 이 균등 사전분포로만 돕니다 (F-15, F-28) | `user_ingredient_pref`·`event_log`·`user_cluster_stat` 에서 읽는 저장소 함수를 만들고(조리 레시피의 재료 집합과 제목 `cooked_titles` 포함 — 없으면 "지난번 만드신 X 와 비슷해요" 사유가 나오지 않습니다) `build_context` 에 넣습니다 | 12 페르소나 재실행에서 두 피처가 None 이 아니고, 죽은 가중치 합이 0.26 → 0.05 로 줄어듦 | 대기 |
| M-04 | 코퍼스 통계 로드 | `CorpusStats.flavor_mean` 과 `ingredient_idf` 를 Mock 카탈로그에서 계산합니다. IDF 의 원천인 `ingredient.freq_count` 는 A 가 채우는 배치를 만들었습니다(`def3d5b`, `make freq-build`) | `feature_stats.flavor_mu` 와 실제 IDF 를 읽습니다. `stats_version` 을 함께 받아 로그에 싣습니다 (M-05) | 맛 코사인이 실제 코퍼스 평균을 차감하는지, `stats_version` 이 로그에 남는지 | 대체됨(09-22) — 맛 평균과 재료 희소도를 백엔드에서 받은 레시피로 계산합니다(`engine/catalog.py`). 표본 21,491건에서 여섯 축의 평균이 모두 계산됐습니다. **DB 의 배치 값과 대조하지는 않았습니다** — 재료 → 맛 계산은 데이터 파트의 `ingest/flavor.py` 를 그대로 씁니다. 사전의 판 번호가 추적의 `feature_version` 에 실립니다 |
| M-05 | 로그 적재 연결 | `service` 가 `write_recommendation` 을 부르지 않습니다. 서빙 로그가 한 행도 쌓이지 않습니다 | 응답 직후 호출합니다. **`config_hash`·`warm_alpha`·`stats_version` 을 반드시 함께 넘깁니다** — 안 넘겨도 행은 저장되고 `not_reproducible` 플래그만 붙어 그 요청의 점수는 영영 재현되지 않습니다 | `make log-test` 통과. 저장된 행의 `policies` 가 `REQUIRED_TRACE_PARAMS` 10종을 전부 가짐 | 대기 — **09-22 에 서빙이 이것 없이 먼저 열렸습니다**(4절의 묶음을 뗐습니다). `recommendation_log.user_id` 가 `app_user(id)` 를 참조하는데 사용자의 정본이 백엔드에 있어 그 표가 비어 있고, 지금 이으면 전건이 외래키로 실패합니다(묘비 행도 같은 이유로 실패). 그동안 노출 기록을 잃지 않게 `serving.RecommendationSink` 가 추천마다 한 줄을 파일(`RECO_LOG_DIR`, JSONL)에 남기고, 받은 이벤트도 전량 같은 폴더에 남깁니다(09-22 저녁 추가 — 취향 저장소는 맛이 있는 이벤트만 상한까지 들어 노출과 반응을 잇는 원본이 못 됩니다). 두 파일에 로그 · 노출 항목(점수 · 피처 · propensity) · 이벤트(`request_id` · 순위)가 다 들어 있어 나중에 DB 로 옮길 수 있습니다. 여는 조건: 데이터 파트와 외래키를 어떻게 할지(떼기 / 요청 때 `app_user` 에 빈 행 넣기) 정하고, 적재를 요청 경로 밖(큐 + 스레드)에 둡니다 — `connect_timeout` 이 없어 DB 가 닿지 않으면 추천이 함께 멈춥니다 |
| M-06 | 난수 시드 실사용 | `rank_candidates` 가 추적의 `rng_seed` 로 난수원을 직접 만듭니다(2026-09-14, F-51·F-52). 그 전에는 `rng` 를 따로 받아 평가 스크립트가 `SystemRandom` 을 넘기며 `rng_seed=user_id` 를 적었습니다 (F-16, N-10) | 라우터가 요청마다 시드를 정해 넘기고 응답·로그에 남깁니다 | 같은 `rng_seed` 로 두 번 호출해 탐색 슬롯의 아이템과 위치가 같음 — 검사 `test_the_logged_seed_reproduces_the_list` | 완료(09-22) — 실서빙이 요청마다 시드를 새로 정해 `rank_candidates` 에 넘기고 그 값이 추적의 `rng_seed` 에 남습니다 |
| M-07 | 실패 카운터 노출 | `write_recommendation` 이 모든 예외를 삼키고 `bump()` 만 합니다. **그 카운터를 읽는 곳이 없습니다** — `/health` 도 대시보드도 싣지 않고 `QualityExtra.log_counters` 는 계약만 있고 채우는 코드가 없습니다 (F-33) | `counters()` 를 `/health` 응답이나 대시보드 수집에 싣습니다 | 적재를 일부러 실패시키고 `failed` 가 밖에서 보이는지 | 완료 — 09-22 `main.create_app()` 이 `service.counters` 를 지표 수집기에 등록해 `/metrics` 의 `reco_internal_events_total{key=...}` 로 나갑니다(`evaluation/monitor.py` 의 `InternalCounters`). `fail` · `error` 가 든 키가 늘면 관리자 페이지와 경보 `RecoSwallowedFailures` 가 critical 로 띄웁니다. `/health` 에는 싣지 않았습니다 — 헬스체크는 몇 초마다 불려 읽는 사람이 없습니다 |
| M-08 | 손잡이 정본 결정 | `candidate_limit`·`explore_pool_size`·`propensity_mc` 가 `Settings` 와 `RankingPolicy` 두 곳에 같은 값으로 있고 **엔진은 `RankingPolicy` 만 읽습니다.** `.env` 로 바꿔도 아무 일이 없고 에러도 없습니다 (F-32, N-02) | 한쪽을 정본으로 정합니다. `Settings` 쪽이면 `RankingPolicy` 가 그 값을 받아 만들어지도록 바꿉니다 | `.env` 값을 바꿨을 때 `trace.params` 의 값이 따라 바뀜 | 대기 |
| M-09 | 합성 피처 격리 확인 | `retrieve_for_user` 가 `feature_version LIKE 'test-%'` 를 뺍니다. 실 DB 없이는 그 필터가 도는지 확인할 수 없습니다 | 합성 행을 넣고 서빙 조회에 섞이지 않는지 실측합니다 | `make ddl-test` 통과. `include_test=false` 로 조회했을 때 합성 행이 0건 | 대기 |
| M-10 | `f_time_fit` 재정의 판단 | 후보 조회가 `cook_minutes <= p_max_minutes` 를 이미 걸러서 살아남은 후보는 **전부 1.0** 입니다. 가중치 0.03 이 순위를 못 바꿉니다 (F-27, N-13) | 상한 대비 여유분을 재는 쪽으로 바꾸거나, 가중치를 `f_pantry_use` 로 옮깁니다. 값을 바꾸는 결정이라 W3 가중치 학습과 함께 정합니다 | 실데이터 260건 이상에서 값 종류가 2 이상 | 대기 |
| M-11 | `/health` 의 `redis` | 저장소 어디에도 redis 클라이언트가 없는데 `HealthOut.redis` 기본값이 True 라 항상 참으로 나갑니다 (F-31, G-19) | 실제로 붙여 확인하거나 필드를 뺍니다. `db` 는 이미 실제 확인으로 고쳤습니다 (F-24) | redis 를 내린 상태에서 `redis: false` 이거나, 필드가 응답에 없음 | 대기 |
| M-12 | DB 가 필요한 A 게이트 실행 | 병합 시점에 `make smoke`·`log-test`·`ddl-test`·`feature-test` 를 돌리지 못했습니다. 통과 여부를 모릅니다 | 넷을 돌리고 결과를 검증 기록에 남깁니다 | 네 명령 모두 종료 코드 0 | 대기 |
| M-14 | 취향 원본을 DB 로 | 취향 원본(고른 음식·3축 척도·**음식 유형**·행동 이벤트)이 `profile_store.JsonProfileStore` 의 사용자당 JSON 파일에 있습니다. 데이터 파트의 `user_vector.onboarding_picks`·`onboarding_scales` 와 `event_log` 가 같은 내용을 담을 자리입니다 | `ProfileStore` 프로토콜의 DB 구현을 `repository.py` 에 만들고, JSON 의 이벤트를 `event_log` 로 옮긴 뒤 두 저장소의 페르소나가 같은지 대조하고 바꿉니다 | 같은 사용자에 대해 JSON 과 DB 구현의 `derive_persona` 결과가 축마다 1e-6 안에서 같음. JSON 은 배치 간 재시도 중복을 흡수하지 않고 `event_log` 는 `DO NOTHING` 으로 흡수하므로, 중복이 있는 사용자는 차이 원인을 따로 적음 | 대기 |
| M-15 | 온보딩·이벤트 라우트 실연결 | `/v1/onboarding/{user_id}` 와 `/v1/events` 가 목업을 부릅니다. `PersonaService.save_onboarding`·`record_events` 는 있으나 라우터가 쓰지 않습니다 | M-01 과 같은 변경에서 라우터가 `PersonaService` 를 부르게 합니다. 이벤트 시각은 서버 수신 시각입니다 | 온보딩 실호출이 범위 밖 인덱스를 거부하고, 이벤트 실호출 뒤 `counters()` 의 `persona_events_stored` 가 늘어남. 추천 실호출의 추적에 `explore_fallback`·`dropped.explore_shortfall` 이 실림 | 완료(09-22) — 실서빙이 `PersonaService.save_onboarding` · `record_events` 를 부릅니다. 저장소는 아직 파일입니다(M-14) — **컨테이너에서는 지속 볼륨이 필요합니다**(`PROFILE_STORE_DIR`) |
| M-16 | 음식 유형을 `user_preference` 에서 읽기 | 온보딩이 받은 음식 유형이 취향 원본 JSON 의 `cuisines` 에만 있습니다. 서빙 문맥은 `build_context(preferred_cuisines=...)` 를 넘기지 않으면 페르소나에 저장된 값으로 되돌아가는데, DB 전환 뒤에는 그 JSON 이 없으므로 **고른 유형이 없는 사용자로 조용히 바뀝니다.** 유형 슬롯과 `f_cuisine` 이 아무 일도 하지 않고 응답은 200 입니다 | 저장소가 `user_preference.pref_cuisines` 를 읽어 `build_context` 에 명시적으로 넘깁니다. 빈 목록을 넘기는 것과 넘기지 않는 것은 뜻이 다릅니다 — 빈 목록은 "고른 유형이 없다" 이고 페르소나로 되돌아가지 않습니다. M-14 와 같은 변경에서 JSON 의 `cuisines` 를 `pref_cuisines` 로 옮깁니다 | 실호출 추적의 `preferred_cuisines` 가 그 사용자의 DB 값과 같고, 유형을 고른 사용자에게 `n_cuisine` 또는 `cuisine_unmet` 가운데 하나가 실림 | 대기 |
| M-13 | 커버리지 측정 범위 되돌리기 | `ingest/*`·`repository.py`·`repository_ingest.py`·`engine/mock.py` 를 `[tool.coverage.run] omit` 으로 빼 두었습니다. 검사가 pytest 밖(`make`)에 있기 때문입니다 | 단독 스크립트를 pytest 함수로 옮기는 만큼 `omit` 에서 뺍니다 (G-08) | `omit` 이 비고 커버리지 80% 이상 | 대기 |

## 4. 함께 처리해야 하는 묶음

항목을 하나씩 떼어 처리하면 중간 상태가 위험합니다. 아래 셋은 같은 변경에서 끝냅니다.

| 묶음 | 항목 | 떼면 생기는 일 |
|---|---|---|
| 서빙 개통 | M-01 · M-02 · M-05 · M-06 · M-15 | 라우터만 연결하면 실엔진이 응답하는데 로그가 한 행도 안 쌓입니다. 그 구간의 노출은 사후 평가에서 영영 복원되지 않습니다. **09-22 에 M-05 만 떼고 열었습니다** — DB 적재가 외래키로 전건 실패하는 상태라서입니다(M-05). 노출을 잃지 않게 추천마다 파일에 한 줄을 남깁니다 |
| 피처 개통 | M-03 · M-04 · M-14 · M-16 | 이력만 붙이고 코퍼스 평균을 Mock 값으로 두면 맛 코사인이 틀린 기준에서 계산됩니다. 값이 0~1 이라 검사에 안 걸립니다. 음식 유형도 같은 자리입니다 — 안 읽어 오면 빈 집합이 되어 슬롯이 조용히 꺼집니다 |
| 관측 개통 | M-05 · M-07 · M-12 | 적재를 켜면서 실패 카운터를 안 내보내면 **적재가 전부 실패해도 200 이 나갑니다** |

## 5. 전환 전까지 지킬 것

- **이 기간의 서빙 로그를 평가에 쓰지 않습니다.** 라우터가 목업을 서빙하는 동안 `model_version` 은 요청이 보낸 값을 되돌려주므로, 실엔진 이름으로 적힌 행이 목업 산출일 수 있습니다 (F-29).
- 데이터가 없는 피처의 가중치를 0 으로 내리지 않습니다. Zero-Drop 이 분모에서 함께 빼므로 남은 가중치가 비례 재분배되고, **데이터가 오면 코드를 고치지 않아도 켜집니다.** 0 으로 내리면 그때 코드를 고쳐야 하는데 고쳐야 한다는 사실이 잊힙니다.
- 같은 값을 두 곳에 두지 않습니다 (D-27). 두면 한쪽만 바뀌어도 에러가 나지 않습니다.
