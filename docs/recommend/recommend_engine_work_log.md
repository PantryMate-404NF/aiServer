# 파트 B 추천 엔진 작업 기록 (에이전트용)

**정하는 것**: `PM-ENG-RECO-B-001` 의 실행 이력. 상태, 결정(D), 가정(A), 접점(I), 계획 항목(T), 선행 조건(P), 고려사항(C), 환경(E)

**적용 대상**: 파트 B 추천 엔진을 이어서 작업하는 AI 코딩 에이전트. 사람은 `human/` 의 서술본을 읽습니다

**버전**: 1.4.0 · **최종 수정**: 2026-09-11 · **작성자**: 유재현

---

## 1. 상태 요약

| 키 | 값 |
|---|---|
| 정본 | 이 파일. 사람용 서술본 `human/recommend_engine_work_log.md` 는 식별자로 대응하는 파생본 |
| 계획서 | `recommend_engine_design.md` (사람용, 정본) · `recommend_engine_design_digest.md` (요약, 어긋나면 원본이 이김) |
| 브랜치 | `feat/recommend-engine-core`. `origin/main` 병합 완료. `origin/develop-data-part` 는 두 번 병합했습니다 — 658d79a(9.6), def3d5b(9.10). `main` 병합은 PR #8 로 올렸습니다(2026-09-11, 승인 대기) |
| 단계 | ② Ranking 과 ③ Re-ranking 을 A 계약 위에서 구현 완료. **취향 페르소나**(고른 음식 → 3축 척도 → 없음, 시간 감쇠·주기 가중, 사용자당 JSON 저장)를 9.11 에서 구현. ① Retrieval·로그 적재·DDL·배치는 A 것이 브랜치에 있습니다. 라우터에 `rank_candidates`·`PersonaService` 를 끼우는 것(M-01·M-15)과 DB 연결이 남았습니다 |
| 검증 (2026-09-11, 9.11) | ruff check OK · ruff format OK · mypy **62 files** OK · `pytest tests/unit` **252 passed / 0 failed** / coverage **89.74%**. A 자체 게이트 전부 종료코드 0 — `contract` 98건은 설정값을 채운 환경에서만 끝까지 돕니다(E-13). 출력은 `recommend_engine_verification.md` 13절 |
| 다음 행동 | PR #8 에 9.11 커밋을 얹었으니 본문 갱신 → 승인·병합 → N-01(.env) → 라우터 실연결(M-01·M-15) → G-24(3축 척도 범위) 확인. 남은 회의 안건은 `recommend_engine_meeting_agenda.md` 2절 |
| 병합 정책 | PR 없음. 브랜치 커밋·push 만. `main` 병합은 A·B 파트 완료 후 논의 (P-10). `origin/main` 은 merge 로 따라감 (A 가 브랜치를 본 뒤라 rebase 금지, 01의 2.1) |
| DB 전환 | `recommend_engine_db_cutover.md` 의 M-01~M-15. DB 와 닿는 변경을 시작할 때 먼저 엽니다. `tests/unit/recommend/test_db_cutover.py` 가 못을 박아 두어 건너뛰면 검사가 깨집니다 |
| 갱신 규칙 | 세션마다 1절·3절·9절 갱신. 새 항목은 다음 번호, 번호 재사용 금지. 사람용 서술본 동시 갱신 (2절). **3.3 의 수치는 그 세션의 실행에서 다시 잽니다** — 앞 세션 값을 옮기지 않습니다 (01의 3.4, D-28). 9절의 세션별 수치는 그 시점 기록이므로 고치지 않습니다 |

---

## 2. 읽는 법

- 계획서(`recommend_engine_design.md`)의 체크박스는 템플릿입니다(04의 2.2). 결과는 이 파일과 검증 기록에만 적습니다.
- 세션 시작은 1절 → 4절 → 5절 순서로 읽습니다. 실데이터·백엔드·DB 와 합치는 작업이면 8절을 먼저 봅니다. 타 파트와 정할 일은 `recommend_engine_meeting_agenda.md` 에 있습니다.
- 검증은 실행 출력이 있는 것만 기록합니다. 추정으로 통과를 적지 않습니다.
- 식별자 접두는 D 결정 · A 가정 · I 접점 · T 계획 항목 · P 선행 조건 · C 고려사항 · E 환경입니다. 검증 기록은 S·V·F·X, 회의 안건은 G(타 파트)·N(내부)을 씁니다. 번호는 재사용하지 않고 폐기는 상태 열에 표기합니다.
- 두 형식 기록의 근거와 식별자 대조 명령은 `../decisions/2026-09-10_recommend_record_dual_format.md` 에 있습니다. 세션이 끝나면 에이전트용을 갱신하고, 사람용 서술본을 맞추고, 대조 명령을 돌린 뒤 `docs(recommend): ...` 로 커밋합니다.
- 초안은 Claude Code 가 쓰고 유재현이 검토합니다. 검토 전 문장은 남기지 않습니다.

---

## 3. 실행된 것

### 3.1 계획대로

전부 2026-09-10, 브랜치 `feat/recommend-engine-core`, 커밋 8개(9.1). 단계 번호는 계획서 6절.

| 단계 | 계획 항목 | 산출물 | 검증 |
|---|---|---|---|
| Step 0 | Pydantic v2 스키마 | `src/features/recommend/schema.py` | TC-0-1, TC-0-2 통과 (`test_schema.py`) |
| Step 0 | 12인 가상 인벤토리 | `scripts/generate_mock_fixtures.py` → `tests/fixtures/recommend/` (페르소나 12, 레시피 120, 재료 60, 알레르기 코드군 18) | TC-0-3 통과 |
| Step 2 | 알레르기 하드컷, 후보 부족 시 완화·인기순 폴백 | `engine/candidate.py` | TC-2-1, TC-2-2 통과 (`test_candidate.py`) |
| Step 3 | Zero-Drop 가중합, 중심화, 감점 곱연산 | `engine/rank.py`, `engine/penalty.py` | TC-3-1, TC-3-2, TC-3-3 통과 |
| Step 4 | MMR(λ=0.7), 탐색 4건(Thompson 2 + 균등 2), 위치 무작위화, IPS, z-salience 사유 | `engine/rerank.py`, `engine/explain.py` | TC-4-1, TC-4-2, TC-4-3 통과 |
| Step 5 (일부) | 파이프라인 조립, 입력 어댑터, EMA 취향 갱신 | `service.py` (`run_pipeline`), `engine/context.py` (`build_context`), `engine/feedback.py` | TC-5-1, TC-5-2 통과 (`test_service.py`, `test_context.py`, `test_feedback.py`) |

### 3.2 계획과 다르게 (D)

