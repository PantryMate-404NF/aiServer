# 추천 평가 관측 파이프라인 구축 계획 (Track C)

**정하는 것**: 설계 명세 `01_recommend_evaluation_design.md` 1.1.0 을 코드로 옮기는 순서, 단계별 완료 조건과 검증 게이트, 막힌 경로의 대안, 타 파트에 받을 것과 기한, 위험과 대응, 일정 가정. 지표 정의와 데이터 계약은 정하지 않습니다(명세가 정합니다)

**적용 대상**: 파트 C 구현자와 AI 코딩 에이전트. 파트 A·B 와 백엔드는 5절만 봅니다

**버전**: 1.1.0 · **최종 수정**: 2026-09-17 · **작성자**: 김민경

---

## 1. 읽은 것과 현재 위치

### 1.1 근거 문서

| 출처 | 내용 | 이 계획에 쓴 것 |
|---|---|---|
| Notion 「추천 평가 시스템」 | 「평가 관측」 인라인 DB. 항목은 Track B 해설서 1건뿐 | 파트 C 의 Notion 진행판이 아직 비어 있다는 사실 |
| Notion 「Pantry-Mate AI 추천 코어 엔진 (Track B)」 2.1.0 | 3단계 흐름, 17개 피처, 로그 테이블, 실 DB 전환 점검 15항목, 품질 목표, 미결 안건 7건 | 목표치(nDCG@10 0.85, ILD 0.95, 커버리지 15%, p95 58ms)를 리포트의 참고값으로. 전환 점검 가운데 "추천 로그 DB 적재"와 "온보딩·이벤트 API 실연결"이 내보내기 PR 의 선행 |
| `01_recommend_evaluation_design.md` 1.1.0 | 데이터 계약, 파이프라인 8단계, 모듈 8개, 지표, 판정, 오프폴리시, 스냅샷, 검증 계획 13건, PR 분할 6단계 | 이 계획의 골격. 어긋나면 명세가 이깁니다 |
| `02_recommend_evaluation_work_log.md` 1.1.0 | 결정, 가정, 접점, 계획 항목, 타 파트 안건 | 선행 조건과 막힌 경로. 이 문서의 용어와 그 기록의 번호는 11절에서 잇습니다 |
| `../recommend_engine_how_it_works.md` 7절·11절 | 엔진이 남기는 기록과 평가 파트 FAQ | 라우터·엔진 연결 전 로그는 시험용 응답이라 쓰지 않는다는 제약 |
| `deploy/init/02_schema.sql`, `enums.py`, `stage.py`, `schema.py`, `engine/score.py`, `engine/rerank.py` | 실제 계약 | `weighted_score`, `mmr_select`, `interleave_with`, `HealthOut`, `is_simulated`, `session_id` 접두어가 모두 존재함을 확인 |

### 1.2 현재 위치

| 항목 | 상태 | 계획에 미치는 영향 |
|---|---|---|
| 설계 | 1.1.0 완료. 리뷰 blocker 4건 반영 | 설계를 다시 열지 않습니다 |
| 코드 | `evaluation/threshold.py` 1개. 검사 없음. 나머지 0줄 | 전부 신규 작성 |
| 재사용 가능한 것 | `scripts/eval_recommend_mock.py` 의 `jaccard`, `intra_list_distance`. `engine/score.py` 의 `weighted_score`(순수 함수). `engine/rerank.py` 의 `mmr_select` | 이관·호출만 하고 다시 쓰지 않습니다 |
| 실데이터 | 없음. 라우터가 엔진에 연결되지 않아 `recommendation_log` 는 시험용 응답. 실 DB 전환 점검 15항목 미완 | 합성 기록이 첫 몇 주의 유일한 입력. 내보내기 PR 전의 모든 PR 은 DB 없이 끝나야 합니다 |
| IDF 가중 Jaccard | `mmr_select` 안에 인라인이며 별도 함수가 없음. 파트 B 에 분리를 요청했으나 미응답 | 기다리지 않고 복제 + 동일성 검사로 시작합니다(4.2) |
| 의존성 | `numpy`, `scikit-learn` 은 `pyproject.toml` 의 `ml` extra. 기본 설치에 없음 | 평가 모듈이 `numpy` 를 import 하면 기본 설치의 `uv run pytest tests/unit` 이 깨집니다(6.3) |
| 파일 위치 | `data/*` 가 `.gitignore` 에 있음 | 산출물 커밋 위험 없음 |

