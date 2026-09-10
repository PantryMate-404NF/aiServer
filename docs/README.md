# aiServer · 문서 index

**정하는 것**: 규칙 문서와 설계·기록 문서의 목록, 읽는 순서. 규칙 본문은 없습니다.

**적용 대상**: 이 저장소에 기여하는 모든 인원 및 AI 코딩 에이전트

**버전**: 1.5.0 · **최종 수정**: 2026-09-10 · **작성자**: 김민경

---

## 1. 문서 목록

**이 표가 문서의 유일한 목록입니다.** 문서를 추가하거나 제거하면 여기만 고칩니다. 다른 파일에 목록을 복사하지 않습니다.

| 번호 | 문서 | 정하는 것 |
|---|---|---|
| 01 | [DEVELOPMENT_RULES](convention/01_DEVELOPMENT_RULES.md) | 일하는 방식. 브랜치, 커밋, 검증, 리뷰, PR, 테스트, 배포 |
| 02 | [DIRECTORY_STRUCTURE](convention/02_DIRECTORY_STRUCTURE.md) | 파일을 어디에 두는가. 배치, 네이밍, 분리 기준, import |
| 03 | [IMPLEMENTATION_RULES](convention/03_IMPLEMENTATION_RULES.md) | 코드를 어떻게 쓰는가. 설정, 예외·로깅, 외부 자원, DB, 테스트 |
| 04 | [DOCUMENTATION_RULES](convention/04_DOCUMENTATION_RULES.md) | 문서를 어디에 두고 어떻게 쓰는가. 파일명, 배치, 헤더, 본문 형식, 형식 점검 |

충돌 시 번호가 작은 문서가 이깁니다. 규칙을 바꾸려면 01의 9절 개정 절차를 따릅니다.
설계 결정 기록은 [decisions/](decisions/) 에 날짜별로 있습니다.

파트별 설계 명세와 진행 기록은 아래에 있습니다. 규칙이 아니라 진행 상태를 담습니다. 에이전트용 정본과 사람용 서술본을 함께 두는 이유는 [decisions/2026-09-10_recommend_record_dual_format.md](decisions/2026-09-10_recommend_record_dual_format.md) 에, 추천 엔진이 데이터 파트의 계약을 따르는 이유는 [decisions/2026-09-10_recommend_engine_follows_data_track_contract.md](decisions/2026-09-10_recommend_engine_follows_data_track_contract.md) 에, 데이터 파트 병합에서 검사 예외를 어디까지 허용했는지는 [decisions/2026-09-10_merge_data_track_gate_exceptions.md](decisions/2026-09-10_merge_data_track_gate_exceptions.md) 에 있습니다. `recommend/` 폴더는 04의 1.1 트리에 아직 없으며 개정 신청 대상입니다.

| 폴더 | 문서 | 담는 것 |
|---|---|---|
| `recommend/` | [recommend_engine_design.md](recommend/recommend_engine_design.md) | 파트 B 추천 코어 엔진 설계 명세. 사람용 정본 |
| `recommend/` | [recommend_engine_design_digest.md](recommend/recommend_engine_design_digest.md) | 위 명세의 에이전트용 압축본. 어긋나면 명세가 이깁니다 |
| `recommend/` | [recommend_engine_work_log.md](recommend/recommend_engine_work_log.md) | 파트 B 작업 기록. 결정·가정·남은 일. 에이전트용 정본 |
| `recommend/` | [recommend_engine_verification.md](recommend/recommend_engine_verification.md) | 파트 B 검증 기록. 명세 정합, Mock 동작, 수정 반영. 에이전트용 정본 |
| `recommend/` | [recommend_engine_meeting_agenda.md](recommend/recommend_engine_meeting_agenda.md) | 파트 B 통합 회의 안건. 타 파트 논의와 내부 처리 구분. 에이전트용 정본 |
| `recommend/human/` | [recommend_engine_work_log.md](recommend/human/recommend_engine_work_log.md) · [recommend_engine_verification.md](recommend/human/recommend_engine_verification.md) · [recommend_engine_meeting_agenda.md](recommend/human/recommend_engine_meeting_agenda.md) | 위 세 기록의 사람용 서술본 |

### 1.1 추천 파트의 계약은 코드와 DDL 이 정합니다

위 표의 기록 문서는 **진행 상태와 결정의 근거**를 담습니다. 실제 계약(컬럼, 조회 조건, 요청·응답 모양)은
코드와 DDL 이 정하며 데이터 파트의 설계 노트와 초안은 저장소에 두지 않고 팀 채널에 있습니다.

| 알고 싶은 것 | 볼 곳 |
|---|---|
| `recipe_feature` 가 어떤 컬럼을 갖는가 | [deploy/init/02_schema.sql](../deploy/init/02_schema.sql) |
| 후보 조회가 어떻게 도는가 | [deploy/init/04_functions.sql](../deploy/init/04_functions.sql) |
| `flavor_vec` 을 어떻게 만드는가 | [src/features/recommend/ingest/flavor.py](../src/features/recommend/ingest/flavor.py) |
| API 요청·응답 모양 | [src/features/recommend/schema.py](../src/features/recommend/schema.py) · [stage.py](../src/features/recommend/stage.py) |
| 재료 사전을 어떻게 채우는가 | [seeds/README.md](../seeds/README.md) |
| DB 를 어떻게 띄우는가 | [deploy/README.md](../deploy/README.md) |

문서와 코드가 어긋나면 **코드가 맞습니다.**

---

## 2. 신규 합류자가 읽는 순서

| 순서 | 할 일 | 걸리는 시간 |
|---|---|---|
| 1 | 저장소 루트의 `README.md` 로 프로젝트와 실행 절차 파악 | 5분 |
| 2 | 01 전체 통독. 이 저장소에서 무엇이 차단되는지 먼저 압니다 | 30분 |
| 3 | 02의 1절과 2절만. 나머지는 필요할 때 찾아봅니다 | 15분 |
| 4 | 03은 건드릴 영역의 절만 | 10분 |
| 5 | 04의 2절을 보고 문서만 고치는 PR 1건으로 리뷰·승인·병합을 1회 경험 | 30분 |

3절부터는 필요할 때 찾아 읽습니다. 처음부터 전부 외우지 않습니다.

---

## 3. 상황별로 어디를 보는가

| 지금 하려는 일 | 볼 곳 |
|---|---|
| 브랜치 이름을 정할 때 | 01의 2.1 |
| 커밋 메시지를 쓸 때 | 01의 2.2 |
| 완료를 선언하기 전 | 01의 3.2 |
| PR을 올릴 때 | 01의 5.2와 5.3 |
| 리뷰할 때 | 01의 5.4 |
| 배포할 때 | 01의 6.3 |
| 규칙 자체를 바꿀 때 | 01의 9절 |
| 새 파일이나 폴더를 만들 때 | 02의 6절 |
| 이름이 정해지지 않을 때 | 02의 4절 |
| 파일이 길어졌을 때 | 02의 5절 |
| import가 꼬였을 때 | 02의 7절 |
| 새 문서를 만들 때 | 04의 1.2와 2.1 |
| 문서 PR을 올리기 전 | 04의 2.3 |
| 설정값이나 비밀값을 다룰 때 | 03의 2절 |
| 예외를 만들거나 로그를 남길 때 | 03의 3절 |
| 외부 API나 무거운 자원을 붙일 때 | 03의 4절 |
| 테이블이나 쿼리를 건드릴 때 | 03의 5절 |
