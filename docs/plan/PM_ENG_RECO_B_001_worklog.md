# 파트 B 추천 엔진 작업 기록 (에이전트용)

**정하는 것**: `PM-ENG-RECO-B-001` 의 실행 이력. 상태, 결정(D), 가정(A), 접점(I), 계획 항목(T), 선행 조건(P), 고려사항(C), 환경(E)

**적용 대상**: 파트 B 추천 엔진을 이어서 작업하는 AI 코딩 에이전트. 사람은 `human/` 의 서술본을 읽습니다

**버전**: 0.5.0 · **최종 수정**: 2026-09-10 · **작성자**: 유재현

---

## 1. 상태 요약

| 키 | 값 |
|---|---|
| 정본 | 이 파일. 사람용 서술본 `human/PM_ENG_RECO_B_001_worklog.md` 는 식별자로 대응하는 파생본 |
| 계획서 | `PM_ENG_RECO_B_001.md` (사람용, 정본) · `PM_ENG_RECO_B_001_agent.md` (요약, 어긋나면 원본이 이김) |
| 브랜치 | `feat/recommend-engine-core` = `origin/feat/recommend-engine-core`. 코드 HEAD `7d72324` (9.3), 문서 커밋은 그 뒤 |
| 단계 | Layer 1 완료 = Step 0 · Step 2~4 의 순수 함수 · Step 5 의 조립·피드백 수식. Step 1, Step 6 미착수. DB·라우터 미연결 |
| 검증 (2026-09-10) | ruff check OK · ruff format OK · mypy 36 files OK · `pytest tests/unit/recommend` 111 passed · 전체 191 passed / 1 failed (P-02, 기존) / coverage 95.16% · 기획서 정합·Mock 동작·2차 재검증은 `PM_ENG_RECO_B_001_eval.md` (X-01~X-10 반영 완료) |
| 다음 행동 | T-17 (A 트랙과 3자 회의 안건 P-11~P-15 정리) → P-01 → T-05 를 A 의 `repository.retrieve` 호출로 재정의 (A-10) |
| 병합 정책 | PR 없음. 브랜치 커밋·push 만. `main` 병합은 A·B 파트 완료 후 논의 (P-10) |
| 갱신 규칙 | 세션마다 1절·3절·9절 갱신. 새 항목은 다음 번호, 번호 재사용 금지. 사람용 서술본 동시 갱신 (`README.md` 2절) |

---

## 2. 읽는 법

- 계획서 체크박스는 템플릿(02의 2.5). 결과는 이 파일에만 적는다.
- 세션 시작 순서: 1절 → 4절 → 5절. 통합(실데이터·BE·DB) 작업이면 8절 먼저.
- 검증은 실행 출력이 있는 것만 기록. 추정 금지.
- 식별자 접두: D 결정 · A 가정 · I 접점 · T 계획 항목 · P 선행 조건 · C 고려사항 · E 환경. 폐기는 삭제하지 않고 상태 열에 "폐기" 표기.
- 초안은 Claude Code 가 쓰고 유재현이 검토한다. 검토 전 문장은 남기지 않는다.

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
| D-18 | 3.2 수식·3.3 탐색 규칙 | 검증 기록 X-02(맛 신뢰도), X-05(탐색 축소), X-06(요리군 항), X-07(맛 거리 novelty) 로 확장 | Mock 검증 F-02·F-04·F-06·F-07. 상세는 `PM_ENG_RECO_B_001_eval.md` 7.1 | W3 가중치 학습 또는 명세 소유자 결정 |

### 3.3 검증 출력 (2026-09-10, 커밋된 트리)

```text
uv run ruff check .                   → All checks passed!
uv run ruff format --check .          → docs/plan 제외 72 files already formatted (계획서 코드블록 1건은 docs 커밋에서 포맷)
uv run python -m mypy src             → Success: no issues found in 35 source files   (E-01)
uv run pytest tests/unit/recommend    → 96 passed
uv run pytest tests/unit (유효한 .env 를 둔 별도 cwd, E-03)
                                      → 1 failed (P-02), 177 passed, coverage 95.04% (기준 80%)
```

모듈 커버리지: `candidate` 100 · `context` 100 · `rank` 100 · `penalty` 100 · `feedback` 100 · `service` 100 · `rerank` 99 · `schema` 99 · `explain` 98.
변경 규모: src 1,072줄 · 테스트 1,276줄 · 스크립트 346줄 · 생성 JSON 4,020줄.

---

## 4. 실행되어야 할 것

### 4.1 계획 항목 (T)