---

## 2. 목표와 완료 정의

### 2.1 한 문장 목표

DB 없이 합성 기록만으로 검증 단계부터 리포트 단계까지 한 번에 돌아가는 파이프라인을 먼저 완성하고, 실 DB 가 붙는 날 내보내기 단계 하나만 얹어 실기록이 같은 경로를 끝까지 통과하게 합니다.

### 2.2 파이프라인 단계의 이름

명세 3절의 8단계를 이 문서에서는 아래 이름으로 부릅니다.

| 단계 | 하는 일 | 코드 |
|---|---|---|
| 내보내기 | DB 의 추천 로그와 이벤트 로그를 가명화해 JSONL 로 | `scripts/reco_eval.py export` |
| 합성 | 실데이터 대신 같은 형식의 JSONL 을 생성 | `scripts/reco_eval.py synth` |
| 검증 | 줄마다 불변식 확인, 제외 규칙 적용 | `evaluation/record.py` |
| 라벨 | 이벤트를 레시피별 정답 점수로 | `evaluation/labels.py` |
| 지표 | 순위·목록 지표 계산 | `evaluation/metrics.py` |
| 비교 | 순열 baseline, 부트스트랩, Interleaving, 판정 | `evaluation/stats.py` |
| 추정 | 오프폴리시 추정과 지원 진단 | `evaluation/estimator.py` |
| 리포트 | 위 셋을 JSON 한 벌로 | `scripts/reco_eval.py run` |
| 스냅샷 | 매일 품질 스냅샷 1행 | `evaluation/quality.py` |

### 2.3 마일스톤과 완료 조건

| 마일스톤 | 내용 | 완료 조건 | 단계 |
|---|---|---|---|
| 준비 | IDF 가중 Jaccard 대안 확정, `EvalRecord` 스키마 동결, 의존성 결정 | 작업 기록 1절에 결정 기록. 코드 0줄 | 없음 |
| 오프라인 코어 | 기록 모델·불변식, 라벨, 순위·목록 지표, 합성 생성기 | 3.1 의 오프라인 코어 PR 의 테스트 전부 통과 | 합성, 검증, 라벨, 지표 |
| 비교·판정·추정 | 순열 baseline, 부트스트랩, Interleaving, 판정, SNIPS | 통계 PR 과 추정기 PR 의 테스트 통과 | 비교, 추정 |
| 리포트와 명령 | `run` 서브커맨드, 리포트 JSON, `make eval` | 리포트가 명세 9.2 의 키를 전부 가짐. `make eval` 종료 코드 0 | 리포트 |
| 품질 스냅샷 | `quality.py`, `quality` 서브커맨드, `threshold.py` 검사 | 스냅샷 PR 의 테스트 통과. 스냅샷 1행이 `data/quality/` 에 쌓임 | 스냅샷 |
| 실데이터 연결 | `export`, `make eval-smoke`, 통합 테스트 | 실 DB 에서 1건 이상 내보낸 파일이 끝까지 통과. 원본 `user_id` 와 냉장고 필드가 파일에 없음 | 내보내기 |
| 인계 | 화면 브랜치에 입력 계약과 샘플 산출물 전달. Track B 해설서의 품질 목표 표에 "실데이터 연동 후 실측치" 기입 | 화면 브랜치가 `report_*.json` 과 `quality/*.jsonl` 샘플만으로 착수 가능 | 명세 13절 |

품질 스냅샷까지는 실데이터가 없어도 끝납니다. 실데이터 연결만 파트 B 의 DB 전환에 걸립니다.

---

## 3. 단계별 계획

### 3.1 PR 단위

명세 12절의 6단계를 그대로 따릅니다. 1단계의 기록·라벨·지표·합성은 서로의 픽스처라 한 PR 로 냅니다.

