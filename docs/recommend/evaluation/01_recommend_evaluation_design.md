# 추천 평가 시스템 설계 명세 (Track C)

**정하는 것**: 추천 로그를 평가용 기록으로 바꾸고, 순위 지표·정책 비교·오프폴리시 추정·품질 스냅샷을 내는 평가 관측 파이프라인의 데이터 계약, 단계, 모듈 배치, 지표 정의, 판정 규칙, 검증 계획, 개인정보 처리. 화면 4종은 입력 계약까지만 정합니다

**적용 대상**: 파트 C(관측·평가) 구현자와 AI 코딩 에이전트. 파트 A·B 와 백엔드는 2절, 3.4, 13절의 접점만 봅니다

**버전**: 1.1.1 · **최종 수정**: 2026-09-17 · **작성자**: 김민경

---

## 1. 목적과 경계

### 1.1 한 문장 정의

평가 관측 파이프라인은 `recommendation_log` 와 `event_log` 를 **요청 1건 = JSONL 1줄**로 바꾼 뒤, 단계마다 파일만 주고받으며 순위 지표, 정책 비교, 오프폴리시 추정, 품질 스냅샷을 내는 순수 파이썬 계층입니다. 관측 화면은 마지막 산출 파일만 읽고 계산을 하지 않습니다. 엔진이 무엇을 기록하고 왜 남기는지는 `../recommend_engine_how_it_works.md` 7절과 11절에 있습니다.

### 1.2 경계

| 구분 | 이 명세가 정하는 것 | 정하지 않는 것 |
|---|---|---|
| 숫자를 만드는 쪽 | 평가 라이브러리, 로그 변환, 합성 로그, 품질 스냅샷 기록기 | 엔진의 점수 산식과 가중치(파트 B) |
| 숫자를 보여주는 쪽 | 화면 4종이 읽을 입력 계약(13절) | 화면 구현. 별도 브랜치 |
| 데이터 | JSONL 형식, 불변식, 제외 규칙, 가명화 | DB 테이블 구조. 변경은 3인 합의 |

이 명세는 트랙 C 작업 분해의 C-2, C-3, C-4, C-5, C-11, C-12 에 해당합니다. C-1, C-6, C-7, C-8, C-9, C-10, C-13, C-14 는 13절의 입력 계약 위에서 다음 브랜치가 맡습니다.

### 1.3 설계 원칙

1. **정답과 계약은 코드가 정합니다.** 라벨 가중치는 `enums.LABEL_WEIGHT`, 노출 항목은 `stage.RankedItem`, 다양성은 엔진의 IDF 가중 유사도를 그대로 씁니다. 평가 계층이 같은 것을 다시 정의하지 않습니다.
2. **이 로그로 잴 수 있는 것은 순서의 품질입니다.** 정답은 노출된 항목의 반응에서만 오므로 노출되지 않은 후보의 품질은 오프라인으로 알 수 없습니다. 리포트 첫 줄에 이 한계를 적습니다.
3. **통계 단위는 유저입니다.** 한 유저의 요청들은 독립이 아니므로 신뢰구간과 대응 비교는 유저 단위로 리샘플합니다.
4. **판정은 사전 등록 지표 하나로만 합니다.** 세그먼트와 지표를 전부 보고하되 통과·보류·실패는 6.3 의 지표 하나로 정합니다.
5. **쓸 수 없는 결과는 예외가 아니라 결과입니다.** 오프폴리시 추정은 지원 진단을 추정치와 분리할 수 없게 돌려주고, 표본 부족은 "보류"로 냅니다.
6. **조용히 건너뛰지 않습니다.** 불변식 위반 줄은 즉시 실패하고, 제외 규칙에 걸린 줄은 세어서 보고하며, 모든 리포트에 버전 스탬프를 찍습니다.
7. **평가 데이터도 개인정보입니다.** 평가 코드는 원본 `user_id` 와 냉장고 내용을 보지 않습니다(14절).

### 1.4 1.0.0 에서 바뀐 것

| 항목 | 1.0.0 | 1.1.0 | 이유 |
|---|---|---|---|
| Recall | @20 주지표 | @5·@10 만. @20 은 정의상 1.0 이라 계산하지 않음. 유저 단위 Recall 을 별도 계열로 추가 | 정답이 노출분에서만 오므로 K = top_k 에서 퇴화 |
| baseline | 후보 풀 재정렬 | 노출 목록의 순열 3종(popularity, random, coverage 단독) | 후보 풀 재정렬은 라벨 부재를 검정하는 게이트가 됨 |
| 정책 비교 | 오프라인만 | Team-Draft Interleaving 승률 추가. 정책 채택의 유일한 근거 | 계약에 `team`·`policies` 가 있고 소표본에서 유일하게 검출력이 있음 |
| 그룹 키 | `user_mode` | `model_version` 필수, `user_mode` 는 하위 세그먼트 | 정책 혼합 방지 |
| 위치 편향 | 없음 | 위치별 CTR 곡선 필수, examination 보정 옵션 | 측정이 있어야 보정 근거가 생김 |
| ILD | `all_ids` Jaccard | 엔진의 IDF 가중 Jaccard | 최적화 대상과 측정 대상 일치 |
| 불변식 | 통합 테스트만 | 줄 단위 검증기 | 조용한 실패 차단 |
| 목표 정책 | Stage 2 재채점만 | MMR 까지 포함 | 서빙 결과의 가치를 추정 |
| 개인정보 | 없음 | 가명화, 필드 제거, 보존 기간 | 평가 파일의 개인정보 |
| 판정 | 통과·실패 | 통과·보류·실패 | 표본 부족을 통과로 읽지 않게 |
| 파이프라인 | 없음 | 3절 | 단계·계기·소유를 명시 |
| 음식 유형 칸 | 없음 | 불변식, 슬롯 변형, 목록 지표, 스냅샷에 `is_cuisine_slot` 반영 | 2026-09-17 `main` 병합으로 들어온 계약(`../../decisions/2026-09-15_cuisine_choice_as_slots_not_weight.md`) |

