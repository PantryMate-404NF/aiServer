# 시뮬 시드 실행 안내 (에이전트용)

**정하는 것**: 기획측 가상운영데이터(xlsx, v0.4)를 추천 엔진 DB 에 넣고, 사용자 이용 시나리오(콜드 → 웜 전환, 냉장고·행동에 따른 추천 변화)를 돌려 통과를 확인하기까지의 순서와 판정 기준. DB 가 없는 PC 에서 엔진만으로 같은 시나리오를 돌리는 길도 포함합니다.

**적용 대상**: 이 작업을 맡은 Claude Code 및 인원. 첨부된 패키지(`sim_warm_package.zip`)를 저장소에 풀어 넣는 것부터 시작합니다.

**버전**: 1.5.0 · **최종 수정**: 2026-09-18 · **작성자**: 추천 코어 엔진(Track B)

---

## 1. 무엇을 넣는가

패키지 안의 파일과 저장소 안 위치입니다. 위치를 바꾸면 스크립트의 상대 경로가 깨집니다.

| 패키지 경로 | 저장소 위치 | 역할 |
|---|---|---|
| `scripts_sim/amplify_events.py` | `scripts/sim/amplify_events.py` | 기획 이벤트를 웜 전환 규모로 증폭 (xlsx → xlsx) |
| `scripts_sim/convert_planning_data.py` | `scripts/sim/convert_planning_data.py` | xlsx → reco 스키마 SQL 시드 |
| `scripts_sim/scenario_run.py` | `scripts/sim/scenario_run.py` | API 로 시나리오 실행 · 종료 코드로 판정 |
| `scripts_sim/scenario_engine.py`, `scripts_sim/sim_seed.py`, `scripts_sim/sim_world.py` | `scripts/sim/` 아래 같은 이름 | DB 없이 시드를 엔진에 직접 넣어 시나리오 실행 · 종료 코드로 판정. `sim_seed.py` 는 시드 SQL·Mock 카탈로그 읽기, `sim_world.py` 는 시드를 엔진 입력으로 바꿔 한 명을 서빙(09-15 분리, 02의 5.1) |
| `planning_data_original/*.xlsx` | `tests/fixtures/sim/planning_v0.4/` | 기획 원본 11개. 단위 검사의 입력 |
| `planning_data_amplified/*.xlsx` | (저장소에 넣지 않음) | 증폭본. 기획측 공유용. 재생성 가능 |
| `sim_seed/*.sql`, `load_sim.sh`, `README.md` | `deploy/seed/sim/` | 적재 SQL · 적재기 · 매핑 규칙 |
| `tests/test_sim_seed.py` | `tests/unit/sim/test_sim_seed.py` (`__init__.py` 함께) | DB 없이 도는 검사 6건 (변환기 불변식 5 + 엔진 시나리오 1) |
| `docs/recommend_sim_seed_runbook.md` | `docs/recommend/recommend_sim_seed_runbook.md` | 이 문서 |

`deploy/seed/sim/` 의 `00~06_*.sql` 과 `stats.json` 은 생성물입니다. 손으로 고치지 않고 3절로 다시 만듭니다.

## 2. 전제

- `uv sync` 가 끝난 상태. 변환기(3-2 · 3-3)는 pandas 와 openpyxl 을 쓰며 둘은 `dev` 의존성 묶음에 있습니다(2026-09-14, `uv add --group dev`). 3-4 부터는 생성물만 쓰므로 필요 없습니다.
- DB (3-4 · 3-5): `make up` 으로 postgres 가 떠 있고 `deploy/init/01~04` 가 적용된 상태. `make bootstrap` 이면 됩니다. `ingredient` 시드가 적재되어 있어야 합니다 (`make seed`). 냉장고 재료를 **이름으로 조인**하기 때문입니다. `make` 와 `psql` 이 없는 PC(Windows)에서는 Makefile 이 부르는 명령을 직접 돌립니다 — `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d`, `uv run python scripts/reco/migrate.py`, `uv run python tests/integration/test_smoke.py --keep`. published 합성 레시피를 남기려면 `--keep` 이 필요합니다(`make smoke` 는 끝나면 지웁니다). 적재는 `PSQL_VIA_COMPOSE=1` 로 컨테이너의 psql 을 씁니다.
- `recipe` 에 `status='published'` 행이 **119건 이상** 있어야 합니다. 없으면 `06_event_log.sql` 이 적재를 중단합니다. 크롤 레시피가 없으면 `make smoke` 가 만드는 합성 레시피로도 됩니다. 이 가드는 일부러 둔 것이니 우회하지 않습니다.
- 3-6 은 아무것도 필요 없습니다. 저장소 안의 생성물과 Mock 카탈로그만 읽습니다.

