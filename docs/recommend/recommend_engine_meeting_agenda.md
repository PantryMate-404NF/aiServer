# 추천 엔진 통합 회의 안건 (에이전트용)

**정하는 것**: 파트 B 추천 엔진을 A(데이터)·C(평가)·백엔드와 합칠 때 정해야 할 안건. 타 파트와 논의할 것(G)과 B 안에서 처리할 것(N)을 나눕니다

**적용 대상**: 3자 회의 참석자와 파트 B. 사람은 `human/` 의 서술본을 읽습니다

**버전**: 1.0.0 · **최종 수정**: 2026-09-10 · **작성자**: 유재현

---

## 1. 요약

| 키 | 값 |
|---|---|
| 근거 | A 트랙 공유 문서(2026-09-10, 맞출 것 3가지)와 `origin/develop-data-part` 실체 확인(작업 기록 9.3) |
| 타 파트 안건 | G-01~G-12. 회의 없이 못 정하는 것 7건(G-01~G-07), 통보·확인으로 끝나는 것 5건 |
| 내부 안건 | N-01~N-10. 회의 결과에 걸린 것 3건(N-02, N-03, N-08), 나머지는 B 가 바로 처리 |
| 급한 것 | G-02·G-04 (같은 경로 파일과 같은 이름 모델의 정본 결정). 미루면 양쪽 브랜치가 더 벌어짐 |
| 이미 맞춘 것 | 맛 축 순서(D-16), `stage.py` 배치(D-17). 회의에서는 확인만 |
| 갱신 | 회의 뒤 결정 열을 채우고 결론이 되돌리기 어려운 것은 `docs/decisions/` 에 파일로 남깁니다 |

---

## 2. 타 파트와 논의할 것 (G)