| PR | 파일 | 테스트 | 완료 기준 | 선행 |
|---|---|---|---|---|
| 오프라인 코어 PR | `evaluation/record.py`, `evaluation/labels.py`, `evaluation/metrics.py`, `evaluation/synth.py`, `scripts/reco_eval.py` 의 `synth`, `scripts/eval_recommend_mock.py` 의 `intra_list_distance` 이관 | `test_eval_record.py`: 불변식을 하나씩 깨뜨린 줄이 각각 실패. `test_eval_labels.py`: 최댓값 합성, 음수 절단, 14일 창, 유저 단위 라벨 분리. `test_eval_metrics.py`: 손 계산 골든 6건, `sklearn.metrics.ndcg_score` 와 100건 대조, 성질 3종, ILD 유사도가 `mmr_select` 와 같음. `test_eval_synth.py`: 시드 재현, 검증 왕복, 4.3 의 항목 전부, 심은 확률 회수 | 네 파일 전부 통과. 실패가 줄 번호와 필드를 담음. 제외 3종이 건수로 보고됨 | 준비 마일스톤 |
| 통계 PR | `evaluation/stats.py` | `test_eval_stats.py`: 같은 순서 둘을 비교하면 CI 가 0 포함, 시드 고정 시 재현, 유저 20명 미만이면 보류, `team` 이 섞인 합성 기록에서 Interleaving 승률과 p 값이 손 계산과 일치 | 네 검사 통과 | 오프라인 코어 PR |
| 추정기 PR | `evaluation/estimator.py` | `test_eval_estimator.py`: 로그 정책 자신을 목표로 주면 관측 평균과 일치, 미지원 항목이 있으면 `usable=False` | 두 검사 통과 | 오프라인 코어 PR |
| 리포트 PR | `scripts/reco_eval.py` 의 `run`, `Makefile` 의 `eval` | 리포트 스키마 검사 1건 | 명세 9.2 의 키 전부. `stamp.input_sha256` 이 입력 파일과 일치 | 통계 PR, 추정기 PR |
| 스냅샷 PR | `evaluation/quality.py`, `reco_eval.py` 의 `quality`, `threshold.py` 검사 | `test_eval_quality.py`: 기대 None 패턴 위반이 `alerts` 로, DB 없으면 해당 항목 `null`. `test_eval_threshold.py`: `calibrate` 가 목표 정밀도 미달이면 `None`, Wilson 하한이 관측 정밀도보다 작음 | 두 파일 통과 | 리포트 PR |
| 내보내기 PR | `reco_eval.py` 의 `export`, `Makefile` 의 `eval-smoke` | `tests/integration/test_eval_export.py`: 실 DB 에서 1건 이상 내보낸 뒤 검증부터 리포트까지 통과, 원본 `user_id`·냉장고 필드 부재 | 통합 테스트 통과 | 파트 B 의 DB 전환, 5절의 조인 허용·가명화 salt·연결일 |
| 결정 문서 PR | `docs/decisions/` 3건(라벨 정의, 스냅샷 저장, 판정 규칙) | 04 의 2.3 형식 점검 | 결정 문서 3건 등록 | 5절의 라벨 정의·스냅샷 테이블 합의 |

### 3.2 PR 마다 반복하는 것

| 순서 | 할 일 | 근거 |
|---|---|---|
| 1 | 건드리는 영역의 규칙 원문 확인(02 의 6절·7절, 03 의 3절·6절) | `CLAUDE.md` 3절 |
| 2 | 테스트를 먼저 쓰고 구현. 골든 케이스는 손 계산 값을 주석에 남김 | 명세 10절의 "손으로 계산한 케이스" |
| 3 | 6.1 의 검증 명령 실행. 종료 코드로 판정 | 01 의 3.4 |
| 4 | PR 본문은 01 의 5.3 다섯 항목. 통과 출력 첨부 | 01 의 5.3 |
| 5 | 작업 기록 1절(상태)과 7절(세션) 갱신. 계획 항목 상태 변경 | 작업 기록 갱신 규칙 |

---

## 4. 순서의 근거와 크리티컬 패스

### 4.1 의존 관계

```text
준비 ──▶ 오프라인 코어 ──┬──▶ 통계 ──┐
                         │           ├──▶ 리포트 ──▶ 스냅샷 ──▶ 내보내기 ──▶ 인계
                         └──▶ 추정기 ┘                            ▲
                                                                 │
    파트 B DB 전환 · 파트 A 조인 허용 · 가명화 salt 합의 · 연결일 ┘

결정 문서 ── 라벨 정의와 스냅샷 테이블이 합의된 뒤 언제든
```