## 3. 순서

각 단계는 **종료 코드**로 판정합니다 (01의 3.4). 화면 마지막 줄로 판정하지 않습니다.

```bash
# 3-1. 정적 검사 — 새 파일이 저장소 규칙을 지키는지
uv run ruff check scripts/sim tests/unit/sim && uv run ruff format --check scripts/sim tests/unit/sim

# 3-2. DB 없는 검사 — 변환기 불변식 5건 + 엔진 시나리오 1건 (일부만 돌리므로 --no-cov)
uv run pytest tests/unit/sim --no-cov

# 3-3. 시드 재생성 (증폭본 → SQL). 결정론적이고 줄바꿈도 LF 로 고정이라 어느 OS 에서든 diff 가 없어야 정상
uv run python scripts/sim/amplify_events.py --src tests/fixtures/sim/planning_v0.4 --out /tmp/sim_amp
uv run python scripts/sim/convert_planning_data.py --src /tmp/sim_amp --out deploy/seed/sim
git diff --stat deploy/seed/sim    # 변경 없음이 기대값

# 3-4. 적재 + 검증 쿼리 (재실행 가능 — sim_u* 계정을 지우고 다시 넣는다)
DATABASE_URL=$(grep ^DATABASE_URL deploy/.env | cut -d= -f2-) bash deploy/seed/sim/load_sim.sh
PSQL_VIA_COMPOSE=1 bash deploy/seed/sim/load_sim.sh    # psql 이 없는 PC(Windows): 컨테이너의 psql 사용

# 3-5. API 시나리오 (서버를 먼저 띄운다). 모든 라우터가 내부 API 키를 요구한다 —
#      --api-key 를 주지 않으면 서버와 같은 설정(config.get_settings)에서 읽는다
uv run uvicorn main:create_app --factory --port 8000 &
uv run python scripts/sim/scenario_run.py --user 1000184 --cold 1000001

# 3-6. DB 없는 엔진 시나리오 — 시드 1,600명 전원을 엔진에 넣고 전환·행동·냉장고 시나리오까지
uv run python scripts/sim/scenario_engine.py
```

3-4 의 `99_verify.sql` 출력에서 확인할 것:

| 항목 | 기대값 |
|---|---|
| 테이블별 건수 | app_user 1600 · user_preference 1600 · user_vector 1600 · user_allergy 90 · pantry_item 4442 · event_log 10195. 시뮬 id 는 1,000,001~1,001,600 (스모크 합성 유저 1~8 과 겹치지 않음). 스모크 합성 유저(`smoketest_*`, is_simulated)가 남아 있어도 `99_verify.sql` 이 `sim_u%` 만 세므로 건수는 그대로입니다 |
| 집단 × 모드 | `sim_funnel_A`: behavior 235 · blended 90 · onboarding 475 / `sim_funnel_B`: onboarding 800 |
| 시나리오 후보 상위 10 | 전부 `sim_funnel_A` · `behavior` · pantry_n ≥ 8 · cooks ≥ 11 |
| 후보 조회 가능 유저 | `recipe_feature` 가 채워진 환경에서 `users_with_candidates > 0`. 합성 레시피만 있는 환경에서는 0 이 정상 |

3-5 의 `scenario_run.py` 는 `RESULT: PASS` 와 종료 코드 0 이 판정입니다. 단계 [5] "상위 10 중 N 자리 변동" 은 **지금은 0 이 정상**입니다 — 4절을 보십시오. DB 가 없는 PC 에서는 `/health` 가 DB 접속을 기다리다 응답하지 않으므로(4절) `--skip-health` 로 목업 라우터의 나머지 단계만 확인합니다.