---

## 2. 데이터 계약

### 2.1 평가 기록 `EvalRecord`

JSONL 한 줄이 추천 요청 1건입니다. 기존 계약 타입을 그대로 담아 새 스키마를 최소화합니다.

| 필드 | 타입 | 출처 |
|---|---|---|
| `request_id`, `model_version`, `config_hash`, `warm_alpha`, `stats_version`, `created_at` | `schema.RecommendationLogOut` 과 같은 타입 | `recommendation_log` |
| `user_hash` | `str` | `user_id` 의 HMAC 가명(14절). 원본 `user_id` 는 싣지 않습니다 |
| `session_prefix` | `"c" \| "g" \| "d" \| None` | `session_id` 의 접두어만 |
| `user_mode`, `degraded`, `total_latency_ms` | `stage.UserMode`, `bool`, `int` | 같은 행의 `stage_trace.totals` |
| `items` | `list[stage.RankedItem]` | `candidates` JSONB 중 노출분. `final_rank` 오름차순 |
| `candidates` | `list[stage.ScoredCandidate] \| None` | `export --with-candidates` 일 때만. 오프폴리시 목표 정책의 입력 |
| `policies` | `list[dict] \| None` | Interleaving 시 `[{team, model_version}]`. 단일 정책이면 None |
| `ingredients` | `dict[int, list[int]]` | 노출 레시피의 `recipe_feature.all_ids`. ILD 의 입력 |
| `ingredient_idf` | `dict[int, float]` | `feature_stats` 또는 코퍼스의 IDF. 파일 첫 줄의 헤더 레코드에 한 번만 |
| `events` | `list[EvalEvent]` | `event_log` 를 `request_id` 로 조인. 2.3 의 창 안 |
| `user_events` | `list[EvalEvent]` | 같은 유저의 `cook` 이벤트 전부(`request_id` 무관). 유저 단위 Recall 전용 |
| `is_simulated` | `bool` | `app_user.is_simulated` |
| `excluded_reason` | `str \| None` | 2.2 의 제외 규칙 |

`EvalEvent` 는 `recipe_id`, `event_type`(`enums.EventType`), `value`(`float | None`), `position`(`int | None`), `created_at` 입니다.

파일 첫 줄은 헤더 레코드이며 `label_version`, `metric_version`, `catalog_size`, `ingredient_idf`, `exported_at`, `source`(`db` 또는 `synth`)를 담습니다.

### 2.2 불변식과 제외 규칙

`record.py` 의 검증기가 줄마다 확인합니다. 위반은 줄 번호와 필드를 담아 즉시 실패하고, 제외는 `excluded_reason` 을 채우고 건수를 셉니다.

| 규칙 | 종류 | 처리 |
|---|---|---|
| `items` 의 `recipe_id` 가 `candidates` 에 포함(후보가 있을 때) | 불변식 | 즉시 실패 |
| `final_rank` 가 1 부터 연속 | 불변식 | 즉시 실패 |
| `is_exploration=True` 이면 `0 < propensity < 1`, 아니면 `propensity = 1.0` | 불변식 | 즉시 실패 |
| `is_cuisine_slot=True` 이면 `is_exploration=False` 이고 `propensity = 1.0`. 유형 칸은 결정적 슬롯입니다 | 불변식 | 즉시 실패 |
| `events.position` 이 있으면 같은 `recipe_id` 의 `final_rank` 와 일치 | 불변식 | 즉시 실패 |
| 파일 안의 `label_version`, `metric_version` 이 헤더와 일치 | 불변식 | 즉시 실패 |
| `session_prefix = "d"` | 제외 | `excluded_reason = d-session` |
| `is_simulated = True` | 제외 | `excluded_reason = simulated_user`. `run --include-simulated` 면 포함하고 리포트 상단에 표기 |
| `config_hash`, `warm_alpha`, `stats_version` 중 결측 | 제외 | `excluded_reason = not_reproducible` |

제외된 줄은 파일에서 지우지 않습니다. 제외 비율 자체가 품질 지표입니다.

### 2.3 정답 라벨

`labels.py` 가 `events` 를 `{recipe_id: gain}` 으로 바꿉니다.

