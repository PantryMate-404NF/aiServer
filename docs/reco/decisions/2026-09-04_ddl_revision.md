# DDL 개정 — ✅ 적용 완료 *(2026-09-01)*

| | |
|---|---|
| 근거 | `07` E-3 (소급 불가 10항목) |
| 시한 | **크롤 데이터 적재 전** |
| 방식 | `db/init/*.sql` **직접 수정** — ALTER 스니펫 금지 |
| 적용 | ✅ `make down-v && make bootstrap` 완료 |
| 검증 | ✅ 스모크 13/13 · 계약 44건 · **`make ddl-test` 17건** |
| 손실 | 없음 — 현재 DB는 시드 525행 + 스모크 3건뿐, 전부 재생성 가능 |

> 🔴 **왜 ALTER 스니펫이 아닌가.** `deploy/init/` 은 컨테이너 초기화 DDL 이다.
> 마이그레이션 파일을 따로 두면 `make down-v` 한 번에 조용히 사라진다.

## E-3 10항목 처리 방침

| # | 항목 | 처리 |
|---|---|---|
| ① | propensity 정의 메타 | **DDL 아님** — `request_params` JSONB 키 규약 동결 + 계약 테스트 |
| ② | `served ⊆ candidates` | **DDL 아님** — 저장 정책 규약 + 계약 테스트 |
| ③ | 냉장고 제거 사유 | `pantry_item` tombstone + 부분 유니크 |
| ④ | 실효 weights 복원 | **신규** `scoring_config` + `recommendation_log.warm_alpha` |
| ⑤ | pantry_detail | `recommendation_log.pantry_detail JSONB` |
| ⑥ | session_id | `recommendation_log`·`event_log` 양쪽 |
| ⑦ | team / policies | `recommendation_log.policies JSONB` |
| ⑧ | f_popularity·f_quality 스냅샷 | **이미 해결** — `FEATURE_KEYS` 17종에 포함, validator 강제 |
| ⑨ | event_log FK 제거 | `recipe_id` 에서 `REFERENCES` 삭제 |
| ⑩ | 개인정보 동의 | `app_user.consent_at`·`consent_version` |

---

# 1. `02_schema.sql`

## 1-1. `app_user` — 동의 (⑩)

```diff
 CREATE TABLE app_user (
     id            BIGSERIAL   PRIMARY KEY,
     username      VARCHAR(64) NOT NULL UNIQUE,
     display_name  VARCHAR(64),
     is_simulated  BOOLEAN     NOT NULL DEFAULT FALSE,
     persona_id    INT         REFERENCES sim_persona(id),
+    -- 🔴 소급 불가 (07 E-3 ⑩). 수료와 동시에 연락이 끊겨 재동의를 받을 수 없다.
+    --    NULL 을 허용하는 이유: 계정 생성과 동의 시점이 다르다.
+    --    분석에 넣기 전에 consent_at IS NOT NULL 을 반드시 확인할 것.
+    consent_at      TIMESTAMPTZ,
+    consent_version VARCHAR(16),
     created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
-    CHECK (persona_id IS NULL OR is_simulated)
+    CHECK (persona_id IS NULL OR is_simulated),
+    CHECK (consent_version IS NULL OR consent_at IS NOT NULL)
 );
```

## 1-2. `pantry_item` — tombstone (③)

```diff
 CREATE TABLE pantry_item (
     ...
     added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
-    UNIQUE (user_id, ingredient_id)
+    -- 🔴 소급 불가 (07 E-3 ③). 소비기한 낭비율 · shelf_life 검증 · 소진 시퀀스가
+    --    전부 "언제 넣어 언제 어떻게 뺐나"의 쌍을 요구한다.
+    --    삭제하지 않고 tombstone 을 남긴다 — 한 행에 생애 전체가 담긴다.
+    removed_at     TIMESTAMPTZ,
+    -- 사유를 모르면 NULL 이다. "부호 없는 데이터가 부호 틀린 데이터보다 낫다."
+    removed_reason VARCHAR(10) CHECK (removed_reason IN ('consumed','discarded')),
+    CHECK (removed_reason IS NULL OR removed_at IS NOT NULL)
+    -- ⚠️ UNIQUE 제약을 뺐다. 같은 재료를 다시 넣으면 새 행이 생겨야 하기 때문이다.
+    --    현재 보유분에 대한 유일성은 03_indexes.sql 의 부분 유니크가 강제한다.
 );
```

## 1-3. `recommendation_log` — session / detail / policies (⑤⑥⑦)