| ID | 단계 | 항목 | 상태 | 선행 조건 |
|---|---|---|---|---|
| T-01 | Step 0 | `POST /v1/recommend`, `POST /v1/events` 라우터 (prefix `/recommendations` → `/v1`) | 미착수 | T-09 와 함께 실연결 권장 |
| T-02 | Step 1 | `tables.py` (`recommendation_log`, `event_log`, `user_vector`) + `infra/metadata.py` 등록 | 미착수 | P-01. 운영 반영·되돌리기 방법 커밋 body (03의 5절) |
| T-03 | Step 1 | `repository.log_serving_result` 비동기 예외 격리 (TC-1-1, TC-1-2) | 미착수 | `service.ServingLog` → JSONB. I-05 |
| T-04 | Step 1 | `/health` 카운터 `reco_served_total`, `reco_failed_total`, `reco_degraded_total` (TC-1-3) | 미착수 | `main.py` 공용 파일. 카운터 저장 위치 |
| T-05 | Step 2 | `repository.fetch_candidates` intarray (TC-2-3 p95 < 15ms) | 미착수 | Track A `recipe_feature`, `intarray`, GIN. 컬럼 확장 D-13. 조회 범위 A-10 |
| T-06 | Step 2 | 알레르기 코드군(19종) → 재료 ID 조회 | 미착수 | P-07. 픽스처 `allergen_groups` 는 대역 |
| T-07 | Step 3 | `feature_stats` → `CorpusStats` | 미착수 | Track A `feature_stats` 계약. I-03 |
| T-08 | Step 4 | 요리군별 `cuisine_priors` Beta(α, β) 집계 | 미착수 | `event_log` 누적. 그전엔 (1, 1) |
| T-09 | Step 5 | `service.recommend()` DB 조립, `RankConfig` ← `Settings` (`config.py` + `.env.example`) | 미착수 | T-02, T-05. D-04 이행 |
| T-10 | Step 5 | `POST /v1/events` → `feedback.update_behavior_vector` → `user_vector` | 미착수 | T-02 |
| T-11 | Step 5 | `evaluation/feature_report.py` (TC-5-3) | 미착수 | 17개 특성 목록 확정 (현재 5블록 입력만) |
| T-12 | Step 6 | 계약 검증 42건 (TC-6-1) | 미착수 | 42건 정의 문서, P-05 |
| T-13 | Step 6 | Locust p95 < 58ms (TC-6-3) | 미착수 | P-06 |
| T-14 | 7절 | NDCG@10, Recall@20, 커버리지, ILD 실측 · Bradley-Terry 가중치 | 미착수 | 600쌍 라벨, Track C 하네스 |
| T-15 | 문서 | `docs/plan/` 커밋과 `docs/README.md` 등록 | 완료 (`60f1d40`) | P-09 |
| T-16 | 검증 | `PM_ENG_RECO_B_001_eval.md` 6절 수정 제안 9건의 채택 여부 결정과 반영 | 완료 (X-01~X-10, 9.3) | 2차 재검증 `_eval.md` 7절 |
| T-17 | 통합 | A 트랙과 3자 회의 안건 정리와 병합 계획: P-11~P-15 | 미착수 | A 공유 문서 2026-09-10, `origin/develop-data-part` 실체 확인 (9.3) |

### 4.2 선행 조건 (P)