| 규칙 | 내용 |
|---|---|
| 가중치 | `enums.LABEL_WEIGHT`. `rating` 은 `enums.rating_to_label(value)` |
| 합성 | 같은 레시피에 이벤트가 여럿이면 최댓값 |
| 절단 | 음수(`dismiss`, `unsave`)는 0 으로 절단합니다 |
| 창 | 추천 `created_at` 이후 14일 이내. 엔진의 최근 조리 감점 창과 같은 값 |
| 조인 | `request_id` 가 일치하는 이벤트만. `request_id` 가 없는 이벤트의 비율은 품질 지표로 보고 |
| impression | gain 0. 정답이 아니라 노출 사실 |
| 버전 | 이 표가 바뀌면 `label_version` 을 올립니다. 다른 `label_version` 의 리포트는 비교하지 않습니다 |

유저 단위 Recall(5.2)만 `user_events` 를 씁니다. 두 계열의 라벨을 합산하지 않습니다.

---

## 3. 파이프라인

### 3.1 흐름

```text
[엔진이 남긴 기록]                     [실데이터가 없을 때]
 recommendation_log, event_log          synth (합성 기록 생성기)
        │                                       │
        ▼                                       │
 ① export ── 가명화·필드 제거 ──▶  data/eval/<날짜>.jsonl  ◀──┘
                                                │
                                                ▼
                                         ② validate   불변식 위반이 있으면 여기서 멈춤
                                                │
                                                ▼
                                         ③ label      events → {recipe_id: gain}
                                                │
                              ┌─────────────────┼─────────────────┐
                              ▼                 ▼                 ▼
                         ④ score           ⑤ compare         ⑥ estimate
                              └─────────────────┼─────────────────┘
                                                ▼
                                         ⑦ report ──▶ data/eval/report_<날짜>.json

 [매일 따로]  data/eval/*.jsonl + /v1/health + (DB 선택)
                  ──▶ ⑧ quality ──▶ data/quality/<날짜>.jsonl

 [다음 브랜치의 화면]  report_*.json 과 quality/*.jsonl 만 읽음
```

단계 사이에는 파일만 오갑니다. 앞 단계가 실패하면 뒤 단계는 돌지 않습니다.

### 3.2 단계

| 단계 | 입력 | 출력 | 멈추는 조건 | 코드 |
|---|---|---|---|---|
| ① export | DB 4개 표 | 2.1 형식 JSONL | DB 접속 실패, 조인 결과 0건, `--since` 가 라우터·엔진 연결일보다 앞섬(그 전 로그는 시험용 응답) | `scripts/reco_eval.py export` |
| ① 대안 synth | 유저 수, 요청 수, 정답률, 위치 감쇠 계수, 시드 | 같은 형식 JSONL. `source = synth` | 없음 | `scripts/reco_eval.py synth` |
| ② validate | JSONL | 검증된 기록, 제외 건수표 | 2.2 의 불변식 위반 | `evaluation/record.py` |
| ③ label | 검증된 기록 | 기록별 gain, 정답 없는 기록 수 | 없음 | `evaluation/labels.py` |
| ④ score | 기록, gain | 5절 지표 | 없음 | `evaluation/metrics.py` |
| ⑤ compare | 기록, gain, ④ | 6절 비교와 판정 | 유저 수 부족이면 판정을 "보류"로 고정 | `evaluation/stats.py` |
| ⑥ estimate | 기록, gain, 목표 정책 | 7절 추정과 진단 | 없음. `usable=False` 는 결과 | `evaluation/estimator.py` |
| ⑦ report | ④⑤⑥ | JSON 과 표준출력 요약 | 없음 | `scripts/reco_eval.py run` |
| ⑧ quality | 최근 JSONL, `/v1/health`, DB(선택) | 스냅샷 1행 | `/v1/health` 실패는 `null` 로 남기고 계속 | `evaluation/quality.py` |

### 3.3 계기

| 계기 | 도는 것 | 목적 |
|---|---|---|
| PR 의 단위 테스트 | synth → ② ~ ⑦ | 코드 정합. DB 없음 |
| `make eval` | 지정 JSONL → ② ~ ⑦ | 현재 정책의 상태 |
| 매일 1회 | ⑧ | 기록 유실 감시. 추이가 쌓여야 이상이 보임 |
| DB 전환일 `make eval-smoke` | ① 1건 이상 → ② ~ ⑦ | 실기록이 끝까지 통과하는가 |
| 가중치·정책 변경 전 | ⑥ 목표 정책 지정 | 배포 전 추정. 대개 `usable=False` |
| 정책 채택 | 요청에 `interleave_with` → 누적 → ⑤ | 채택 판정의 유일한 근거 |

### 3.4 소유

| 경계 | 책임 | 파트 C 가 확인할 것 |
|---|---|---|
| 기록이 쌓이는 것 | 파트 B | `make eval-smoke` 통과 |
| 이벤트에 `request_id`·`position` 이 붙는 것 | 백엔드 | ⑧ 의 고아 이벤트 비율 0 |
| `all_ids`, `ingredient_idf`, `is_simulated` 조인 | 파트 A 와 합의 | 작업 기록 G-02, G-08 |
| IDF 가중 Jaccard 를 `rerank.py` 에서 함수로 분리 | 파트 B 와 합의 | 작업 기록 G-08 |
| ① ~ ⑧ | 파트 C | 단계별 테스트 |

