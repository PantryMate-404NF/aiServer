# db/ — DB 부트스트랩

> 작성자: 박재우 · 작성일: 2026-09-03

`make up` 한 번으로 로컬 스택 전체가 뜬다. 설계 문서 섹션 ①·② 의 구현체다.

## 빠른 시작

```bash
# 최초 1회
uv venv && make install TRACK=A       # 트랙에 맞게 A|B|C

# 기동 → 시드 적재 → 검증
make bootstrap
```

```
  psql        make psql
  MLflow UI   make mlflow-ui     ← 컨테이너 불필요
  관측 도구   make up-obs        ← Grafana 가 필요해질 때
```

## 컨테이너를 단계적으로 올린다

필요한 것만 올린다. 디스크를 아끼는 것보다 **지금 검증할 수 있는 범위를 좁게 유지**하는 것이 목적이다.

| 단계 | 명령 | 올라오는 것 | 크기 | 시점 |
|---|---|---|---|---|
| 1 | `make up` | postgres · redis | ~440MB | **지금** |
| 2 | `make up-obs` | + grafana · mlflow | ~1.4GB | 대시보드 트랙 착수 |
| 3 | `make up-all` | + reco-api · dashboard | — | 산출물 D 이후 |

**1단계만으로 스키마 · 시드 · Retrieval · 성능이 전부 검증된다.**

### PostgreSQL 은 컨테이너가 사실상 필수다

`pgvector` 는 **C 확장이라 PostgreSQL 버전에 맞춰 컴파일**되어야 한다. 네이티브 설치는
20명이 각자 다른 OS 에서 다른 방법으로 해야 하고, 버전 매칭이 자주 깨진다.

`pgvector/pgvector:pg16` 이미지 하나에 전부 들어 있다.

| 확장 | 포함 |
|---|---|
| `vector` | ✅ 컴파일 완료 |
| `intarray` · `pg_trgm` · `ltree` | ✅ contrib 기본 포함 |

진짜 이득은 설치 편의가 아니라 **환경 통일**이다. "제 컴에선 되는데요" 가 원천 차단된다.

### MLflow 는 컨테이너가 필요 없다

순수 Python 패키지이고 **서버가 상태를 갖지 않는다.**

```
[클라이언트]  import mlflow                        ← 학습 스크립트. pip 필수
[서버]        mlflow ui / server                   ← 상태 없음
[상태]        PostgreSQL(mlflowdb) + artifact 경로  ← 전부 여기에
```

각자 로컬에서 `mlflow ui` 를 띄워도 **같은 백엔드 DB 를 보면 같은 실험 데이터가 보인다.**
컨테이너의 가치는 "항상 켜져 있는 공용 UI 주소" 하나뿐이라, 초기에는 필요 없다.

```bash
make mlflow-ui
```

> 🔴 **`backend-store-uri` 는 반드시 `mlflowdb` 다.**
> MLflow 는 백엔드 DB 에 자기 테이블 15개쯤을 자동 생성한다. 우리 접속은
> `search_path = reco, public` 이므로 `recodb` 로 붙이면 **MLflow 테이블이 `reco` 스키마에
> 쏟아진다.** `00_databases.sql` 이 `mlflowdb` 를 따로 만드는 이유가 이것이다.
>
> 원격 공유 DB 에서 `CREATE DATABASE` 권한이 없으면 전용 스키마를 지정한다.
> `postgresql://user:pw@host/db?options=-csearch_path%3Dmlflow`

## 명령

```
make help          명령 목록
make up            핵심 기동 (postgres redis)
make up-obs        + 관측 도구 (grafana mlflow)
make up-all        + 애플리케이션 (산출물 D 이후)
make mlflow-ui     MLflow UI 로컬 실행 (컨테이너 불필요)
make down          정지 (데이터 보존)
make down-v        정지 + 볼륨 삭제

make validate      시드 정합성 검증          (DB 불필요)
make dry-run       적재 계획 확인            (DB 불필요)
make seed          시드 적재 (idempotent)
make seed-reset    시드 비우고 재적재
make verify        적재 결과 확인

make smoke         Retrieval 정확성·지연시간 검증 (1만 건)
make smoke-big     5만 건 규모 지연시간 측정
make post-index    HNSW 인덱스 생성 (대량 적재 후 1회)
make schema-remote 원격 DB 에 스키마 적용
```