오프라인 코어 PR 이 갈림길입니다. 합성 생성기가 나오기 전에는 통계·추정기의 테스트 픽스처가 없고, 나온 뒤에는 둘이 서로 독립입니다. 한 사람이 하면 통계를 먼저 합니다. 판정이 리포트의 결론이고 오프폴리시 추정은 첫 몇 달 대부분 `usable=False` 가 예상되기 때문입니다.

### 4.2 막힌 경로의 대안

| 막힌 것 | 기다리지 않는 방법 | 풀리면 |
|---|---|---|
| 파트 B 가 `mmr_select` 안의 IDF 가중 Jaccard 를 함수로 분리하지 않음 | `metrics.py` 에 같은 계산을 두고 오프라인 코어 PR 의 동일성 검사가 `mmr_select` 의 인라인 결과와 대조합니다 | `metrics.py` 가 분리된 함수를 import 하고 동일성 검사는 그대로 둡니다 |
| 파트 B 의 DB 전환이 안 됨 | 내보내기 전의 모든 PR 을 합성 기록으로 완결합니다. 내보내기는 마지막 PR | 내보내기 PR 착수. `--since` 하한은 파트 B 가 알려 줄 라우터·엔진 연결일 |
| 정답 라벨 정의를 팀이 확정하지 않음 | 코드는 `enums.LABEL_WEIGHT` 를 그대로 쓰고 `label_version = 1` 로 시작합니다 | 바뀌면 `label_version` 을 올리고 결정 문서를 씁니다 |
| Thompson 픽의 노출 확률 귀속이 정해지지 않음 | 추정기가 `explore_source` 별로 분리 보고하고 `thompson` 경로는 인용하지 않습니다 | 반영 뒤 분리 보고를 유지한 채 인용 제한만 풉니다 |
| 가명화 salt 를 후기 해시와 공유할지 미합의 | `config.py` 에 `eval_salt` 를 추가하고 `.env.example` 에 빈 값을 둡니다. `REVIEW_SALT` 를 재사용하지 않습니다 | 합의가 "같은 salt" 면 `eval_salt` 기본값을 `review_salt` 로 |

### 4.3 합성 생성기가 갖춰야 할 것

합성 기록은 이후 모든 테스트의 픽스처이므로 오프라인 코어 PR 에서 다음을 전부 심어야 합니다. 하나라도 빠지면 그 위의 PR 이 테스트를 만들 수 없습니다.

| 심는 것 | 쓰는 곳 |
|---|---|
| 위치 감쇠 클릭(`--position-decay`) | 왕복 검사, 위치별 CTR(명세 5.4) |
| 탐색 슬롯(`is_exploration=True`, `0 < propensity < 1`, `explore_source`) | SNIPS 추정, 불변식(명세 2.2) |
| 유형 칸(`is_cuisine_slot=True`, `propensity = 1.0`) | 불변식, 목록 지표(명세 5.5) |
| Interleaving 요청(`policies`, `items[].team`) | Interleaving 승률 검사 |
| 미노출 후보 포함 `candidates`(`--with-candidates` 와 같은 모양) | 목표 정책, 미지원 비율 |
| `user_mode` 3종과 취향 출처 | 세그먼트(명세 5.6) |
| 제외 대상 줄(`session_prefix = "d"`, `is_simulated = True`, 결측 `config_hash`) | 불변식·제외 검사, 스냅샷의 제외 건수 |
| 유저별 요청 다건과 `user_events` 의 `cook` | 유저 단위 리샘플(명세 6.4), 유저 단위 Recall(명세 5.2) |
| 헤더 레코드(`label_version`, `metric_version`, `catalog_size`, `ingredient_idf`) | 파일 형식(명세 2.1) |

---

## 5. 타 파트에 받을 것과 기한

