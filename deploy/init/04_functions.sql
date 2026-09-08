-- ★ 이 파일의 모든 객체는 reco 스키마에 생성된다.
--   확장 타입(vector·ltree)을 쓰기 위해 public 을 search_path 에 함께 둔다.
SET search_path TO reco, public;

-- ═══════════════════════════════════════════════════════════════
-- 설계 판단을 한 곳에 가두는 함수들.
-- 애플리케이션 코드가 이 로직을 각자 구현하면 반드시 어긋난다.
-- ═══════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────
-- 결정 2 를 여기에 가둔다 — staple 은 "모든 유저가 항상 보유"
--
-- 이 함수를 쓰지 않고 pantry_item 만 조회하면, 간장을 등록하지 않은
-- 유저에게 한식 레시피의 95%가 재료 부족으로 걸러진다.
-- ───────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION user_pantry_ids(p_user_id BIGINT)
RETURNS INTEGER[] LANGUAGE sql STABLE
SET search_path = reco, public AS $$
    SELECT COALESCE(
        (SELECT array_agg(DISTINCT x) FROM (
            -- 🔴 removed_at IS NULL 이 빠지면 버린 재료가 계속 '보유 중'으로 잡혀
            --    Retrieval 이 **에러 없이** 틀린다 (02_schema.sql pantry tombstone).
            SELECT ingredient_id AS x FROM pantry_item WHERE user_id = p_user_id
                                                         AND removed_at IS NULL
            UNION
            SELECT id             AS x FROM ingredient WHERE is_staple
        ) s),
        ARRAY[]::INTEGER[]
    );
$$;

-- ───────────────────────────────────────────────────────────────
-- 알러지 전개 — 4경로의 합집합 (①직접 ②카테고리 ③그룹컬럼 ③'그룹확산)
--
-- ① 직접 지정한 재료
-- ② 카테고리 하위 전량 (ltree)       예) 견과류 → 아몬드·호두·잣…
-- ③ allergen_group 컬럼 일치         예) buckwheat — 카테고리로는 못 잡는다
--
-- ②만 쓰면 메밀 알러지를 놓치고, ③만 쓰면 계층 누락을 놓친다.
-- 안전 관련은 이중화한다(설계 2-2). 반드시 합집합이어야 한다.
-- ───────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION expand_user_allergens(p_user_id BIGINT)
RETURNS INTEGER[] LANGUAGE sql STABLE
SET search_path = reco, public AS $$
    SELECT COALESCE(array_agg(DISTINCT x), ARRAY[]::INTEGER[]) FROM (
        -- ① 직접 지정
        SELECT ua.ingredient_id AS x
        FROM   user_allergy ua
        WHERE  ua.user_id = p_user_id AND ua.ingredient_id IS NOT NULL

        UNION
        -- ② 카테고리 하위 전개
        SELECT i.id
        FROM   user_allergy ua
        JOIN   ingredient_category c  ON c.id = ua.category_id
        JOIN   ingredient_category c2 ON c2.path <@ c.path
        JOIN   ingredient i           ON i.category_id = c2.id
        WHERE  ua.user_id = p_user_id AND ua.category_id IS NOT NULL

        UNION
        -- ③ allergen_group 컬럼 일치 (카테고리로 못 잡는 그룹)
        SELECT i.id
        FROM   user_allergy ua
        JOIN   ingredient i ON i.allergen_group = ua.allergen_group
        WHERE  ua.user_id = p_user_id AND ua.allergen_group IS NOT NULL

        UNION
        -- ③' 직접 지정한 재료의 allergen_group 을 통한 확산
        --     '아몬드 알러지' 등록 시 같은 nut 그룹 전체로 넓힌다
        SELECT i2.id
        FROM   user_allergy ua
        JOIN   ingredient i  ON i.id = ua.ingredient_id
        JOIN   ingredient i2 ON i2.allergen_group = i.allergen_group
        WHERE  ua.user_id = p_user_id
          AND  ua.ingredient_id IS NOT NULL
          AND  ua.severity = 'allergy'      -- 'avoid'(단순 기피)는 확산하지 않는다
          AND  i.allergen_group IS NOT NULL
    ) s;