---

## 4. 모듈 배치와 책임

```text
src/features/recommend/evaluation/
├── __init__.py     기존. re-export 하지 않습니다 (02 의 7.3)
├── threshold.py    기존. 임계값 캘리브레이션. 인수하고 검사를 붙입니다
├── record.py       EvalRecord · EvalEvent · 헤더 모델, JSONL 읽기·쓰기, 불변식과 제외 규칙
├── labels.py       events → gain. 유저 단위 라벨은 별도 함수
├── metrics.py      ndcg_at_k · recall_at_k · recall_user_level · ild · catalog_coverage · position_ctr · latency_percentiles · exploration_positions
├── stats.py        순열 baseline, 유저 단위 부트스트랩, 대응 비교, Interleaving 승률, 검출력, 판정
├── estimator.py    SNIPS 추정, 지원 진단, 가중치 교체 목표 정책
└── quality.py      품질 스냅샷 계산과 기록
scripts/reco_eval.py    서브커맨드 export · synth · run · quality
```

| 모듈 | 의존 | 의존하지 않는 것 |
|---|---|---|
| `record`, `labels`, `metrics`, `stats`, `estimator`, `quality` | `enums`, `stage`, `schema`, `engine/score.py` 와 `engine/rerank.py`(estimator·metrics), `numpy` | DB, HTTP, `repository`, `service` |
| `scripts/reco_eval.py` | 위 전부, `infra/db.py`(export 만), `httpx`(quality 의 카운터 수집만) | |

`scripts/eval_recommend_mock.py` 의 `jaccard`, `intra_list_distance` 는 `metrics.py` 로 옮기고 그 스크립트가 import 합니다. ILD 의 유사도는 `rerank.mmr_select` 안의 IDF 가중 Jaccard 를 함수로 꺼내 양쪽이 같은 함수를 씁니다(G-08). 같은 계산이 두 벌이면 한쪽이 조용히 어긋납니다.

디렉터리 파일 수는 8개로 02 의 5.1 상한 안입니다.

---

## 5. 지표 정의

### 5.1 순위 지표

K 는 인자이며 top_k 보다 작아야 합니다. 주지표는 nDCG@10 입니다.

$$
\text{DCG@K} = \sum_{i=1}^{K} \frac{g_i}{\log_2(i+1)}, \qquad
\text{nDCG@K} = \frac{\text{DCG@K}}{\text{IDCG@K}}
$$

$g_i$ 는 `final_rank` $i$ 번째 노출 레시피의 gain 이고 IDCG 는 gain 을 내림차순으로 놓았을 때의 DCG 입니다. 양성이 없는 요청은 분모에서 빼고 건수를 보고합니다.

$$
\text{Recall@K} = \frac{|\{i \le K : g_i > 0\}|}{|\{i \le \text{top\_k} : g_i > 0\}|}
$$

K 는 5 와 10 입니다. K = top_k 이면 정의상 1.0 이므로 계산하지 않고 리포트에 "측정 불가" 로 표기합니다.

### 5.2 유저 단위 Recall

$$
\text{Recall}_{\text{user}}@K = \frac{|\{r \in \text{served}_K : r \in \text{Cooked}_{14}\}|}{|\text{Cooked}_{14}|}
$$

$\text{Cooked}_{14}$ 는 `user_events` 에서 추천 후 14일 안에 그 유저가 조리한 레시피 전체입니다. `request_id` 없이 잇기 때문에 위치 정보가 없고 5.1 과 합산하지 않습니다. "만들 만한 요리를 후보에 넣었는가"에 대한 유일한 오프라인 근사이며 편향을 리포트에 명시합니다.

### 5.3 탐색 슬롯 변형

| 변형 | 정의 | 용도 |
|---|---|---|
| 전체 목록 (주지표) | 노출 목록 그대로. 탐색 항목 반응도 정답 | 사용자가 실제로 본 목록의 품질 |
| 탐색 제외 (보조) | 탐색 항목을 빼고 남은 항목의 순위를 1 부터 다시 매김 | 개인화 엔진의 회귀 감시 |
| 유형 칸 | `is_cuisine_slot` 항목은 결정적이므로 두 변형 모두에 남깁니다. 건수와 위치는 5.5 에서 따로 봅니다 | 온보딩 유형 반영 확인 |

### 5.4 위치 편향

| 항목 | 정의 |
|---|---|
| 위치별 CTR | 위치 $i$ 의 (gain > 0 인 노출 수) / (노출 수). 리포트 필수 |
| examination 보정 (옵션) | $\theta_i$ = 위치별 CTR 을 1위로 정규화. 보정 gain $g_i / \theta_i$ 로 nDCG 를 다시 계산. `run --position-correct` |

impression 의 `source` 가 `served` 인 동안 하위 순위는 실제 열람이 아닐 수 있으므로 보정은 근사입니다. `viewport` 로 바뀌면 두 시대를 나눠 보고합니다.

### 5.5 목록 지표