| 받을 것 | 대상 | 필요한 시점 | 답이 없을 때의 기본값 |
|---|---|---|---|
| `mmr_select` 안의 IDF 가중 Jaccard 를 함수로 분리 | 파트 B | 준비 마일스톤. 기다리지 않음 | 복제 + 동일성 검사 |
| 정답 라벨 정의(등급형 `LABEL_WEIGHT` 인지 이진인지) | 파트 B | 결정 문서 PR 전 | 등급형, `label_version = 1` |
| 카탈로그 커버리지 분모(46,353 인지 46,552 인지) | 파트 A | 리포트 PR 전 | `--catalog-size` 인자 필수. 기본값 없음 |
| 배치 미처리 레시피의 맛 벡터가 0 인지 null 인지 | 파트 A | 스냅샷 PR 전 | 0 과 null 을 따로 세어 둘 다 보고 |
| Interleaving 요청의 `team` 을 이벤트에 되돌려 기록 | 백엔드 | 첫 Interleaving 요청 전 | `interleaving = null` |
| 내보내기가 `recipe_feature.all_ids` 와 IDF 를 조인하는 것 | 파트 A | 내보내기 PR 전 | 조인 허용을 가정하고 SQL 초안을 PR 에 첨부 |
| 가명화 salt 를 후기 해시와 공유할지 | 3인 | 내보내기 PR 전 | 평가 전용 `eval_salt` |
| 라우터·엔진 연결 커밋과 시각 | 파트 B | 내보내기 PR 전 | `--since` 필수 인자. 연결일 전 요청은 거부 |
| 품질 스냅샷 테이블 신설 여부 | 3인 | 추이 3주 이상 쌓인 뒤 | JSONL 유지 |
| Thompson 픽의 노출 확률 귀속 | 파트 A, B | 없음 | `thompson` 경로 미인용 |
| `evaluation/threshold.py` 를 파트 C 가 인수할지 | 파트 A, B | 스냅샷 PR 전 | 파트 C 인수. 검사만 붙이고 위치는 두지 않습니다 |

기본값은 답이 오면 되돌립니다. 기본값으로 만든 코드가 답을 바꾸게 두지 않습니다.

---

## 6. 검증 게이트