$$;

-- ───────────────────────────────────────────────────────────────
-- Stage ① Retrieval — 설계 2-4 의 단일 쿼리
--
-- 4.4만 건 → 500건. 목표 20~50ms.
-- 성능이 안 나오면 플랜 B 는 Redis Set 역색인이다.
-- ───────────────────────────────────────────────────────────────
-- 🔴 시그니처를 바꾸면 CREATE OR REPLACE 가 **구버전을 남긴다** (오버로드).
--    호출부가 기본 인자로 부르면 어느 쪽이 잡힐지 모호해진다. 먼저 지운다.
DROP FUNCTION IF EXISTS retrieve_for_user(BIGINT, INT, INT, INT);
DROP FUNCTION IF EXISTS retrieve_candidates(INTEGER[], INTEGER[], INT, INT, INT);

CREATE OR REPLACE FUNCTION retrieve_candidates(
    p_pantry_ids   INTEGER[],
    p_allergy_ids  INTEGER[] DEFAULT ARRAY[]::INTEGER[],
    p_max_missing  INT       DEFAULT 2,
    p_max_minutes  INT       DEFAULT NULL,
    p_limit        INT       DEFAULT 500,
    -- 🔴 TRUE 면 test-* 합성 피처도 본다. **스모크·B 의 단위테스트 전용**이다.
    --    기본이 FALSE 라 실서빙은 켤 수 없다 — 명시적으로 넘겨야만 열린다.
    p_include_test BOOLEAN   DEFAULT FALSE
)
RETURNS TABLE (
    recipe_id     BIGINT,
    missing_count INT,
    missing_ids   INTEGER[],
    coverage      REAL,
    -- 🔴 ③ Re-ranking 의 클러스터 쿼터·Thompson 탐색이 이 값을 쓴다 (설계 5-3-5).
    --    계약(reco/schemas/pipeline.py:49)에는 있었는데 여기서 안 넘겨주고 있었다.
    --    NULL 이면 균등 탐색으로 폴백하므로 k-means 배치(S-A) 전에도 안전하다.
    cluster_id    SMALLINT
) LANGUAGE sql STABLE
SET search_path = reco, public AS $$
    SELECT rf.recipe_id,
           icount(rf.essential_ids - p_pantry_ids)                       AS missing_count,
           (rf.essential_ids - p_pantry_ids)                             AS missing_ids,
           -- n_essential=0 은 "필수가 전부 기본양념"(간장계란밥)이라는 뜻이다.
           -- 정규화 실패로 재료가 0개인 경우는 위 ⓪ 가 이미 걸렀다.
           CASE WHEN rf.n_essential = 0 THEN 1.0
                ELSE 1.0 - icount(rf.essential_ids - p_pantry_ids)::REAL
                           / rf.n_essential
           END                                                           AS coverage,
           rf.cluster_id                                                 AS cluster_id
    FROM   recipe_feature rf
    -- 🔴 ⓪' 격리 게이트 (09-03). `feature_version` 이 `test-` 로 시작하면 뺀다.
    --    B·C 가 스코어러·화면을 만들며 합성 recipe_feature 를 넣는데, 필터가
    --    없으면 **합성 5만 건이 실추천에 그대로 섞인다.** 지우기로 약속하는 것보다
    --    구조로 막는 편이 낫다 — 약속은 언젠가 잊는다.
    --      v1, v2, …    A 의 실배치 산출물. 추천에 나간다
    --      test-*       B·C·스모크. **조회에서 자동 제외**
    WHERE  (p_include_test OR rf.feature_version NOT LIKE 'test-%')
      AND  rf.n_total > 0                                                -- ⓪ 🔴 빌더 게이트
                                                                         --   재료가 하나도 안 붙은
                                                                         --   레시피를 후보에서 뺀다.
                                                                         --   아래 coverage 의
                                                                         --   n_essential=0 → 1.0 이
                                                                         --   정규화 실패 레시피에도
                                                                         --   만점을 주기 때문이다.
      AND  (rf.essential_ids && p_pantry_ids                             -- ① GIN 1차 축소
            OR cardinality(rf.essential_ids) = 0)                        --   ★필수재료가 전부
                                                                         --    staple 인 레시피.
                                                                         --    빈 배열은 && 가 항상
                                                                         --    FALSE 라 따로 잡아야 한다
      AND  (cardinality(p_allergy_ids) = 0
            OR NOT (rf.all_ids && p_allergy_ids))                        -- ② 알러지 하드 컷
      AND  icount(rf.essential_ids - p_pantry_ids) <= p_max_missing      -- ③ 부족 재료 k개 이하
      AND  (p_max_minutes IS NULL
            OR rf.cook_minutes IS NULL
            OR rf.cook_minutes <= p_max_minutes)                         -- ④ 조리시간
    ORDER  BY missing_count ASC, rf.popularity_score DESC
    LIMIT  p_limit;