| 지표 | 정의 |
|---|---|
| ILD | 노출 목록의 모든 쌍에 대해 $1 - \text{Jaccard}_{\text{IDF}}(A_r, A_s)$ 의 평균. 엔진의 MMR 과 같은 함수 |
| 카탈로그 커버리지 | 기간 내 노출된 고유 `recipe_id` 수 / `catalog_size`. 기간, 요청 수, 유저 수를 병기 |
| 탐색 위치 분포 | `is_exploration` 항목의 `final_rank` 히스토그램 |
| 유형 칸 | `is_cuisine_slot` 항목 수와 `final_rank` 분포. `stage_trace` 의 `cuisine_unmet` 이 있는 요청 비율 |
| degraded 비율 | `degraded=True` 요청의 비율 |
| 지연시간 | `total_latency_ms` 의 p50, p95 |

### 5.6 그룹과 세그먼트

`model_version` 이 필수 그룹 키입니다. 한 그룹 안에 `config_hash` 가 둘 이상이면 경고를 내고 가장 많은 쪽만 집계합니다. 그 아래를 `user_mode` 3종과 전체로 나눕니다. `run --model-version` 으로 한 그룹만 볼 수 있습니다. `stage_trace` 에 취향 출처(고른 음식·척도·없음)가 있으면 보조 세그먼트로 함께 냅니다. 취향 없는 사용자는 탐색 비율이 달라 따로 봐야 합니다.

---

## 6. 정책 비교와 판정

### 6.1 순열 baseline

노출된 목록을 순서만 바꿔 같은 라벨로 지표를 냅니다. 모든 항목에 라벨이 있으므로 "같은 집합 안에서의 순서 품질"을 공정하게 비교합니다.

| baseline | 정렬 |
|---|---|
| popularity | `features["f_popularity"]` 내림차순. None 은 뒤 |
| random | 시드 고정 무작위 |
| coverage | `features["f_coverage"]` 내림차순. 재료 매칭 외의 피처가 무엇을 더하는지 가르는 기준 |

### 6.2 Interleaving

`policies` 가 있는 요청에서 gain > 0 인 항목의 `team` 으로 승패를 매깁니다. 유저 단위 승률과 양측 이항 검정(5%)을 냅니다. 정책 채택은 이 결과로만 하고, 오프라인 통과는 회귀 없음의 증거로 씁니다.

### 6.3 사전 등록 지표와 판정

사전 등록 지표는 전체 세그먼트 nDCG@10 의 "서빙 − coverage baseline" 차이입니다.

| 판정 | 조건 |
|---|---|
| 통과 | 불변식 위반 0건, 유저 수 20명 이상(예시값, 실제 데이터로 대체 필요), 대응 부트스트랩 95% CI 하한 > 0 |
| 보류 | 유저 수 미달이거나 CI 가 0 을 포함 |
| 실패 | CI 상한 < 0 이거나 8절의 `feature_none_ratio` 패턴 위반 |

세그먼트별·지표별 CI 는 전부 보고하되 판정에 쓰지 않습니다.

### 6.4 통계

| 항목 | 정의 |
|---|---|
| 리샘플 단위 | 유저. 유저를 복원 추출하고 그 유저의 요청 전부를 가져옵니다 |
| 리샘플 횟수 | 1,000회 (예시값, 실제 데이터로 대체 필요). 시드는 인자 |
| 신뢰구간 | 백분위 95% |
| 대응 비교 | 같은 유저 집합에서 두 순서의 지표 차이를 리샘플 |
| 검출력 | 관측 표준편차와 목표 효과 크기(기본 0.02, 예시값, 실제 데이터로 대체 필요)로 유저 수를 역산 |

---

## 7. 오프폴리시 추정

### 7.1 가정과 추정기

item-position IPS 가정을 둡니다. 보상은 항목별로 더해지고, `propensity` 는 그 항목이 그 슬롯에 노출될 주변 확률입니다. 탐색 항목이 다른 항목의 반응을 빼앗는 슬레이트 효과는 이 추정기가 보지 못하며 리포트에 적습니다.

$$
\hat{V}_{\text{SNIPS}}(\pi) = \frac{\sum_{n} \sum_{r \in \pi_K^{(n)}} \frac{\mathbb{1}[r \in \text{served}^{(n)}]}{p_r^{(n)}} \, g_r^{(n)}}{\sum_{n} \sum_{r \in \pi_K^{(n)}} \frac{\mathbb{1}[r \in \text{served}^{(n)}]}{p_r^{(n)}}}
$$

### 7.2 지원 진단

| 진단 | 정의 | `usable=False` 조건 |
|---|---|---|
| 확률 1 미만 비율 | 노출 항목 중 `propensity < 1.0` 인 비율 | 정보용 |
| 유효 표본 수 ESS | $(\sum w)^2 / \sum w^2$, $w = 1/p$ | 유저 수의 10% 미만 (예시값, 실제 데이터로 대체 필요) |
| 미지원 비율 | 목표 정책 상위 K 중 로그에서 노출되지 않은 항목의 비율 | 0 보다 크면 |
| 필요 탐색 노출 수 | ESS 기준을 넘기기 위해 더 필요한 확률 1 미만 노출 수의 역산 | 정보용 |
| 경로별 분리 | `explore_source`(`uniform`, `thompson`)별 ESS 와 추정치 | 정보용. Thompson 확률 귀속 문제(파트 B 안건 G-28)가 반영되기 전에는 `thompson` 경로의 추정치를 인용하지 않습니다 |