| ID | 계획서 | 실제 | 이유 | 되돌릴 조건 |
|---|---|---|---|---|
| D-01 | Stage 3 흐름도: 개인화 16 · 탐색 4 병렬 | `rerank`: MMR 개인화 먼저 → 남은 후보에서 탐색 | 탐색 선행 시 개인화가 난수 의존 → TC-5-1 실패. 개인화가 보여 줄 것을 탐색이 고르면 탐색이 아님 | 탐색이 개인화 상위와 겹쳐도 된다는 결정 |
| D-02 | 후보 20 미만 시 완화 | 임계 `max(min_candidates=20, top_k)` | 고정 20 이면 `top_k=50` 이 25건만 받음 (페르소나 1011 실측) | `top_k` 상한 20 |
| D-03 | `engine/candidate.py` 에 intarray SQL | SQL 은 `repository.py`(미작성). `candidate.py` 는 WHERE 동치 조건을 파이썬으로 재검증 | 03의 5절 "SQL 은 repository 밖으로 나가지 않는다" | 없음 |
| D-04 | 가중치·계수 → `config.py` (03의 2절) | `schema.RankConfig` dataclass 기본값. `fingerprint()` 여기서 | 공용 파일·`.env.example` 변경을 Step 5 service 실연결과 묶기 위해 | Step 5 진입 시 이동 필수. 기본값 두 곳 금지 |
| D-05 | `test_serve.py`, `test_events.py` | `test_service.py`, `test_feedback.py` | 02의 4절 테스트 파일명 규칙. `-k` 필터 동작 유지 | 없음 |
| D-06 | Step 0 더미 라우터 | 미구현. `router.py` prefix `/recommendations` 그대로 | 엔진을 같은 세션에 구현해 더미가 즉시 폐기될 코드였음 | Step 5 에서 `/v1/recommend`, `/v1/events` |
| D-07 | `meta: dict[str, Any]` | `RecommendMeta` 고정 5필드 (`degraded`, `fallback_stage`, `candidate_count`, `latency_ms`, `config_fingerprint`) | 필드 누락을 타입 검사가 잡음. JSON 모양 동일 | BE 가 자유 형식 요구 |
| D-08 | `event_type` 정규식, `timestamp: str` | `Literal["click","cook","dismiss"]`, `datetime` | 03의 4절 검증된 값만 뒷단으로 | 없음 |
| D-09 | 맛 코사인 그대로 | `(cos+1)/2` 로 0~1. 코퍼스 평균 없음·영벡터 → None(측정 불가) | 블록 점수 0~1 규약. 평균 없이 계산하면 원칙 4 위반 | 없음 |
| D-10 | `__init__.py` 배럴에 Request/Response | 비움 | 02의 6절 "다른 도메인이 쓰는 것만" | 도메인 밖 사용처 발생 |
| D-11 | (명시 없음) | 난수 `random.SystemRandom()`. 스크립트는 blake2b 해시 | `random.Random(seed)` 는 ruff S311, noqa 는 Tech Lead 승인(01의 3.3) | 시드 재현 필요 시 S311 예외 승인 |
| D-12 | (명시 없음) | `pyproject.toml` addopts `--import-mode=importlib` (공용 파일) | 도메인마다 같은 테스트 파일명 → prepend 모드 수집 실패 | 없음 |
| D-13 | SQL SELECT 7컬럼 | `RecipeCandidate` 에 `title`, `all_ids`, `cuisine`, `product_ids` 추가 | 응답 제목·상품, 탐색 요리군, 자카드·기피 감점에 필요 | 없음. T-05 에서 쿼리 확장 |
| D-14 | (명시 없음) | `dismiss` 는 취향 벡터 불변. Position Bias 는 로깅만 | dismiss 는 어느 맛이 싫은지 미지정. 보정은 Track C | 이벤트별 가중치 정책 확정 |
| D-15 | Input Adapter 는 2절 그림에만 | `build_context` 를 `service.py` 아닌 `engine/context.py` 에. engine 파일 7개 | 02의 3.2 service 는 흐름만. 픽스처가 service 없이 문맥 생성 가능해야 커밋 단위 검증 가능 | 없음 |
| D-16 | 맛 축 순서 미명시 (구현은 매움·단맛·짠맛) | `(spicy, salty, sweet)` = A 트랙 D-11 의 (매움, 짠맛, 단맛). `RecipeCandidate` 는 6축 입력의 앞 3축만 | A 의 `recipe_feature.flavor_vec` 6축 앞 3축과 같은 순서여야 함. 값·길이가 같아 검사에 안 걸리는 오류 (A 공유 문서 1절) | 없음. `ba3e0f1` |
| D-17 | 5절 파일 배치: 도메인 루트 6개 | `stage.py` 추가. `RecipeCandidate`, `UserHistory`, `UserContext`, `CorpusStats`, `RankConfig`, `ScoredCandidate`, `ServedItem` 이동. `schema.py` 는 HTTP 계약만 | A 가 같은 배치를 쓰고 병합 충돌을 줄이기 위해 요청 (A 공유 문서 2절). 저장소 02 규약은 아직 미개정 (P-11) | 02 규약이 `stage.py` 를 거부하면. `e2fc72d` |
| D-18 | 3.2 수식·3.3 탐색 규칙 | 검증 기록 X-02(맛 신뢰도), X-05(탐색 축소), X-06(요리군 항), X-07(맛 거리 novelty) 로 확장 | Mock 검증 F-02·F-04·F-06·F-07. 상세는 `recommend_engine_verification.md` 7.1 | 일부 폐기 (D-20). X-02·X-05 는 유지, X-06·X-07 은 17 피처의 `f_cuisine` 으로 흡수 |
| D-19 | 3축 맛 벡터 | **6축**. 사용자에게 받는 것은 앞 3축뿐이고 뒤 3축은 None 으로 들어와 계산에서 빠짐 | 회의 결정 G-01. A DDL 이 `flavor_vec`·`flavor_mu`·`taste_vec` 을 6축으로 두고, 뒤 3축 데이터가 오면 코드 변경 없이 켜져야 함 | 없음. `256c702` |
| D-20 | 5블록 가중합, `RecommendRequest` 를 B 가 정의 | A 의 **17 피처**(`enums.FEATURE_KEYS`)와 `stage.ScoredCandidate`·`RankedItem`. B `schema.py` 폐기 | 회의 결정 G-02·G-03·G-07. A 가 먼저 구현했고 계약 98건과 DDL 이 그 위에서 돎 | 없음. D-04·D-07·D-08·D-13 이 이 결정으로 대체됨 |
| D-21 | propensity 는 노출확률의 **역수** | **확률** (0 < p <= 1). `enums.PROPENSITY_SEMANTICS` = "item" | 회의 결정 G-05. A DDL 과 C 의 IPS 계산이 확률을 전제 | 없음. C-07 대체 |
| D-22 | 엔진 순수 함수를 B 가 전부 구현 | A 의 `rank`(z-salience·로그 헬퍼), `reason`(템플릿), `explore`(슬롯·interleaving), `serendipity`(Thompson)를 그대로 쓰고 B 는 **점수 계산만** 채움 | 회의 결정 G-04. 두 벌이 되면 propensity 정의가 갈려 off-policy 평가가 못 쓰게 됨. B 의 `explain`·`penalty`·`feedback` 폐기 | 없음. `c459c2f` |
| D-23 | A 가 가져온 파일은 그대로 둔다 | `src/**` 의 ruff 45건·mypy 31건을 손으로 고침. 의미를 바꾸지 않는 표기·타입·구조 수정만 | 서빙 경로에 예외를 두면 그 예외가 요청 처리 코드에 남습니다. A 자체 게이트(`validate`·`contract` 98건·`normalize-test` 125건)로 의미 불변을 확인했습니다 | A 가 거부하면 해당 파일만 되돌림 |
| D-24 | 게이트는 예외 없이 통과시킨다 | 도구 파일에 한해 범위를 좁힌 예외. `adapter.py` ANN401, `scripts/reco/bench` 검사 제외, 도구 9개 파일별 규칙 코드, 커버리지 `omit` 3항목(9.10 에서 `repository_ingest.py` 를 더해 4항목) | 규칙이 막으려는 것이 자리마다 다릅니다. 근거와 해소 조건은 `../decisions/2026-09-10_merge_data_track_gate_exceptions.md` | Tech Lead 가 거부하면 해당 항목을 손으로 고침 (01의 3.3, 6.1) |
| D-25 | 공유 파일은 A 것을 받는다 | `uv.lock` 의 `pillow-heif` 만 `main` 값 1.6.0 으로 되돌림 | 이 병합과 무관한 버전 올림이고, 1.7.0 의 DLL 이 Windows 앱 제어 정책에 걸려 영수증 파이프라인 검사 8건이 수집 단계에서 죽습니다 | 1.7.0 이 필요한 이유가 나오면 (G-13) |
| D-26 | A 의 `tests/conftest.py` 를 그대로 받는다 | `collect_ignore_glob` 두 줄을 파일 8개 명시로 | 글로브가 B 의 pytest 검사 63건과 `integration/test_receipt_pipeline.py` 를 함께 뺍니다. 통과 건수가 줄어드는 것이 아니라 **세어지지 않아** 알아챌 수 없습니다 | 없음. G-08 의 (a)안이고 (b)안으로 가면 이 줄들이 사라집니다 |
| D-27 | 설정 기본값을 편한 곳에 둔다 | 같은 값을 두 곳에 두지 않습니다. `health_payload(db_ok)`·`build_context(warm_event_count)` 에서 기본값을 없애고 `mixed_exploration(mc=)` 로 정책값을 넘깁니다 | 기본값이 있으면 호출자가 빠뜨려도 **에러가 안 납니다.** 확인 없이 참으로 나가거나(F-24), 로그와 계산이 갈라지거나(F-25), 손잡이가 안 먹습니다(F-26) | 없음. 회귀 검사 `tests/unit/recommend/test_wiring.py` 6건 |
| D-28 | 통과 여부를 화면 출력으로 판단 | **종료 코드로 판정합니다.** 안 돌린 명령의 결과를 적지 않고, 남이 적어 둔 건수를 자기가 잰 것처럼 인용하지 않습니다 | 실제로 오보가 세 건 났습니다 — `make contract` 를 tail 로 통과로 읽었고(F-30), 안 돌린 242건을 통과로 적었고, 앞선 실행의 128·193 을 그대로 옮겼습니다. 01의 3.4 로 규칙에 넣었고 사례는 `../decisions/2026-09-11_report_only_verified_output.md` | 없음. 확인 비용이 `echo $?` 한 줄입니다 |
| D-29 | 취향 = 온보딩 3축 척도 | **고른 음식의 6축 평균**이 먼저, 없으면 3축 척도, 둘 다 없으면 없음. 고른 음식이 있으면 척도는 저장만 합니다 | 2차 회의 결정(G-21). 고른 음식은 6축 전부를 알려 주고 자기 보고보다 믿을 만합니다. 척도는 나중에 LLM 추천의 보정값입니다 | 기획이 고른 음식을 필수로 만들면 2단계가 사라질 뿐 식은 그대로. `04ec783` |
| D-30 | 행동은 EMA 로 갱신, 이벤트 수로 선형 전이 | 이벤트를 시각과 함께 저장하고 매번 다시 계산. 무게 = 종류 x 반감기 감쇠 x 연·주·일 주기 친화도. 축마다 `(k·prior + S·behavior)/(k + S)` | 2차 회의 결정(G-22). EMA 는 순서만 보고 시각을 보지 않아 1년 전 스무 건과 지난주 스무 건을 구분하지 못합니다 | 반감기·주기 세기·k 는 예시값. W3 학습에서 재조정. `04ec783` |
| D-31 | 취향 저장은 A 의 `user_vector` 뿐 | 사용자당 JSON 파일(`profile_store.py`, 256 샤드, 원자적 쓰기, 저장 시 잘라내기). 원본(인덱스·척도)과 6축 스냅숏을 함께 둠 | 2차 회의 결정(G-23). 실 DB 전에도 돌고 검사할 수 있어야 합니다. 원본이 있어야 시드가 바뀌어도 재계산됩니다 | 실 DB 가 붙으면 `user_vector`·`event_log` 로 이전 (M-14) |
| D-32 | 취향이 없으면 그냥 맛 피처 제외 | 맛 피처 제외에 더해 **탐색 비율을 0.4 로** 올립니다 (`cold_exploration_ratio`) | 회의가 요구한 "완전 임의값으로 다양한 레시피". 임의 취향을 넣으면 일관되게 엉뚱해지고, 비우면 다양성 장치가 대신 일합니다 | 실데이터에서 탐색 비율 재조정 (N-05) |
| D-33 | 음의 이벤트(무시·저장 취소)도 취향에 반영 | 무게 0 으로 두어 취향을 만들지 않습니다. 별점은 3점 아래를 0 으로 자릅니다 | "싫어하는 맛" 은 좋아하는 것의 반대가 아닙니다. 가중 평균에 음수를 넣으면 분모가 0 이나 음수가 될 수 있습니다 | 싫어함 모델이 따로 생기면 그때 |