$$;

-- 유저 ID 하나로 끝내는 편의 래퍼. 애플리케이션은 보통 이것을 호출한다.
CREATE OR REPLACE FUNCTION retrieve_for_user(
    p_user_id      BIGINT,
    p_max_missing  INT DEFAULT 2,
    p_max_minutes  INT DEFAULT NULL,
    p_limit        INT DEFAULT 500,
    p_include_test BOOLEAN DEFAULT FALSE      -- 테스트 전용. 서빙은 건드리지 않는다
)
RETURNS TABLE (
    recipe_id     BIGINT,
    missing_count INT,
    missing_ids   INTEGER[],
    coverage      REAL,
    cluster_id    SMALLINT          -- ③ 재랭킹용. retrieve_candidates 와 동일
) LANGUAGE sql STABLE
SET search_path = reco, public AS $$
    SELECT * FROM retrieve_candidates(
        user_pantry_ids(p_user_id),
        expand_user_allergens(p_user_id),
        p_max_missing, p_max_minutes, p_limit, p_include_test
    );
$$;

-- ───────────────────────────────────────────────────────────────
-- 지표 쿼리용 뷰 — is_simulated 필터를 구조적으로 강제한다.
-- 설계 2-5: "시뮬 4,950명과 실유저 50명을 섞으면 숫자가 무의미해진다"
-- ───────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_real_events AS
    SELECT e.* FROM event_log e
    JOIN   app_user u ON u.id = e.user_id
    -- 🔴 두 겹으로 거른다. is_simulated 는 '가상 유저' 만 잡고,
    --    **실유저 ID 로 디버거를 눌러본 것**은 session_id 'd-' 로 잡는다.
    WHERE  NOT u.is_simulated
      AND  (e.session_id IS NULL OR e.session_id NOT LIKE 'd-%');

CREATE OR REPLACE VIEW v_real_recommendations AS
    SELECT r.* FROM recommendation_log r
    JOIN   app_user u ON u.id = r.user_id
    WHERE  NOT u.is_simulated
      -- 디버거·시딩 트래픽 제외 (v_real_events 와 같은 규칙)
      AND  (r.session_id IS NULL OR r.session_id NOT LIKE 'd-%')
      -- 🔴 0단계(스코어러 이전) 의 **난수 점수 행**을 제외한다.
      --    mock 은 model_version 을 'mock-*' 로 남긴다. 이 행들은 점수가
      --    난수라 지표에 섞이면 아무 의미가 없는데 겉보기엔 정상이다.
      AND  r.model_version NOT LIKE 'mock-%';

COMMENT ON VIEW v_real_events IS
    '실유저 이벤트만. 지표 쿼리는 event_log 가 아니라 이 뷰를 읽는다.';
COMMENT ON VIEW v_real_recommendations IS
    '실유저·실엔진 추천 로그만. 가상유저·디버거(d-)·mock 난수 점수를 모두 제외한다. '
    'Grafana 패널과 평가는 recommendation_log 가 아니라 이 뷰를 읽는다.';