### 7.3 목표 정책

`Callable[[EvalRecord], list[int]]` 입니다. 기본 제공은 가중치 교체 정책 하나이며 `candidates` 의 저장된 17개 특성값에 새 가중치를 곱해 `engine/score.py` 의 `weighted_score` 로 다시 매기고, 로그의 `penalty` 를 곱한 뒤 `engine/rerank.py` 의 `mmr_select` 까지 태웁니다. 탐색 슬롯은 넣지 않으며 그 차이를 리포트에 적습니다. 디버거의 가중치 시뮬레이션(C-7)이 같은 함수를 씁니다.

---

## 8. 품질 스냅샷

`quality.py` 가 스냅샷 1행을 계산하고 `data/quality/YYYY-MM-DD.jsonl` 에 추가합니다. 테이블 승격은 작업 기록 G-03 뒤에 합니다.

| 항목 | 정의 | 경보 조건 |
|---|---|---|
| `orphan_request_ratio` | `request_id` 가 없는 이벤트 비율 | 0 보다 크면 백엔드 연동 결함 |
| `orphan_position_ratio` | `position` 이 없는 이벤트 비율 | 0 보다 크면 백엔드 연동 결함 |
| `feature_none_ratio` | 17개 특성별 None 비율 | 측정 불가 6개(`f_cuisine`, `f_dish_type`, `f_quality`, `f_content`, `f_ing_cf`, `f_group_pref`)는 100% 가 정상. 즉시 가능 5개(`f_coverage`, `f_missing`, `f_popularity`, `f_time_fit`, `f_skill_fit`)는 0% 가 정상 |
| `flavor_all_zero_ratio` | 맛 6축이 전부 0 인 레시피 비율 | 배치 미처리와 무미를 구분하지 못하는 신호 |
| `match_method_violation` | `recipe_ingredient.match_method` 가 `fuzzy` 또는 `embed` 인 행 수 | 0 보다 크면 규약 위반 |
| `health_counters` | `/v1/health` 카운터 원값과 수집 시각 | 누적하지 않습니다. 재시작으로 0 이 되는 것이 보여야 유실이 보입니다 |
| `degraded_ratio` | 기간 내 `degraded=True` 비율 | 추이 상승 |
| `excluded_counts` | `d-session`, `simulated_user`, `not_reproducible` 건수 | 정보용 |
| `exploration_positions` | 5.5 의 히스토그램 | 하위권 고정 |
| `cuisine_unmet_ratio` | 고른 유형이 목록에 없어 `cuisine_unmet` 이 찍힌 요청 비율 | 추이 상승. 유형별 레시피 부족 신호 |

DB 를 읽는 항목은 `quality --db` 가 있을 때만 계산하고 없으면 `null` 입니다. `null` 과 0 을 구분합니다.

---

## 9. 실행 인터페이스

### 9.1 서브커맨드

| 명령 | 주요 인자 | 출력 |
|---|---|---|
| `reco_eval.py export` | `--since --until --out --with-candidates` | 2.1 형식 JSONL. 14절의 가명화와 필드 제거 적용 |
| `reco_eval.py synth` | `--users --requests --hit-rate --position-decay --seed --out` | 같은 형식. `position-decay` 로 위치 감쇠 클릭을 심습니다 |
| `reco_eval.py run` | `--records --k 5 10 --catalog-size --model-version --include-simulated --position-correct --target-weights --seed --out` | 리포트 JSON, 표준출력 요약 |
| `reco_eval.py quality` | `--records --health-url --db --out` | 스냅샷 1행 추가 |

`Makefile` 에는 `eval`(synth 뒤 run)과 `eval-smoke`(export 뒤 run) 두 대상만 추가합니다.

### 9.2 리포트 JSON

```text
{
  "stamp": {"generated_at", "label_version", "metric_version", "seed", "input_sha256",
            "catalog_size", "include_simulated", "source"},
  "records": {"total", "excluded": {...}, "evaluated", "no_positive"},
  "limits": ["정답은 노출분에서만 옵니다", "impression 은 served 기준입니다"],
  "groups": {
    "<model_version>": {
      "config_hash": {"dominant", "others"},
      "segments": {
        "all" | "onboarding" | "blended" | "behavior": {
          "ndcg@10": {"full": {"mean", "ci95"}, "no_exploration": {...}, "position_corrected": {...}},
          "ndcg@5", "recall@5", "recall@10", "recall_user@10", "ild", "coverage",
          "latency_ms": {"p50", "p95"}, "degraded_ratio", "position_ctr": [...],
          "baseline": {"popularity", "random", "coverage"},
          "power": {"min_detectable", "users_needed_for": {"0.02": n}}
        }
      },
      "interleaving": {"pairs", "win_rate", "p_value"} | null,
      "verdict": {"metric": "ndcg@10 vs coverage", "diff": {"mean", "ci95"}, "status": "pass|hold|fail", "reason"}
    }
  },
  "off_policy": {"target", "snips", "ess", "unsupported_ratio", "exposures_needed", "usable"},
  "reference_targets": {"ndcg@10": 0.85, "ild": 0.95, "coverage": 0.15, "latency_p95_ms": 58}
}
```