## 구성

```
db/
├── docker-compose.yml       6서비스 (2개는 profile 로 기본 제외)
├── .env.example             cp → .env
├── (requirements.txt 폐지)  → 루트 pyproject.toml + uv.lock
├── init/                    컨테이너 최초 기동 시 알파벳 순 자동 실행
│   ├── 00_databases.sql       mlflowdb 분리 생성
│   ├── 01_extensions.sql      확장 4종(public) + CREATE SCHEMA reco
│   ├── 02_schema.sql          테이블 24개. FK 의존 순서
│   ├── 03_indexes.sql         인덱스 전량
│   ├── 04_functions.sql       설계 판단을 가두는 함수 3개 + 뷰 2개
│   ├── 05_roles.sql           역할 3종 + 권한 (로컬 전용)
│   └── post_index.sql         HNSW (수동 실행)
├── apply_schema.sh          원격 DB 에 스키마 적용 (일회용 컨테이너)
├── mlflow/Dockerfile        공식 이미지 + psycopg2
├── grafana/provisioning/    PostgreSQL 데이터소스 자동 등록
├── migrate.py               seeds/ → DB
└── smoke_test.py            합성 데이터로 설계 검증
```

## 스키마 네임스페이스 — `reco`

모든 테이블·함수·뷰는 **`reco` 스키마**에 있다. `public` 이 아니다.

```
recodb
├── reco     ← AI 파트 24테이블 + 함수 3 + 뷰 2
├── app      ← 백엔드팀 (별도 관리)
└── public   ← 확장만 (vector · intarray · pg_trgm · ltree)
```

`recipe` · `app_user` · `event_log` 는 극히 일반적인 이름이라 `public` 에 두면
백엔드팀 테이블과 **반드시 충돌한다.**

> ⚠️ **확장은 반드시 `public` 한 곳에만 설치한다.** 스키마마다 따로 설치하면
> `vector` · `ltree` 타입이 스키마별로 달라져 조인·비교가 실패한다. 공유 DB 에서 특히 치명적이다.

`search_path` 는 세 겹으로 보장한다.

| 층 | 방법 | 대상 |
|---|---|---|
| SQL 파일 | 각 파일 상단 `SET search_path TO reco, public` | DDL |
| 함수 정의 | `CREATE FUNCTION ... SET search_path = reco, public` | 호출 스키마 무관 |
| 역할 | `ALTER ROLE reco_app SET search_path ...` | 앱 연결 |
| 접속 | `migrate.py` / `smoke_test.py` 가 접속 직후 `SET` | 슈퍼유저 접속 대비 |

공유 DB 에서 `ALTER DATABASE ... SET search_path` 는 **쓰지 않는다.** 다른 팀에 영향을 준다.

## 역할 3종

```
reco_app    SELECT INSERT UPDATE DELETE          ← FastAPI
reco_batch  + TRUNCATE, CREATE                   ← 배치
reco_ro     SELECT 만                            ← Grafana
```

**`reco_app` 에 TRUNCATE 를 주지 않는 것이 핵심이다.** 정규화 재실행(설계 4-1)이
TRUNCATE 기반이라, 앱 계정에 권한이 있으면 버그 하나로 크롤링 데이터 전체가 날아간다.

Grafana 도 `reco_ro` 로 붙는다. 대시보드가 쓰기 권한을 들고 있으면 언젠가 사고가 난다.

> `05_roles.sql` 의 비밀번호는 **로컬 개발용**이다. 원격 공유 DB 에서는 DB 관리자가
> 다른 값으로 생성한다. 이 파일을 그대로 원격에 적용하지 말 것.

## 로컬 ↔ 원격 2단 구성