2026-09-14 DB 실행(Docker Desktop 29.7, `pgvector/pgvector:pg16`, 재료 시드 536종, 합성 published 레시피 10,007건): 3-4 의 여섯 건수와 집단 × 모드 분포가 위 표와 같았고 `users_with_candidates` 는 0(합성 레시피가 `test-smoke` 라 정상), 3-5 는 `/health` 가 0.11초에 `db: true` 로 답하고 `RESULT: PASS`, [5] 변동 0, [6] 겹침 8 이었습니다. 첫 적재는 시드 id 1~1600 이 스모크 합성 유저 1~8 과 충돌해 멈췄고, 그래서 시뮬 id 를 100만 대로 옮겼습니다.

3-6 의 `scenario_engine.py` 도 `RESULT: PASS` 와 종료 코드 0 이 판정입니다. 여섯 가지를 봅니다.

| 판정 | 뜻 |
|---|---|
| `invariants` | 1,600명 전원에서 알러지 위반 0 · 중복 없음 · 노출 확률 (0,1] · 사유 문구 채움 · 조리시간 상한 · 순위 연속 · 음식 유형 칸이 고른 유형이고 노출 확률 1.0 |
| `cold_to_warm` | 이벤트가 가장 무거운 A 유저의 이력을 시간순으로 따라가면 모드가 onboarding → blended → behavior 로 바뀜 |
| `behavior_moves_list` | 개인화 상위 5건을 지금 조리한 것으로 넣으면 페르소나 무게가 오르고 상위 10 이 움직임 |
| `expiring_reaches_list` | 아직 없는 재료 2종을 임박으로 넣으면 그 재료를 쓰는 개인화 레시피가 늘고 `f_expiring` 이 측정됨 |
| `repeatable` | 같은 시드로 두 번 돌리면 목록이 같음 |
| `cuisine_reaches_list` | 유형 슬롯을 끈 정책과 견줘, 고른 음식 유형이 목록에 한 건도 없는 사람이 늘지 않음. 채울 것이 있었는데 한 칸도 안 떼면 실패입니다 — 늘지 않은 것만 보면 슬롯을 꺼도 통과합니다 |

2026-09-15 실행에서는 여섯 가지가 전부 통과했습니다 (33초). 음식 유형은 1,600명 가운데 유형 칸이 291개 생겼고, 고른 유형이 Top-20 에 한 건도 없는 사람이 슬롯을 끄면 460명, 켜면 178명이었습니다(A 집단 122명 · B 집단 163명이 칸을 받음). 남은 178명은 Mock 카탈로그 120건에 그 유형의 후보가 아예 없는 경우이며, 대부분 `asian_other` 입니다. 2026-09-18 재실행(Mock 재료 이름을 시드와 맞추고 빈 팬트리 규칙 D-48 을 넣은 뒤)은 유형 칸 298개, 끄면 450명 → 켜면 161명(A 집단 129명 · B 집단 163명)이었습니다. 이 시드는 임의 데이터라 수치를 '실측' 으로 쓰지 않습니다.

## 4. 알아야 하는 한계

이 시드는 DB 를 채우지만, **서빙 경로가 아직 그 DB 를 읽지 않습니다.** DB 전환 점검표
`recommend_engine_db_cutover.md` 의 두 항목이 대기 상태이기 때문입니다.

- **M-01** 라우터가 `engine/mock.py` 를 부릅니다. `/v1/recommend` 응답은 유저 id 와 무관하게 mock 카탈로그에서 나옵니다.
- **M-03** `UserHistory` 가 항상 비어 있습니다. `event_log` 에 cook 이 40건 있어도 `f_ing_pref` · `f_cooccur` 는 None 이고 모드는 콜드입니다.

그래서 시나리오 [5] 의 변동이 0 이고 [6] 의 cold 유저와 warm 유저 상위 10 이 전부 겹칩니다. 이것은 시드의 문제가 아니라 전환 전 상태이며, 스크립트도 그렇게 출력합니다. M-01 · M-03 이 붙는 순간 같은 명령으로 변동이 나타나야 하고, 그때 [5] 의 기대값을 `moved > 0` 으로 바꿔 못을 박습니다.

3-6 은 그 두 항목이 할 일을 파이썬 안에서 대신하므로 엔진이 시드에 반응하는 것을 지금 볼 수 있습니다. 대신 다음을 알고 읽습니다.

