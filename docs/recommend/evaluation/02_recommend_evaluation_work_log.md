# 추천 평가 시스템 작업 기록 (에이전트용)

**정하는 것**: `01_recommend_evaluation_design.md` 의 실행 이력. 상태, 결정(D), 가정(A), 접점(I), 계획 항목(T), 타 파트 안건(G), 세션 기록

**적용 대상**: 파트 C 평가 시스템을 이어서 작업하는 AI 코딩 에이전트와 김민경

**버전**: 1.6.0 · **최종 수정**: 2026-09-18 · **작성자**: 김민경

---

## 1. 상태 요약

| 키 | 값 |
|---|---|
| 정본 | 이 파일. 설계 명세 `01_recommend_evaluation_design.md` 1.2.0 이 사람용 정본이며 어긋나면 명세가 이깁니다 |
| 브랜치 | `feat/recommend-eval-metrics`. 2026-09-17 에 `origin/main`(PR #11 까지)을 fast-forward 로 받아 같은 지점입니다 |
| 단계 | 코드 8단계 전부 작성 후 2차 리뷰 10건 반영. 오프라인 코어(커밋 677c788), 나머지 미커밋. E-10 은 `make eval-smoke` 로 옮겼고 실 DB 전까지 미실행 |
| 검증 | 2026-09-18 `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit` 종료 코드 0. 단위 425건 통과, 커버리지 93.27%. `make eval` 종료 코드 0(합성 500건, 판정 통과) |
| 다음 행동 | `04_recommend_evaluation_requests.md` 의 요청을 전달. 가장 급한 것은 라우터·엔진 연결일(G-12)과 `EVAL_SALT`(G-10). 실 DB 가 붙으면 `make eval-smoke SINCE=<연결일>`. 그 뒤 결정 문서 3건(T-08)과 화면 브랜치 인계 |
| 갱신 규칙 | 세션마다 1절과 7절 갱신. 새 항목은 다음 번호, 번호 재사용 금지. 사람용 서술본은 두지 않습니다. 명세가 사람용이고 이 기록은 표만 담습니다 |

---

## 2. 결정 (D)

명세 15절의 D-01~D-07, D-11~D-16 이 정본입니다. 여기에는 명세에 없는 실행 결정만 적습니다.

| ID | 결정 | 근거 | 되돌릴 조건 |
|---|---|---|---|
| D-08 | 스크립트는 `scripts/reco_eval.py` 하나에 서브커맨드 4개 | 파일 수가 적고 공통 인자를 한 곳에서 다룹니다 | 서브커맨드가 각각 300줄을 넘으면 나눕니다 |
| D-09 | `scripts/eval_recommend_mock.py` 의 `jaccard`, `intra_list_distance` 를 `metrics.py` 로 이관 | 같은 계산이 두 벌이면 한쪽이 조용히 어긋납니다 | 없음 |
| D-10 | 결정 문서(`docs/decisions/`)는 G-01~G-04 가 회의에서 확정된 뒤 씁니다 | 합의 전 결정을 결정 문서로 올리면 되돌리는 비용이 큽니다 | 없음 |
| D-17 | 문서 폴더와 파일명을 소스 폴더에 맞춰 `docs/recommend/evaluation/NN_recommend_evaluation_*.md` 로. 읽는 순서의 두 자리 번호 접두어와 소문자 | 소스 `src/features/recommend/evaluation/` 과 이름이 같아야 찾기 쉽고, 번호가 읽는 순서를 말합니다 | 없음 |
| D-18 | 명세 1.1.0 의 새 결정은 D-11 부터 번호를 이어 이 파일의 D-08~D-10 과 충돌하지 않게 합니다 | 두 문서가 같은 접두어를 쓰므로 번호가 겹치면 참조가 깨집니다 | 없음 |
| D-19 | 합성 생성기는 `evaluation/synth.py` 모듈이고 `scripts/reco_eval.py synth` 는 인자만 넘기는 래퍼입니다. 명세 4절의 배치(스크립트 안)와 다릅니다 | 단위 테스트가 subprocess 없이 import 로 픽스처를 만들어야 합니다. 명세 4절은 다음 개정에서 맞춥니다 | 없음 |
| D-20 | 평가 모듈은 표준 라이브러리만 씁니다. `scikit-learn` 은 외부 대조 검사에서만 `pytest.importorskip` | `numpy` 가 `ml` extra 라 기본 설치의 단위 테스트가 깨집니다 | 부트스트랩이 유저 수천 명에서 느려지면 |
| D-21 | 헤더의 `label_version`·`metric_version` 이 코드 상수와 다르면 읽기를 거부합니다(명세 2.2 의 6번 불변식 해석) | 다른 버전의 파일을 조용히 같은 지표로 재지 않게 합니다 | 없음 |
| D-23 | 02 의 2.2 를 "뒤 축은 앞 축의 순수 함수와 계약 타입을 import 할 수 있다(MAY)" 로 개정. 평가가 엔진의 `jaccard_idf`·`weighted_score`·`mmr_select` 를 그대로 씁니다 | 원문이 평가의 엔진 호출을 금지해 명세 4절과 충돌했습니다. 같은 계산을 두 벌 두면 한쪽이 조용히 어긋납니다 | 없음 |
| D-24 | Interleaving 은 요청별 승패를 유저별 다수결로 모아 유저 단위 승률과 정확 양측 이항 검정(p = 0.5)을 냅니다. 동점 요청과 동점 유저는 빼고 셉니다 | 명세 6.2 의 "유저 단위 승률" 을 구체화한 것입니다 | 유저당 요청이 많아져 다수결이 정보를 버리면 요청 단위 가중으로 |
| D-25 | 리포트 조립은 `evaluation/report.py` 모듈이고 `scripts/reco_eval.py run` 은 인자만 넘깁니다. 평가 디렉터리는 11개 파일로 02 의 5.1 검토 문턱(8)을 넘습니다 | 리포트 스키마 검사가 subprocess 없이 돌아야 합니다. 파일을 합치면 300줄을 넘겨 다른 문턱에 걸립니다 | 화면 브랜치가 리포트 모양을 바꾸자고 하면 그때 재검토 |
| D-26 | `EvalRecord.cuisine_unmet: bool = False` 를 더했습니다. export 가 `stage_trace` 의 rerank params 에서 채웁니다 | 명세 8절의 `cuisine_unmet_ratio` 를 기록만으로 잴 수 있어야 합니다. 기본값이 있어 이전 파일도 읽힙니다 | 없음 |
| D-27 | 오프폴리시의 `exposures_needed` 는 "노출 1건이 ESS 를 최대 1 올린다" 는 상한으로 역산합니다 | 가중치 분포를 가정하지 않는 가장 단순한 식입니다 | `usable=True` 가 나오기 시작하면 실제 분포로 |
| D-28 | export 의 가명화 salt 는 `config.eval_salt`(`EVAL_SALT`)이고 없으면 멈춥니다. 후기 salt 를 재사용하지 않습니다 | G-10 의 (b). 후기 작성자 가명과 유저 가명이 같은 키로 묶이지 않게 | 3인 합의가 (a) 면 기본값을 `review_salt` 로 |
| D-29 | 리포트의 위치 보정은 `--position-correct` 옵션이고 θ 는 세그먼트의 위치별 CTR 을 1위로 정규화한 값입니다 | 명세 5.4·D-15 | impression 이 `viewport` 로 바뀌면 기본으로 |
| D-30 | 제외 규칙에 `no_items`(노출 항목 없음)를 더하고 `include_simulated` 를 `exclusion_reason` 의 인자로 넣었습니다. `apply_exclusions` 가 유일한 재판정 경로입니다 | `candidates` 를 저장하지 않는 load_test 행이 분모에 섞였고, 리포트·스냅샷이 각자 제외를 다시 판정하면서 `--include-simulated` 가 다른 사유까지 지웠습니다 | 없음 |
| D-31 | 평가 파트의 SQL 은 `src/features/recommend/repository_eval.py` 에 둡니다(03 의 5절, 데이터 파트의 `repository_ingest.py` 선례). `export.py`·`quality.py` 는 순수 함수만 갖고 `scripts/reco_eval.py` 가 둘을 잇습니다 | 축 안에 SQL 을 두면 규칙 위반이고 DB 없이 검사할 수 없습니다 | 없음 |
| D-32 | E-10(실기록 왕복과 개인정보 부재)은 pytest 가 아니라 `make eval-smoke` 가 돌립니다 | 루트 conftest 의 autouse 픽스처가 통합 검사에도 가짜 DB 환경을 세워 pytest 통합 검사는 항상 skip 됩니다. 이 저장소의 DB 통합 검사 선례도 `make` 입니다 | conftest 가 통합 검사를 예외로 두면 pytest 로 |
| D-33 | 고아 `request_id` 비율은 목록 이벤트(impression·click·save·dismiss·rating)만, 최근 1일로 셉니다 | cook·search·unsave 는 목록 밖에서도 일어나 `request_id` 가 없는 것이 정상이라 전부 세면 경보가 항상 울립니다 | 백엔드가 cook 에도 `request_id` 를 붙이기로 하면 |
| D-34 | 검출력은 판정에 쓴 대응 부트스트랩의 산포(`se × √n`)로 냅니다. 손으로 따로 낸 표준편차를 쓰지 않습니다 | 두 통계량이 다르면 "필요 유저 수" 가 실제 판정과 어긋납니다 | 없음 |
| D-35 | random baseline 의 시드는 `seed:request_id` 로 기록마다 다릅니다 | 시드 하나로 만들면 모든 기록이 같은 순열을 받아 무작위가 아닙니다 | 없음 |
| D-22 | `gains` 는 노출된 레시피만 담고 이벤트가 없으면 0.0 입니다. `request_id` 불일치 제외는 export 의 조인이 맡습니다 | `EvalEvent` 에 `request_id` 가 없으므로 라벨 단계에서는 가를 수 없습니다 | 없음 |

---

## 3. 가정 (A)

| ID | 가정 | 어긋나면 고칠 곳 |
|---|---|---|
| A-01 | `recommendation_log.candidates` JSONB 에 노출분의 `propensity`, `is_exploration`, `final_rank`, `features` 17개가 들어 있습니다(`repository.merge_served_detail`) | `record.py` 의 `items` 변환, `export` |
| A-02 | `candidates` 에 미노출 후보의 `features` 가 들어 있습니다. mock 모드에서는 절단 폭이 작을 수 있습니다 | `estimator.py` 의 미지원 비율이 커집니다. 진단이 잡습니다 |
| A-03 | `event_log.position` 은 `final_rank` 와 같은 1-base 입니다 | `record.py` 불변식 4번 |
| A-04 | `stage_trace.totals.user_mode` 가 모든 행에 있습니다 | 없으면 `user_mode=onboarding` 으로 두고 `alerts` |
| A-05 | `app_user.is_simulated` 로 가상 유저를 가를 수 있고 `d-` 접두어가 개발 트래픽을 가릅니다 | `record.py` 제외 규칙 |
| A-06 | 확인됨(2026-09-15). `engine/score.py` 의 `weighted_score(features, weights)` 가 순수 함수입니다 | `estimator.py`. 서명이 바뀌면 E-06 |
| A-07 | `recipe_feature.all_ids` 와 `ingredient_idf` 를 `export` 가 조인할 수 있습니다 | G-02 |
| A-08 | 정정(2026-09-17). `engine/feature.py` 에 `jaccard_idf` 가 함수로 있고 `mmr_select` 의 인라인 계산과 같은 식·같은 `DEFAULT_IDF` 폴백입니다. `metrics.ild` 가 그 함수를 import 하며 E-13 이 `mmr_select` 결과와 대조합니다 | 없음. G-08 해소 |
| A-09 | `recommendation_log.policies` 와 `RankedItem.team` 이 Interleaving 요청에서 채워집니다 | `stats.py` 의 Interleaving. 비어 있으면 `interleaving=null` |
| A-10 | `data/*` 는 `.gitignore` 에 이미 있습니다(확인됨, 2026-09-17) | 없음 |
| A-11 | 확인됨(2026-09-17). `RankedItem.is_cuisine_slot` 은 결정적 슬롯이라 `propensity = 1.0` 이고 탐색 슬롯과 배타입니다. 추적에 `n_cuisine`, `cuisine_unmet` 이 실립니다(`service.py`) | `record.py` 불변식, 명세 5.3·5.5·8절 |
| A-12 | 라우터가 엔진에 연결되기 전의 `recommendation_log` 는 시험용 응답입니다(`../recommend_engine_how_it_works.md` 11절). 연결일 이후 행만 평가에 씁니다 | `export --since` 의 하한. 연결일은 파트 B 의 DB 전환 점검표에서 받습니다 |

---

## 4. 접점 (I)

| ID | 방향 | 계약 | 비고 |
|---|---|---|---|
| I-01 | DB → `export` | `recommendation_log`, `event_log`, `recipe_feature.all_ids`, `feature_stats` 또는 코퍼스 IDF, `app_user.is_simulated` | 읽기만 합니다. `user_id` 는 가명화해 내보냅니다 |
| I-02 | 엔진 → `estimator`·`metrics` | `engine/score.py` 의 `weighted_score`, `engine/rerank.py` 의 `mmr_select` 와 IDF 가중 Jaccard, `enums.DEFAULT_WEIGHTS` | 서명 변경 시 E-06, E-13 |
| I-03 | 계약 → `record` | `stage.RankedItem`, `stage.ScoredCandidate`, `schema.RecommendationLogOut`, `enums.EventType`, `enums.LABEL_WEIGHT` | 계약이 바뀌면 `label_version` |
| I-04 | `quality` → HTTP | `GET /v1/health` 의 `HealthOut` | 카운터 키 이름이 바뀌면 `alerts` |
| I-05 | 산출물 → 화면 | `data/quality/*.jsonl`, `data/eval/report_*.json` | 명세 13절 |
| I-06 | 백엔드 → 로그 | 이벤트의 `request_id`, `position`, Interleaving 시 `team` 귀속 | 명세 8절의 고아 비율이 감시 |

---

## 5. 계획 항목 (T)

| ID | PR | 내용 | 상태 | 선행 |
|---|---|---|---|---|
| T-01 | 1 | `record.py`(불변식), `labels.py`, `metrics.py`, `synth.py`, `scripts/reco_eval.py synth`, `test_eval_record.py`, `test_eval_labels.py`, `test_eval_metrics.py`, `test_eval_synth.py`, mock 스크립트 함수 이관 | 완료(2026-09-17). 미커밋 | 없음 |
| T-02 | 2 | `stats.py`: 순열 baseline 3종, 유저 단위 부트스트랩·대응 비교, Interleaving, 검출력, 판정. `test_eval_stats.py` 12건 | 완료(2026-09-18). 미커밋 | 없음 |
| T-03 | 3 | `estimator.py`: SNIPS, ESS·미지원·경로별 진단, 가중치 교체 목표 정책. `test_eval_estimator.py` 7건 | 완료(2026-09-18). 미커밋 | 없음 |
| T-04 | 4 | `report.py`(D-25), `run` 서브커맨드, 리포트 JSON, `make eval`. `test_eval_report.py` 8건 | 완료(2026-09-18). 미커밋 | 없음 |
| T-05 | 5 | `quality.py`, `quality` 서브커맨드, `cuisine_unmet` 필드(D-26). `test_eval_quality.py` 6건, `test_eval_threshold.py` 4건 | 완료(2026-09-18). 미커밋. DB 항목 3종은 실 DB 에서 미검증 | 없음 |
| T-06 | 6 | `repository_eval.py`(SQL, D-31), `export.py`(순수 `build_record`·`build_records`), `export` 서브커맨드, `EVAL_SALT`(D-28), `make eval-smoke SINCE=`(E-10, D-32), `test_eval_export.py` 11건 | 코드 완료(2026-09-18). 미커밋. E-10 은 실 DB 전까지 미실행 | 파트 B 의 DB 전환, G-02, G-12 |
| T-07 | 문서 | `docs/README.md` 등록 | 완료 | 없음 |
| T-08 | 문서 | 결정 문서(라벨 정의, 스냅샷 저장, 판정 규칙) | 선행 대기 | G-01, G-03 |
| T-09 | 문서 | 명세 1.1.0: 리뷰 반영과 파이프라인 | 완료(2026-09-17) | 없음 |
| T-10 | 문서 | 구축 계획 `03_recommend_evaluation_build_plan.md` 1.0.0, 문서 3건 번호 접두어 | 완료(2026-09-17) | 없음 |
| T-11 | 문서 | 명세 1.2.0: 구현이 정한 것을 되돌려 적음(명세 1.5 의 표) | 완료(2026-09-18) | 없음 |
| T-12 | 문서 | 구축 계획 1.2.0: 현재 위치, 마일스톤 상태, PR 표, 남은 위험 | 완료(2026-09-18) | 없음 |
| T-13 | 문서 | 타 파트 요청 초안 `04_recommend_evaluation_requests.md` 1.0.0. 15건 | 완료(2026-09-18). 전달은 김민경 | 없음 |

---

## 6. 타 파트 안건 (G)

| ID | 안건 | 대상 | 선택지 | C 의 제안 | 막히는 것 |
|---|---|---|---|---|---|
| G-01 | 정답 라벨 정의(명세 D-01)의 팀 확정 | 파트 B | (a) `LABEL_WEIGHT` 등급형 (b) 이진 | (a). 계약에 이미 있는 값입니다 | T-08 |
| G-02 | `export` 가 `recipe_feature.all_ids` 와 IDF 를 조인하는 것 | 파트 A | (a) 조인 허용 (b) `recommendation_log` 에 저장 | (a). 로그를 키우지 않습니다 | T-06 |
| G-03 | 품질 스냅샷 테이블 신설 여부 | 3인 | (a) `reco_quality_snapshot` 신설 (b) JSONL 유지 | 지금은 (b). 화면이 붙고 추이가 3주 이상 쌓이면 (a) | T-08 |
| G-04 | `evaluation/threshold.py` 인수(파트 B 안건 G-17) | 파트 A, B | (a) C 인수 (b) `ingest/` 로 이동 (c) 삭제 | (a) 또는 (b). 재료 정규화 도구라 (b) 가 도메인에 맞습니다 | 없음 |
| G-05 | 배치 미처리 레시피의 맛 벡터가 0 인지 null 인지(09-11 보고서 Q5) | 파트 A | | 확인 요청 | `flavor_all_zero_ratio` 해석 |
| G-06 | 카탈로그 커버리지 분모 | 파트 A | 46,353 또는 46,552 | 도구는 인자로 받고 값은 A 가 정합니다 | 없음 |
| G-07 | `Makefile` 에 `eval`, `eval-smoke` 추가 | 공용 | | 트랙 C 명령만 넣습니다 | T-04, T-06 |
| G-08 | 해소(2026-09-17). `engine/feature.py` 의 `jaccard_idf` 가 이미 그 함수입니다(A-08 정정). 요청하지 않습니다 | 파트 B | | | 없음 |
| G-09 | 정정(2026-09-18). `team` 은 로그의 항목에 있고 이벤트는 `request_id`·`recipe_id` 로 이으므로 백엔드가 `team` 을 보낼 필요가 없습니다. 필요한 것은 목록 이벤트의 `request_id` 와, 한 목록에 같은 `recipe_id` 가 두 번 나오지 않는 것입니다. 원래 안건: 백엔드가 Interleaving 요청의 `team` 을 이벤트에 되돌리는 것 | 파트 B → 백엔드 | | 파트 B 의 B-16 전달 항목에 포함 요청 | 명세 6.2 |
| G-10 | export 파일 보존 기간 30일과 가명화 방식(`REVIEW_SALT` 재사용 여부) | 3인 | (a) 같은 salt (b) 평가 전용 salt | (b). 후기 작성자 가명과 유저 가명이 같은 키로 묶이지 않게 | T-06 |
| G-11 | Thompson 픽의 노출 확률 귀속(파트 B 안건 G-28, F-65) | 파트 A, B | B 의 (a) 조건부 확률 분배 (b) `explore_source` 구분만 (c) 그대로 | (a). 확률의 정의가 IPS 의 분모이므로 C 는 (a) 를 지지합니다. 반영 전에는 `thompson` 경로 추정치를 인용하지 않습니다(명세 7.2) | 없음. 값이 작게 어긋날 뿐 |
| G-12 | 라우터·엔진 연결일을 DB 전환 점검표에 기록해 `export --since` 하한으로 쓰는 것 | 파트 B | | 연결 커밋과 시각을 M 항목에 남겨 달라고 요청 | T-06 |

---

## 7. 세션 기록

### 7.1 2026-09-15 · 설계 1.0.0

- Notion 「Pantry-Mate AI Track B 최종 해설서」, 「추천 시스템 설계 파트 분할」, 「민경 시점으로 다시 읽기」, 「설계 검토 보고서(트랙 C 관점)」와 `docs/` 전체, 계약 코드(`enums.py`, `stage.py`, `schema.py`, `deploy/init/02_schema.sql`)를 읽었습니다.
- 09-11 보고서의 Q1~Q6 중 Q2, Q4, Q6 은 코드로 해결되었음을 확인했습니다. Q1 은 명세 D-02 로 결정했고 Q5 는 G-05 로 남겼습니다.
- 인터뷰로 결정 8건(범위, 라벨, 탐색 슬롯, 게이트, 입력 계약, 오프폴리시 깊이, 이벤트 귀속, 스냅샷 저장)을 확정하고 명세를 썼습니다.
- 코드는 작성하지 않았습니다. 검사는 04 의 2.3 형식 점검만 실행했습니다.

### 7.2 2026-09-17 · 리뷰 반영과 파이프라인, 1.1.0

- AI 엔지니어링 관점 리뷰에서 blocker 4건(Recall@20 퇴화, 후보 풀 baseline 게이트, Interleaving 누락, `model_version` 그룹 키), major 6건(위치 편향, ILD 정의, simulated 제외 기본값, 목표 정책의 Stage 3, 줄 단위 불변식, IPS 가정 명시), nit 8건을 냈고 명세 1.4 의 표대로 반영했습니다.
- 파이프라인(명세 3절)을 추가했습니다. 단계 8개, 계기 6종, 소유 경계.
- 개인정보 처리(명세 14절)를 추가했습니다. 가명화, 필드 제거, 보존 기간.
- 문서 폴더와 파일명을 `docs/recommend/evaluation/recommend_evaluation_*.md` 로 바꿨습니다(D-17).
- 코드는 작성하지 않았습니다. 검사는 04 의 2.3 형식 점검만 실행했습니다.

### 7.3 2026-09-17 · `main` 병합과 계약 대조

- `origin/main` 26커밋을 fast-forward 로 받았습니다(PR #9, #11 병합분). `docs/README.md` 는 main 의 1.10.0 위에 평가 문서 2행을 다시 얹어 1.11.0 으로 올렸습니다.
- 병합으로 들어온 계약 변경을 명세에 반영했습니다. `is_cuisine_slot`(불변식·슬롯 변형·목록 지표·스냅샷), 취향 출처 보조 세그먼트, `explore_source` 별 오프폴리시 진단, `export --since` 하한. 가정 A-11·A-12 와 안건 G-11·G-12 를 추가했습니다.
- 코드는 작성하지 않았습니다. 검사는 04 의 2.3 형식 점검만 실행했습니다.

### 7.4 2026-09-17 · 구축 계획과 오프라인 코어 1단계

- 구축 계획 `03_recommend_evaluation_build_plan.md` 를 쓰고 문서 3건에 번호 접두어를 붙였습니다(T-10, D-17 갱신).
- TDD 로 `record.py`(모델·불변식 5종·제외 3종·JSONL 읽기쓰기), `labels.py`(gain·유저 단위 조리), `metrics.py`(nDCG·Recall·유저 단위 Recall·ILD·커버리지·위치별 CTR·지연 백분위·탐색 위치), `synth.py`(계획 4.3 의 항목 전부 심음), `scripts/reco_eval.py synth` 를 만들었습니다. 검사 54건. `scripts/eval_recommend_mock.py` 의 `intra_list_distance` 를 `metrics.py` import 로 바꿨습니다(D-09).
- 명세와 다르게 한 것 3건을 D-19~D-21 로 남겼습니다. E-07 의 "Recall@10 이 hit-rate 의 CI 안" 은 K = top_k 에서 정의상 1.0 이라 대신 "전체 양성 비율이 hit-rate 안, 위치별 CTR 이 심은 확률 안" 으로 검사합니다.
- 검증: 4종 명령 종료 코드 0(1절). 커밋하지 않았습니다.

### 7.5 2026-09-18 · 커밋, 규칙 개정, 통계

- 오프라인 코어(677c788), PR 라인 상한 제거(67c4e07), 문서 번호 접두어와 구축 계획(15a59ec)을 커밋·푸시했습니다. 첫 커밋에 문서 2건의 이름 변경(내용 없음)이 함께 실렸습니다. `git mv` 로 인덱스에 올라 있던 것을 확인하지 않은 실수이며 그대로 두기로 했습니다.
- 코드 리뷰 10건 가운데 9건을 반영했습니다(접두어 중복, 동어반복 검사, 탐색 경로 교대 기준, 파싱 빠른 경로, 재료 캐시, 픽스처 정리, 라벨 창 주석). 남은 1건이 02 의 2.2 와 명세 4절의 충돌이며 D-23 으로 02 를 개정했습니다.
- TDD 로 `stats.py` 를 만들었습니다. 순열 baseline(popularity·random·coverage), 유저 단위 부트스트랩과 대응 비교, Interleaving(D-24), 검출력(필요 유저 수·최소 검출 효과), 판정. 합성 기록에 위치 감쇠를 심으면 서빙 순서가 coverage baseline 을 이겨 "통과" 가 나오는 것을 검사로 확인했습니다.
- 검증: 4종 명령 종료 코드 0(1절). 02 개정과 통계는 커밋하지 않았습니다.

### 7.6 2026-09-18 · 추정기, 리포트, 스냅샷, 내보내기

- TDD 로 네 단계를 만들었습니다. `estimator.py`(SNIPS·ESS·미지원 비율·경로별 분리·가중치 교체 정책, D-27), `report.py` 와 `run`(명세 9.2 모양, 그룹·세그먼트·baseline·검출력·판정·Interleaving·오프폴리시, D-25·D-29), `quality.py` 와 `quality`(기대 None 패턴 경보, `/v1/health` 원값, DB 항목은 `--db` 일 때만, D-26), `export.py` 와 `export`(가명화·화이트리스트 필드·`--since` 필수, D-28). `Makefile` 에 `eval`·`eval-smoke`.
- 명세와 다르게 한 것: 리포트 모듈 신설(D-25), `cuisine_unmet` 필드(D-26). 취향 출처 보조 세그먼트(명세 5.6)는 기록에 그 필드가 없어 넣지 않았습니다. 명세 4절 개정 때 함께 정합니다.
- 검증: 4종 명령 종료 코드 0, 단위 416건. `make eval` 종료 코드 0. E-10 은 실 DB 가 없어 skip 이며 `quality --db` 의 SQL 3종도 실 DB 에서 돌리지 못했습니다.
- 커밋하지 않았습니다.

### 7.7 2026-09-18 · 2차 코드 리뷰와 리팩터링

- 8각도 리뷰에서 10건을 보고하고 전부 반영했습니다. 정확성 7건(양성 없는 그룹의 ZeroDivisionError, 후보 없는 행의 분모 오염, random baseline 동일 순열, `--since` naive 시각, `user_mode` null, 고아 경보 상시 발화, 미노출 후보 재료 누락), 검사 1건(항상 skip 되던 통합 검사), 규칙 1건(SQL 위치), 기록 0건의 오프폴리시 `usable=True`. 결정 D-30~D-35.
- 함께 정리한 것: 부트스트랩이 유저별 (합, 건수)만 더하고(결과 동일, 리샘플당 O(유저 수)), 라벨을 기록당 한 번 계산해 통계·Interleaving·추정기에 넘기며, `Estimate` 에 `se` 를 더해 검출력이 판정과 같은 산포를 씁니다. `RunOptions.now` 삭제, `asdict` 로 통일, 백분위는 `metrics.nearest_rank` 하나, 리포트 `warnings` 와 `stamp.catalog_size_source` 추가, `build_record` 인자 5개(`ExportOptions`), `make eval-smoke` 의 경로 따옴표.
- 반영하지 않은 것: `MIN_USERS`·`RESAMPLES`·`TARGET_EFFECT`·`ESS_MIN_SHARE`·`HEALTH_TIMEOUT_SEC` 를 `config.py` 로 옮기는 것(03 의 2절). 사전 등록 지표의 판정 문턱은 환경마다 바꾸는 값이 아니라 분석 규약이며 엔진도 `policy.py` 에 손잡이를 둡니다. `pseudonymize` 를 `scripts/reco/load_recipes.py` 의 `author_hash` 와 합치는 것은 스크립트를 `src` 로 올리는 별도 작업입니다. `cuisine_unmet` 필드 추가에 버전을 올리지 않은 것은 이전 파일이 기본값으로 읽히기 때문입니다.
- 검증: 4종 명령 종료 코드 0, 단위 425건, 커버리지 93.27%. `make eval` 종료 코드 0. 커밋하지 않았습니다.

### 7.8 2026-09-18 · 명세 개정, 구축 계획 갱신, 요청 초안

- 명세를 1.2.0 으로 올렸습니다(T-11). 모듈 배치와 의존, 제외 규칙 `no_items`, `cuisine_unmet` 필드, random baseline, Interleaving 의 다수결, 검출력 산식, 고아 비율의 범위, 리포트 JSON 의 추가 키, E-04·E-05·E-07·E-10·E-11 의 실제 검사 방식, `EVAL_SALT`. 바뀐 것은 명세 1.5 의 표에 모았습니다.
- 구축 계획을 1.2.0 으로 올렸습니다(T-12). 현재 위치, 마일스톤 상태 열, PR 표의 실제 파일과 검사, 해소된 막힌 경로 삭제, 남은 위험 2건(실 DB 에서 못 돌린 SQL, `no_items` 행) 추가.
- 타 파트 요청 초안을 썼습니다(T-13). 파트 A 4건, 파트 B 6건, 백엔드 2건, 3인 합의 3건. G-09 는 코드와 대조해 정정했습니다.
- 검증: 04 의 2.3 형식 점검 4종 출력 없음. 코드는 바꾸지 않았습니다.