### 3.3 검증 출력 (2026-09-11, 세션 9.11 종료 시점)

```text
uv run ruff check .                   → All checks passed!
uv run ruff format --check .          → 142 files already formatted
uv run python -m mypy src             → Success: no issues found in 62 source files   (E-01)
uv run pytest tests/unit              → 252 passed, coverage 89.74% (기준 80%)
```

A 자체 게이트(`Makefile`, DB 불필요분). Windows 는 `PYTHONIOENCODING=utf-8` 이 필요합니다 (E-11, G-14).

```text
python seeds/validate.py                    → 통과 (경고 13건)
python -m tests.unit.recommend.test_contract → 98건 전부 통과, 종료코드 0 (설정값을 채운 환경. E-13)
                                              빈 .env 에서는 92통과 2실패 종료코드 1 로 끝까지 돕니다
python -m tests.unit.recommend.run           → 74건 전부 통과
python -m tests.unit.recommend.test_match    → 전부 통과 (23건)
python -m tests.unit.recommend.test_role     → 전부 통과 (23건)
python -m tests.unit.recommend.test_batch    → 5건 중 5건 통과
```

커버리지 측정 범위는 `ingest/*`·`repository.py`·`repository_ingest.py`·`engine/mock.py` 를 뺀 1,993문입니다. 뺀 근거는 D-24 의 결정 기록에 있습니다.
변경 규모: 병합 커밋 `da6de58` 에서 `main` 대비 194 파일 · +60,961줄. 들어온 A 커밋 21개.

---

## 4. 실행되어야 할 것

### 4.1 계획 항목 (T)

| ID | 단계 | 항목 | 상태 | 선행 조건 |
|---|---|---|---|---|
| T-01 | Step 0 | `POST /v1/recommend`, `POST /v1/events` 라우터 (prefix `/recommendations` → `/v1`) | 미착수 | T-09 와 함께 실연결 권장 |
| T-02 | Step 1 | `tables.py` (`recommendation_log`, `event_log`, `user_vector`) + `infra/metadata.py` 등록 | 미착수 | P-01. 운영 반영·되돌리기 방법 커밋 body (03의 5절) |
| T-03 | Step 1 | `repository.log_serving_result` 비동기 예외 격리 (TC-1-1, TC-1-2) | 미착수 | `service.ServingLog` → JSONB. I-05 |
| T-04 | Step 1 | `/health` 카운터 `reco_served_total`, `reco_failed_total`, `reco_degraded_total` (TC-1-3) | 미착수 | `main.py` 공용 파일. 카운터 저장 위치 |
| T-05 | Step 2 | A `repository.retrieve` 호출과 완화 재조회 연결 | 미착수 | A 브랜치 병합. 조회 범위 A-10(회의 G-06) |
| T-06 | Step 2 | 알레르기 코드군(19종) → 재료 ID 조회 | 미착수 | P-07. 픽스처 `allergen_groups` 는 대역 |
| T-07 | Step 3 | `feature_stats` → `CorpusStats` | 미착수 | Track A `feature_stats` 계약. I-03 |
| T-08 | Step 4 | 요리군별 `cuisine_priors` Beta(α, β) 집계 | 미착수 | `event_log` 누적. 그전엔 (1, 1) |
| T-09 | Step 5 | `service.recommend()` DB 조립, `RankConfig` ← `Settings` (`config.py` + `.env.example`) | 미착수 | T-02, T-05. D-04 이행 |
| T-10 | Step 5 | `POST /v1/events` → 취향 이벤트 저장 → 페르소나 재계산 | **엔진 쪽 완료** (`PersonaService.record_events`, `engine/persona.py`, `04ec783`). 라우터 연결은 M-15 | T-02 |
| T-11 | Step 5 | `evaluation/feature_report.py` (TC-5-3) | 미착수 | 17개 목록 확정됨(`enums.FEATURE_KEYS`). 지금 재는 것은 9종 |
| T-12 | Step 6 | 계약 검증 42건 (TC-6-1) | 미착수 | 42건 정의 문서, P-05 |
| T-13 | Step 6 | Locust p95 < 58ms (TC-6-3) | 미착수 | P-06 |
| T-14 | 7절 | NDCG@10, Recall@20, 커버리지, ILD 실측 · Bradley-Terry 가중치 | 미착수 | 600쌍 라벨, Track C 하네스 |
| T-15 | 문서 | `docs/recommend/` 커밋과 `docs/README.md` 등록 | 완료 (`60f1d40`) | P-09 |
| T-16 | 검증 | `recommend_engine_verification.md` 6절 수정 제안 9건의 채택 여부 결정과 반영 | 완료 (X-01~X-10, 9.3) | 2차 재검증 `recommend_engine_verification.md` 7절 |
| T-17 | 통합 | A 트랙과 3자 회의 안건 정리와 병합 계획: P-11~P-16 | 1차 회의 완료(G-01~G-05, G-07 반영). 남은 안건 G-06, G-08~G-12 | 9.5 |
| T-18 | 통합 | `UserHistory` 를 채우는 repository 함수 (선호·기피 재료, 조리 이력, 클러스터 관측) | 미착수 | A 의 `user_ingredient_pref`·`event_log`·`user_cluster_stat`. 회의 안건 N-11 |

### 4.2 선행 조건 (P)