### 6.1 PR 마다 실행하는 명령

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit
```

여기에 그 PR 의 테스트 파일을 따로 한 번 더 돌립니다. 통과는 종료 코드로 판정하고 돌리지 못한 명령은 못 돌렸다고 씁니다(01 의 3.4).

### 6.2 마일스톤 게이트

| 마일스톤 | 추가 게이트 |
|---|---|
| 오프라인 코어 | 합성으로 만든 파일이 검증을 통과하고, 불변식 6종을 하나씩 깨뜨린 파일이 각각 실패 |
| 리포트와 명령 | `make eval` 종료 코드 0. 리포트 첫 줄 `limits` 에 명세 16절의 한계 3개 |
| 품질 스냅샷 | `quality` 를 이틀 연속 실행해 `data/quality/` 에 2행. `/v1/health` 를 끈 채 실행하면 `health_counters = null` 과 `alerts` |
| 실데이터 연결 | `make eval-smoke` 종료 코드 0. 내보낸 파일을 `grep` 해 `user_id`, `pantry` 문자열 0건 |

### 6.3 의존성 결정

명세 4절은 평가 모듈이 `numpy` 에 의존한다고 적었으나 `numpy` 는 기본 설치에 없습니다. 이 계획은 다음을 제안하며 준비 마일스톤에서 확정합니다.

| 선택 | 내용 | 대가 |
|---|---|---|
| (a) 표준 라이브러리만 | `math`, `random`, `statistics` 로 지표·부트스트랩 구현. `scikit-learn` 은 외부 대조 검사에서만 `pytest.importorskip` | 부트스트랩 1,000회 × 유저 수백 명이면 수 초. 유저가 수천 명이 되면 재검토 |
| (b) `numpy` 를 기본 의존성으로 | `uv add numpy`. 커밋 body 에 사유 | 기본 이미지가 커짐. 컨테이너 인수인계 문서 갱신 |

제안은 (a) 입니다. 지금 규모에서 `numpy` 가 바꾸는 것은 속도뿐이고, 기본 설치의 단위 테스트가 깨지지 않는 것이 더 중요합니다.

---

## 7. 운영 계기

| 계기 | 명령 | 담당 | 시작 시점 |
|---|---|---|---|
| PR 단위 테스트 | `uv run pytest tests/unit/recommend/test_eval_*.py` | 작성자 | 오프라인 코어 PR |
| 현재 정책 상태 | `make eval` | 파트 C | 리포트와 명령 마일스톤 |
| 매일 1회 스냅샷 | `uv run python scripts/reco_eval.py quality --records data/eval/<최신>.jsonl --health-url <URL> --out data/quality/<날짜>.jsonl` | 파트 C. 수동 | 품질 스냅샷 마일스톤 |
| DB 전환일 | `make eval-smoke` | 파트 B 와 C 함께 | 실데이터 연결 마일스톤 |
| 가중치·정책 변경 전 | `reco_eval.py run --target-weights <파일>` | 변경하는 사람 | 리포트와 명령 마일스톤 |
| 정책 채택 | 요청에 `interleave_with` 를 붙여 누적한 뒤 `make eval` | 파트 B 와 C | 백엔드가 `team` 을 이벤트에 되돌린 뒤 |

매일 1회 실행의 자동화(스케줄러)는 지금 만들지 않습니다. 화면 브랜치가 붙고 스냅샷이 3주 이상 쌓인 뒤 스냅샷 테이블 신설 여부와 함께 정합니다.

---

## 8. 위험과 대응

| 위험 | 신호 | 대응 |
|---|---|---|
| 유저 수가 두 자릿수라 오프라인 판정이 대부분 "보류" | 리포트의 `verdict.status = hold` 가 계속됨 | 판정을 낮추지 않습니다. `power.users_needed_for` 를 리포트에 실어 필요한 유저 수를 매번 보이고, 정책 채택은 Interleaving 승률로 합니다(명세 6.2) |
| impression 이 `served` 라 하위 순위 열람 여부를 모름 | 위치별 CTR 이 하위에서 0 에 붙음 | 위치 보정은 옵션으로 둡니다(명세 5.4). `viewport` 전환 뒤 두 시대를 나눠 보고 |
| Thompson 픽의 확률이 실제 노출 확률과 다름 | `explore_source = thompson` 의 ESS 가 `uniform` 과 크게 다름 | `thompson` 경로 추정치를 인용하지 않습니다 |
| mock 모드의 `candidates` 절단 폭이 작아 추정기가 `usable=False` 만 냄 | `unsupported_ratio > 0` | 결과로 보고합니다. `usable=True` 를 만들기 위해 진단 기준을 낮추지 않습니다 |
| 라우터·엔진 연결 전 로그가 섞임 | `--since` 가 연결일보다 앞섬 | 내보내기가 거부합니다. 연결일은 파트 B 에게 받습니다 |
| 가명화 누락으로 원본 `user_id` 가 파일에 남음 | 통합 테스트 실패 | 통합 테스트가 파일을 문자열 검색합니다. 내보내기는 화이트리스트 필드만 씁니다 |
| 한 파일에 `config_hash` 가 여럿 | 리포트의 `config_hash.others` 가 비어 있지 않음 | 경고하고 최다 쪽만 집계(명세 5.6). 조용히 합치지 않습니다 |
| 합성 기록이 실기록과 모양이 어긋남 | `make eval-smoke` 의 검증 단계에서 실패 | 검증의 실패 메시지로 차이를 찾아 합성 생성기를 실기록 쪽에 맞춥니다. 불변식을 완화하지 않습니다 |
| `numpy` import 로 기본 설치의 단위 테스트가 깨짐 | CI 의 `ModuleNotFoundError` | 6.3 의 (a) |

---

## 9. 일정 가정

아래는 구현자 1인, PR 1건에 하루 이하라는 가정의 상대 일정입니다. 날짜가 아니라 순서와 크기를 적은 것이며 실측으로 갱신합니다.

| 작업일 | PR | 산출물 |
|---|---|---|
| 0 | 준비 | 작업 기록에 IDF 가중 Jaccard 대안·의존성·스키마 동결 기록 |
| 1~3 | 오프라인 코어 | `record.py`, `labels.py`, `metrics.py`, `synth.py`, `synth` 서브커맨드, mock 스크립트 이관, 테스트 4개 |
| 4~5 | 통계 | `stats.py`, 테스트 1개 |
| 6 | 추정기 | `estimator.py`, 테스트 1개 |
| 7 | 리포트 | `run`, 리포트, `make eval` |
| 8 | 스냅샷 | `quality.py`, `quality`, threshold 검사 |
| DB 전환일 | 내보내기 | `export`, `make eval-smoke`, 통합 테스트 |
| 라벨 정의·스냅샷 테이블 합의 뒤 | 결정 문서 | 결정 문서 3건 |

작업일 8 까지 실데이터 없이 끝납니다. 이후는 파트 B 의 DB 전환을 기다리는 동안 `make eval` 로 합성 리포트를 내며 화면 브랜치에 샘플을 넘깁니다.

---

## 10. 열린 질문

1. 6.3 의 의존성 선택 (a)·(b) 가운데 무엇으로 갈지. 답이 없으면 (a) 로 시작합니다.
2. 이 계획을 별도 문서로 유지할지, 작업 기록의 계획 항목 표에 흡수하고 이 파일을 지울지. 답이 없으면 리포트와 명령 마일스톤까지 유지하고 그때 정합니다.
3. Notion 「평가 관측」 DB 를 마일스톤 진행판으로 쓸지. 지금은 Track B 해설서 1건만 있습니다. 답이 없으면 저장소의 작업 기록만 갱신합니다.

---

## 11. 다른 기록과의 대응

이 문서는 번호 없이 읽히도록 썼습니다. 작업 기록과 명세의 번호로 찾을 때는 아래 표를 씁니다.

| 이 문서의 표현 | 작업 기록 | 명세 |
|---|---|---|
| 오프라인 코어 PR | T-01, D-09(mock 함수 이관) | 12절의 1단계. E-01, E-02, E-03, E-04, E-07, E-11, E-13 |
| 통계 PR | T-02 | 12절의 2단계. E-05, E-12 |
| 추정기 PR | T-03 | 12절의 3단계. E-06 |
| 리포트 PR | T-04, G-07 | 12절의 4단계 |
| 스냅샷 PR | T-05 | 12절의 5단계. E-08, E-09 |
| 내보내기 PR | T-06 | 12절의 6단계. E-10 |
| 결정 문서 PR | T-08, D-10 | 없음 |
| IDF 가중 Jaccard 분리 | G-08, A-08 | 4절 |
| 정답 라벨 정의 | G-01 | D-01 |
| 카탈로그 커버리지 분모 | G-06 | 5.5 |
| 맛 벡터 0 과 null | G-05 | 8절 `flavor_all_zero_ratio` |
| `team` 을 이벤트에 되돌리기 | G-09 | 6.2 |
| `all_ids`·IDF 조인 | G-02, A-07 | 2.1 |
| 가명화 salt | G-10 | 14절 |
| 라우터·엔진 연결일 | G-12, A-12 | 3.2 의 내보내기 멈춤 조건 |
| 스냅샷 테이블 신설 | G-03 | D-06 |
| Thompson 확률 귀속 | G-11 | 7.2 |
| `threshold.py` 인수 | G-04 | 4절 |
| mock 모드의 `candidates` 절단 | A-02 | 7.2 |

---

## 구성 근거

명세 12절이 이미 PR 순서를 정했으므로 이 문서는 순서를 다시 정하지 않고 그 순서가 왜 성립하는지(4절), 각 단계가 끝났다고 말할 조건(2.3, 3.1, 6.2), 기다리지 않고 갈 방법(4.2, 5절)만 더했습니다. 계획이 명세를 되풀이하면 둘 중 하나가 반드시 낡습니다.

가운데(검증·라벨·지표)부터 만들고 내보내기를 마지막에 두는 순서를 유지한 것은 실데이터가 없다는 사실 때문입니다. 대안으로 내보내기부터 만들어 실기록 모양을 먼저 보는 순서를 검토했으나, 라우터가 엔진에 연결되지 않아 지금 쌓이는 로그는 시험용 응답이고 그 모양에 맞춘 코드는 연결 뒤 다시 고쳐야 합니다.

본문에서 다른 문서의 번호를 걷어내고 11절에 대응표만 둔 것은, 이 문서를 읽는 사람이 작업 기록과 명세를 나란히 펴 놓지 않아도 무엇을 어떤 순서로 만드는지 알 수 있어야 하기 때문입니다. 번호는 찾을 때만 필요하므로 한 곳에 모았습니다.