---

## 10. 검증 계획

| ID | 대상 | 통과 기준 | 명령 |
|---|---|---|---|
| E-01 | nDCG·Recall 골든 | 손으로 계산한 케이스 6건 이상 일치. 빈 라벨, 동점 gain, K 가 목록보다 큼, 탐색 제외 시 순위 재부여 포함 | `uv run pytest tests/unit/recommend/test_eval_metrics.py` |
| E-02 | 외부 대조 | 무작위 100케이스에서 `sklearn.metrics.ndcg_score` 와 1e-9 이내 일치 | 같은 파일 |
| E-03 | 성질 | 정답을 위로 옮기면 nDCG 비감소. 무관 항목의 순열에 불변. 라벨을 섞으면 지표가 바뀜 | 같은 파일 |
| E-04 | 라벨 | 최댓값 합성, 음수 절단, 14일 창, `request_id` 불일치 제외, 유저 단위 라벨 분리 | `test_eval_labels.py` |
| E-05 | 통계 | 같은 순서 둘을 비교하면 CI 가 0 을 포함. 시드 고정 시 재현. 유저 20명 미만이면 보류 | `test_eval_stats.py` |
| E-06 | 추정기 | 로그 정책 자신을 목표로 주면 관측 평균과 일치. 미지원 항목이 있으면 `usable=False` | `test_eval_estimator.py` |
| E-07 | 왕복 | `synth --hit-rate 0.3` 뒤 `run` 의 Recall@10 이 0.3 의 CI 안. `position-decay` 를 주면 위치별 CTR 이 단조 감소 | `test_eval_record.py` |
| E-08 | 품질 | 기대 None 패턴과 어긋나면 `alerts`. DB 없으면 해당 항목 `null` | `test_eval_quality.py` |
| E-09 | threshold 인수 | `calibrate` 의 목표 정밀도 미달 시 `None`, Wilson 하한이 관측보다 작음 | `test_eval_threshold.py` |
| E-10 | export | 실 DB 에서 1건 이상 export 뒤 검증 통과. 원본 `user_id` 와 `pantry` 필드가 파일에 없음 | `tests/integration/test_eval_export.py` |
| E-11 | 불변식 | 2.2 의 불변식 5종 각각을 깨뜨린 줄이 줄 번호와 함께 즉시 실패 | `test_eval_record.py` |
| E-12 | Interleaving | `team` 이 섞인 합성 기록에서 승률과 p 값이 손 계산과 일치 | `test_eval_stats.py` |
| E-13 | ILD 정합 | `metrics.ild` 의 유사도가 `rerank.mmr_select` 의 인라인 계산과 같음 | `test_eval_metrics.py` |

단위 테스트는 DB·네트워크·유료 API 를 쓰지 않습니다(03 의 6절). E-10 만 통합 테스트입니다.

---

## 11. 오류 처리 원칙

| 상황 | 처리 |
|---|---|
| JSONL 줄이 검증에 실패 | 즉시 실패. 줄 번호와 필드를 예외에 담습니다 |
| 제외 규칙에 해당 | `excluded_reason` 을 채우고 지표에서 빼며 건수를 보고합니다 |
| 양성이 없는 요청 | nDCG·Recall 분모에서 빼고 건수를 보고합니다 |
| 유저 수 부족 | CI 를 내지 않고 판정을 "보류" 로 둡니다 |
| 오프폴리시 지원 부족 | `usable=False` 와 진단 수치를 돌려줍니다 |
| `/v1/health` 수집 실패 | `health_counters` 를 `null` 로 두고 `alerts` 에 사유를 적습니다 |
| 한 파일에 `model_version` 이 여럿 | 그룹별로 나눠 보고합니다. 한 그룹에 `config_hash` 가 여럿이면 경고 |

---

## 12. 단계와 PR 분할

가운데부터 만듭니다. ②③④가 나머지 단계의 입력 형식을 정하고 DB 없이 검증이 끝나기 때문입니다.

| 순서 | 내용 | 검증 | 선행 |
|---|---|---|---|
| 1 | `record.py`(불변식 포함), `labels.py`, `metrics.py`, `synth`, mock 스크립트의 함수 이관 | E-01~E-04, E-07, E-11, E-13 | G-08 |
| 2 | `stats.py`: 순열 baseline, 부트스트랩, Interleaving, 판정 | E-05, E-12 | 1 |
| 3 | `estimator.py` | E-06 | 1 |
| 4 | `run` 서브커맨드, 리포트 JSON, `make eval` | 리포트 스키마 검사 | 2, 3 |
| 5 | `quality.py`, `quality` 서브커맨드, `threshold.py` 검사 | E-08, E-09 | 4 |
| 6 | `export` 서브커맨드, `make eval-smoke` | E-10 | 파트 B 의 DB 전환, G-02 |

---

## 13. 화면 4종의 입력 계약

| 화면 | 입력 | 이 명세의 산출물 |
|---|---|---|
| 홈 | `GET /v1/health`, `data/quality/` 최신 행 | 8절 |
| 추천 디버거 | `GET /v1/recommendations/{request_id}`, 7.3 의 목표 정책 | 7.3 |
| 검수 큐 | `normalization_queue` 테이블 | 없음. 파트 A 의 테이블 |
| 데이터 품질 | `data/quality/*.jsonl`, `data/eval/report_*.json` | 8절, 9.2 |