|  | 로컬 (compose) | 공용 원격 |
|---|---|---|
| 용도 | 개발 · 실험 · 스키마 변경 | **SoT** — 크롤링 데이터 · 실유저 로그 · 검수 큐 |
| 배치 실행 | 자유 | `reco_batch` 계정으로만 |
| 스키마 변경 | 자유 (`make down-v`) | DB 관리자 승인 |
| 전환 | — | `.env` 의 `DATABASE_URL` 한 줄 |

**로컬 DB 를 없애면 안 된다.** 20명이 공용 DB 하나를 쓰는 상태에서 누군가 재정규화를
돌리면 `recipe_ingredient` 가 TRUNCATE 되어 **전원이 멈춘다.** 가능성이 아니라 확실히 일어난다.

### 원격 DB 에 스키마 적용

`/docker-entrypoint-initdb.d` 자동 실행은 **컨테이너가 볼륨을 처음 만들 때만** 동작한다.
원격 DB 에는 그 메커니즘이 없으므로 명시적 경로가 유일하다.

```bash
./deploy/apply_schema.sh "postgresql://user:pw@db.example.ac.kr:5432/recodb"
# 또는
DATABASE_URL=... make schema-remote
```

로컬에 `psql` 이 없어도 되도록 일회용 컨테이너로 실행한다.
`05_roles.sql` 은 포함하지 않는다 — 원격 역할과 비밀번호는 DB 관리자가 만든다.

### 원격 전환 시 성능 주의

유닉스 소켓 → 네트워크 TCP 로 바뀌면 **쿼리마다 RTT 가 붙는다.** 교내망이면 0.5~2ms
수준이라 p95 300ms 목표엔 여유가 있지만, **왕복 횟수가 곱해진다.**

현재 구조는 `retrieve_for_user()` 로 감싸 **1왕복**이라 안전하다. 애플리케이션에서
3~4번 나눠 호출하도록 리팩터링하면 그때 문제가 된다 —
**원격 DB 에서는 "함수로 감싸 1왕복" 원칙이 로컬일 때보다 훨씬 중요하다.**

## 함수가 존재하는 이유

애플리케이션 코드가 같은 로직을 각자 구현하면 **반드시 어긋난다.** 세 가지 판단을
`04_functions.sql` 한 곳에 가둔다.

| 함수 | 가두는 판단 | 안 쓰면 |
|---|---|---|
| `user_pantry_ids(user_id)` | **결정 2** — staple 은 항상 보유 | 간장 미등록 유저에게 한식 95%가 걸러진다 |
| `expand_user_allergens(user_id)` | 알러지 **4경로 합집합** | 메밀 알러지를 놓친다 (아래 참조) |
| `retrieve_candidates(...)` | **결정 3** — 배열 GIN 단일 쿼리 | 조인이 늘어 p95 300ms 를 못 지킨다 |

애플리케이션은 보통 래퍼 하나만 호출한다.

```sql
SELECT * FROM retrieve_for_user(:user_id, :max_missing, :max_minutes, 500);
```

### 알러지 4경로는 반드시 합집합이어야 한다

```
① 직접 지정한 재료
② 카테고리 하위 전량 (ltree)     견과류 → 아몬드·호두·잣…
③ allergen_group 컬럼 일치       buckwheat — 카테고리로는 못 잡는다
③' 직접 지정 재료의 그룹 확산     '아몬드 알러지' → nut 그룹 전체 (severity='allergy' 일 때만)
```

②만 쓰면 **메밀 알러지를 놓친다.** 메밀은 잡곡류에 속하지만 같은 잡곡류의 보리·귀리는
메밀 알러젠이 아니므로 카테고리 전개가 성립하지 않는다. 설계 2-2의 "안전 관련은
이중화한다" 원칙이 여기서 구체화된다.

`severity='avoid'`(단순 기피)는 ③' 확산을 하지 않는다. 오이를 싫어한다고 오이 계열
전체를 막으면 과하다.

### 지표 쿼리는 뷰를 읽는다

```sql
-- ✗ 시뮬 유저가 섞여 숫자가 무의미해진다
SELECT count(*) FROM event_log WHERE event_type='click';

-- ✓
SELECT count(*) FROM v_real_events WHERE event_type='click';
```

