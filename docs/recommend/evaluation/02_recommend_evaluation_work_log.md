# 추천 평가 시스템 작업 기록 (에이전트용)

**정하는 것**: `01_recommend_evaluation_design.md` 의 실행 이력. 상태, 결정(D), 가정(A), 접점(I), 계획 항목(T), 타 파트 안건(G), 세션 기록

**적용 대상**: 파트 C 평가 시스템을 이어서 작업하는 AI 코딩 에이전트와 김민경

**버전**: 1.2.0 · **최종 수정**: 2026-09-17 · **작성자**: 김민경

---

## 1. 상태 요약

| 키 | 값 |
|---|---|
| 정본 | 이 파일. 설계 명세 `01_recommend_evaluation_design.md` 1.1.0 이 사람용 정본이며 어긋나면 명세가 이깁니다 |
| 브랜치 | `feat/recommend-eval-metrics`. 2026-09-17 에 `origin/main`(PR #11 까지)을 fast-forward 로 받아 같은 지점입니다 |
| 단계 | 오프라인 코어 구현 중. `record.py`, `labels.py`, `metrics.py`, `synth.py` 와 `scripts/reco_eval.py synth`, 검사 4파일(54건) 완료. 통계·추정기·리포트·스냅샷·내보내기 미착수 |
| 검증 | 2026-09-17 `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit` 종료 코드 0. 단위 372건 통과, 커버리지 91.92%. `scikit-learn` 대조는 `uv sync --extra ml` 환경에서 실행 |
| 다음 행동 | T-02(`stats.py`: 순열 baseline, 유저 단위 부트스트랩, Interleaving, 판정). 픽스처는 `synth.generate` 를 씁니다 |
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
| T-02 | 2 | `stats.py`: 순열 baseline, 부트스트랩, Interleaving, 판정. `test_eval_stats.py` | 미착수 | T-01 |
| T-03 | 3 | `estimator.py`, `test_eval_estimator.py` | 미착수 | T-01 |
| T-04 | 4 | `run` 서브커맨드, 리포트 JSON, `make eval` | 미착수 | T-02, T-03 |
| T-05 | 5 | `quality.py`, `quality` 서브커맨드, `test_eval_quality.py`, `test_eval_threshold.py` | 미착수 | T-04 |
| T-06 | 6 | `export` 서브커맨드(가명화·필드 제거), `tests/integration/test_eval_export.py`, `make eval-smoke` | 선행 대기 | 파트 B 의 DB 전환, G-02 |
| T-07 | 문서 | `docs/README.md` 등록 | 완료 | 없음 |
| T-08 | 문서 | 결정 문서(라벨 정의, 스냅샷 저장, 판정 규칙) | 선행 대기 | G-01, G-03 |
| T-09 | 문서 | 명세 1.1.0: 리뷰 반영과 파이프라인 | 완료(2026-09-17) | 없음 |
| T-10 | 문서 | 구축 계획 `03_recommend_evaluation_build_plan.md` 1.0.0, 문서 3건 번호 접두어 | 완료(2026-09-17). 미커밋 | 없음 |

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
| G-09 | 백엔드가 Interleaving 요청의 `team` 을 이벤트에 되돌리는 것 | 파트 B → 백엔드 | | 파트 B 의 B-16 전달 항목에 포함 요청 | 명세 6.2 |
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