- 레시피는 Mock 카탈로그 120건입니다. 시드의 냉장고 재료 가운데 Mock 에 이름이 없는 것이 냉장고 4,267건 중 1,577건(2026-09-18, Mock 재료 이름을 `seeds/ingredient.csv` 와 맞춘 뒤. 그 전 1,836건)이라, 후보가 모자라 대부분 인기순 폴백까지 내려갑니다(A 집단 800명 중 폴백 없음 41 · 부족 재료 완화 139 · 인기순 620). 실 DB 에서는 이름 조인이 전부 붙습니다.
- 시드의 `computed_from` 은 이벤트 20건 기준이고, 엔진의 모드는 `engine/persona.derive_persona` 가 무게(cook 1.0 · click 0.3 · 반감기 90일)로 정합니다. 그래서 둘은 같지 않습니다 — 이번 실행에서 시드 behavior 235명 중 엔진 behavior 195 · blended 40 이었습니다. 배치 검증에 `computed_from` 을 그대로 기대값으로 쓰지 않습니다.
- B 집단(냉장고 없음)도 후보가 0 건이 아닙니다. `user_pantry_ids()` 가 staple 을 합치지만 사용자가 넣은 재료가 없으므로 2026-09-18 부터는 완화 사다리를 밟지 않고 **첫 조회부터 인기순**입니다(D-48, 800명 전원 `popularity`). 3-4 의 `users_with_candidates` 는 냉장고 있는 유저만 셉니다.
- 알러지 그룹은 시드와 Mock 카탈로그가 같은 어휘(DDL 의 소문자 10종)를 쓰므로 3-6 에서 열 그룹 전부가 검사 대상입니다(2026-09-18, D-49). 그 전에는 대문자 사본으로 옮기는 표가 있었고 `sesame` 은 대응이 없어 빠져 있었습니다.
- 음식 유형은 시드에 문항이 없어 **고른 음식의 계열**로 대신합니다(`synth_onboarding`). 실제 응답이 오면 원본으로 바꿉니다. 저장 값은 라벨이 아니라 `cuisine_family` 코드이고, Mock 카탈로그는 한식 편중(120건 중 80)이라 실 코퍼스와 같은 모양입니다. 실 DB 에서는 `recipe.cuisine_family` 가 전수 비어 있어 유형 슬롯도 `f_cuisine` 도 돌지 않습니다 — 회의 안건 G-30.
- `/health` 는 `db.healthy()` 로 DB 에 접속하는데 DSN 에 접속 시간 제한이 없어, DB 가 없는 PC 에서는 150초가 지나도 응답하지 않습니다. 인프라 쪽 항목이며 회의 안건 G-29 입니다. DB 가 있으면 0.11초에 답합니다 — 문제는 DB 부재 시의 대기입니다.

M-03 을 붙일 때 이 시드가 주는 것: `user_vector.n_events` 와 `event_log` 가 있으니, 배치가 `event_log` 에서 행동 벡터를 계산한 뒤 3-6 의 집단별 모드 분포와 대조하면 배치 검증이 됩니다. `taste_vec` 은 고른 음식의 6축 평균이며 행동 반영 전 값입니다.

## 5. 완료 선언 전

아래 명령이 전부 종료 코드 0 이어야 합니다. 못 돌린 명령은 결과를 적지 말고 못 돌렸다고 적습니다.

```bash
uv run ruff check scripts/sim tests/unit/sim && uv run ruff format --check scripts/sim tests/unit/sim
uv run pytest tests/unit/sim --no-cov
uv run python scripts/sim/scenario_engine.py
DATABASE_URL=... bash deploy/seed/sim/load_sim.sh
uv run python scripts/sim/scenario_run.py
```

저장소 전체 게이트(`uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit`)도 깨지지 않아야 합니다. 이 작업은 `src/` 를 건드리지 않으므로 `mypy src` 결과는 변하지 않습니다.

## 6. 절대 하지 않는 것

- `06_event_log.sql` 의 published 가드를 지우거나 숫자를 낮추기. 가드가 없으면 `event_log.recipe_id` 에 존재하지 않는 id 가 조용히 들어갑니다 (FK 가 없습니다).
- `d-` 세션 접두어를 `c-` 로 바꾸기. 시딩 트래픽이 실유저 지표에 섞입니다.
- 생성물 SQL 을 손으로 고치기. 규칙을 바꾸려면 스크립트를 고치고 재생성합니다.
- `is_simulated=FALSE` 로 넣기. `sim_persona` FK CHECK 가 막기도 하지만, 뚫리면 분석이 오염됩니다.