`v_real_events` / `v_real_recommendations` 가 `is_simulated` 필터를 구조적으로 강제한다.
Grafana 패널은 반드시 이 뷰를 기준으로 작성한다.

## smoke_test 가 증명하는 것

크롤링 데이터가 오기 전에 **설계가 실제로 동작하는지**를 합성 데이터로 확인한다.

| 검사 | 검증 대상 |
|---|---|
| A1 | 빈 냉장고로도 staple 전용 레시피가 나온다 (결정 2) |
| A2·A3 | 부족 재료 계산과 `max_missing` 필터 |
| A4 | 직접 지정 알러지 하드 컷 |
| A5 | 카테고리 ltree 전개 (견과류 → 호두·잣) |
| A6 | **컬럼 전용 그룹** (buckwheat — 카테고리로 못 잡는 경로) |
| A7 | **알러젠이 고명에만 있어도 차단** → `all_ids` 로 검사한다는 설계 증명 |
| A8 | 같은 알러지 그룹 확산 (아몬드 → 호두·잣) |
| A9 | `cook_minutes IS NULL` 이 시간 필터에서 버려지지 않는다 |
| 성능 | p95 < 300ms + **GIN 인덱스를 실제로 타는지** EXPLAIN 확인 |

A7 이 가장 중요하다. `essential_ids` 로 알러지를 검사하면 양념·고명에 든 알러젠을
놓치고, 그건 사고다.

## 🔴 실행 검증 상태

작성 시점에 이 머신에 **Docker 와 PostgreSQL 이 설치되어 있지 않았다.**
따라서 다음은 **아직 실행으로 확인되지 않았다.**

| 항목 | 상태 |
|---|---|
| 시드 파싱 · 참조 해석 (`make dry-run`) | ✅ 통과 |
| 시드 정합성 (`make validate`) | ✅ 통과 |
| Python 문법 (migrate / smoke_test / validate) | ✅ 통과 |
| YAML 문법 (compose · grafana 프로비저닝) | ✅ 통과 |
| SQL 괄호·따옴표 균형 | ✅ 통과 |
| **SQL 실행 (`make up`)** | ⬜ **미검증** |
| **시드 적재 (`make seed`)** | ⬜ **미검증** |
| **Retrieval 동작·성능 (`make smoke`)** | ⬜ **미검증** |

Docker 설치 후 **`make bootstrap` 을 가장 먼저 돌려야 한다.** 첫 실행에서 SQL 오타나
확장 문제가 드러날 가능성이 있다.

## 문제 해결

**`확장 누락` 예외로 기동 실패**
`pgvector/pgvector:pg16` 이미지가 맞는지 확인한다. 순정 `postgres:16` 에는 pgvector 가 없다.

**init 스크립트가 실행되지 않음**
`/docker-entrypoint-initdb.d` 는 **볼륨이 비어 있을 때만** 동작한다.
스키마를 고쳤다면 `make down-v && make up`.

**`function retrieve_for_user does not exist`**
`04_functions.sql` 이 실행되지 않았다. 위와 같은 원인이다.

**Grafana 데이터소스 연결 실패**
`deploy/.env` 의 `POSTGRES_PASSWORD` 와 compose 환경변수가 어긋났을 수 있다.
`make down && make up` 으로 재기동한다.

**`make seed` 가 FK 오류**
`make validate` 를 먼저 돌린다. 시드 파일 참조가 깨진 경우가 대부분이다.

## 스키마를 바꿀 때

1. `deploy/init/02_schema.sql` 수정
2. `docs/reco/design/02_RECOMMENDER_DESIGN.md` 섹션 ② 동기화 — **문서가 SoT 다**
3. `make down-v && make up && make seed && make smoke`
4. 시드 구조가 바뀌었으면 `seeds/validate.py` 와 `migrate.py` 도 함께 수정

마이그레이션 도구(Alembic 등)는 쓰지 않는다. 8주 프로젝트에서 스키마가 바뀌면
**볼륨을 지우고 다시 만드는 편이 빠르고 안전하다.** 실데이터가 쌓이기 시작하는
시점(크롤링 완료 이후)부터는 이 원칙을 재검토한다.