-- ═══════════════════════════════════════════════════════════════
-- 소비기한 추정 — f_expiring 을 유저 입력 없이 동작시킨다 (설계 5-2-1)
--
-- 🔴 이전 정의는 유저가 expires_at 을 직접 입력해야만 동작했다.
--    실제로는 대부분 비우므로 가중치 0.15 짜리 최대 차별화 피처가 죽는다.
--    게다가 분모가 "입력한 항목 수"라 1건만 입력한 유저는 0 아니면 1 로 튄다.
--
--    등록일 + ingredient.shelf_life_days 로 추정하고, 유저 입력이 있으면 그것이 이긴다.
--    출처를 pantry_item.expires_at_source 로 구분해 나중에 유용성을 측정한다.
-- ═══════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION effective_expiry(p_user_id BIGINT)
RETURNS TABLE (ingredient_id INT, expiry DATE, src TEXT, days_left INT)
LANGUAGE sql STABLE AS $$
    -- 🔴 추정 기준일은 **구매일**이다 (09-02). added_at 은 앱에 넣은 시각이라
    --    마트에서 사고 사흘 뒤에 등록하면 **사흘을 공짜로 벌어준다.**
    --    실제로는 이미 지난 시간이고, 그만큼 임박 판정이 늦어져 재료가 상한다.
    --    purchased_at 이 없으면 added_at 으로 폴백한다.
    SELECT p.ingredient_id,
           COALESCE(p.expires_at,
                    (COALESCE(p.purchased_at, p.added_at::date)
                     + make_interval(days => i.shelf_life_days))::date) AS expiry,
           CASE WHEN p.expires_at IS NOT NULL THEN 'user'
                WHEN i.shelf_life_days IS NOT NULL THEN 'estimated'
                ELSE 'unknown' END AS src,
           (COALESCE(p.expires_at,
                     (COALESCE(p.purchased_at, p.added_at::date)
                      + make_interval(days => i.shelf_life_days))::date)
            - current_date)::int AS days_left
    FROM   pantry_item p
    JOIN   ingredient  i ON i.id = p.ingredient_id
    WHERE  p.user_id = p_user_id
      AND  p.removed_at IS NULL     -- 🔴 tombstone 제외. 빠지면 f_expiring 이 틀린다
      AND  NOT i.is_staple          -- staple 은 유저가 등록한 게 아니다 (결정 2)
$$;

-- f_expiring — 임박 재료 중 이 레시피가 실제로 쓰는 비율
--
-- 분모가 "expires_at 이 있는 항목"이 아니라 **"임박한 항목 전체"**다.
-- 그래야 "임박 재료가 3개인데 이 레시피는 그중 2개를 쓴다"가 0.67 로 나온다.
-- 임박 재료가 하나도 없으면 0 이 아니라 NULL 을 돌려준다 — 계산 불가와 0 은 다르다.
CREATE OR REPLACE FUNCTION f_expiring(p_user_id BIGINT, p_recipe_all_ids INTEGER[],
                                      p_horizon INT DEFAULT 3)
RETURNS REAL LANGUAGE sql STABLE AS $$
    WITH e AS (
        SELECT * FROM effective_expiry(p_user_id) WHERE days_left <= p_horizon
    )
    SELECT CASE WHEN count(*) = 0 THEN NULL
                -- 🔑 분모 캡 3 (설계 5-2-6 v2.1): 임박 재료가 5개인 유저는 어떤
                --    레시피도 1.0 에 못 닿아 천장이 눌린다. "임박 3개 이상을 쓰면
                --    만점"으로 캡을 두면 모수 0개로 해결된다. NULL 의미는 그대로.
                ELSE LEAST(count(*) FILTER (WHERE ingredient_id = ANY(p_recipe_all_ids)), 3)::real
                     / LEAST(count(*), 3) END
    FROM e
$$;