| ID | 항목 | 왜 | 담당 / 승인 |
|---|---|---|---|
| P-01 | `.env`: 필수 6개(`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `INTERNAL_API_KEY`, `GEMINI_API_KEY`)만 채우고 기본값 있는 15줄 삭제 | `KEY=` 빈 값을 pydantic-settings 가 값으로 취급 → 숫자 13필드 실패 → 단위 테스트 56건 실패. 추천 테스트는 무관 | 유재현 |
| P-02 | 해결됨. `main`(c504fdc 계열)의 conftest 가 `env_file` 을 막습니다 | | |
| P-03 | `config.py` `env_ignore_empty=True` 제안 | `.env.example` 복사만으로 기본값 동작. 검증 완료 | 김민경 |
| P-04 | `--import-mode=importlib` 승인 (D-12) | 적용·검증 완료. 공용 파일. A 도 `pyproject.toml` 을 고쳤으므로 병합 때 함께 봅니다 (회의 안건 G-11) | Tech Lead |
| P-05 | `Makefile` (`make contract`, `smoke`, `log-test`, `feature-test`) | 계획서 검증 명령이 참조하나 저장소에 없음. 신규 도구 승인(01의 1절) | Tech Lead |
| P-06 | `locust` 의존성 (`uv add`, body 에 사유) | TC-6-3 | Tech Lead |
| P-07 | 기피 재료(`avoid_ingredient_ids`) 출처 | 요청 스키마에 없음. `UserHistory` 에 자리만 | BE 계약 협의 |
| P-08 | 대체재(`substitute_ids`) 출처 | 2단계 폴백용 데이터 없음. 없으면 건너뜀 | Track A |
| P-09 | `docs/recommend/` 파일명 언더바, `docs/README.md` 목록 등록, 계획서 코드블록 포맷 | 04의 1.2·2.2. 2026-09-10 세션에서 처리 | 유재현 → 김민경 통보 |
| P-10 | 브랜치·병합 정책 | PR 은 현 팀 정책상 대상 아님. `main` 병합은 A·B 완료 후. 선례 PR #3(2,409줄·9커밋·merge commit)은 OCR 단독이라 상황 다름 | 유재현 |
| P-11 | 02 규약에 `stage.py` 추가 | D-17 로 도메인 루트 파일이 7개. 원격 `develop-data-part` 의 02 문서에도 `stage.py` 없음. 01의 9절 개정 절차 | A, 김민경 |
| P-12 | A 브랜치와의 파일·이름 충돌 조정 | `engine/rank.py`, `service.py`, `router.py`, `repository.py`, `__init__.py` 가 양쪽에 별개 구현. `stage.ScoredCandidate` 형태 상이(A 17 피처 dict, B 5 블록). `RecommendRequest/Response` 는 A `make contract` 98건이 의존 | 3자 회의 |
| P-13 | A `tests/conftest.py` 의 `collect_ignore_glob = ["unit/recommend/*.py"]` | B 테스트 전체가 수집에서 빠짐. A 가 파일명 명시로 수정 예정. 병합 전 확인 | A |
| P-14 | 폐기 (D-21). propensity 는 확률로 통일했습니다 | 회의 결정 G-05 | |
| P-15 | 폐기 (D-19). 6축으로 맞췄고 값이 없는 축은 갱신에서도 건드리지 않습니다 | | |
| P-16 | 04의 1.1 트리에 `docs/recommend/`(설계 명세·기록·회의 안건) 추가 | 기록을 저장소에 두기로 한 결정(`../decisions/2026-09-10_recommend_record_dual_format.md`)의 배치 근거가 규칙에 없음. 01의 9절 절차. 회의 안건 G-09 | 유재현 → 팀 전원 |

### 4.3 MUST TODO — 지금은 할 수 없는 것

여기 있는 것은 **하기 싫어서 미룬 것이 아니라 선행 조건이 없어서 못 하는 것**입니다.
각 줄의 "언제 가능한가" 가 채워지는 순간 바로 착수합니다. 새 정보를 만들지 않고 이미
있는 추적 번호를 가리킵니다 — 같은 사실을 두 곳에 적으면 한쪽이 낡습니다.

| 무엇 | 왜 지금 못 하는가 | 언제 가능한가 | 추적 |
|---|---|---|---|
| 라우터를 실엔진에 연결 | 후보를 줄 DB 가 없습니다. 지금 연결하면 빈 후보로 200 을 돌려줍니다 | DB 기동 후 | M-01, N-03, F-29 |
| 서빙 로그 적재 | 쓸 테이블이 없습니다. 로그는 소급이 안 되므로 켜는 순간 재현 인자 셋을 함께 넘겨야 합니다 | DB 기동 후. M-01 과 같은 변경에서 | M-05, T-03 |
| 사용자 이력 피처 4종 | `user_ingredient_pref`·`event_log`·`user_cluster_stat` 이 비어 있습니다. 가중치 0.21 이 순위에 관여하지 않습니다 | A 의 배치가 채운 뒤 | M-03, N-11, F-15 |
| 로그 실패 카운터 노출 | 로그를 아직 쓰지 않아 셀 것이 없습니다 | M-05 와 같은 변경에서 | M-07, F-33 |
| 손잡이 정본 통일 | `Settings` 와 `RankingPolicy` 중 어느 쪽인지 정해야 합니다. 지문에 들어갈 값 집합이 바뀝니다 | N-02 결정 후 | M-08, F-32 |
| `f_time_fit` 재정의 | 값을 바꾸는 결정이라 혼자 정할 수 없습니다. 실데이터 분포도 필요합니다 | W3 가중치 학습에서 | M-10, N-13, F-27 |
| 죽은 가중치 0.26 재배분 | 같은 이유입니다. 이력이 붙으면 0.23 이 저절로 살아납니다 | M-03 이후 재측정 → W3 | F-28, N-04, N-05 |
| `/health` 의 `redis` | 저장소에 redis 클라이언트가 없어 찔러 볼 대상이 없습니다 | A 가 붙이거나 필드를 뺄 때 | M-11, G-19, F-31 |
| DB 가 필요한 A 게이트 4종 | `make smoke`·`log-test`·`ddl-test`·`feature-test` 는 실 DB 를 씁니다. 병합 시 못 돌렸습니다 | DB 기동 후 | M-12 |
| 커버리지 `omit` 되돌리기 | A 의 단독 스크립트를 pytest 로 옮겨야 합니다. A 의 파일이라 단독 결정 불가 | G-08 결정 후 | M-13, D-24 |
| 게이트 예외 4건 승인 | 01의 3.3·6.1 이 Tech Lead 승인을 요구합니다 | 회의에서 | G-16 |
| 01의 3.4 개정 승인 | 01의 9절이 팀 전원 승인을 요구합니다 | 회의에서 | D-28 |
| 규약 개정 2건 | `docs/recommend/` 배치와 `stage.py`·`enums.py`·`policy.py` 가 규약에 없습니다 | 회의에서 | G-09, G-10 |
| `.env` 채우기 | 값이 팀 비밀 저장소에 있고 유재현이 직접 넣습니다. 비면 서버도 `make contract` 도 못 돕니다 | 유재현 | N-01, E-13 |
| `repository_ingest.py` 629줄 분리 | A 가 904줄이던 `repository.py` 를 365줄과 이 파일로 나눴지만(`5d41e8f`) 이 파일이 다시 02의 5.1 상한 500줄을 넘습니다. 도메인 루트도 9개 파일로 같은 절의 디렉터리 상한 8개를 넘습니다(F-34). A 의 파일이라 단독 결정 불가 | A 와 합의 후 | G-10 묶음 |
| B 함수 인자 5개 초과 6곳 | 02의 5.1 의 확정 기준입니다. 요청·문맥 dataclass 로 묶는 것이 N-02 와 겹칩니다 | N-02 와 함께 | N-02 |
| 상대 import 차단이 꺼져 있음 | `ban-relative-imports` 가 설정돼 있으나 `select` 에 `TID` 가 없습니다. `main` 의 설정이라 단독 수정 불가 | 회의에서 | G-16 묶음 |
| 취향 원본 JSON → DB 이전 | `user_vector`·`event_log` 가 준비돼야 합니다. 옮긴 뒤 두 저장소의 페르소나를 대조해야 합니다 | DB 기동 후 | M-14, D-31 |
| 온보딩·이벤트 라우트 실연결 | 라우터가 목업을 부릅니다. `PersonaService` 는 있으나 쓰는 곳이 없습니다 | M-01 과 같은 변경에서 | M-15 |
| 3축 척도 범위 확정 | 계약은 0~4, 회의 기록은 1~5 입니다. 엔진은 0~1 만 받아 한 곳에서 바꾸면 되지만 어느 쪽이 맞는지는 정해야 합니다 | 회의에서 | G-24 |
| Mock 카탈로그 맛 스케일 | 시드(실집계)와 분포가 달라 고른 음식으로 만든 취향이 Mock 평균 아래에 놓입니다. 맛 정합 lift 가 작게 나옵니다 | 생성기 수정 | N-14, F-35 |

## 5. 고려사항 (C)

명세가 정하지 않아 구현자가 정한 동작. 의도인지 실수인지 헷갈릴 지점.

| ID | 내용 | 위치 |
|---|---|---|
| C-01 | 폐기 (X-04, `05e47a3`). 인기순 폴백은 조리시간 상한을 먼저 지키고 부족할 때만 해제 | `candidate.select_candidates` |
| C-02 | 탐색 축소 조건: 후보 수 == `top_k`, 낯선 후보 없음, 낯선 풀 < 슬롯 × 2, 품질 < 후보 중위수 (X-05). 낯선 후보 = 비선호 요리군 또는 맛 블록 < 0.4 (X-07). `preferred_cuisines` 비면 요리군 있는 전부가 낯선 후보 | `rerank.pick_exploration`, `_is_novel` |
| C-03 | `expiring_ingredient_ids ⊄ pantry` 허용. 교집합 안 취함 (페르소나 1011 의 `99`) | `rank.expiring_score` |
| C-04 | `household_size` 는 문맥에 있으나 어떤 블록도 사용 안 함 | `UserContext` |
| C-05 | 품질 블록 부분 결측: 한쪽만 있으면 그쪽, 둘 다 없으면 None. 픽스처 20건에 1건 품질 결측 | `rank.quality_score` |
| C-06 | `config_fingerprint` = `RankConfig` 전 필드 MD5(`usedforsecurity=False`). 비가중치 값도 포함 | `RankConfig.fingerprint` |
| C-07 | propensity: 개인화 1.0 · 균등 = 풀 크기 / 뽑는 수 · Thompson = MC 32회 추정 역수(라플라스, X-08 로 64→32). 표본 수 `propensity_samples` | `rerank._win_probability` |
| C-08 | MMR 은 점수 상위 200건 × 슬롯 수, IDF 합 사전 계산, 최대 유사도 증분 갱신 (X-08). 후보 500 기준 rerank 중앙값 8.5 ms, 엔진 p95 22 ms (`recommend_engine_verification.md` 7.2) | `rerank.mmr_select` |
| C-09 | 알레르기 코드군 픽스처 18종. 19종 중 SULFITE 는 대응 재료 없어 제외 | `generate_mock_fixtures.ALLERGEN_GROUPS` |
| C-10 | 픽스처 JSON 은 생성 산출물이나 커밋 대상(02의 2.3). 개인정보 없음 | `tests/fixtures/recommend/` |
| C-11 | engine 파일 7개(`candidate`, `context`, `explain`, `feedback`, `penalty`, `rank`, `rerank`). 02의 5.1 디렉토리당 8개 기준 안 | `engine/` |

---

## 6. 다음 세션 시작 절차

```bash
git checkout feat/recommend-engine-core && git pull
uv run pytest tests/unit/recommend --no-cov          # .env 없이 96 passed 가 정상
uv run ruff check . && uv run python -m mypy src
```

1. 1절 상태 요약 → 4절 → 5절.
2. 4.2 의 P 항목 중 풀린 것 확인. 특히 P-01, P-04, P-09.
3. 계획서 다음 단계 착수. 종료 시 1절·3절·9절 갱신, 사람용 서술본 갱신, `docs(plan): ...` 커밋.

---

## 7. 작업 환경 메모 (E)

2026-09-10, 유재현 Windows 11.

| ID | 현상 | 대응 |
|---|---|---|
| E-01 | `uv run mypy` → 앱 제어 정책 차단 (os error 4551) | `uv run python -m mypy src` |
| E-02 | `ruff format --check .` 가 md 코드블록도 검사 (ruff 0.16) | 계획서 코드블록 1건은 `uv run ruff format <file>` |
| E-03 | `.env` 안 건드리고 전체 스위트 | 유효한 `.env` 둔 임시 cwd 에서 `uv run --project <repo> pytest <repo>/tests/unit -c <repo>/pyproject.toml --rootdir <repo> --cov=<repo>/src` |
| E-04 | ruff E501 이 한글 폭 2 | 한글 docstring 한 줄 45자 안팎 |
| E-05 | 한글 문장의 `×` → RUF002 | `*` 또는 "곱하기" |
| E-06 | `core.autocrlf=true`. Git Bash `grep -c $'\r'` 가 줄 수를 돌려줌 | 줄 끝은 `python -c "print(b'\r\n' in open(f,'rb').read())"` |
| E-07 | `Path.write_text` 가 CRLF | 생성 스크립트 `newline="\n"` |
| E-08 | Bash heredoc 10KB 초과 → 명령 잘림 | 큰 파일은 편집기 도구(Write) |
| E-09 | addopts 에 `-q` 있음. `-q` 추가 시 요약 줄 사라짐 | 명령줄에 `-q` 안 붙임 |
| E-11 | A 의 `make` 검사가 통과 표시(U+2713)를 찍다가 `UnicodeEncodeError` (cp949). 검사 실패가 아니라 콘솔 인코딩 | `PYTHONIOENCODING=utf-8` 을 세우고 실행. 항구 대책은 G-14 |
| E-13 | `make contract` 가 `.env` 의 빈 값으로 `create_app()` 에서 죽습니다. `try` 가 `ImportError` 만 잡아 `ValidationError` 가 그대로 올라오고, **마지막으로 보이는 줄이 통과 표시(U+2713)라 눈으로는 통과처럼 읽힙니다**(F-30) | 설정값 20종을 환경변수로 채우고 실행하면 98건 전부 통과·종료코드 0. 항구 대책은 N-01(.env 정리)과 G-18 |
| E-14 | `psycopg` 의 `pq.cp312-win_amd64.pyd` 가 어제 그대로인데 앱 제어 정책이 오늘 막기 시작. `tests/conftest.py` 가 `infra.db` 를 import 하므로 **전체 pytest 가 수집 단계에서 죽음** | 같은 버전 재설치(`uv pip install --reinstall --no-deps psycopg-binary==3.3.5`)로 해소. 정책이 파일 인스턴스 단위로 막는 듯합니다(E-12 와 같은 현상). 코드 변경 없음 |
| E-12 | A `uv.lock` 의 `pillow-heif` 1.7.0 → `_pillow_heif` DLL 이 앱 제어 정책에 차단. 영수증 검사 8건이 수집에서 죽음 | `uv.lock` 항목만 `main` 값 1.6.0 으로 되돌림 (D-25). 1.6.0 은 같은 머신에서 정상 import |
| E-10 | git 사용자 설정 전무 | 저장소 로컬 `user.name=유재현`, `user.email=yjhorion@gmail.com`. 변경은 `git config --local` 후 push 전 `git rebase --exec 'git commit --amend --no-edit --reset-author' main` |

---

## 8. 통합 접점과 가정

데이터 없는 상태의 파이프라인 선 설계. 실데이터(Track A)·BE·DB 확정 시 아래 접점에서 수정. 코드 구조는 코드와 계획서 5절로 파악하고 여기서는 **코드에서 읽히지 않는 가정**만 대조.

### 8.1 접점 (I)

| ID | 방향 | 계약 | 지금 | 실연결 시 |
|---|---|---|---|---|
| I-01 | BE → 엔진 | `RecommendRequest`, `FeedbackEventRequest` | 페르소나 12건 | BE DTO. `extra="ignore"` 라 필드 추가는 무해, 이름 변경은 깨짐 |
| I-02 | Track A → 엔진 | `stage.RecipeCandidate` | `catalog.json` 120건 | `recipe_feature` 행 변환. 컬럼: `recipe_id`, `title`, `essential_ids`, `all_ids`, `flavor_vec`(6축, 앞 3축만 사용), `popularity_score`, `quality_score`, `cook_minutes`, `cuisine`, `product_ids`. A 확인(`origin/develop-data-part`): `recipe_feature` 46,353건에 `essential_ids`·`all_ids`·`flavor_vec`·`popularity_score`·`n_unmatched` 채워짐. `title`·`cuisine`·`product_ids` 는 `recipe`·`cuisine_taxonomy` 조인 필요 |
| I-03 | Track A → 엔진 | `stage.CorpusStats` | conftest 가 픽스처에서 계산 | `feature_stats.flavor_mu` REAL[6] 존재(A DDL). 앞 3축 사용. `ingredient_idf`·`ingredient_names` 는 별도 조회 |
| I-04 | DB 이력 → 엔진 | `stage.UserHistory` | 빈 값 / 테스트 지정 | `user_vector`(A DDL: `taste_vec` REAL[6], P-15) · 알레르기(A DDL `user_allergy` 테이블, T-06) · 기피 재료(P-07) · 최근 7일 노출(`recommendation_log`) · 최근 14일 조리(`event_log`) · 대체재(A DDL `ingredient_substitute` 테이블, P-08) · `cuisine_priors`(T-08) |
| I-05 | 엔진 → DB | `service.ServingLog` | 테스트 모양 검사 | A 에 `repository.write_recommendation` 이 이미 있음. `candidates_features` ← `ScoredCandidate.blocks/base_score/score`, `propensity_scores` ← `ServedItem.propensity`(역수. A 는 확률, P-14) |
| I-06 | 엔진 → BE / Track C | `RecommendResponse`, `FeedbackEventResponse`, `meta.config_fingerprint` | 테스트 모양 검사 | 라우터 연결 시 BE 재확인. `meta` 고정 5필드 (D-07) |

### 8.2 데이터 확정 시 대조할 가정 (A)

| ID | 가정 | 어긋나면 고칠 곳 |
|---|---|---|
| A-01 | **확정 (D-16)**: 축은 `(spicy, salty, sweet)` = A 의 (매움, 짠맛, 단맛), 0~1 척도. 6축 입력은 앞 3축만(`stage.RecipeCandidate` 검증기). `user_vector.taste_vec` 은 A DDL 에서 6축 (P-15) | `schema.FLAVOR_AXES`, `TastePreference.as_vector`, `feedback.DEFAULT_BEHAVIOR_VEC` |
| A-02 | `popularity_score`, `quality_score` 0~1 정규화 완료 | `rank.quality_score` 앞 정규화 (`_clamp` 은 절단만) |
| A-03 | 재료 ID 정수, 보유·레시피·알레르기 동일 ID 체계 | 전 모듈 집합 연산 |
| A-04 | `essential_ids ⊂ all_ids` | 충족도 `essential`, 자카드·기피·알레르기 `all` |
| A-05 | 알레르기 코드군 19종 ↔ 재료 ID 매핑 존재 | T-06. 픽스처 `allergen_groups` 대역. A DDL 에 `user_allergy` 테이블 있음 — 매핑 형태는 A 스키마에서 확인 |
| A-06 | `cuisine` 어휘 == BE `preferred_cuisines` 어휘 | `rerank._is_novel` 문자열 일치 |
| A-07 | `cook_minutes` null 비율 낮음 | null → ctx 블록 측정 불가 → 분모 변동 |
| A-08 | `expiring ⊂ pantry` 여부 | C-03 |
| A-09 | `feature_stats` 평균·IDF 는 전체 코퍼스, 갱신 주기 정의 | 중심화 결과가 갱신 시점에 흔들림 |
| A-10 | **후보 조회 범위**: `select_candidates` 는 넘겨받은 풀 안에서만 완화. 계획서 SQL 처럼 k=2 + `LIMIT 500` 이면 완화 후보가 없어 폴백 빈손 | T-05: k=4 로 한 번에 조회(정렬 부족수 오름차순이라 상위 500 이 k=2 포함) 또는 단계별 재조회. 인기순 폴백은 별도 조회. A 에 `repository.retrieve` 와 SQL 함수 `retrieve_candidates`(A D-14) 가 이미 있어 T-05 는 그 호출·통합으로 재정의. 조회 범위 결정은 그대로 필요 |
| A-11 | 46,552건을 파이썬으로 거르지 않음. `candidate.retrieve` 는 SQL 결과 재검증·Mock 용 | 전체 풀 메모리 적재 금지 |

### 8.3 수정·롤백 손잡이

| 대상 | 손잡이 |
|---|---|
| 점수 수식 | `engine/rank.py` 블록 함수 1개. `test_rank.py` 의 `test_zero_drop_*`, `test_centering_*` 가 먼저 깨져야 함 |
| 가중치·계수 | `RankConfig` 한 곳. `config_fingerprint` 자동 변경. Step 5 후 `Settings` |
| 탐색 정책 | `rerank.pick_exploration`, 비율 `exploration_ratio`, 위치 `rerank._mix` |
| 계획 대비 결정 되돌리기 | 3.2 의 "되돌릴 조건" |
| 단계 단위 되돌리기 | 커밋이 모듈 단위(9.1). `git revert <hash>`, 3~8번은 뒤에서 앞 순서 |
| 통합 전 기준점 | `cbdf312` 에 태그 제안 (예 `reco-b-layer1`). 미부여 |

---

## 9. 세션 기록

### 9.1 2026-09-10 — Layer 1

| 항목 | 내용 |
|---|---|
| 브랜치 | `feat/recommend-engine-core` (main 분기) → push. 원격에 파트 A `develop-data-part`, `data_part_backup` 별도 |
| 커밋 | 8개. 각 커밋 시점 트리로 ruff·mypy·`pytest tests/unit/recommend` 통과 후 커밋. 커버리지 게이트는 최종 상태만(E-03) |
| 커밋 목록 | `63f3614` build: enable pytest importlib import mode · `2acc09a` feat(recommend): add contracts, domain models and mock fixtures · `d172b94` feat(recommend): add the input adapter and taste feedback loop · `c575fd1` feat(recommend): add candidate selection with tiered fallback · `4edbb6f` feat(recommend): add zero-drop ranking and multiplicative penalties · `f4ba7c4` feat(recommend): add mmr rerank with exploration slots · `c241ab7` feat(recommend): add z-salience recommendation reasons · `cbdf312` feat(recommend): assemble the serving pipeline |
| 세션 중 수정한 결함 | D-01(개인화 슬롯 난수 의존), D-02(`top_k=50` 에서 25건) |
| 공용 파일 변경 | `pyproject.toml` addopts (D-12) |
| 문서 | 작업기록 두 형식 규칙 도입(`README.md`). 파일명 언더바. `docs/README.md` 에 `plan/` 등록 |
| 넘긴 것 | 4절 전체. 특히 P-01, P-04, A-10 |

### 9.2 2026-09-10 — Mock 검증

| 항목 | 내용 |
|---|---|
| 산출물 | `scripts/eval_recommend_mock.py`. 12 페르소나 실행, 판정 지표, 감점·피드백·결정론 시나리오, 후보 500건 지연시간과 단계 분해 |
| 기록 | `recommend_engine_verification.md` + `human/` 서술본. 기획서 조항 23건 대조, 검증 항목 16건, 발견 12건, 수정 제안 9건 |
| 코드 변경 | 없음. 수정은 결과를 보고 결정 (T-16) |
| 커밋 | `chore(recommend): add the mock evaluation script` · `docs(plan): record the mock evaluation and fix proposals` |

### 9.3 2026-09-10 — 수정 반영과 A 트랙 정렬

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 결정: X 전부 반영. A 트랙 공유 문서(맞출 것 3가지). `origin/develop-data-part` 를 fetch 해 실체 확인 |
| 우선순위 판단 | 맛 축 순서(D-16)를 먼저 고침. 축이 어긋난 채로는 1차 평가와 X 재검증이 전부 틀린 축 위에서 나오기 때문 |
| 커밋 (순서대로) | `ba3e0f1` fix: 축 순서 · `0706f66` fix: 맛 사유 방향·부족 개수 (X-01, X-03) · `05e47a3` fix: 폴백 조리시간 상한 (X-04) · `c1f26f4` feat: 맛 블록 감쇠 (X-02) · `0d87904` feat: 요리군 항 (X-06) · `3ed126e` fix: 탐색 축소·맛 novelty (X-05, X-07) · `1d26e0d` perf: IDF 합·MMR 풀 (X-08) · `85c9dcd` docs: 재정렬 금지 (X-09) · `e2fc72d` refactor: `stage.py` (D-17) · `33ea107` fix: 0점 블록 제외 (X-10) · `7d72324` chore: 평가 분류 |
| 검증 | 커밋마다 ruff·mypy·`pytest tests/unit/recommend` 통과. 최종 111 passed, 전체 191 passed / 1 failed(P-02) / coverage 95.16%. 2차 Mock 재검증은 `recommend_engine_verification.md` 7절 |
| A 브랜치에서 확인한 것 | `stage.py`(RetrievalInput, Candidate, ScoredCandidate(17 피처 dict), RankedItem …) · `enums.FEATURE_KEYS` 17개와 `DEFAULT_WEIGHTS`(f_coverage .24, f_taste .16, f_expiring .15, f_ing_pref .11, f_cooccur .10, f_popularity .10, f_missing .05, f_cuisine .04, f_time_fit .03, f_season .02) · `engine/{rank,explore,reason,serendipity,mock}.py` 별개 구현 · `service.py` 는 카운터만(흐름은 `engine/mock.py`) · `repository.py` 365줄(`retrieve`, `write_recommendation`) · `router.py` 에 `/v1/recommend`, `/v1/events`, pantry·onboarding·search · `deploy/init/02_schema.sql`(`recipe_feature` flavor_vec REAL[6], `feature_stats.flavor_mu` REAL[6], `user_vector.taste_vec` REAL[6], `user_allergy`, `ingredient_substitute`, `recommendation_log`, `event_log`, `scoring_config`) · `Makefile`(`contract`, `log-test` 등) · `tests/conftest.py` 의 `collect_ignore_glob` (P-13). 공유 파일 변경: `pyproject.toml` +85, `config.py` +64, `main.py` +24, `conftest.py` +22, `.gitignore` +51 |
| 넘긴 것 | T-17 (P-11~P-15), P-01 |

### 9.4 2026-09-10 — 문서 규칙 정렬

| 항목 | 내용 |
|---|---|
| 입력 | `origin/main` 에 04_DOCUMENTATION_RULES 분리 도착. 유재현 지시: 기록을 규약에 맞춰 다시 쓰고 회의 안건을 타 파트·내부로 나눠 작성 |
| 병합 | `origin/main` 을 merge (`chore: merge origin/main …`). 충돌은 `docs/README.md` 헤더 1줄. `main` 의 conftest 가 `env_file` 을 막아 P-02 해결 |
| 이름·배치 | `docs/plan/` → `docs/recommend/`. 식별자 파일명(`PM_ENG_RECO_B_001_*`) → 내용이 드러나는 이름(`recommend_engine_design`, `_design_digest`, `_work_log`, `_verification`, `_meeting_agenda`). 하위 `README.md` 삭제(목록은 `docs/README.md` 한 곳, 04의 1.3). 두 형식 규칙은 `docs/decisions/2026-09-10_recommend_record_dual_format.md` 로 |
| 형식 | 계획서를 04 형식으로 재작성(표준 헤더, `####` 제거, `~합니다`체, 체크박스 결과 열 제거, 예시값 표기). 에이전트용 문서의 글머리 문장을 `~합니다`체로. `02의 2.5` 참조를 `04의 2.2` 로 |
| 신규 | `recommend_engine_meeting_agenda.md` + 서술본. G-01~G-12, N-01~N-10 |
| 넘긴 것 | G 전부(회의), P-16(04 개정 신청), N-01 |

### 9.5 2026-09-10 - A 계약 채택과 엔진 재작성

| 항목 | 내용 |
|---|---|
| 입력 | 1차 3자 회의 결정(G-01~G-05, G-07). `origin/main`(8aab6bf) 병합, `origin/develop-data-part`(658d79a) 참조 |
| 확인한 사실 | A 는 **아직 main 에 병합되지 않았습니다.** `develop-data-part` 가 main 을 자기 쪽으로 받아 PR 준비를 마친 상태이며 main 대비 21 커밋 앞섭니다. main 의 `src/features/recommend/` 에는 여전히 빈 골격과 `router.py` 뿐입니다 |
| 가져온 것 | `enums.py`, `stage.py`, `engine/{rank,reason,explore,serendipity}.py` 6개. 26곳만 고쳤습니다 - 수학 기호 21(전역 ignore 는 01의 3.3상 승인 필요), import 순서 2, 코드 3(S311 noqa, 항상 참인 멤버십 검사, 인자 타입) |
| 지운 것 | B 의 `schema.py`, `engine/{explain,penalty,feedback}.py`. A 가 같은 일을 이미 하고 있어 두 벌이 되면 propensity 정의가 갈립니다 |
| 새로 쓴 것 | `policy.py`(손잡이·지문·trace 파라미터), `engine/taste.py`(6축 None-aware), `engine/context.py`(RecipeFeature·UserHistory·UserContext), `engine/feature.py`(17 피처), `engine/score.py`(가중합·감점), `engine/rerank.py`(MMR·탐색), `engine/candidate.py`(완화 계획), `service.py`(A 카운터 + `rank_candidates`) |
| 커밋 | `3929ee9` merge main · `c459c2f` A 계약·엔진 채택 · `256c702` 17 피처·6축 점수 계산 · `9896fbc` 픽스처·테스트 |
| 검증 | `pytest tests/unit` **190 passed / 0 failed**, coverage 92.21%. ruff·mypy 통과. Mock 재검증은 검증 기록 8절 |
| 넘긴 것 | G-06·G-08~G-12(회의), N-01, N-03, N-11 |

### 9.6 2026-09-10 - A 브랜치 전량 병합

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 지시: `origin/develop-data-part` 현재 버전을 전부 우리 브랜치에 합치고 `main` 에 병합 가능한 상태로 만들 것. `main` 병합 자체와 `stage.py` 규약 논의는 A 개발자와 함께 처리하므로 제외 |
| 병합 | `git merge --no-ff origin/develop-data-part`(658d79a). 충돌 8건 — `engine/{explore,rank,reason,serendipity}.py`, `enums.py`, `stage.py`, `service.py` 는 우리 쪽 채택(A 내용 + 9.5 의 26곳 수정, `service.py` 는 A 카운터 + B `rank_candidates`), `docs/README.md` 는 양쪽 병기 |
| 규모 | 병합 커밋 `da6de58` 에서 `main` 대비 194 파일 · +60,961줄. 병합으로 들어온 A 커밋 21개 |
| 게이트 시작값 | ruff 373건 · mypy 31건 · 커버리지 46.15%. A 는 자기 `pyproject.toml` 에 게이트 미통과를 명시해 두었습니다 |
| 경계 | 서빙 경로(`src/**`)는 손으로 고치고, 사람이 한 번 돌려 읽는 도구는 범위를 좁힌 예외. 근거는 `../decisions/2026-09-10_merge_data_track_gate_exceptions.md` |
| `src/` 수정 (D-23) | ruff 45건 — `repository.py` 중간 import 8개를 위로, `mock.py` 세미콜론·미사용 언팩·S311 사유, `flavor.py`·`threshold.py` `zip(strict=)`, `match.py` 대문자 지역변수, `parse.py` 정규식 줄 분리, `__init__` 반환형 4곳. mypy 31건 — `tuple`·`dict` 타입 인자, `fetchone()` 의 None 처리 4곳, `_trgm` 반환형 오기(`str \| float` → `float`), `IngredientRole \| None`·`int \| None` 좁히기, `db.py` 커서 캐스트 |
| 예외로 둔 것 (D-24) | `adapter.py` 의 `ANN401`(모양을 모르는 크롤러 JSON 경계), `scripts/reco/bench` 검사 제외, 도구 파일 9개에 파일별 규칙 코드, 커버리지 `omit` 3항목. 전부 Tech Lead 승인 대기 |
| 되돌린 것 (D-25) | `uv.lock` 의 `pillow-heif` 1.7.0 → `main` 값 1.6.0. 이 병합과 무관한 변경이고 1.7.0 의 DLL 이 Windows 앱 제어 정책에 걸려 영수증 검사 8건이 수집 단계에서 죽습니다 |
| 고친 것 (D-26) | A `tests/conftest.py` 의 `collect_ignore_glob = ["unit/recommend/*.py", "integration/*.py"]` 를 파일 8개 명시로. 그대로 두면 B 의 pytest 검사 63건과 `integration/test_receipt_pipeline.py` 가 **세어지지 않은 채** 사라집니다 (G-08 의 (a)안) |
| 추가한 것 | `types-PyYAML`(dev). A 의 `src/` 3개 파일이 `yaml` 을 import 하는데 스텁이 없어 mypy 가 막혔습니다 |
| A 파일에 남은 차이 | 37 파일. 대부분 `ruff format` 출력이고 의미 변경은 위 D-23 뿐입니다 |
| 검증 | ruff `All checks passed!` · `ruff format --check` 129 files · mypy 58 files · `pytest tests/unit` 190 passed / coverage 88.12%. A 자체 게이트 — `validate` 통과(경고 13), `normalize-test` 74+23+23+5건 통과. `contract` 는 이때 통과로 적었으나 **오기입니다** — `.env` 의 빈 값 때문에 59번째 체크에서 죽고 있었습니다. 바로잡은 값은 10절 |
| 새 안건 | G-13~G-17, N-12 |
| 넘긴 것 | `main` 병합(A 개발자와), G-06, G-09~G-17, N-01, N-03, N-11 |

### 9.7 2026-09-11 - 조용한 실패 점검

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 질문: 규칙 적합성과 동작 재확인, 그리고 "에러는 안 나는데 기능을 못 하는 것" 이 있는지 |
| 방법 | 게이트가 전부 통과하는 상태에서 세 축으로 측정 — 설정값이 계산에 닿는가(정책 손잡이 18종 역참조), 피처가 실제로 변하는가(12 페르소나 260 노출의 분포), 응답이 근거 있는 값인가(HTTP 실호출) |
| 찾은 것 | 8건. 상세는 검증 기록 10절 F-24~F-31 |
| 고친 것 (D-27) | F-24 `/health` 가 DB 확인 없이 `db: true` · F-25 로그의 `propensity_mc` 가 실계산과 다를 수 있음 · F-26 `warm_event_count` 이중 정의. 회귀 검사 6건 추가 |
| 남긴 것 | F-27 `f_time_fit` 구조적 상수(N-13) · F-28 가중치 0.26 무효 · F-29 라우터가 목업(N-03) · F-30 `make contract` 가 조용히 중단(G-18) · F-31 `redis: true` 근거 없음(G-19) |
| 바로잡은 기록 | 9.6 의 `contract 통과` 는 오기였습니다. 종료 코드를 보지 않고 tail 의 통과 표시만 읽었고, 실제로는 `.env` 빈 값 때문에 59번째 체크에서 죽고 있었습니다. 설정값을 채우면 98건 전부 통과입니다 |
| 문서 정정 | `ruff format` 128→129, 변경 규모 193→194, 통과 표시 문자 3곳, G-08·G-10 수치, 계획서 두 편의 "회의 이전" 표시, 결정 기록의 실측 범위 |
| 문서 점검 (같은 세션) | 04 기계 검사를 21개 문서에 돌려 2건 수정 — 구분선이 `###` 앞에 온 것(2.2의 3), 사람용 세션 제목이 `### N.M` 이 아니던 것(2.2의 2). 1절의 `132 files` 도 문서 2개를 추가한 뒤라 낡아 고쳤습니다(3.4의 4번에 제가 다시 걸린 것입니다). 남은 위반 2건은 `main` 의 01에 원래 있던 것입니다 |
| 식별자 충돌 | 점검표 항목이 고려사항과 같은 `C-` 를 쓰고 있어 `M-` 로 분리했습니다. 두 형식 규칙의 범례에 M 을 넣고, 대조는 **그 문서가 소유한 접두어**로 한다는 것을 명시했습니다 — 다른 문서의 항목을 참조로 인용한 것은 자기 문서의 쌍에서 이미 대조되기 때문입니다 |
| 검증 | ruff·format·mypy 통과, `pytest tests/unit` **196 passed** / coverage **88.18%**. A 자체 게이트 — `validate` 통과, `contract` **98건 전부 통과**(설정값을 채운 환경), `normalize-test` 125건 통과 |
| 넘긴 것 | N-13, G-18, G-19, N-01, N-03 |

### 9.8 2026-09-11 - DB 전환 준비와 보고 규칙

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 지시 네 가지 — DB 구축 시점의 수정 내역을 기록하고 건너뛰지 못하게 할 것, 오보가 재발하지 않게 룰셋에 넣을 것, 지금 개선 가능한 것은 개선하고 불가능한 것은 MUST TODO 로 남길 것, 전부 `main` 병합 가능한 형태일 것 |
| DB 전환 점검표 | `recommend_engine_db_cutover.md` + 사람용. M-01~M-13. 항목마다 지금 상태·전환할 때 하는 일·확인 근거를 적고, 함께 처리해야 하는 묶음 셋을 따로 표시 |
| 건너뛰지 못하게 하는 장치 | `tests/unit/recommend/test_db_cutover.py` 10건. 각 항목이 아직 전환 전 상태임을 못 박습니다. 건드리면 검사가 깨지고 실패 메시지가 항목 번호를 가리킵니다. `Settings` 와 `RankingPolicy` 의 값 3종은 갈라지지 않게 상시로 붙잡습니다 |
| 보고 규칙 (D-28) | 01에 3.4 를 추가했습니다 — 종료 코드로 판정, 안 돌린 명령의 결과를 적지 않음, 남의 건수를 자기 측정처럼 인용하지 않음, 앞선 실행의 수치를 옮기지 않음, 검사 스크립트는 실패가 종료 코드로 드러나게. 기존 절 번호는 바꾸지 않았습니다(참조 12곳이 깨집니다). `CLAUDE.md` 4절에 요약을 반영했습니다 |
| 개선한 것 | F-30 계약 검증이 조용히 멈추던 것을 실패로 세게 · F-33 `service.py` 서두의 사실과 다른 문장 · F-28 평가 출력에 죽은 가중치와 실효 분모 표시 |
| 새로 찾은 것 | F-32 `Settings` 와 `RankingPolicy` 의 값이 두 벌이고 엔진은 후자만 읽음 · F-33 로그 실패 카운터를 읽는 곳이 없음 |
| MUST TODO | 4.3 에 17줄. 전부 선행 조건이 없어 못 하는 것이고 이미 있는 추적 번호를 가리킵니다 |
| 검증 | ruff·format(132)·mypy(58) 통과, `pytest tests/unit` **206 passed** / coverage 88.18%. `make contract` 98건 전부 통과·종료코드 0(설정 채움), 빈 `.env` 에서는 92통과 2실패로 끝까지 돎. `validate`·`normalize-test` 통과. Mock 종단 정상 |
| 넘긴 것 | M-01~M-13 전부, D-28 승인, MUST TODO 17줄 |

### 9.9 2026-09-11 - 회의 안건을 상태 체크리스트로

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 요청: 안건 문서를 체크리스트 형태로. 결정된 것은 언제 결정·수정됐고 어느 코드에 반영됐는지, 결정이 필요한 것은 그렇다는 표시 |
| 형식 | 04의 2.2 규칙 6 이 문서 체크박스의 완료 표시를 금지하므로 **상태 열**로 대신했습니다. 상태 낱말 여섯 개(`결정·반영` · `일부 반영` · `결정 필요` · `착수 가능` · `선행 대기` · `관찰`) |
| 구조 | 2절 상태 표(32건 전부 · 결정일 · 반영 커밋 · 결정자)와 커밋 범례 → 3.1 전부 반영(결정일·근거·반영 코드) → 3.2 일부 반영(남은 결정) → 4절 결정 필요(급한 순서, 막히는 것) → 5절 내부 안건 |
| 반영 위치의 근거 | `git show --stat` 로 확인한 커밋별 변경 파일. 1차 회의 결정은 `c459c2f`·`256c702`(2026-09-10 17:44·17:45), 병합 해소는 `da6de58`(18:30), 이후 조치는 `0da1aa6`·`50d77cf`(2026-09-11) |
| 집계 | G 19건 — 결정·반영 7, 일부 반영 4, 결정 필요 8. N 13건 — 착수 가능 3, 일부 반영 1, 선행 대기 5, 결정 필요 3, 관찰 1 |
| 뺀 것 | 회의 전의 배경·선택지 원문은 5.0.0 판(`86e5c07`)과 결정 기록에 있어 결정된 항목에서는 뺐습니다. "회의 전에 확인할 사실"(A 미푸시 커밋)은 A 전량 병합으로 의미가 없어져 뺐습니다 |

### 9.10 2026-09-11 - A 최신 내용 두 번째 병합

| 항목 | 내용 |
|---|---|
| 입력 | 유재현: A 가 검토 뒤 자기 코드를 고쳐 커밋했으니 최신 내용을 합쳐 검수·커밋하고 마지막에 `main` 에 병합 |
| A 의 새 커밋 | `5d41e8f` `repository.py` 에서 배치 SQL 분리(904→365줄, `repository_ingest.py` 신설) · `def3d5b` `ingredient.freq_count` 채움(`ingest/freq_build.py`, `make freq-build`). A 는 우리 브랜치를 받지 않았습니다 |
| 병합 | `b9105ae`. 충돌은 `repository.py` 두 구간 — 파일 중간의 import 블록은 우리 쪽(맨 위로 올려 둔 것 유지, E402 방지), 배치 SQL 540줄은 A 쪽(새 파일로 이동). 해소 뒤 쓰이지 않게 된 `Mapping`·`cast` import 를 뺐습니다 |
| 설정 | `repository_ingest.py` 를 커버리지 `omit` 에 더했습니다(4항목). `repository.py` 에서 떼어 낸 DB 전용 SQL 이라 근거가 같고, G-16 승인 대상에 포함됩니다 |
| 새로 본 것 (F-34) | `repository_ingest.py` 가 629줄입니다. A 의 커밋 메시지는 570줄로 상한 아래라 적었지만 다음 커밋에서 59줄이 붙었고 02의 5.1 상한은 500줄입니다. 도메인 루트가 9개 파일이 되어 같은 절의 디렉터리 상한 8개도 넘습니다. A 의 파일이라 나누지 않고 G-10 에 올렸습니다 |
| 새로 본 것 (G-20) | 01의 2.1 은 `main` 을 보호 브랜치로 둔다고 정하지만, GitHub 공개 API 로 확인하니 `main` 의 `protected` 가 `false` 입니다. 직접 push 해도 막히지 않습니다. 회의 안건으로 올렸습니다 |
| 검증 | ruff·format(136)·mypy(60) 종료코드 0, `pytest tests/unit` 206 passed / coverage 88.18%. A 게이트 validate·run 74·test_match 23·test_role 23·test_batch 5 전부 종료코드 0, `contract` 98건 종료코드 0(설정값 채움). `freq_build`·`repository_ingest` 는 실 DB 없이 import 까지 확인. Mock 종단 판정 지표 이전과 동일, p95 28.1ms |
| 돌리지 못한 것 | `make freq-build` 와 DB 가 필요한 A 게이트 4종은 실 DB 가 없어 돌리지 못했습니다 |
| `main` 병합 방식 | 유재현 결정: PR. 01의 2.1 이 `main` 직접 push 를 금지하고 PR #3·#7 이 선례입니다. 이 PC 에 `gh` 가 없어, 유재현의 동의를 받아 git 에 저장된 GitHub 인증으로 REST API 를 불러 PR #8 을 만들었습니다(HTTP 201). 토큰은 출력하거나 파일에 저장하지 않았습니다 |
| 넘긴 것 | PR #8 승인(1명, 300줄 초과 사전 승인), G-16, G-10 에 F-34, G-20 |

### 9.11 2026-09-11 - 취향 페르소나 (2차 회의 반영)

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 전달, 2차 3자 회의 결정 — 6축은 음식의 맛이고 사용자 취향은 온보딩에서 고른 음식 3개 이상에서 계산. 직접 적은 3축(1~5)은 고른 음식이 있으면 저장만(추후 LLM 추천 보정). 우선순위 고른 음식 → 3축 → 없으면 임의 다양. 레시피 선택 이벤트를 백엔드가 보내면 누적해 점진 갱신. 시간 감쇠와 계절·월·일 주기 가중. 사용자별 저장은 우리 DB 나 JSON, 사용자가 늘어도 관리되게. 끝나면 코드 복기 여러 번, 문서화, 노션용 일반 설명서 |
| 결정 | D-29~D-33. 근거는 `../decisions/2026-09-11_taste_persona_from_picks_with_time_decay.md` |
| 새 코드 | `engine/persona.py`(모델·우선순위·무게·합치기·잘라내기, 순수) · `profile_store.py`(JSON 저장소, 제시 목록 로더) · `service.py` 의 `onboarding_profile()`·`PersonaService`·추적 파라미터 덧붙임 · `policy.with_trace_extra()` · `rerank.exploration_ratio()` |
| 바뀐 코드 | `policy.py` 손잡이(EMA 2개 → 페르소나 9개) · `taste.py`(EMA·선형 전이 삭제) · `context.py`(`persona` 필수, `UserHistory` 에서 취향 제거) · `rerank.py`(취향 없으면 탐색 확대) |
| 계약 정합 | A 의 `OnboardingIn.picks`(제시 목록 인덱스)·`scales`(0~4)·`UserMode`(onboarding/blended/behavior)·`LABEL_WEIGHT`·`rating_to_label` 을 그대로 씁니다. 제시 목록은 `seeds/onboarding_recipes.yaml` 을 읽고 축 순서를 검사합니다 |
| 검사 | 새 46건 — `test_persona.py` 27(우선순위·식·감쇠·주기·시각·잘라내기), `test_profile_store.py` 8, `test_service.py` 8, `test_rerank.py`·`test_wiring.py`·`test_db_cutover.py` 3. 지운 것 — EMA·선형 전이 4건 |
| 픽스처·평가 | 생성기가 척도에 가장 가까운 제시 음식 4개를 picks 로 둡니다(1004 척도만, 1012 취향 없음). 평가 스크립트에 페르소나 출처·감쇠 대조(최근 16건 vs 1년 전 16건)·연 주기 세기별 무게를 추가 |
| 복기 | 1차 — 새 손잡이 9개 전부 읽힘, 낡은 참조는 코드에 없고 문서 7곳(이번에 정리). 02의 5.1: `trace_params` 가 6인자가 됐던 것을 `with_trace_extra()` 로 분리해 5로 되돌림. `persona.py` 302줄·`service.py` 308줄은 검토 문턱(300, 예시값) 초과이나 필수 분리(500) 아님. 2차 — 문서가 가리키는 심볼·경로 실재 확인. 3차 — 깨진 취향 파일이 추천을 죽이지 않게 `persona_for` 가 받아서 세도록 추가(검사 1건) |
| 검증 | ruff·format(142)·mypy(62) 종료코드 0, `pytest tests/unit` **252 passed** / coverage **89.74%**(`persona.py`·`profile_store.py`·`context.py` 100%). A 게이트 전부 0, `contract` 98건 0. Mock 종단 — 출처 picks 10·scales 1·none 1, 취향 없는 사용자 탐색 8/20, 최근 16건 조리 뒤 `behavior`(무게 15.07), 1년 전 조리는 `blended`(0.91)로 온보딩 쪽 복귀, p95 19.6ms. 상세는 검증 기록 13절 |
| 새로 본 것 | F-35 Mock 카탈로그의 맛 분포가 시드와 달라 맛 정합 lift 가 작아짐(N-14). E-14 psycopg DLL 차단 |
| 문서 | 결정 기록 1편 · 노션용 일반 설명서 `recommend_engine_how_it_works.md`(사람용 단일 판 — 독자가 사람뿐이라 두 형식 규칙의 대상이 아닙니다) · 계획서 두 편 3.4 에 대체 표시 · 점검표 M-14·M-15 · 안건 G-21~G-24·N-14 |
| 커밋 | `d35b619` 결정 기록 · `04ec783` 코드·검사·픽스처·평가 |
| 넘긴 것 | M-14·M-15, G-24, N-14, PR #8 본문 갱신 |