| ID | 안건 | 배경 | 선택지 | B 의 제안 | 결정자 | 결정 |
|---|---|---|---|---|---|---|
| G-01 | 맛 벡터 축 수와 갱신 | 축 순서는 맞춤(D-16). A DDL 은 `flavor_vec`·`feature_stats.flavor_mu`·`user_vector.taste_vec` 전부 6축, B 계산은 3축(P-15) | (a) B 는 앞 3축만 읽고 `user_vector` 갱신도 앞 3축만, 뒤 3축 보존 (b) B EMA 를 6축으로 확장 (c) W3 학습 뒤 결정 | (a). 6축 사용 여부는 A D-11 대로 W3 학습에서 정함 | A, B | 미정 |
| G-02 | 스테이지 모델 통일 | `stage.ScoredCandidate` 가 양쪽에 다른 형태. A 는 `features` 17종 dict + `score` + `penalty`, B 는 `blocks` 5종 + `base_score` + `score`. A `Candidate`(coverage, cluster_id) vs B `RecipeCandidate`(essential_ids, all_ids, flavor_vec …). A `RankedItem` vs B `ServedItem` (P-12) | (a) A 형태를 채택하고 B 엔진이 17 피처 dict 를 채움(계산 안 되는 피처는 None) (b) B 형태 유지, A 의 로그 라이터가 변환 (c) 두 층: B 내부는 블록, 로그 경계에서 A 형태로 변환 | (c). 엔진 순수 함수는 그대로 두고 `service` 가 A 의 `RankedItem` 으로 바꿔 `write_recommendation` 에 넘김. 17 피처 채움은 G-07 | A, B | 미정 |
| G-03 | `RecommendRequest`/`RecommendResponse` 계약 | A 것은 `make contract` 98건과 pantry·onboarding·search 라우터가 의존. B 것은 랭킹 파이프라인이 씀. 차이: `meta` 고정 5필드(D-07), `FeedbackEventRequest` 의 `Literal`·`datetime`(D-08), 응답 순서 재정렬 금지(X-09) | (a) A 계약을 정본으로 하고 B 가 맞춤 (b) B 계약을 정본 (c) 필드 단위로 병합 | (c). 요청은 A 의 필드 집합 ⊇ B 라 A 기준, 응답 `meta` 는 고정 필드로, `recommendations` 순서 보존 조항 추가. 98건 계약 테스트는 유지 | A, B, 백엔드 | 미정 |
| G-04 | 같은 경로 파일의 정본 | `engine/rank.py`·`explore.py`·`reason.py`·`serendipity.py`·`mock.py`, `service.py`, `router.py`, `repository.py`, `__init__.py` 가 양쪽에 별개 구현(P-12). A `service.py` 는 카운터만이고 흐름은 `engine/mock.py` | (a) 파일별 소유 지정 (b) 한쪽 폴더를 통째로 채택 | (a). 엔진(`engine/*`)과 `stage.py` 는 B, `repository.py`·DDL·`ingest/` 는 A, `router.py`·`service.py` 는 A 의 엔드포인트 집합에 B 의 `run_pipeline` 을 끼우는 공동 작업. A 의 `mock.py`·`reason.py`·`serendipity.py` 는 대시보드용이면 `engine/` 밖 이름으로 | A, B | 미정 |
| G-05 | propensity 의미 | A `RankedItem.propensity` 는 확률(≤1, `1/|pool|`), B `ServedItem.propensity` 는 역수(≥1)(P-14). 로그 스키마는 하나여야 함 | (a) 확률 저장, 역수는 학습 시 계산 (b) 역수 저장 | (a). A DDL 과 C 의 IPS 계산이 확률을 전제. B 는 `_mix` 에서 역수 대신 확률을 싣도록 바꿈 | A, C | 미정 |
| G-06 | 후보 조회 계약 | A 에 `repository.retrieve` 와 SQL 함수 `retrieve_candidates`(A D-14) 가 있음. B `select_candidates` 는 넘겨받은 풀 안에서 k 2→3→4 완화(A-10). A 의 LIMIT 과 k 처리 방식에 따라 폴백이 빈손이 될 수 있음 | (a) A 함수가 k=4 로 넉넉히 반환하고 B 가 안에서 완화 (b) B 가 단계마다 A 함수를 재호출 (c) 완화 로직을 SQL 함수 안으로 | (a). 정렬이 부족수 오름차순이라 상위 500 이 k=2 후보를 포함함. 인기순 폴백은 별도 호출 | A, B | 미정 |
| G-07 | 17 피처와 5 블록의 대응 | A `enums.FEATURE_KEYS` 17개, `DEFAULT_WEIGHTS`(f_coverage .24, f_taste .16, f_expiring .15, f_ing_pref .11, f_cooccur .10, f_popularity .10, f_missing .05, f_cuisine .04, f_time_fit .03, f_season .02, 나머지 0). B 는 5 블록(S-23, T-11). `candidates_features` 로그와 `feature_report` 가 17 키를 기대 | (a) B 블록을 17 키로 펼쳐 기록(f_coverage=match, f_expiring=expiring, f_taste=taste, f_popularity·f_quality=quality 의 두 항, f_time_fit·f_cuisine=ctx 의 두 항, 나머지 None) (b) B 가 17 피처를 전부 계산 | (a). 계산 안 되는 키는 None(A 의 "None 과 0 은 다르다" 규약대로). W3 학습 전까지는 5 블록으로 서빙 | A, B, C | 미정 |
| G-08 | A `tests/conftest.py` 의 `collect_ignore_glob` | `unit/recommend/*.py` 가 B 테스트 12 파일을 수집에서 뺌(P-13). A 가 파일명 명시로 고치겠다고 함 | (a) A 의 단독 실행 스크립트를 파일명으로 명시 (b) A 스크립트를 `scripts/` 로 이동 | (b) 가 02의 3.3 에 맞음. 급하면 (a) | A | 미정 |
| G-09 | 문서 배치 규칙 개정 | 04의 1.1 트리에 `docs/recommend/` 가 없음(P-16). A 는 설계 문서 42개를 저장소 밖에 두고 결정사항 문서만 올리자고 제안 | (a) 04 에 `docs/<도메인>/` 를 "설계 명세·기록·회의 안건" 자리로 추가 (b) 기록은 저장소 밖, 결정만 `decisions/` | (a). 01의 9절 절차로 개정 PR. 두 형식 기록의 근거는 `../decisions/2026-09-10_recommend_record_dual_format.md` | 김민경, 팀 전원 | 미정 |
| G-10 | 02 규약에 `stage.py` | 양쪽 도메인 루트가 7개 파일(P-11). 원격 A 브랜치의 02 에도 없음 | (a) 02의 2.2 표에 `stage.py` 추가 (b) 모델을 `schema.py` 로 되돌림 | (a). A 공유 문서 2절과 D-17 | 김민경, A | 미정 |
| G-11 | 공유 파일 병합 | `pyproject.toml`(B addopts, A 의존성·per-file-ignores), `docs/README.md`(B 목록, A 04 등록), `tests/conftest.py`(A env_file 차단 — B P-02 해결됨), `config.py`(A +64), `main.py`(A +24) | 병합 담당이 둘 다 살림 | 내용 충돌 없음. B 는 `origin/main` 을 이미 merge 해 `docs/README.md`·`pyproject.toml` 은 해소됨. A 브랜치 병합 시 재확인 | 병합 담당 | 미정 |
| G-12 | 결정사항 문서 저장소 반영 | A 의 `05_작업분담_결정사항.md`(D-1~D-17)가 로컬에만 있어 축 순서 사고가 났음 | (a) 그 문서를 `docs/decisions/` 형식으로 나눠 올림 (b) 통째로 올림 | (a). 04의 1.2 파일명 규칙(`YYYY-MM-DD_제목`). B 도 D-01·D-16·D-17 을 같은 형식으로 올림(N-07) | A | 미정 |