| ID | 항목 | 왜 | 담당 / 승인 |
|---|---|---|---|
| P-01 | `.env`: 필수 6개(`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `INTERNAL_API_KEY`, `GEMINI_API_KEY`)만 채우고 기본값 있는 15줄 삭제 | `KEY=` 빈 값을 pydantic-settings 가 값으로 취급 → 숫자 13필드 실패 → 단위 테스트 56건 실패. 추천 테스트는 무관 | 유재현 |
| P-02 | `tests/conftest.py` 에서 `Settings.env_file` 끄기 | `test_missing_required_key_fails_at_startup` 가 실제 `.env` 를 읽어 `DB_HOST` 있으면 항상 실패 | 김민경 (공용) |
| P-03 | `config.py` `env_ignore_empty=True` 제안 | `.env.example` 복사만으로 기본값 동작. 검증 완료 | 김민경 |
| P-04 | `--import-mode=importlib` 승인 (D-12) | 적용·검증 완료. 공용 파일 | Tech Lead |
| P-05 | `Makefile` (`make contract`, `smoke`, `log-test`, `feature-test`) | 계획서 검증 명령이 참조하나 저장소에 없음. 신규 도구 승인(01의 1절) | Tech Lead |
| P-06 | `locust` 의존성 (`uv add`, body 에 사유) | TC-6-3 | Tech Lead |
| P-07 | 기피 재료(`avoid_ingredient_ids`) 출처 | 요청 스키마에 없음. `UserHistory` 에 자리만 | BE 계약 협의 |
| P-08 | 대체재(`substitute_ids`) 출처 | 2단계 폴백용 데이터 없음. 없으면 건너뜀 | Track A |
| P-09 | `docs/plan/` 파일명 언더바, `docs/README.md` 목록 등록, 계획서 코드블록 포맷 | 02의 2.4·2.5. 2026-09-10 세션에서 처리 | 유재현 → 김민경 통보 |
| P-10 | 브랜치·병합 정책 | PR 은 현 팀 정책상 대상 아님. `main` 병합은 A·B 완료 후. 선례 PR #3(2,409줄·9커밋·merge commit)은 OCR 단독이라 상황 다름 | 유재현 |
| P-11 | 02 규약에 `stage.py` 추가 | D-17 로 도메인 루트 파일이 7개. 원격 `develop-data-part` 의 02 문서에도 `stage.py` 없음. 01의 9절 개정 절차 | A, 김민경 |
| P-12 | A 브랜치와의 파일·이름 충돌 조정 | `engine/rank.py`, `service.py`, `router.py`, `repository.py`, `__init__.py` 가 양쪽에 별개 구현. `stage.ScoredCandidate` 형태 상이(A 17 피처 dict, B 5 블록). `RecommendRequest/Response` 는 A `make contract` 98건이 의존 | 3자 회의 |
| P-13 | A `tests/conftest.py` 의 `collect_ignore_glob = ["unit/recommend/*.py"]` | B 테스트 전체가 수집에서 빠짐. A 가 파일명 명시로 수정 예정. 병합 전 확인 | A |
| P-14 | propensity 의미 통일 | A `RankedItem.propensity` 는 확률(≤1), B `ServedItem.propensity` 는 역수(≥1). 로그 스키마 하나로 | 3자 회의 |
| P-15 | `user_vector.taste_vec` 6축 (A DDL) vs B EMA 3축 | 갱신 시 앞 3축만 쓰고 뒤 3축을 보존할지, 6축 EMA 로 갈지 | A, 유재현 |

---

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
| C-07 | propensity: 개인화 1.0 · 균등 = 풀 크기 / 뽑는 수 · Thompson = MC 64회 추정 역수(라플라스). 표본 수 `propensity_samples` | `rerank._win_probability` |
| C-08 | MMR 비용 = 후보 수 × 슬롯 수 (500×16=8,000 자카드), 최대 유사도 증분 갱신. 지연시간 실측 없음 | `rerank.mmr_select` |
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
| 기록 | `PM_ENG_RECO_B_001_eval.md` + `human/` 서술본. 기획서 조항 23건 대조, 검증 항목 16건, 발견 12건, 수정 제안 9건 |
| 코드 변경 | 없음. 수정은 결과를 보고 결정 (T-16) |
| 커밋 | `chore(recommend): add the mock evaluation script` · `docs(plan): record the mock evaluation and fix proposals` |

### 9.3 2026-09-10 — 수정 반영과 A 트랙 정렬

| 항목 | 내용 |
|---|---|
| 입력 | 유재현 결정: X 전부 반영. A 트랙 공유 문서(맞출 것 3가지). `origin/develop-data-part` 를 fetch 해 실체 확인 |
| 우선순위 판단 | 맛 축 순서(D-16)를 먼저 고침. 축이 어긋난 채로는 1차 평가와 X 재검증이 전부 틀린 축 위에서 나오기 때문 |
| 커밋 (순서대로) | `ba3e0f1` fix: 축 순서 · `0706f66` fix: 맛 사유 방향·부족 개수 (X-01, X-03) · `05e47a3` fix: 폴백 조리시간 상한 (X-04) · `c1f26f4` feat: 맛 블록 감쇠 (X-02) · `0d87904` feat: 요리군 항 (X-06) · `3ed126e` fix: 탐색 축소·맛 novelty (X-05, X-07) · `1d26e0d` perf: IDF 합·MMR 풀 (X-08) · `85c9dcd` docs: 재정렬 금지 (X-09) · `e2fc72d` refactor: `stage.py` (D-17) · `33ea107` fix: 0점 블록 제외 (X-10) · `7d72324` chore: 평가 분류 |
| 검증 | 커밋마다 ruff·mypy·`pytest tests/unit/recommend` 통과. 최종 111 passed, 전체 191 passed / 1 failed(P-02) / coverage 95.16%. 2차 Mock 재검증은 `_eval.md` 7절 |
| A 브랜치에서 확인한 것 | `stage.py`(RetrievalInput, Candidate, ScoredCandidate(17 피처 dict), RankedItem …) · `enums.FEATURE_KEYS` 17개와 `DEFAULT_WEIGHTS`(f_coverage .24, f_taste .16, f_expiring .15, f_ing_pref .11, f_cooccur .10, f_popularity .10, f_missing .05, f_cuisine .04, f_time_fit .03, f_season .02) · `engine/{rank,explore,reason,serendipity,mock}.py` 별개 구현 · `service.py` 는 카운터만(흐름은 `engine/mock.py`) · `repository.py` 365줄(`retrieve`, `write_recommendation`) · `router.py` 에 `/v1/recommend`, `/v1/events`, pantry·onboarding·search · `deploy/init/02_schema.sql`(`recipe_feature` flavor_vec REAL[6], `feature_stats.flavor_mu` REAL[6], `user_vector.taste_vec` REAL[6], `user_allergy`, `ingredient_substitute`, `recommendation_log`, `event_log`, `scoring_config`) · `Makefile`(`contract`, `log-test` 등) · `tests/conftest.py` 의 `collect_ignore_glob` (P-13). 공유 파일 변경: `pyproject.toml` +85, `config.py` +64, `main.py` +24, `conftest.py` +22, `.gitignore` +51 |
| 넘긴 것 | T-17 (P-11~P-15), P-01 |