```diff
 CREATE TABLE recommendation_log (
     request_id       UUID        PRIMARY KEY,
     user_id          BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
+    -- 🔴 소급 불가 (07 E-3 ⑥). 규약: c-{user_id}-{uuid4hex12} (클라이언트)
+    --    · g-{user_id}-{YYYYMMDDHHMI} (서버 갭 폴백). prefix 로 출처를 가른다.
+    session_id       VARCHAR(64),
     model_version    VARCHAR(32) NOT NULL,
     mlflow_run_id    VARCHAR(64),
     config_hash      VARCHAR(32),
+    -- 🔴 소급 불가 (07 E-3 ④). 실효 w 는 α = min(1, n_events/20) 의 함수라
+    --    유저·요청마다 다르다 (01 5-2-3). config_hash 만으로는 복원되지 않는다.
+    warm_alpha       REAL,
     pantry_snapshot  INTEGER[]   NOT NULL,
+    -- 🔴 소급 불가 (07 E-3 ⑤). pantry_snapshot 은 id 만 담아 f_expiring 원값을
+    --    검증할 수 없다. [{ingredient_id, quantity, unit, expires_at, expires_at_source}]
+    pantry_detail    JSONB,
     allergy_snapshot INTEGER[],
     request_params   JSONB       NOT NULL DEFAULT '{}'::jsonb,
+    -- 🔴 소급 불가 (07 E-3 ⑦). Interleaving 승패 귀속 — 'A' 가 어느 모델이었나.
+    --    [{team, model_version, mlflow_run_id}]. 단일 정책이면 NULL.
+    policies         JSONB,
     stage_trace      JSONB       NOT NULL,
     candidates       JSONB,
     served           BIGINT[]    NOT NULL,
     total_latency_ms INT         NOT NULL,
     created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
+    CHECK (session_id IS NULL OR session_id ~ '^[cg]-')
 );
```

## 1-4. `event_log` — session / FK 제거 (⑥⑨)

```diff
 CREATE TABLE event_log (
     id           BIGSERIAL   PRIMARY KEY,
     user_id      BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
-    recipe_id    BIGINT      REFERENCES recipe(id) ON DELETE SET NULL,
+    -- 🔴 FK 를 뺀다 (07 E-3 ⑨). ON DELETE SET NULL 이면 레시피 1건 삭제로
+    --    학습 행이 에러 없이 라벨만 남고 아이템을 잃는다. event_type='search' 가
+    --    recipe_id IS NULL 을 정상값으로 쓰므로 죽은 행과 구분조차 안 된다.
+    --    append-only 로그에 도메인 테이블의 수명주기를 묶지 않는다.
+    recipe_id    BIGINT,
     event_type   VARCHAR(16) NOT NULL CHECK (...),
     value        REAL,
     request_id   UUID        REFERENCES recommendation_log(request_id) ON DELETE SET NULL,
     position     SMALLINT,
+    -- 🔴 소급 불가 (07 E-3 ⑥). impression 이 이벤트의 95% 라 여기가 비면 세션이 없다.
+    session_id   VARCHAR(64),
     context      JSONB,
     created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
+    CHECK (session_id IS NULL OR session_id ~ '^[cg]-')
 );
```

## 1-5. **신규** `scoring_config` — 가중치 레지스트리 (④)

```sql
-- [25] 점수 설정 레지스트리. config_hash 를 되돌리는 유일한 경로.
--      🔴 소급 불가 (07 E-3 ④). 이 테이블이 없으면 과거 요청의 점수를 재현할 수 없다.
--      recommendation_log.config_hash → 여기 base_weights,
--      + recommendation_log.warm_alpha → 실효 w 가 결정된다 (01 5-2-3).
CREATE TABLE scoring_config (
    config_hash   VARCHAR(32) PRIMARY KEY,
    base_weights  JSONB       NOT NULL,   -- {f_coverage: 0.24, ...} 17키 전수
    penalty_spec  JSONB,                  -- {p_recent, p_cooked, p_avoid} 계수
    n_warm        INT         NOT NULL DEFAULT 20,  -- α = min(1, n_events/n_warm)
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

# 2. `03_indexes.sql`

```diff
-CREATE INDEX idx_pantry_user  ON pantry_item (user_id);
-CREATE INDEX idx_pantry_exp   ON pantry_item (user_id, expires_at)
-                               WHERE expires_at IS NOT NULL;
+-- 🔴 현재 보유분의 유일성. 02_schema.sql 에서 뺀 UNIQUE 를 여기서 대신한다.
+--    부분 인덱스라 이력이 쌓여도 담는 것은 활성 행뿐이다.
+CREATE UNIQUE INDEX idx_pantry_active ON pantry_item (user_id, ingredient_id)
+                                       WHERE removed_at IS NULL;
+CREATE INDEX idx_pantry_user  ON pantry_item (user_id) WHERE removed_at IS NULL;
+CREATE INDEX idx_pantry_exp   ON pantry_item (user_id, expires_at)
+                               WHERE expires_at IS NOT NULL AND removed_at IS NULL;
+-- 이력 분석용 (폐기율 · 보유기간). 활성 행은 담지 않는다.
+CREATE INDEX idx_pantry_hist  ON pantry_item (ingredient_id, removed_reason)
+                               WHERE removed_at IS NOT NULL;