회의 전에 확인할 사실: A 의 미푸시 커밋 10개에 02 규약 변경이나 `stage.py` 형태 변경이 있는지, `FEATURE_KEYS` 가 최신인지.

---

## 3. B 안에서 논의·처리할 것 (N)

| ID | 안건 | 배경 | 처리 | 상태 |
|---|---|---|---|---|
| N-01 | `.env` 정리 | 필수 6개만 채우고 기본값 있는 15줄 삭제(P-01). `main` 의 conftest 가 `env_file` 을 막아 P-02 는 해결됨 | 유재현이 값을 넣음. 이후 `uv run pytest tests/unit` 전체 통과 확인 | 대기 |
| N-02 | `RankConfig` 를 `Settings` 로 옮기는 시점 | 규약 03의 2절(D-04). G-07 결과에 따라 지문에 들어갈 값 집합이 바뀜 | G-07 뒤 T-09 에서. `.env.example` 갱신 동반 | G-07 대기 |
| N-03 | 라우터 실연결 | `router.py` prefix `/recommendations`(D-06, T-01). A 의 `router.py` 가 `/v1/recommend`·`/v1/events` 를 이미 가짐 | G-04 에서 공동 소유로 정해지면 A 라우터에 `run_pipeline` 을 연결 | G-04 대기 |
| N-04 | 요리군 항과 맛 정합의 절충 | 양식 선호 단맛 사용자에서 맛 lift 가 −0.02(F-13). `w_ctx` 0.10 안에서 평균이라 반영 폭 ≤ 0.05 | 실데이터 재측정 후 W3 학습 대상으로 넘김. 지금은 값 유지 | 관찰 |
| N-05 | 탐색·맛 손잡이 값 | `exploration_min_pool_ratio` 2, `novel_taste_max` 0.4, `taste_min_norm` 0.25, `taste_reason_min_deviation` 0.125, `mmr_pool_size` 200(예시값, 실제 데이터로 대체 필요) | 실데이터 후보 500 기준으로 재조정. 지문에 들어가므로 바꿀 때마다 기록 | 대기 |
| N-06 | 기준점 태그 | Layer 1 완료 시점 `cbdf312`, 수정 반영 완료 `7d72324` | 통합 전에 `reco-b-layer1` 태그 부여 여부 결정 | 대기 |
| N-07 | 결정 기록 분리 | D-01~D-18 중 되돌리기 어려운 것은 `docs/decisions/` 에 1건 1파일이 규칙 | D-01(개인화 선행), D-16(축 순서), D-17(`stage.py`)을 `YYYY-MM-DD_제목.md` 로 작성. G-12 와 형식 통일 | 대기 |
| N-08 | 계약 검증 42건의 정의 | T-12. A 의 `make contract` 98건이 이미 있음 | G-03 결과에 따라 B 계약 테스트를 A 의 `test_contract.py` 에 편입할지 결정 | G-03 대기 |
| N-09 | `feature_report` 의 17개 특성 | T-11. G-07 의 키 대응이 정해져야 계산 가능 | G-07 뒤 착수 | G-07 대기 |
| N-10 | 난수원 | `SystemRandom` 이라 시드 재현이 안 됨(D-11). 디버깅에 필요해지면 S311 예외 승인이 필요 | 필요할 때 01의 3.3 절차로 신청. 지금은 로그의 `propensity_scores` 로 충분 | 보류 |