화면은 계산하지 않습니다.

---

## 14. 개인정보와 보존

| 항목 | 처리 |
|---|---|
| `user_id` | `REVIEW_SALT` 와 같은 방식의 HMAC-SHA256 앞 16hex 로 가명화. 평가에는 구분만 필요합니다 |
| `pantry_snapshot`, `pantry_detail`, `allergy_snapshot` | 평가에 불필요. export 에서 제외 |
| `session_id` | 접두어만 남깁니다 |
| 파일 위치 | `data/eval/`, `data/quality/`. `.gitignore` 의 `data/*` 에 해당하며 커밋하지 않습니다 |
| 보존 | export JSONL 은 30일 뒤 삭제(예시값, 실제 데이터로 대체 필요). 리포트 JSON 과 스냅샷만 보관 |
| 검증 | E-10 이 파일에 원본 `user_id` 와 냉장고 필드가 없음을 확인합니다 |

---

## 15. 결정과 되돌릴 조건

| ID | 결정 | 되돌릴 조건 |
|---|---|---|
| D-01 | 정답은 `LABEL_WEIGHT` 등급형, 음수 절단, 14일 창, `request_id` 엄격 조인. 유저 단위 Recall 만 별도 조인 | 라벨 정의를 바꾸면 `label_version` 을 올리고 결정 문서를 씁니다 |
| D-02 | 탐색 슬롯은 전체 목록이 주지표, 탐색 제외가 보조지표 | 두 값의 추이가 3회 연속 같은 방향이면 보조를 뺄 수 있습니다 |
| D-03 | 판정은 사전 등록 지표(nDCG@10 vs coverage baseline) 하나. 절대 목표치는 참고값 | 라벨이 검출력 목표에 도달하면 절대 목표치를 다시 논의합니다 |
| D-04 | 입력 계약은 JSONL, DB 는 `export` 만. `candidates` 는 선택 | 파일이 실행마다 1GB 를 넘으면 컬럼 저장 형식을 검토합니다 (예시값, 실제 데이터로 대체 필요) |
| D-05 | 오프폴리시는 item-position SNIPS 와 가중치 교체 정책 1종(MMR 포함) | `usable=True` 가 나오기 시작하면 목표 정책을 늘립니다 |
| D-06 | 품질 스냅샷은 JSONL 파일, 테이블은 합의 뒤 | 테이블이 생기면 쓰기 대상만 바꿉니다 |
| D-07 | 화면은 별도 브랜치, 이 명세는 입력 계약까지 | 없음 |
| D-11 | Recall 은 K < top_k 만. K = top_k 는 계산하지 않음 | top_k 보다 큰 후보 라벨이 생기면(예: 노출 외 조사 라벨) 재검토 |
| D-12 | baseline 은 노출 목록의 순열. coverage 단독이 판정 기준 | 후보 풀 전체에 라벨이 생기면 후보 재정렬 baseline 을 추가합니다 |
| D-13 | 정책 채택은 Interleaving 승률로만 | 유저 수가 A/B 검출력을 넘으면 A/B 를 병행합니다 |
| D-14 | `model_version` 필수 그룹 키 | 없음 |
| D-15 | 위치별 CTR 필수, examination 보정은 옵션 | impression 이 `viewport` 로 바뀌면 보정을 기본으로 검토합니다 |
| D-16 | `user_id` 가명화와 냉장고 필드 제거는 export 단계에서 | 없음 |

D-08~D-10 은 작업 기록의 실행 결정입니다.

---

## 16. 한계

이 설계로도 못 하는 것입니다. 노출되지 않은 후보의 품질은 오프라인으로 잴 수 없습니다. impression 이 `served` 인 동안 실제 열람 여부를 모르며 위치 보정은 근사입니다. 유저 수가 두 자릿수인 동안 오프라인 판정은 대부분 보류입니다. 리포트가 매번 이 셋을 적습니다.

---

## 구성 근거

1절에서 "숫자를 만드는 쪽"과 "보여주는 쪽"으로 경계를 긋고 3절에서 단계 사이에 파일만 오가게 한 이유는, 화면이 계산을 갖거나 단계가 DB 를 직접 읽기 시작하면 어느 숫자가 맞는지 알 수 없게 되고 단위 테스트가 막히기 때문입니다.

1.1.0 의 변경은 전부 "숫자가 나오지만 뜻이 없는" 자리를 지운 것입니다. Recall@20 은 라벨 구조상 항상 1.0 이었고, 후보 풀 baseline 은 라벨 부재를 검정했으며, 유저 100명에서 검출력이 있는 유일한 비교인 Interleaving 이 빠져 있었습니다. 판정을 세 값으로 나누고 사전 등록 지표를 하나로 고정한 것은, 세그먼트와 지표가 많을수록 우연히 0 을 벗어나는 CI 가 생기기 때문입니다. 버린 대안은 후보 풀 재정렬 baseline 과 절대 목표치 게이트이며, 둘 다 통과가 품질을 뜻하지 않았습니다.
