# 시뮬 시드 — 기획 가상운영데이터 v0.4 → reco 스키마

**정하는 것**: 기획측 xlsx(퍼널·코호트 기획 데이터)를 추천 엔진 DB 형태로 바꾸는 규칙과, 무엇을 합성했는지.

**적용 대상**: 사용자 이용 시나리오(콜드→웜 전환, 냉장고 변화에 따른 추천 변화)를 DB 위에서 돌려보는 인원.

생성: `uv run python scripts/sim/convert_planning_data.py --src <xlsx 폴더>` · 적재: `bash deploy/seed/sim/load_sim.sh`

## 1. 매핑

| 기획 | reco | 규칙 |
|---|---|---|
| users.user_id `U00004` | app_user.id `1000004` (1,000,000 + 번호), username `sim_u00004`, display_name `U00004` | is_simulated=TRUE, persona_id = 집단(A/B). 낮은 id 는 실유저·스모크 합성 유저 몫이라 피하고 시퀀스는 건드리지 않는다 |
| users.group | sim_persona `sim_funnel_A` / `sim_funnel_B` | params 에 퍼널 정의 |
| users.signup_date | app_user.created_at, consent_at (v1-min) | |
| recipe_events `recipe_detail_view` | event_log `click` | source='client', context.origin |
| cart_events source=recipe | event_log `cook` | recipe_id = 그 날짜 이전 마지막 열람 레시피. 열람 이력 없으면 건너뜀 |
| cart_events source=browse, browse_events, orders | (미적재) | 엔진이 안 읽는 이벤트. 필요하면 context 로 따로 |
| pantry_events `pantry_entry` / `pantry_revisit` | pantry_item 추가 시점 | 진입 6~10종, 재방문 2~3종 |
| recipe_id `RCP0102` | recipe.id | 적재 시 published 레시피 id 순 102번째. context.rcp_code 에 원본 보존 |

## 2. 합성한 것 (기획 데이터에 없음)

- 온보딩 picks 3개(제시 20 중) · scales[매움,짠맛,단맛] → user_preference, user_vector.taste_vec
- 냉장고 재료: `PANTRY_POOL` 51종에서 결정론적 추출. 30% 는 소비기한 유저 입력(`user`), 나머지 `estimated`
- 알러지: 약 6% 유저에 그룹 1개 (`severity='allergy'`)
- 세션: `d-{user}-{yyyymmdd}` (일 단위). 'd-' 는 시딩 트래픽 표시 — 실유저 지표에서 걸러진다

전부 해시 기반이라 재생성해도 같은 값이다. 실측이 아니므로 리포트에 '실측'으로 쓰지 않는다.

## 3. 페르소나(모드) 전환이 실제로 일어나는 조건

엔진의 모드는 `user_vector.computed_from` 이 아니라 `engine/persona.derive_persona` 가 정한다 —
이벤트 무게 합(cook 1.0 · click 0.3 · 반감기 90일)이 사전 취향 무게(picks 12)에 닿으면 behavior,
그 사이면 blended 다. 시드의 `computed_from` 은 이벤트 20건 기준이라 둘은 같지 않다
(`scripts/sim/scenario_engine.py` 2026-09-14 실행: 시드 behavior 235명 중 엔진 behavior 195 · blended 40).
이 시드는 event_log 와 n_events 만 넣는다. **행동 취향 벡터는 배치가 event_log 에서 계산해야 한다** —
그 배치와 `build_context` 의 이력 적재는 DB 전환 점검표 **M-03 (대기)** 이다. M-03 전에는 시드를 넣어도
서빙 경로가 이력을 읽지 않아 모든 유저가 콜드로 보인다. `taste_vec` 은 고른 음식의 6축 평균이며
척도는 원본으로만 둔다 — picks 가 있으면 엔진이 척도를 쓰지 않는다 (결정 D-29).

시나리오를 돌리는 순서:
1. `99_verify.sql` 로 집단×모드 분포와 후보 조회 가능 유저를 확인한다.
2. 이벤트 많은 A 유저 하나를 고른다 (verify 의 상위 10).
3. `/v1/recommend` 를 호출해 결과를 잡고, 같은 유저에 `PUT /v1/users/{id}/pantry` 로 재료를 바꾸거나
   `POST /v1/events` 로 cook 을 몇 건 더 넣은 뒤 다시 호출해 순위 변화를 본다.
4. B 유저(냉장고 없음)도 후보가 0 건은 아니다 — `user_pantry_ids()` 가 staple 을 합치고, 모자라면
   인기순 폴백이 채운다. 비교용 냉장고가 필요하면 `--pantry-for-b` 로 재생성한다.
5. DB 없이 같은 시나리오를 보려면 `uv run python scripts/sim/scenario_engine.py` 를 돌린다
   (Mock 카탈로그 120건 위에서 1,600명 전원 + 전환·행동·냉장고 시나리오, 종료 코드로 판정).

## 4. 생성 통계

생성 시 `stats.json` 에 기록된다.