+-- 세션 재구성 (07 E-3 ⑥). ARI 검증과 시퀀스 모델의 입력 단위.
+CREATE INDEX idx_ev_session   ON event_log (session_id, created_at)
+                               WHERE session_id IS NOT NULL;
```

---

# 3. `04_functions.sql` — 🔴 **이걸 빠뜨리면 조용히 틀린다**

부분 유니크 방식의 유일한 실제 위험이다. 버린 재료가 계속 "보유 중"으로 잡힌다.

## 3-1. `user_pantry_ids()`

```diff
             SELECT ingredient_id AS x FROM pantry_item WHERE user_id = p_user_id
+                                                         AND removed_at IS NULL
```

## 3-2. 소비기한 조회 함수 (`f_expiring` 원천, 약 183행)

```diff
     FROM   pantry_item p
     JOIN   ingredient  i ON i.id = p.ingredient_id
     WHERE  p.user_id = p_user_id
+      AND  p.removed_at IS NULL
       AND  NOT i.is_staple
```

---

# 4. DDL 아닌 것 — 규약으로 처리 (①②)

## 4-1. `request_params` 키 동결 (①)

`StageInfo.params` 에 아래 키를 **반드시** 넣는다. 값이 아니라 **정의**가 소급 불가다.

```
policy_id              어느 정책이었나
propensity_semantics   'item' | 'item_position'  ← 무엇의 확률인지
explore_pool_size      탐색 풀 크기 (설계 5-3-3 = 상위 200)
uniform_share          혼합 정책의 균등 비율 (5-3-5 = 0.5)
propensity_mc          MC 반복 수 (= 200)
rng_seed               재현용
max_missing_final      폴백 완화 후 실제 값
```

## 4-2. `served ⊆ candidates` 불변식 (②)

exploration 아이템은 **상위 200 풀**에서 뽑히므로 저장 정책이 top-50 으로 자르면
**propensity ≠ 1.0 인 유일한 행이 사라진다.** 계약 테스트로 강제한다.

---

# 5. 적용 절차

```bash
# 1. DDL 수정 (위 1~3)
# 2. 계약 동기화 (reco/schemas · docs/05)
# 3. 재초기화
make down-v && make bootstrap
# 4. 검증
make smoke            # 13건
make contract         # 계약
# 5. 신규 컬럼 왕복 테스트 (새로 작성)
```

## 검증 결과 — 전부 통과

- [x] 스모크 13건 · 계약 44건
- [x] `user_pantry_ids()` 가 tombstone 을 제외한다
- [x] 부분 유니크가 중복 활성 행을 차단한다
- [x] 뺐다 다시 넣으면 행이 쌓인다 (총 2행 · 활성 1행)
- [x] `session_id` prefix CHECK 가 `x-`·`abc`·`''` 를 막는다
- [x] `event_log.recipe_id` 에 FK 가 없다
- [x] 신규 컬럼 왕복에서 값이 보존된다 (`pantry_detail` 의 `expires_at_source` 포함)
- [x] `config_hash` → `scoring_config` 로 기준 가중치를 되찾는다

`make ddl-test` 로 언제든 재확인한다 (`tests/integration/test_ddl.py`, 17건).

## 🔴 아직 안 끝난 것 — ①②

**DDL 이 아니라 규약이라 W4 로 넘어간다.** 로그 쓰기 경로를 **처음 작성할 때** 함께 해야 한다.

- ① `request_params` 키 동결 — `explore_pool_size`·`uniform_share` 는 mock 에 이미 있으나
  `policy_id`·`propensity_semantics`·`rng_seed`·`max_missing_final` 이 없다
- ② `served ⊆ candidates` 불변식 — 저장 정책이 top-50 으로 자르면
  propensity ≠ 1.0 인 유일한 행이 사라진다
