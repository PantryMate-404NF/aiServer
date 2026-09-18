# 파트 B 추천 엔진 작업 기록 (에이전트용)

**정하는 것**: `PM-ENG-RECO-B-001` 의 실행 이력. 상태, 결정(D), 가정(A), 접점(I), 계획 항목(T), 선행 조건(P), 고려사항(C), 환경(E)

**적용 대상**: 파트 B 추천 엔진을 이어서 작업하는 AI 코딩 에이전트. 사람은 `human/` 의 서술본을 읽습니다

**버전**: 1.19.0 · **최종 수정**: 2026-09-18 · **작성자**: 유재현

---

## 1. 상태 요약

| 키 | 값 |
|---|---|
| 정본 | 이 파일. 사람용 서술본 `human/recommend_engine_work_log.md` 는 식별자로 대응하는 파생본 |
| 계획서 | `recommend_engine_design.md` (구현 명세 2.2.1, 현재 구현 기준, 사람용 정본 · 노션 공유용) · `recommend_engine_design_digest.md` (에이전트용 압축본 3.2.0, 어긋나면 원본이 이기고 코드가 이김) |
| 브랜치 | `feat/recommend-engine-core`. `origin/main` 병합 완료. `origin/develop-data-part` 는 두 번 병합했습니다 — 658d79a(9.6), def3d5b(9.10). `main` 병합은 PR #8 로 올렸고 **2026-09-11 11:52(KST) kmk9259 가 병합**했습니다(`5846a72`, `80cc832` 까지). 그 뒤 커밋 20개는 **PR #9 로 2026-09-16 병합**했습니다(`77da2bc`, 97 파일 +28,640/-1,897). 리뷰 없이 유재현 지시로 병합했고 규약 예외 둘(라인 상한 초과 · 승인자 0명)은 PR 본문 최상단에 적었습니다. 지금 브랜치와 `origin/main` 은 내용이 같습니다 |
| 단계 | ② Ranking 과 ③ Re-ranking 을 A 계약 위에서 구현 완료. **취향 페르소나**(고른 음식 → 3축 척도 → 없음, 시간 감쇠·주기 가중, 사용자당 JSON 저장)를 9.11 에서 구현. ① Retrieval·로그 적재·DDL·배치는 A 것이 브랜치에 있습니다. 라우터에 `rank_candidates`·`PersonaService` 를 끼우는 것(M-01·M-15)과 DB 연결이 남았습니다. 9.12 에서 세 방향 복기(명세 · 무음 실패 · 규약)로 구멍 15개(F-36~F-50)를 찾아 14개를 코드로 고쳤습니다(D-34~D-38). 9.15 의 4회차 복기는 20건(F-51~F-70)을 더 찾아 코드 17건·기록 3건으로 닫았습니다(D-39~D-41). 9.16 은 기획측 시뮬 시드(1,600명)를 DB 없이 엔진에 넣는 도구 `scripts/sim/scenario_engine.py` 를 만들어 전원 불변식 · 콜드 → 웜 전환 · 행동·냉장고 반응을 확인했습니다(D-42·D-43). 9.17 은 유재현이 Docker Desktop 을 설치한 뒤 DB 경로(적재 · 검증 쿼리 · `/health` 포함 API 시나리오)까지 통과시켰습니다(D-44). 9.19 는 온보딩에 추가된 **좋아하는 음식 유형** 문항을 계약 → 페르소나 → 목록까지 이었습니다 — 맛 6축에 섞지 않고 재정렬의 유형 슬롯으로 반영합니다(D-45~D-47, F-82~F-89). 9.23 은 A 의 09-17 결정 기록이 B 에 넘긴 5건 — 빈 팬트리 규칙 · Mock 알러지 어휘 · 동결 키 2종 · `occurred_at` · `RecipeFeature` 로더 — 를 처리했습니다(D-48~D-51, F-97~F-106). 백엔드 연동(M-01·M-02·M-04·M-15)은 스키마 회신 뒤로 미룹니다 |
| 검증 (2026-09-18, 9.23) | ruff check OK · ruff format OK(168 files) · mypy **67 files** OK · `pytest tests/unit` **358 passed / 0 failed** / coverage **91.20%**(새 검사 21건 포함). `test_contract.py` 98건 0(설정값을 채운 환경). 시뮬 시나리오 1,600명 RESULT: PASS(불변식 위반 0, B 집단 800명 전원 첫 조회부터 인기순). 시드 재생성 → 저장소와 `04_user_allergy.sql` 만 다르고 그것을 커밋. 실 DB 대조는 Docker 미기동으로 미실행(E-17) |
| 다음 행동 | **군집 다양성 반영 방식 결정(N-18, 검증 20.3 의 선택지 4종)** → A 에 중간 벡터 저장·`user_cluster_stat` 배치 요청(G-33) → 클라우드 RDS 회신 반영 — 파라미터 그룹 시간대·역할 3종·keepalive 확인 요청(G-32, I-07) → **백엔드 회신·RDS 적용이 오면 `docs/env_variables.md` 4절 TODO 8줄을 확정해 1.1.0 으로 올리고 클라우드에 재전달(유재현에게 먼저 알림)** → 백엔드 스키마 회신 대기(`docs/backend_schema_request.md` 2.0.0) → 회신에 맞춰 DB 전환 점검표의 연동 항목(M-01·M-02·M-04·M-15) 재작성 후 라우터 실연결 → A 에 F-104~F-106(`judge()` 비결정 · 배정 건수 불일치 · 골든에 `cuisine_family`)과 `enums.py` 주석 수정을 알림(G-31) → 백엔드에 `EventIn.occurred_at` 을 실어 달라고 전달 → 브랜치 PR 은 유재현 지시 전까지 보류. 남은 회의 안건은 `recommend_engine_meeting_agenda.md` 2절 |
| 병합 정책 | `main` 은 PR 로만 병합합니다(PR #8 `5846a72` · PR #9 `77da2bc`). 브랜치 커밋·push 는 자유. `origin/main` 은 merge 로 따라감(rebase 금지, 01의 2.1). 병합된 브랜치 `feat/recommend-engine-core` 는 01의 2.1 대로 삭제 대상이며, 이어서 쓸지 삭제할지는 유재현이 정합니다 |
| DB 전환 | `recommend_engine_db_cutover.md` 의 M-01~M-16(M-16 은 음식 유형을 `user_preference` 에서 읽는 일, 09-15 추가). DB 와 닿는 변경을 시작할 때 먼저 엽니다. `tests/unit/recommend/test_db_cutover.py` 가 못을 박아 두어 건너뛰면 검사가 깨집니다 |
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
| D-34 | 완화 조회 종료 기준 = `top_k` + 탐색 슬롯 수(24) | `top_k + 2 x exploration_min_pool_ratio x 슬롯`(36, 취향 없으면 52). `needed(policy, top_k, exploration_ratio)` | 탐색은 잔여 후보의 상위 절반에서 슬롯의 2배가 있어야 채워지는데 24건에서는 4칸 중 1칸만 채워졌습니다(F-41, F-04 의 뿌리). 취향 없는 사용자는 8칸이 필요합니다 | Mock 에서 인기순 폴백이 7/12 로 늘었습니다(N-15). 실데이터에서 후보가 충분하면 되돌릴 이유가 없고, 부족하면 `exploration_min_pool_ratio` 를 먼저 낮춥니다. `b7b25b4` |
| D-35 | Thompson 몫은 A 함수가 채운다 | 군집이 없으면 B 가 `uniform_share` 1.0 으로 넘겨 전부 균등으로 채우고, 추적에 `explore_fallback: uniform` 을, 채우지 못한 칸 수를 `dropped.explore_shortfall` 로 남기며 둘 다 셉니다 | `Candidate.cluster_id` 계약("None 이면 균등 폴백")을 `mixed_exploration` 이 구현하지 않아 클러스터링 배치 전까지 탐색 절반이 예외 없이 사라집니다(F-42). `uniform_share` 는 동결 키라 덮지 않습니다 | A 가 함수 안에 폴백을 넣으면(G-27) B 우회를 지웁니다. `b7b25b4` |
| D-36 | 사전 취향이 없으면 이벤트 하나로 `behavior` | 기준을 `scales_prior_weight`(6.0)로 둡니다. 그 아래는 `blended` | 클릭 하나(0.3)로 '행동' 사용자가 되면 탐색이 줄고 로그가 그렇게 적힙니다(F-43) | 기준값은 손잡이와 함께 재조정(N-05). `b7b25b4` |
| D-37 | 온보딩 입력은 받아서 맞춘다(범위 밖 척도는 잘라 넣고 중복은 그대로) | 범위 밖 척도는 거부하고 셉니다. 중복 인덱스는 하나로 두고 셉니다. 3개 미만은 거부하지 않고 셉니다(`MIN_PICKS`) | 잘라 넣으면 5 가 4 와 같아지고 에러가 없습니다(F-48). 개수는 계약이 프론트의 일로 두어 서버가 거부하지 않습니다 | 계약이 개수를 서버 몫으로 바꾸면 거부로 전환. `b7b25b4` |
| D-38 | 별점은 0 아래만 자른다 | 1.0 위도 자르고 1~5 밖의 별점은 잘못된 이벤트로 셉니다(`RATING_MIN`·`RATING_MAX`) | 별점 180 하나(무게 88.5)가 온보딩 전체(무게 12)를 덮었습니다(F-36). `EventIn.value` 에 범위가 없고 체류 시간과 필드를 같이 씁니다 | A 계약이 `value` 에 범위를 넣으면 서비스 검사는 이중이 됩니다. `b7b25b4` |
| D-39 | 난수원은 호출자가 넘기고 시드는 추적에 적는다 | `rank_candidates` 가 추적의 `rng_seed` 로 `random.Random(rng_seed)` 를 직접 만듭니다. `rng` 인자를 없앴습니다 | 둘이 따로 있으면 로그의 시드가 실제 난수와 무관해집니다. 평가 스크립트가 실제로 `SystemRandom` 을 넘기며 `rng_seed=user_id` 를 적어, 같은 명령이 실행마다 다른 목록을 냈습니다(F-51, F-52) | 라우터가 요청마다 시드를 정해 넘기는 것이 남았습니다(M-06). `noqa: S311` 한 줄은 A 의 목업·Thompson 과 같은 사유입니다(G-16 묶음) |
| D-40 | ② 는 받은 후보를 전부 매긴다(빼지 않음) | 피처 행이 없는 후보와 같은 레시피의 중복은 서비스가 점수 전에 빼고 세어 ranking 단계의 `filters` 에 남깁니다. `score_all` 자체는 그대로 전부 매깁니다 | 피처가 없는 후보는 갖춘 재료 하나만으로 만점이 되어 1위로 나가고 제목도 비어 있습니다(F-53). 정책으로 거르는 것이 아니라 볼 수 없는 것을 세는 것이라 "제외는 ① 에서만" 과 충돌하지 않습니다 | 실 DB 에서 피처 행이 빠지는 일이 없다고 확인되면 카운터만 남기고 제외를 뺄 수 있습니다 |
| D-41 | 균등 폴백 판정은 탐색 풀 기준 | **후보 전체** 기준이며 `rerank.ExplorationSpec` 하나를 재정렬과 서비스가 같이 씁니다. 풀에만 군집이 없으면 폴백이 아니라 부족분입니다 | 두 곳이 따로 판정하면 군집이 일부 후보에만 있을 때 로그와 실제가 갈라집니다(F-62). 군집은 배치 단위라 후보 어딘가에 있으면 배치가 돈 것입니다 | A 가 함수 안에 폴백을 넣으면(G-27) 규격의 균등 비율 계산만 남습니다 |
| D-42 | (시뮬 시드) `user_vector.taste_vec` 은 picks 평균과 척도를 앞 3축에서 절반씩 섞은 값 | **고른 음식의 6축 평균만.** 척도는 `onboarding_scales` 원본으로만 둡니다 | D-29(고른 음식이 있으면 척도는 저장만)와 시드가 어긋나면 배치 검증 때 기대값이 둘이 됩니다(F-75). 변환기 `synth_onboarding()` 을 고치고 03 파일을 재생성했습니다 | 기획이 척도를 계산에 넣기로 하면 D-29 와 함께 |
| D-43 | (계획에 없음) 시드 검증은 DB 적재 뒤 API 로만 | DB 없이 시드를 엔진에 직접 넣는 `scripts/sim/scenario_engine.py`(읽기는 `sim_seed.py`)를 둡니다. 냉장고·임박은 A 의 SQL 규칙(`user_pantry_ids` staple 합집합, `effective_expiry` 구매일 + 기본 소비기한, D-3 이내와 지난 것)을 파이썬으로 따르고, '지금' 은 유저마다 마지막 활동 한 시간 뒤, 난수 시드는 `user_id` 입니다 | 이 PC 에 Docker 가 없고(E-15) M-01·M-03 전에는 API 가 시드를 읽지 않습니다. 이 도구가 없으면 시드가 엔진과 맞는지 알 수 없습니다 | M-01·M-03 이 붙으면 같은 시나리오를 `scenario_run.py` 로 API 에서 돌리고 이 도구는 회귀 검사(`test_engine_scenario_passes_without_a_db`)로만 남깁니다 |
| D-45 | (신규 문항) 온보딩의 음식 유형을 맛 6축 취향에 합침 | 맛 6축과 **섞지 않습니다.** 유형은 `TasteProfile.cuisines` → `Persona.cuisines` → `UserContext.preferred_cuisines` 로 따로 흐릅니다 | 고른 유형의 평균 맛을 취향에 더하면 "한식을 좋아함" 이 "짜고 매운 것을 좋아함" 으로 번역되어, 문항 하나가 맛 취향 전체를 움직입니다. 담백한 한식만 좋아하는 사람에게 틀린 목록이 나가고 에러는 없습니다 | 유형과 맛의 상관을 실데이터로 재고 W3 학습이 한 항으로 흡수하기로 하면 |
| D-46 | (신규 문항) 유형 반영은 `f_cuisine` 가중치를 올려서 | 가중치는 0.04 그대로 두고 **재정렬에서 자리를 뗍니다**(`engine/cuisine.py`, Top-20 에 두 칸). 조건은 "고른 유형이 목록에 한 건도 없을 때" 이고 행동이 쌓인 사용자(behavior)는 0칸입니다 | Σw 의 0.44 가 재료 매칭이라 0.04 로는 고른 유형이 Top-K 에 한 건도 안 들어올 수 있고(시뮬 1,600명 중 460명), 가중치를 올리면 반대로 고른 유형 하나가 20칸을 물들입니다. 자리를 떼면 18칸은 그대로이고 그 칸의 이유 문구도 유형으로 적힙니다 | 실데이터에서 유형이 실제로 반응(클릭·조리)을 끌면 W3 학습에서 가중치로 옮깁니다 |
| D-47 | (신규 문항) 유형 값은 화면 라벨("한식")로 저장 | **`cuisine_family` 코드**(`korean`·`chinese`·`japanese`·`western`·`asian_other`)로 저장하고, 라벨로 와도 코드로 바꿉니다. 모르는 값은 버리거나 추측하지 않고 **거부**합니다 | 레시피 쪽 축이 `recipe.cuisine_family` 코드라 라벨로 저장하면 두 축이 영영 안 만납니다(F-82). 모르는 값을 가까운 유형으로 바꾸면 사용자가 고르지 않은 음식이 목록에 오르는데 응답은 200 입니다 | 없습니다. 값의 정본은 `seeds/cuisine_taxonomy.yaml` 이고 검사가 대조합니다 |
| D-48 | 조회 완화 사다리 k=2→3→4→인기순 | 사용자가 넣은 재료가 없으면 첫 조회부터 인기순. `UserContext.own_pantry_ids`(None 이면 `pantry_ids` 전부가 사용자 것) · `candidate.first_plan(pantry_is_bare=)` | A 의 게이트 조임 뒤 빈 팬트리 후보는 양념 제조법 94건뿐인데 완화 종료 기준(52)을 넘겨 폴백이 안 걸림(F-97). `pantry_ids` 는 그대로라 `f_pantry_use` 불변 | 온보딩이 `pantry_item` 을 채우면 자동으로 사다리 복귀. 규칙 삭제는 A 가 게이트를 다시 풀 때 |
| D-49 | 알레르기 코드군 픽스처는 생성기의 표(C-09) | Mock 생성기가 `seeds/ingredient.csv` 의 `allergen_group` 을 읽고 Mock 재료 이름을 시드 이름과 같게 둠. `sim_seed.ALLERGEN_MAP` 삭제 | 정본은 DDL 소문자 10종. 생성기가 표를 들면 다시 갈림(F-99). 시드의 판단(배추김치 = shellfish)을 그대로 따름 | 시드가 바뀌면 생성기를 다시 돌릴 뿐. 표를 되살리지 않음 |
| D-50 | (계획서 없음) `recipe_feature` 행 → `RecipeFeature` | `context.recipe_feature_from_row` 하나가 변환. 없는 값은 None(0 아님) · difficulty 1~5 → 0~1 · `season_vec` 은 달이 있어야 점수 · μ 없으면 `flavor_mean=None` · `CorpusStats.stats_version` 신설 | 저장소와 골든 검사가 같은 함수를 지나야 검사 값과 서빙 값이 안 갈림(F-102). 0 벡터 μ 는 모든 레시피를 평균에서 멀어 보이게 함 | 골든에 `cuisine_family` 가 오면 로더 검사를 골든 기준으로. 변환 규칙이 DDL 주석과 어긋나면 DDL 이 이김 |
| D-51 | 동결 키 10종 | 12종 — `feature_version` · `cluster_version`. 값은 호출자가 `repository.load_batch_versions()` 로 읽어 `rank_candidates(batch_versions=)` 로 넘기고, 목업은 None | 정책이 DB 를 보면 순수하지 않음. 키 자체가 소급 불가라 None 이어도 키는 항상 실음(F-100) | 라우터 연결(M-01) 때 호출이 빠지면 값만 None. M-01 못이 가리킴 |
| D-44 | (시뮬 시드) `app_user.id` = 기획 번호(1~1600), 적재 뒤 시퀀스를 max(id) 로 올림 | **id = 1,000,000 + 기획 번호**(`SIM_ID_BASE`), 시퀀스는 건드리지 않음. 합성 값의 해시 키는 id 가 아니라 기획 번호(`hkey`) | `make smoke --keep` 의 합성 유저 8명이 id 1~8 을 차지해 `01_app_user.sql` 이 PK 충돌로 멈췄습니다(F-79) — 안내서가 권한 경로에서 그대로 생기는 결함입니다. 해시 키를 id 로 두면 id 를 옮기는 순간 알러지 90 → 89, 냉장고 4,442 → 4,474 로 내용까지 바뀝니다(F-81) | 낮은 id 를 시뮬 몫으로 비워 두기로 A 와 정하면 오프셋을 0 으로 |

### 3.3 검증 출력 (2026-09-18, 세션 9.23 종료 시점)

```text
uv run ruff check .                   → All checks passed!
uv run ruff format --check .          → 168 files already formatted
uv run python -m mypy src             → Success: no issues found in 67 source files   (E-01)
uv run pytest tests/unit              → 358 passed, coverage 91.20% (기준 80%)
```

A 자체 게이트(`Makefile`, DB 불필요분). Windows 는 `PYTHONIOENCODING=utf-8` 이 필요합니다 (E-11, G-14). 이번 세션은 종료 코드만 확인했고 건수는 `contract` 만 화면에서 읽었습니다.

```text
python seeds/validate.py                    → 종료코드 0
python -m tests.unit.recommend.test_contract → 종료코드 0, 98건 (설정값을 채운 환경. E-13)
python -m tests.unit.recommend.run           → 종료코드 0
python -m tests.unit.recommend.test_match    → 종료코드 0
python -m tests.unit.recommend.test_role     → 종료코드 0
python -m tests.unit.recommend.test_batch    → 종료코드 0
```

시뮬 시드(9.23 재실행). `uv run python scripts/sim/scenario_engine.py` → 종료코드 0, RESULT: PASS (1,600명, 판정 6종, B 집단 800명 전원 `popularity`). `uv run python scripts/generate_mock_fixtures.py` · 시드 재생성(amplify → convert) → 종료코드 0, 저장소와 `04_user_allergy.sql` 만 다름(F-98, 커밋). `pytest tests/unit/sim` 7건 0.

시뮬 DB 경로(9.17, Docker Desktop). `docker compose ... up -d` → postgres healthy, init 01~04 자동 적용. `uv run python scripts/reco/migrate.py` → 0 (재료 536). `uv run python tests/integration/test_smoke.py --keep` → 0 (published 합성 10,007). `PSQL_VIA_COMPOSE=1 bash deploy/seed/sim/load_sim.sh` → 0, `99_verify.sql` app_user 1600 · user_preference 1600 · user_vector 1600 · user_allergy 90 · pantry_item 4442 · event_log 10195, 집단 × 모드 235/90/475/800. `uv run python scripts/sim/scenario_run.py --base ... --api-key ...`(`/health` 포함, db true 0.11초) → 0.

커버리지 측정 범위는 `ingest/*`·`repository.py`·`repository_ingest.py`·`engine/mock.py` 를 뺀 2,169문입니다. 뺀 근거는 D-24 의 결정 기록에 있습니다.
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
| T-06 | Step 2 | 알레르기 코드군 → 재료 ID 조회 | A 구현 | 어휘는 DDL 소문자 10종(`enums.ALLERGEN_GROUPS`). 실 DB 는 A 의 `expand_user_allergens` 가 `ingredient.allergen_group` 컬럼 일치로만 전개(09-17). 픽스처는 시드에서 같은 어휘를 읽음(D-49) |
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
| 죽은 가중치 0.26 재배분 | 같은 이유입니다. 이력이 붙으면 0.21 이 저절로 살아납니다 | M-03 이후 재측정 → W3 | F-28, N-04, N-05 |
| `/health` 의 `redis` | 저장소에 redis 클라이언트가 없어 찔러 볼 대상이 없습니다 | A 가 붙이거나 필드를 뺄 때 | M-11, G-19, F-31 |
| DB 가 필요한 A 게이트 4종 | `make smoke`·`log-test`·`ddl-test`·`feature-test` 는 실 DB 를 씁니다. 병합 시 못 돌렸습니다 | DB 기동 후 | M-12 |
| 커버리지 `omit` 되돌리기 | A 의 단독 스크립트를 pytest 로 옮겨야 합니다. A 의 파일이라 단독 결정 불가 | G-08 결정 후 | M-13, D-24 |
| 게이트 예외 4건 사후 승인 확인 | 01의 3.3·6.1 이 Tech Lead 승인을 요구합니다. PR #8 이 병합돼(`5846a72`) 예외가 `main` 에 들어갔으나 명시 승인 기록은 없습니다 | 회의에서 | G-16 |
| 01의 3.4 개정 승인 | 01의 9절이 팀 전원 승인을 요구합니다 | 회의에서 | D-28 |
| 규약 개정 2건 | `docs/recommend/` 배치와 `stage.py`·`enums.py`·`policy.py` 가 규약에 없습니다 | 회의에서 | G-09, G-10 |
| `.env` 채우기 | 값이 팀 비밀 저장소에 있고 유재현이 직접 넣습니다. 비면 서버도 `make contract` 도 못 돕니다 | 유재현 | N-01, E-13 |
| `repository_ingest.py` 629줄 분리 | A 가 904줄이던 `repository.py` 를 365줄과 이 파일로 나눴지만(`5d41e8f`) 이 파일이 다시 02의 5.1 상한 500줄을 넘습니다. 도메인 루트도 9개 파일로 같은 절의 디렉터리 상한 8개를 넘습니다(F-34). A 의 파일이라 단독 결정 불가 | A 와 합의 후 | G-10 묶음 |
| B 함수 인자 5개 초과 7곳 | 02의 5.1 의 확정 기준입니다. 요청·문맥 dataclass 로 묶는 것이 N-02 와 겹칩니다 | N-02 와 함께 | N-02 |
| 상대 import 차단이 꺼져 있음 | `ban-relative-imports` 가 설정돼 있으나 `select` 에 `TID` 가 없습니다. `main` 의 설정이라 단독 수정 불가 | 회의에서 | G-16 묶음 |
| 취향 원본 JSON → DB 이전 | `user_vector`·`event_log` 가 준비돼야 합니다. 옮긴 뒤 두 저장소의 페르소나를 대조해야 합니다. JSON 은 배치 간 재시도 중복을 흡수하지 않고 `event_log` 는 흡수하므로(F-47) 중복이 있는 사용자는 따로 봅니다 | DB 기동 후 | M-14, D-31 |
| 온보딩·이벤트 라우트 실연결 | 라우터가 목업을 부릅니다. `PersonaService` 는 있으나 쓰는 곳이 없습니다 | M-01 과 같은 변경에서 | M-15 |
| 3축 척도 범위 확정 | 계약은 0~4, 회의 기록은 1~5 입니다. 엔진은 0~1 만 받아 한 곳에서 바꾸면 되지만 어느 쪽이 맞는지는 정해야 합니다 | 회의에서 | G-24 |
| Mock 카탈로그 맛 스케일 | 시드(실집계)와 분포가 달라 고른 음식으로 만든 취향이 Mock 평균 아래에 놓입니다. 맛 정합 lift 가 작게 나옵니다 | 생성기 수정 | N-14, F-35 |
| Thompson 균등 폴백을 A 함수 안으로 | `serendipity.mixed_exploration` 은 A 의 파일입니다. 지금은 B 가 밖에서 우회합니다(D-35, F-42) | A 와 합의 후 | G-27 |
| Thompson 픽의 확률 귀속 | A 의 `mixed_exploration` 이 묶음의 확률을 점수 최고 후보에만 붙여, 균등이 그것을 먼저 가져가면 두 번째 후보가 Thompson 으로 뽑히는데 확률에는 균등 몫만 남습니다(F-65). A 의 파일입니다 | A 와 합의 후 | G-28 |
| 월별 주기 해석 확인 | 연 주기 위상으로 읽은 것이 회의 뜻과 맞는지 확인이 필요합니다 | 회의에서 | G-25 |
| `/health` 의 DB 접속 대기 | `db.healthy()` 가 접속 시간 제한 없이 DB 를 기다려 DB 없는 PC 에서 150초 넘게 응답이 없습니다(DB 가 있으면 0.11초). `infra/db.py`·`config.py` 는 A·공용 파일입니다 | A 와 합의 후 | G-29, F-74 |
| 환경변수 목록 확정 | 이름·기본값은 `docs/env_variables.md` 1.0.0 에 코드 기준으로 적었지만, DB 접속값 · 연동 방식에 따른 새 변수(API 면 `BACKEND_*`) · 로그 DB 분리(`LOG_DB_*`) · `PG_MAX_CONN` 상한 · `OCR_WORKERS` 메모리 · 키 회전 · `.env.example` 누락 9개는 정할 근거가 아직 없습니다 | 백엔드 스키마 회신 + 클라우드 RDS 적용 확인 뒤 | `docs/env_variables.md` 4절, G-32, I-07 |

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
| C-09 | 알레르기 코드군 픽스처는 `seeds/ingredient.csv` 에서 읽은 10종(09-18, D-49). 09-17 까지는 생성기의 대문자 18종이었고 DDL 과 갈려 있었음(F-99) | `generate_mock_fixtures.allergen_groups_from_seed` |
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
| E-15 | 이 PC 에 Docker·postgres 가 없습니다. `/health` 가 `db.healthy()` 로 DB 를 기다려 응답이 없고(F-74) 시뮬 안내서 3-4·3-5 를 돌릴 수 없습니다. `openpyxl` 은 `uv.lock` 에 없습니다(F-72) | DB 없는 단계만 돌리고 못 돌린 것은 못 돌렸다고 적습니다(D-28). API 스크립트는 `--skip-health`. openpyxl 은 `uv pip install openpyxl` 로 이 PC 에만 설치(잠금 밖) |
| E-16 | Docker Desktop 29.7.2(WSL2 백엔드)·`pgvector/pgvector:pg16` 은 유재현이 관리자 권한으로 설치(2026-09-14 저녁). 이 PC 에 `make` 와 `psql` 은 없습니다 | Makefile 이 부르는 명령을 직접 실행(compose up · `migrate.py` · `test_smoke.py --keep`), 적재는 컨테이너의 psql 을 stdin 으로(`PSQL_VIA_COMPOSE=1`). `deploy/.env` 는 템플릿 복사(값 비어 있어 compose 기본값, `.gitignore` 대상). E-15 의 DB 부재 항목은 해소 |
| E-17 | Docker Desktop 데몬이 꺼져 있으면 `docker ps` 가 `npipe:////./pipe/dockerDesktopLinuxEngine` 접속 실패로 끝납니다(9.23). 앱을 띄운 뒤 다시 돌려야 하며, 그 세션에서는 로더의 실 DB 대조(19.2절)를 미실행으로 남겼습니다 |

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
| I-07 | 엔진·배치 → DB | `DB_HOST` · `DB_USER`(원격에서는 `reco_app`) · 역할 3종(`05_roles.sql`) | 로컬 compose(`pgvector/pgvector:pg16`, 앱이 소유자 `reco` 로 접속) | 클라우드 회신(2026-09-18): 통합 전 DB 는 **AWS RDS for PostgreSQL 16**. 확장 4종 지원 확인, 로케일 C.UTF-8, `reco_batch` 에 DDL·TRUNCATE, 파라미터 그룹 `maintenance_work_mem` 상향, 스냅샷 매일 1회 · 7일 보관. 원격 TCP 라 `tcp_keepalives_*` 와 `ALTER DATABASE ... timezone` 이 RDS 에서도 적용돼야 함(G-32). 기록: `docs/container_handover.md` 12절 |

### 8.2 데이터 확정 시 대조할 가정 (A)

| ID | 가정 | 어긋나면 고칠 곳 |
|---|---|---|
| A-01 | **확정 (D-16)**: 축은 `(spicy, salty, sweet)` = A 의 (매움, 짠맛, 단맛), 0~1 척도. 6축 입력은 앞 3축만(`stage.RecipeCandidate` 검증기). `user_vector.taste_vec` 은 A DDL 에서 6축 (P-15) | `schema.FLAVOR_AXES`, `TastePreference.as_vector`, `feedback.DEFAULT_BEHAVIOR_VEC` |
| A-02 | `popularity_score`, `quality_score` 0~1 정규화 완료 | `rank.quality_score` 앞 정규화 (`_clamp` 은 절단만) |
| A-03 | 재료 ID 정수, 보유·레시피·알레르기 동일 ID 체계 | 전 모듈 집합 연산 |
| A-04 | `essential_ids ⊂ all_ids` | 충족도 `essential`, 자카드·기피·알레르기 `all` |
| A-05 | 알레르기 코드군 ↔ 재료 ID 매핑 존재 | 확정(09-17): DDL 10종, 매핑은 `ingredient.allergen_group` 컬럼 하나가 유일한 방어선(재료 직접 지정·카테고리 전개는 등록 경로가 안 채움). `ingredient_substitute` 0행은 미완성이 아니라 "측정 전에 구현하지 않는다" 는 결정(F-103) |
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

### 9.12 2026-09-12 - 3회차 복기와 구멍 메우기

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 지시(9.11 과 같은 메시지) — "시간이 걸리더라도 여러 번 전체 코드를 복기". 9.11 의 3회 복기에 더해 독립 검토자 셋(명세 정합 · 무음 실패 · 규약과 문서 드리프트)을 병렬로 돌렸습니다. 셋째는 세션 한도(HTTP 429)로 시작하지 못해 04 검사기·제거 심볼 grep·게이트로 직접 대신했습니다 |
| 결정 | D-34(완화 종료 기준에 탐색 몫 반영) · D-35(군집 없으면 균등 폴백과 추적) · D-36(사전 취향 없을 때 `behavior` 기준) · D-37(온보딩 입력은 거부·하나로·세기) · D-38(별점 무게 상한) |
| 발견 | 열다섯 건 — F-36 별점 무게 상한 없음 · F-37 파일 사용자 미대조 · F-38 깨진 파일 약속의 구멍 · F-39 동시 저장 예외 · F-40 빈 맛 벡터의 행동 집계 · F-41 완화 종료 기준의 탐색 몫 누락 · F-42 군집 없으면 Thompson 몫 소실 · F-43 미세 무게로 behavior · F-44 손잡이 미검증 · F-45 잘라내기 동률 · F-46 평가 lift 조작 · F-47 배치 내 중복 · F-48 척도 클램프·중복 pick·3개 미만 · F-49 온보딩 저장 미잘라내기 · F-50 잘라내기 문서 과장. 검토자가 실험으로 확인한 것만 적었고 상세는 검증 기록 14절 |
| 고친 코드 | `engine/persona.py`(별점 상한·범위, 빈 맛 벡터 미집계, 모드 기준, 잘라내기 동률, `require_aware`·`require_flavor` 공개) · `profile_store.py`(사용자 대조, `mkstemp`, `allow_nan=False`, 형 변환, 제시 목록 축 수) · `service.py`(척도 거부, 중복 pick 하나, `MIN_PICKS` 카운트, 잠금, 배치 내 중복·범위 밖 별점·빈 맛 카운트, 온보딩 저장 잘라내기, `explore_shortfall`·`explore_fallback`) · `policy.py`(`__post_init__`) · `engine/candidate.py`(`needed(policy, top_k, exploration_ratio)`) · `engine/rerank.py`(`effective_uniform_share`) · `scripts/eval_recommend_mock.py`(취향 없는 사용자 lift None, 완화에 탐색 비율) |
| 새 카운터 | `persona_pick_duplicate` · `persona_picks_under_min` · `persona_scale_out_of_range` · `persona_event_invalid` · `persona_event_duplicate` · `explore_shortfall` · `explore_uniform_fallback` |
| 검사 | 새 24건 — `test_persona.py` 6, `test_profile_store.py` 9(parametrize 7 포함), `test_service.py` 9, `test_candidate.py` 갱신. 바뀐 기대값 — 사전 취향 없는 조리 1건은 `blended`, `needed(20)` 은 36 |
| 검증 | ruff·format(142)·mypy(62) 종료코드 0, `pytest tests/unit` **276 passed** / coverage **90.23%**(2,098문). `seeds/validate` 0, `contract` 98건 0(설정값을 셸 환경변수로만 채움). Mock 종단 — 탐색 칸 [4,4,4,4,4,4,4,4,2,4,10,8] — 9.11 은 1·3 칸이 섞여 있었음, 인기순 폴백 7/12(N-15), 취향 없는 사용자 lift None, 피드백 15.07 / 0.91 동일, p95 17.6ms |
| 규약 | 02의 5.1: `persona.py` 340줄 · `service.py` 372줄 · `rerank.py` 302줄로 검토 문턱(300, 예시값) 초과, 필수 분리(500) 아님. 새 함수 인자 최대 5(`next_plan`). 디렉터리 파일 수는 루트 10 · `engine` 13 으로 이미 상한(8) 초과라 새 모듈을 만들지 않았습니다(G-10 묶음). ruff E501 은 한글을 폭 2 로 셉니다(E-04) |
| 문서 | 결정 기록 1.1.0(별점 상한, 저장소 보강, 월별 해석, 3개 미강제, 수신 시각, C 가 읽는 추적 키) · 검증 기록 14절 · 안건 G-25~G-27·N-15 · 점검표 M-14·M-15 문구 · 설명서 1.1.0 |
| 커밋 | `b7b25b4` 코드·검사·평가. 기록은 그다음 커밋 |
| 넘긴 것 | G-25(월별 주기 해석) · G-26(`EventIn` 시각 필드) · G-27(A 함수의 균등 폴백) · N-15(완화 상향의 트레이드오프) · M-14 대조 기준에 중복 사용자 분리 · 미병합 커밋 5개의 새 PR(보류, 9.13) |

### 9.13 2026-09-12 - PR #8 병합 확인과 기록 정리

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 "응 진행해줘" — PR #8 본문 갱신 승인. 갱신 전 공개 API 로 상태를 읽으니 이미 병합돼 있었습니다 |
| 사실 | PR #8 은 2026-09-11 02:52Z(11:52 KST) kmk9259 가 병합, merge commit `5846a72`, head `80cc832`. `origin/main` 에 `80cc832` 까지 있고 그 뒤 커밋 5개(`d35b619` `04ec783` `5b6043f` `b7b25b4` `e853277`)는 없습니다. 열린 PR 없음. 브랜치 `feat/recommend-engine-core` 는 원격에 남아 있음 |
| 규모 | `git diff --stat origin/main...HEAD` 44 파일 +2,781/-253 — `src/features` 8(+847/-84) · `tests/unit` 9(+964/-61) · `docs/recommend` 11(+603/-37) · `scripts` 2 · `tests/fixtures` 12 · `docs/decisions` 1 · `docs/README.md`. `git merge-tree` 충돌 0 |
| 결정 | 새 PR 은 보류(유재현). 선택지는 새 브랜치 `feat/recommend-taste-persona` 에서 PR · 기존 브랜치에서 PR · 보류였습니다. PR #8 본문 갱신은 병합된 PR 이라 하지 않았고 자격증명도 쓰지 않았습니다 |
| 기록 | 1절 브랜치·다음 행동·병합 정책, MUST TODO G-16 줄, 안건 G-16(막히는 것 없음, 사후 확인)·N-12(B 몫 완료)·요약·커밋 범례, 사람용 사본 |
| 넘긴 것 | 새 PR 시점 결정(유재현) · 병합된 브랜치 삭제 여부(01의 2.1) · G-16 사후 승인 확인 · A 가 `main` 을 받는 것(N-12 의 A 몫) |

### 9.14 2026-09-12 - 구현 명세 2.0.0

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "작업 초기에 작성했던 파트 B 구현명세서를 업데이트. 당시 포맷을 따르되 변경·추가된 내용은 현재 기준으로, md 만의 내용을 레퍼런스 삼지 말고 이 문서만 보고도 전체 flow 와 구동 방식을 이해할 수 있게, 사람이 읽기 편한 언어와 포맷으로. 노션 공유용" |
| 한 일 | `recommend_engine_design.md` 를 1.2.0(착수본 + 대체 표시) → **2.0.0** 으로 다시 썼습니다. 착수본의 8절 구조(아키텍처·원칙 → 계약 → 알고리즘·수식 → 저장소·관측 → 배치 → 단계·상태 → 손잡이·지표 → 검증)를 유지하고 9절(열린 결정)을 더했습니다. 내용은 전부 현재 코드에서 읽었습니다 — ① SQL 함수의 조건 7개와 완화 계획, 페르소나 식과 무게, 17 피처의 계산·모름 조건·가중치, 중심화 코사인, MMR·혼합 탐색·노출 확률·이유, 동결 키 10종과 덧붙임 키, 테이블별 읽고 쓰는 칸, JSON 원본 형식, 카운터 목록, 파일 배치와 소유, Step 0~6 상태와 실제 검사, DB 전환 15항목, 손잡이 25개, 검증 수치, 열린 결정 7건. LaTeX 대신 텍스트 블록. 식별자는 쓰지 않았습니다 |
| 함께 | `recommend_engine_design_digest.md` 3.0.0(새 명세의 압축본) · `docs/README.md` 두 행(1.8.1) · 설명서 12절의 "회의 이전 판" 표기(1.2.0) |
| 검증 | 04 형식 검사(명세·압축본 포함) 위반 없음 · 식별자 대조 4쌍 일치 · `ruff format --check` 0 |

### 9.15 2026-09-13~14 - 4회차 복기 (오류 없이 잘못 실행되는 것)

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "오류 없이 잘못 실행되고 있을 가능성을 포함해서 전체 테스트를 꼼꼼하게. 테스트·수정·재테스트를 3회 반복하고 오류가 모두 수정됐을 때만 커밋" |
| 방법 | 1회차: 게이트 4종·A 게이트 6종·평가 스크립트 + 검토자 둘(랭킹 경로 무음 실패 · 문서 드리프트와 검사 품질) + 직접 탐침(손잡이 생존, 결정론, 레시피 없는 이벤트, 저장소 OS 오류). 2회차: 수정 뒤 전체 재실행 + diff 자체 검토(검토자 셋째는 세션 한도 429 로 시작하지 못함). 3회차: 문서 정합 뒤 전체 재실행 |
| 발견 | 스무 건 — F-51 평가 스크립트가 시드 없는 난수원을 쓰며 추적에는 `rng_seed=user_id` 를 적음(실행마다 목록이 다름) · F-52 `rank_candidates` 가 `rng` 와 `rng_seed` 를 따로 받음 · F-53 피처 행 없는 유령 후보가 1위 · F-54 정책 지문이 로그에 없음 · F-55 가중치 덮어쓰기 미검증(오타 키가 24% 를 조용히 없앰) · F-56 `f_cooccur` 사유 템플릿의 `similar_title` 을 아무도 만들지 않음 · F-57 후보 0건에 `explore_fallback` 표시 · F-58 감점 반올림으로 로그 재현이 1e-6 어긋남 · F-59 랭킹 손잡이 미검증(세기 1.5·음수 감점 통과) · F-60 빈 재료명이 "(D-3)을 소진" 문구를 만듦 · F-61 중복 후보가 두 번 노출 · F-62 균등 폴백 판정이 재정렬(풀)과 서비스(전체)에서 다름 · F-63 레시피 없는 조리·별점 이벤트가 카운트 없이 버려짐 · F-64 저장소 OS 오류가 추천을 500 으로 죽임 · F-65 Thompson 픽의 확률 귀속(A 함수) · F-66 `candidate_limit` 은 엔진에서 죽은 손잡이 · F-67 `report_dead_weight` 문구가 MMR 을 무시 · F-68 문서 드리프트 묶음(예비 20개, 기피 감점 0.2배, `cuisine_family`, `data/` 기본값 없음, 로그 카운터 누락, 0.21, 7곳, 못 박은 항목 10개, 점검표 검사 명령 종료코드 1, 척도 저장 형식) · F-69 검사가 못 잡던 행동(MMR λ, 균등 노출 확률 값, 추적 값, score_stats, 출처 라벨)과 동어반복 검사 3건 · F-70 `Makefile` 의 `PY := .venv/bin/python` 이 Windows 에 없음 |
| 결정 | D-39(시드로 난수원 생성) · D-40(유령·중복 후보 제외와 `filters`) · D-41(폴백 판정은 후보 전체) |
| 고친 코드 | `service.py`(`rank_candidates` 서명 — `rng` 제거·`_validate_weights`·`_rankable`·`policy_fingerprint`·`filters`·`ExplorationSpec` 사용, `record_events` 레시피 없는 이벤트, `persona_for` OSError) · `engine/rerank.py`(`ExplorationSpec`·`exploration_spec`, 빈 후보, `_named`, `_similar_cooked_title`) · `engine/score.py`(감점 반올림) · `engine/feature.py`(재료를 모르는 레시피는 None) · `engine/context.py`(`cooked_titles`) · `policy.py`(범위·관계 검증 확대) · `profile_store.py`(문구) · `scripts/eval_recommend_mock.py`(rng 인자 제거, 문구) · `pyproject.toml`·`tests/conftest.py`(낡은 건수 주석) |
| 검사 | 새 16건, 교체 4건 — `test_service.py` 10(유령·중복, 가중치, 지문·추적 값, 후보 0건, 시드 재현, 감점 재현, 분위수, 레시피 없는 이벤트, OS 오류, 폴백·부족분 한 판정), `test_rerank.py` 3(MMR λ, 균등 확률 4/20, 빈 이름·유사 제목) + 출처 라벨 강화, `test_persona.py`(NaN 값, 값 동일성 강화, 중복 검사 병합), `test_profile_store.py` 2(배열 아닌 flavor, 축 모자란 제시 항목), `test_db_cutover.py`(M-06 교체, M-11·M-13 못 추가). 파트 B 검사 함수 151개, 수집 165건 |
| 검증 | ruff·format(142)·mypy(62) 종료코드 0, `pytest tests/unit` **292 passed** / coverage **90.64%**(2,169문. `persona.py`·`profile_store.py`·`score.py`·`candidate.py`·`context.py` 100%). `seeds/validate` 0, `contract` 98건 0. Mock 종단 — 두 번 실행 동일, 탐색 [4,4,4,4,4,4,4,4,2,4,10,8], 인기순 폴백 7/12, 피드백 15.07 / 0.91, 취향 없는 사용자 lift None |
| 규약 | `service.py` 446줄 · `rerank.py` 348줄 · `persona.py` 340줄 — 검토 문턱(300) 초과, 필수 분리(500) 아님. `rank_candidates` 는 인자 10개(원래 11)로 여전히 초과(N-02). `noqa: S311` 한 줄 추가(D-39, G-16 묶음) |
| 문서 | 명세 2.1.0 · 압축본 3.1.0 · 설명서 1.3.0 · 결정 기록 1.2.0 · 점검표 1.4.0(M-03·M-06, 못 10개, `--no-cov`) · 검증 기록 15절 · 안건 G-28·G-14 |
| 넘긴 것 | G-28(Thompson 확률 귀속, A) · G-14 에 `Makefile` 경로 · N-02 묶음의 `candidate_limit` · M-06 의 라우터 몫 |

### 9.16 2026-09-14 - 기획측 시뮬 시드로 이용 시나리오 실행

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "`data/test_Dataset` 에 테스트 데이터를 준비했다. 폴더를 읽고 데이터·코드 수정이 필요하면 고쳐서 테스트를 한번 돌려 보고 싶다. 임의 데이터이며 실제 동작을 가정한 가상 데이터" |
| 패키지 | `sim_warm_package` — 기획 v0.4 xlsx 11개 → `amplify_events.py`(증폭) → `convert_planning_data.py`(SQL 시드: app_user 1,600 · user_preference · user_vector · user_allergy 90 · pantry_item 4,442 · event_log 10,195) → `load_sim.sh` → `scenario_run.py`(API). 안내서 `recommend_sim_seed_runbook.md`. 안내서의 위치대로 `scripts/sim/`·`deploy/seed/sim/`·`tests/fixtures/sim/`·`tests/unit/sim/`·`docs/recommend/` 에 두었습니다(미추적, N-16) |
| 못 돌린 것 | 3-4 적재와 3-5 의 `/health` — 이 PC 에 Docker·postgres 가 없습니다(E-15). 3-5 는 `--skip-health` 로 목업 라우터 단계 2~6 만 확인했습니다(PASS, 변동 0 = 전환 전 정상) |
| 결정 | D-42(시드 `taste_vec` 은 picks 평균만) · D-43(DB 없는 엔진 시나리오 도구) |
| 발견 | 여덟 건 — F-71 변환기가 OS 줄바꿈을 따라 Windows 에서 재생성 diff · F-72 안내서의 의존성 주장(openpyxl 미잠금) · F-73 API 스크립트가 내부 키를 안 보냄(401) · F-74 `/health` 가 DB 접속을 무기한 대기 · F-75 시드 `taste_vec` 이 D-29 와 어긋남 · F-76 시드 README 의 모드 규칙·"B 후보 0건" 서술 오류 · F-77 시드 `computed_from` 과 엔진 모드 불일치(behavior 235 중 blended 40) · F-78 냉장고 시나리오의 첫 판정 기준 오류. 상세는 검증 기록 16절 |
| 새 코드 | `scripts/sim/sim_seed.py`(시드 SQL 정규식 파싱 → `SimUser`, Mock 카탈로그 → `Catalog`, 기본 소비기한, 알러지 매핑; 행 수를 `stats.json` 과 대조) · `scripts/sim/scenario_engine.py`(페르소나·문맥 → `retrieve_with_fallback`·`rank_candidates`; 전원 집계·불변식·시간순 전환·cook 5건·임박 재료·재현 판정) · `tests/unit/sim/test_sim_seed.py` 에 `test_engine_scenario_passes_without_a_db`(앞 200명) |
| 고친 코드 | `convert_planning_data.py`(`newline="\n"` 8곳, `synth_onboarding` taste_vec) · `scenario_run.py`(`Api` 헤더 `X-Internal-Api-Key`, `--api-key` 없으면 `config.get_settings`, `--skip-health`) |
| 문서 | 안내서 1.1.0(의존성 사실, 3-6, 판정 표, 한계 5건, G-29) · `deploy/seed/sim/README.md` 3절 · `docs/README.md` 1.8.2(안내서 등록) · 패키지 사본(`data/test_Dataset/`, 미추적)에 같은 파일 복사. zip 은 갱신하지 않았습니다 |
| 검증 | ruff·format(150)·mypy(62) 종료코드 0, `pytest tests/unit` **298 passed** / coverage **90.64%**, A 게이트 6종 0. `scenario_engine.py` 1,600명 PASS(41초): 불변식 위반 0 · 콜드 → 웜 전환(user 201, onboarding → blended → behavior, 무게 0 → 23.9) · cook 5건 추가 뒤 상위 10 중 9 자리 변동 · 임박 재료 2종 추가 뒤 그 재료를 쓰는 개인화 레시피 4 → 8건 · 두 번 실행 동일. 재생성 시드 = 저장본(03 만 D-42 로 변경). 분리 전후 출력 동일 |
| 규약 | `scenario_engine.py` 715줄이 02의 5.1 필수 분리(500)를 넘어 `sim_seed.py`(270) 와 `scenario_engine.py`(491) 로 나눴습니다. 평가 스크립트(624줄)는 그대로입니다(N-16 에서 함께) |
| 넘긴 것 | N-16(패키지 커밋 범위·openpyxl·평가 스크립트 분리, 유재현) · G-29(`/health` 접속 시간 제한, A) |

### 9.17 2026-09-14 - Docker Desktop 설치 뒤 시뮬 DB 경로

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "docker-postgres 를 설치해서 못 한 부분을 진행하고 싶다. 네가 대행할 수 있는지, 내가 할 부분이 있는지 알려 달라" → 역할 분담 안내 → WSL2 + Docker Desktop 설치 완료 통보. "덜 진행된 부분이 보이면 멈추고 알려 달라. 이 부분이 끝나면 커밋 범위를 정하자" |
| 환경 | Docker 29.7.2 / Compose v5.5.1, `docker run hello-world` 통과, 사용자가 `docker-users` 그룹. `make`·`psql` 없음(E-16). `deploy/.env` 를 템플릿에서 생성(값 비어 있음) |
| 한 일 | compose 로 postgres·redis 기동(init 01~04 자동 적용: 확장 5 · reco 테이블 31 · `retrieve_for_user` 등) → `migrate.py` 재료 536 → `test_smoke.py --keep` 합성 published 10,007 → `load_sim.sh` 적재 → `99_verify.sql` → DB 를 가리키는 서버로 `scenario_run.py`(`/health` 포함) |
| 발견 | 세 건 — F-79 시드 고정 id(1~1600)가 스모크 합성 유저(1~8)와 PK 충돌 · F-80 `99_verify.sql` 이 스모크 유저를 함께 셈(1608 · 94 · 4513) · F-81 합성 값의 해시 키가 id 라 오프셋을 넣자 알러지·냉장고 내용까지 변함. 상세는 검증 기록 17절 |
| 결정 | D-44(시뮬 id = 1,000,000 + 기획 번호, 시퀀스 미변경, 해시 키는 기획 번호) |
| 고친 코드 | `convert_planning_data.py`(`SIM_ID_BASE`·`hkey`, `setval` 제거) · `load_sim.sh`(`PSQL_VIA_COMPOSE` — psql 없는 PC 는 컨테이너 psql 을 stdin 으로) · `99_verify.sql`(`sim_u%` 필터) · `scenario_run.py`(기본 유저 1000184 · 1000001) · 시드 재생성(01~06 은 id 만 변경 — id 정규화 뒤 파일 동일, `stats.json` 동일) |
| 문서 | 안내서 1.2.0(make·psql 없는 PC 절차, id 범위, DB 실행 결과, G-29 보충) · `deploy/seed/sim/README.md` 매핑 · 패키지 사본 동기화(zip 제외) |
| 검증 | 적재 종료코드 0 · `99_verify` 건수·분포가 기대값과 일치 · `scenario_run.py` PASS(`/health` db true 0.11초, [5] 변동 0 = 전환 전 정상) · `scenario_engine.py` PASS · ruff·format(151)·mypy(62) 0 · `pytest tests/unit` 298 passed / 90.64% |
| 넘긴 것 | N-16(커밋 범위 — 이제 유재현과 정함) · G-29(DB 없을 때의 대기. DB 있으면 0.11초) |

### 9.26 2026-09-18 - 군집(k-means) 다양성 검증

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "A 가 벡터화·임베딩·군집한 데이터로 다양성을 확보하려던 것인데 잘 구현됐는지 확인이 필요하다. 검증 뒤 B 가 A 의 일을 적극 활용할 방안과, 개인화 16칸 쿼터 결정의 선택지를 함께 달라" |
| 검증 방법 | 목업 카탈로그는 120건·8군집이라 실 DB(46,353건·50군집)를 대표하지 못합니다. 그래서 목업 생성기로 3,000건을 만들고 **A 의 `cluster_build._tfidf`·`_svd`·`_lloyd` 를 그대로 불러** K=50 을 매긴 뒤, `rerank.mmr_select` 를 몽키패치해 선택지 넷을 같은 조건에서 측정했습니다. 운영 코드 변경 없음 |
| 찾은 것 | F-107(설계의 `content_emb` 임베딩이 아니라 재료 TF-IDF→SVD 위 군집) · F-108(군집을 지워도 20칸 중 1.8칸만 바뀜, 무작위 기준선보다 낮음) · F-109(재료 자카드와 군집은 직교 — 군집을 2.4개 벌려도 재료 ILD 는 0.003 만 움직임) · F-110(Thompson 이 관측 없이 무작위와 같음) |
| 측정 | 검증 기록 20.3. 쿼터 2 는 군집 +0.8, 가산 0.3 은 군집 +2.4 에 점수 99.3% 유지. 쿼터가 값이 작은 이유는 K=50 에 20칸이면 충돌 자체가 드물기 때문 |
| 결정 대기 | N-18 — 개인화 칸에 군집을 반영할지, 반영하면 쿼터인지 가산인지. B 의 제안은 가산 0.3(D) |
| A 에 요청 | G-33 — ① `cluster_build` 가 계산하고 버리는 54차 벡터를 저장(그것이 있어야 `f_content` 를 만들 수 있고, 768차 문장 임베딩을 새로 만들 필요가 없습니다) ② `user_cluster_stat` 을 채우는 배치(없으면 Thompson 이 영영 학습하지 않습니다) |
| 코드 | 변경 없음. 측정 하네스는 스크래치패드에 두었고 저장소에 넣지 않았습니다 |
| 넘긴 것 | 실 DB 대조(E-17 로 미실행, M-02·M-04 개통 때) · 시뮬 1,600명 재측정 · 설계 7절의 "임베딩 기반" 표현 정정(F-107) |

### 9.25 2026-09-18 - 클라우드 팀 요청: 환경변수 목록

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — 클라우드 팀이 환경변수 목록(이름 · 기본값 · 비밀 · 설명 표)을 요청. "백엔드·DB 구성 논의 뒤에 정해지는 것이 맞나? 지금 쓸 수 있으면 쓰고, 바뀔 것은 TODO 로 문서화하고 remind 해 달라" |
| 판단 | 둘로 갈립니다. **이름·기본값·비밀 여부는 코드(`src/config.py`)가 정하는 사실이라 지금 확정**이고, **값과 변수의 존재**(DB 접속값 · 연동 방식에 따른 `BACKEND_*` · 로그 DB 분리 · 풀 상한 · 키 회전)는 백엔드 회신과 RDS 적용 뒤에 정해집니다 |
| 한 일 | `docs/env_variables.md` 1.0.0 신설 — 필수 6 · 선택 24 를 코드 기본값과 이미지 기본값으로 나눠 적고(요청받은 표 형식), 4절에 미정 8줄을 "지금 상태 · 언제 정해지는가 · 정해지면 할 일" 로 남김. `docs/README.md` 1.11.2 등록, 인수인계 문서 1.2.0 의 5.1 이 이 문서를 가리킴 |
| 대조에서 찾은 것 | ① 받은 목록의 `OCR_WORKERS` 기본값 1 은 이미지 값이고 코드는 3 · ② "워커당 약 2.5GB" 는 `config.py` 주석이고 컨테이너 실측은 유휴 456MiB · 처리 중 536MiB 라 둘을 다 적고 부하 측정 전이라 밝힘 · ③ `.env.example` 에 `DB_SCHEMA` · `PG_*` 3종 · 서빙·배치 값 5개, 9개가 빠져 있음(A·`main` 파일, 알림) · ④ `REVIEW_SALT` 는 정의만 있고 읽는 코드가 없음 · ⑤ `recodb` 는 초기화 SQL 이 만들지 않아 RDS 에서 먼저 만들어야 함(인수인계 12절에 추가) |
| 리마인드 | 4.3 MUST TODO 에 "환경변수 목록 확정" 줄, 1절 다음 행동, 에이전트 메모리(`todo-env-variables-after-backend-decision`). 백엔드 회신이나 RDS 소식이 오면 유재현에게 먼저 알립니다 |
| 넘긴 것 | 클라우드 — 문서 전달(유재현) · A — `.env.example` 누락 9개 |

### 9.24 2026-09-18 - 클라우드 팀 회신: DB 는 AWS RDS

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — 인수인계 문서와 함께 물은 네 가지(DB 위치 · 확장 4종과 C.UTF-8 · 배치 권한과 HNSW 메모리 · 백업)에 대한 클라우드 팀 답변. "필요한 부분이 있다면 기록하고 참고" |
| 회신 | AWS RDS for PostgreSQL 16 · 확장 `vector` · `intarray` · `pg_trgm` · `ltree` 지원 확인, 로케일 C.UTF-8 · `reco_batch` 에 스키마 DDL·TRUNCATE, 파라미터 그룹 `maintenance_work_mem` 상향 · 매일 1회 스냅샷과 시점 복구, 7일 보관 |
| 기록 | `docs/container_handover.md` 1.1.0 — 7절 외부 의존에 배치 사실, 12절에 회신 원문과 그 답이 정하는 것, 적용 때 부탁(초기화 SQL 은 마스터로 00~05 순서 · `ALTER DATABASE ... timezone = 'Asia/Seoul'` 필수 · 역할 3종과 비밀번호 · 인스턴스 크기 공유). 접점 I-07 |
| 어긋나는 자리 | RDS 기본 시간대는 UTC 라 `01_extensions.sql` 의 `ALTER DATABASE` 가 적용되지 않으면 소비기한 임박 판정이 하루 어긋남 · `tcp_keepalives_*` 는 compose 에만 있어 원격에서는 파라미터 그룹 몫 · 회신이 `reco_batch` 만 언급해 `reco_app` · `reco_ro` 도 만들어야 함을 적음 → G-32 |
| 코드 | 변경 없음 |
| 넘긴 것 | 클라우드 — G-32 의 세 확인 · 인스턴스 크기와 `maintenance_work_mem` 값 공유 · A — 그 값으로 HNSW 생성 시간 재측정 |

### 9.23 2026-09-18 - 데이터 파트 요청 5건 반영

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — A 가 `main` 병합분을 검수하고 고쳤다는 커밋을 받아 확인 → A 의 요청을 실행/보류로 분류 → "모두 진행. 연동 작업은 그 이후로" |
| A 브랜치 검수 | `origin/develop-data-part` 17 커밋(사전 검수 536→696종 · 게이트 조임 `n_unmatched = 0` · 알러지 어휘 정본 · 판 번호 지문 · 골든 30건 · G-30 규칙 배정 28,604/46,353). 게이트 전부 0(pytest 337, contract 98, 시나리오 PASS) 뒤 `1aa0507` 로 병합. 찾은 것 셋은 A 파일이라 전달만(F-104~F-106) |
| 분류 | A 요청 5건 전부 실행. 보류 1건 — "온보딩이 `category_id` 를 채우게 할 것인가" 는 백엔드 스키마 회신 뒤 |
| 코드 (`0ffa5e9`) | 빈 팬트리 첫 조회 인기순(D-48) · 동결 키 12종(D-51) · `EventIn.occurred_at`(G-26) · `load_recipe_features` · `load_corpus_stats` · `recipe_feature_from_row`(D-50) · `enums.py` · `service.py` 의 낡은 주석. 검사 21건 |
| 데이터 (`52aec67`) | Mock 알러지 어휘를 시드에서(D-49), Mock 재료 이름 6종을 시드 이름으로, 페르소나 4명 코드화, `sim_seed.ALLERGEN_MAP` 삭제, `04_user_allergy.sql` 재생성(F-98), 대조 검사 2건 |
| 검증 | ruff · format(168) · mypy(67) · pytest 358 passed / 91.20% · contract 98 · eval · 시나리오 PASS 전부 종료코드 0. 실 DB 대조는 Docker 미기동으로 미실행(E-17) |
| 문서 | 결정 기록 `docs/decisions/2026-09-18_engine_applies_data_track_requests.md` 신설 · 검증 19절 · 안건(G-26·G-30 결정·반영, G-31 신설, N-09 정정) · 점검표 M-02·M-04 `진행` · 계획서 2.2.1 · 시뮬 안내서 1.5.0 · 기록 정정 4건(F-103) |
| 넘긴 것 | A — F-104(`judge()` 비결정, 재현 명령은 결정 기록 7절) · F-105 · F-106 · 파트 A 파일 4개 변경(`enums.py` · `schema.py` · `repository.py` · `test_contract.py`, A 의 8절 지정분) · 백엔드 — `occurred_at` 을 실어 보낼 것 · 유재현 — PR 시점 |

### 9.22 2026-09-17 - 백엔드 DB 연동을 위한 요청 문서

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "실제 backend DB 와의 연동 작업이 필요. 백엔드가 어떤 스키마를 갖고 우리가 무엇을 쓸지, 엔진 산출 중 무엇을 로깅하고 무엇을 응답으로 낼지 정해야 한다. 요청 문서를 써 달라" |
| 한 일 | `docs/backend_schema_request.md` 신설. 처음에는 우리가 쓰는 여섯 가지만 묻는 형태였는데, 유재현이 **전체 스키마를 받는 쪽**으로 고치자고 해 2.0.0 으로 다시 썼습니다 |
| 왜 전체인가 | 테스트 버전은 백엔드가 무엇을 모으는지 모르는 채로 만들었습니다. 우리가 아는 것만 물으면 **이미 모으고 있는데 몰라서 못 쓰는 데이터**는 계속 못 쓰게 됩니다. 4절에 "있으면 쓸 수 있는 것" 여덟 가지(주문 이력 · 조회 체류 · 리뷰 · 조리 완료 · 알림 반응 · 검색어 · 식이 제약 · 폐기 기록)를 예로 들어, 만들어 달라는 것이 아니라 이미 있는지 확인하려는 것임을 분명히 했습니다 |
| 핵심 질문 | ① 재료 사전의 정본이 어느 쪽인가(추천의 거의 모든 계산이 재료 id 로 돕니다) ② 소비기한이 사용자 입력인지 추정인지 구분되는가 ③ 레시피 정본이 어디인가 ④ 연동 방식 셋(DB 직접 읽기 · API 호출 · 동기화) 중 무엇인가 |
| 응답·로그 경계 | 지금은 `RankedItem` 전체가 응답에 실려 나갑니다. 화면에 필요한 것(`recipe_id` · `final_rank` · `reason` · 부족 재료 · `coverage`)만 남기고, 피처 17종 · `score` · `propensity` · `is_exploration` · `is_cuisine_slot` · `trace` 는 로그로 돌리자고 제안했습니다. **탐색 칸 표시가 응답에 있으면 클라이언트가 그 칸을 걸러낼 수 있어 측정이 깨집니다** |
| 함께 정할 것 | `score` 노출 여부 · 부족 재료를 id 로 줄지 이름으로 줄지 · 행동 이벤트를 백엔드가 `POST /v1/events` 로 보낼지 · 추천 로그를 어느 DB 에 남길지 |
| 넘긴 것 | 문서 전달과 회신 일정은 유재현. 회신이 오면 DB 전환 점검표(M-01~M-16)의 연동 항목을 그 답에 맞춰 다시 씁니다 |

### 9.21 2026-09-16 - PR #9 병합

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "완료된 코드를 main 에 머지. PR 은 기록으로만 남기고 바로 merge" |
| 한 일 | PR #9 생성 후 merge commit 으로 병합(`77da2bc`). 20 커밋 · 97 파일 · +28,640/-1,897 |
| 규약 예외 | 둘 다 PR 본문 최상단에 적었습니다. ① 라인 상한 300줄 초과 — 28,640 중 20,700 이 재생성 가능한 생성물이고 손으로 읽을 표면은 62 파일 7,940 추가(코드만 37 파일 5,326)입니다. ② 최소 승인자 1명 미충족 — 소유자 지시로 리뷰 없이 병합했고, **Tech Lead 사전 승인은 없습니다**. 01의 5.1 이 사후 승인을 인정하지 않으므로 받았다고 적지 않았습니다 |
| 병합 전 확인 | `git merge-tree` 충돌 0 · `mergeable_state` clean · 3.2 게이트 4종 종료코드 0(318 passed / 91.02%) · 병합 뒤 `git diff origin/main HEAD` 비어 있음 |
| 문서 | 병합 뒤 `docs/release_notes.md` 신설(01의 8절이 요구하는 릴리스 노트 — 변경 요약 · 영향 범위 · 롤백 방법). PR #8 때는 이 문서가 없었고 병합 사실만 작업 기록 9.13 에 남았습니다. 파트 A · C · 백엔드/프론트 · 클라우드 네 몫의 전달 사항을 절마다 병합 버전(`77da2bc`)과 날짜를 붙여 자립적으로 적었습니다 |
| 후속 | 릴리스 노트를 PR #10 으로 병합(`ff3c6cf`). **그 merge commit 메시지가 "#9" 로 잘못 적혔습니다** — 병합 스크립트가 앞 PR 의 제목을 그대로 보냈고, 병합 결과(`merged: true`)만 보고 메시지를 확인하지 않았습니다. `main` 이력은 다시 쓰지 않고 릴리스 노트 2.1 에 적어 두는 것으로 갈음합니다 |
| 넘긴 것 | 릴리스 노트의 파트별 절을 각 파트에 전달(유재현) · 브랜치 삭제 여부는 유재현 결정 |

### 9.20 2026-09-16 - 클라우드 팀 요청: 서비스 Dockerfile

파트 B 의 엔진 변경이 아니라 저장소 전체에 걸린 산출물이라 여기 짧게만 남깁니다.

| 항목 | 내용 |
|---|---|
| 입력 | 클라우드 팀 요청 문서 — CI/CD 를 위해 각 서비스 폴더에 Dockerfile. 예시는 `python:3.11-slim` + `requirements.txt` |
| 다른 점 | 이 저장소는 ① Python **3.12**(3.11 은 설치 거부) ② `requirements.txt` 없이 `uv.lock` 정본(01의 1절, pip 직접 사용 금지) ③ 소스 루트가 `src/` 라 패키지를 설치해야 `main:create_app` 이 보임. 셋 다 예시대로 쓰면 빌드가 안 되거나 조용히 틀립니다 |
| 산출물 | 루트 `Dockerfile`(4단계 — base·builder·models·runtime) · `.dockerignore` · `deploy/docker-compose.yml` 의 `reco-api`(profile `app`) · `Makefile` 의 `up-app`(2026-09-04 에 "Dockerfile 과 함께 되살린다" 고 남겨 둔 자리) · `README.md` 3절 |
| 결정 | OCR 모델 가중치 98MB 를 **빌드 때 굽습니다**(유재현 확인). 런타임에 받게 두면 컨테이너마다 받고, 외부 통신이 막힌 클러스터에서는 `/health/ready` 가 영영 503 인데 에러는 안 납니다. 굽는 명령은 앱이 쓰는 `_load_engine()` 을 그대로 부릅니다 — 인자를 따로 적으면 구운 모델과 실제로 쓰는 모델이 갈라집니다 |
| 빌드에서 걸린 것 | 시스템 라이브러리를 실행 단계에만 적었더니 모델 굽는 단계에서 `libGL.so.1` 이 없어 실패. 빌드·실행이 같은 `base` 단계를 쓰도록 고쳤습니다 — 따로 적으면 **빌드는 성공한 뒤 첫 OCR 요청에서** 터집니다. `tzdata` 도 함께 깝니다(없으면 `TZ` 를 줘도 조용히 UTC 로 돌아 임박 판정이 하루 어긋납니다) |
| 검증 | `docker build` 종료코드 0(2.62GB) · 컨테이너 기동 후 `/health/live` 200 · `/health/ready` 200(8초) · 키 없이 `/health` 401 · 키로 `db: true` · `/v1/recommend` 200 · 실행 사용자 uid 10001 · 시간대 KST · 이미지 안에 `.env` 없음. compose 경로(`--profile app`)도 같은 결과 |
| 넘긴 것 | 공유 파일 넷(`Dockerfile` · `.dockerignore` 는 신규, `deploy/docker-compose.yml` · `Makefile` 은 파트 A 소유)을 A 에 알림 · 이미지 경량화와 보안 스캔은 클라우드 팀 몫 · `deploy/.env` 의 `INTERNAL_API_KEY` · `GEMINI_API_KEY` 가 비어 있으면 `make up-app` 이 기동에 실패합니다(의도된 동작) |

### 9.19 2026-09-15 - 온보딩 음식 유형 (신규 문항)

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "온보딩에 좋아하는 음식 유형(한식·중식·일식·양식·아시안)이 추가됐다. 최초 페르소나에 넣되 추천 목록에 절대적이지 않게 조금 반영하고, 맛 페르소나 기준 좋아할 만한 것을 몇 개 포함해 달라. 목업 데이터도 없으면 추가" |
| 확인 | 값을 받을 자리는 이미 있었습니다 — `user_preference.pref_cuisines`(DDL) · `f_cuisine` 0.04 · `UserContext.preferred_cuisines`. 없던 것은 ① 계약(`OnboardingIn` 에 문항이 없어 서버가 받지 못함) ② 정본 목록(목업은 분식, 시뮬은 기타, DDL 은 family 코드로 제각각) ③ 목록에 닿는 경로(F-82~F-84) |
| 결정 | D-45(맛 축과 분리) · D-46(가중치가 아니라 유형 슬롯) · D-47(코드로 저장, 모르는 값 거부) |
| 코드 | `enums.py`(`CuisineFamily`·`ONBOARDING_CUISINES`·`CUISINE_LABELS`·`normalize_cuisine`) · `schema.py`(`OnboardingIn.preferred_cuisines`·`OnboardingOut`) · `persona.py`(`TasteProfile.cuisines`·`Persona.cuisines`·`require_cuisines`) · `profile_store.py`(schema 2, 판 1 도 읽음) · `service.py`(`onboarding_profile(cuisines=)`·추적 `n_cuisine`·`cuisine_unmet`) · `context.py`(페르소나 폴백·코드 정규화) · **`engine/cuisine.py` 신설** · `rerank.py`(슬롯 배치·사유) · `stage.py`(`RankedItem.is_cuisine_slot`) · `policy.py`(`cuisine_slot_ratio` 0.1 · `cuisine_slot_max` 2) · `mock.py`(레시피 유형 5종·온보딩 반영, 유형당 한 칸) |
| 데이터 | `seeds/onboarding_recipes.yaml` 재생성 — 다섯째 계열 "기타" 9종을 아시안 5 · 양식 4 로 가름(F-85, 6축 값 불변) · 목업 카탈로그 재생성(유형 코드 · 한식 편중 120건 중 80, F-86) · 시뮬 `02_user_preference.sql` 재생성(라벨 → 코드, 다른 시드 파일과 `stats.json` 은 동일) |
| 검사 | `tests/unit/recommend/test_cuisine.py` 19건 신설(계약·페르소나·슬롯·목업·시드 대조). `scripts/sim/sim_world.py` 분리(02의 5.1, 500줄) 후 시뮬 시나리오에 [6] 음식 유형 추가 |
| 3회 검수 | 구현 뒤 세 번 다시 읽어 6건(F-91~F-96)을 더 고쳤습니다. 가장 큰 것은 유형 칸이 다른 유형을 목록에서 지우던 것(F-91)이고, 목업이 실제 구현과 다르게 돌던 것 둘(F-92·F-93), 파트 A 파일에 함수를 더한 것(F-94), 시뮬 판정이 아무 일도 안 해도 통과하던 것(F-95), 불필요한 `sys.path` 조작(F-96)입니다 |
| 검증 | ruff · format(154) · mypy(63) · `pytest tests/unit` 315 passed / 91.02% 전부 종료코드 0, A 게이트 6종 0(contract 98건). 시뮬 1,600명 RESULT: PASS(판정 6종, 불변식 위반 0) — 고른 유형이 Top-20 에 한 건도 없는 사람이 슬롯을 끄면 460명, 켜면 178명 |
| 문서 | 결정 기록 `docs/decisions/2026-09-15_cuisine_choice_as_slots_not_weight.md` 신설 · DB 전환 점검표 1.5.0(**M-16** — 음식 유형을 `user_preference.pref_cuisines` 에서 읽지 않으면 전환 순간 전원이 "유형을 고른 적 없는 사용자"가 되고 응답은 200 입니다. `test_db_cutover.py` 에 못을 박았습니다) · `deploy/seed/sim/README.md` · `docs/README.md` 1.8.4 |
| 넘긴 것 | G-30(레시피 `cuisine_family` 전수 결측 — 실 DB 에서는 유형 슬롯도 `f_cuisine` 도 돌지 않음) · 계약 때문에 손댄 파트 A 파일 4개(`enums.py` · `schema.py` · `stage.py` · `test_contract.py`)를 A 에 알림(F-90, 18.2절) |

### 9.18 2026-09-14 - 시뮬 패키지 커밋 (N-16 결정)

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 — "(a)안으로. 의존성이 필요하면 환경 파일 등에 포함해서 진행" |
| 결정 | N-16 → (a) 전부 커밋. openpyxl·pandas 를 `dev` 묶음에 `uv add --group dev --no-sync` (F-72 해소. 가상환경은 같은 버전이 이미 있어 lock 만 갱신, `uv lock --check` 0) |
| 커밋 | `cdf656e` build(deps: pyproject·uv.lock) · `3633ed7` feat(sim): 31 파일 +21,435 — `scripts/sim` 5 · `tests/unit/sim` 2 · `tests/fixtures/sim/planning_v0.4` xlsx 11 · `deploy/seed/sim` 11 · 안내서 1.3.0 · `docs/README.md`. xlsx 수 문구 수정 `1865540` · 기록 `70e3b31` |
| 검증 | 커밋 직전 ruff check · ruff format --check(151) · `uv lock --check` 종료코드 0. 코드는 9.17 이후 변경 없음(pytest 298 passed) |
| 넘긴 것 | 공유 경로(`deploy/`·`tests/fixtures/`)와 `dev` 의존성 변경을 A 에 알림 · 미병합 커밋 13개의 새 PR(보류) |
