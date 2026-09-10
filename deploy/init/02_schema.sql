-- ★ 이 파일의 모든 객체는 reco 스키마에 생성된다.
--   확장 타입(vector·ltree)을 쓰기 위해 public 을 search_path 에 함께 둔다.
SET search_path TO reco, public;

-- ═══════════════════════════════════════════════════════════════
-- 냉장고 기반 개인화 레시피 추천 시스템 — 스키마 v0.5
-- 테이블 24개. 생성 순서는 FK 의존 순서다. 바꾸면 깨진다.
-- 원본: docs/reco/design/02_RECOMMENDER_DESIGN.md 섹션 ②
-- ═══════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────
-- GROUP A. 재료 온톨로지
-- ───────────────────────────────────────────────────────────────

-- [1] 재료 계층. LTREE path 가 알러지 전개의 핵심.
CREATE TABLE ingredient_category (
    id          SERIAL       PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    parent_id   INT          REFERENCES ingredient_category(id),
    depth       SMALLINT     NOT NULL,
    path        LTREE        NOT NULL UNIQUE
);

-- [2] 정규 재료 사전 — 모든 조인의 중심
CREATE TABLE ingredient (
    id              SERIAL       PRIMARY KEY,
    name            VARCHAR(64)  NOT NULL UNIQUE,
    category_id     INT          REFERENCES ingredient_category(id),
    is_staple       BOOLEAN      NOT NULL DEFAULT FALSE,
    is_seasoning    BOOLEAN      NOT NULL DEFAULT FALSE,
    allergen_group  VARCHAR(32),
    embedding       vector(768),
    -- NFD 자모 분해본. 한글 trgm 은 음절 단위로는 해상도가 너무 낮다.
    --   애호박↔얘호박  음절 0.143 → 자모 0.455  (실측)
    -- 자동 매칭이 아니라 "검수 큐 후보 제안"과 "유저 입력 자동완성" 전용이다.
    name_jamo       VARCHAR(192),
    freq_count      INT          NOT NULL DEFAULT 0,
    -- 기본 소비기한(일). f_expiring 을 유저 입력 없이도 동작하게 하는 값이다.
    --   유저가 expires_at 을 비우면 added_at + shelf_life_days 로 추정한다.
    --   ⚠️ 통념 기반 시드값이지 실측이 아니다 (seeds/ingredient_shelf_life.yaml)
    shelf_life_days INT,
    storage_default VARCHAR(8)   CHECK (storage_default IN ('fridge','pantry','freezer')),
    nutrition_100g  JSONB,
    note            VARCHAR(255),
    verified_by     VARCHAR(64),
    verified_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- [3] 표기 변형 사전
CREATE TABLE ingredient_alias (
    id              SERIAL       PRIMARY KEY,
    alias           VARCHAR(96)  NOT NULL,
    ingredient_id   INT          NOT NULL REFERENCES ingredient(id) ON DELETE CASCADE,
    source          VARCHAR(16)  NOT NULL,
    confidence      REAL         NOT NULL DEFAULT 1.0
                    CHECK (confidence > 0 AND confidence <= 1),
    alias_jamo      VARCHAR(288),
    note            VARCHAR(255),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (alias, ingredient_id)
);

-- [4] 대체 재료. 방향성이 있으므로 대칭 테이블로 만들지 않는다.
CREATE TABLE ingredient_substitute (
    from_id     INT   NOT NULL REFERENCES ingredient(id) ON DELETE CASCADE,
    to_id       INT   NOT NULL REFERENCES ingredient(id) ON DELETE CASCADE,
    score       REAL  NOT NULL CHECK (score >= 0 AND score <= 1),
    reason      VARCHAR(32),
    PRIMARY KEY (from_id, to_id),
    CHECK (from_id <> to_id)
);

-- [24] 재료별 개수단위 → g 환산. 계량단위(큰술/컵)는 코드 상수로 분리.
CREATE TABLE ingredient_unit_weight (
    ingredient_id   INT         NOT NULL REFERENCES ingredient(id) ON DELETE CASCADE,
    unit            VARCHAR(16) NOT NULL,
    grams_per_unit  REAL        NOT NULL CHECK (grams_per_unit > 0),
    source          VARCHAR(16) NOT NULL,
    confidence      REAL        NOT NULL DEFAULT 1.0,
    note            VARCHAR(255),
    PRIMARY KEY (ingredient_id, unit)
);

-- ───────────────────────────────────────────────────────────────
-- GROUP B. 레시피
-- ───────────────────────────────────────────────────────────────

-- [23] 요리 계열 2계층. recipe.cuisine 이 참조하므로 먼저 생성.
CREATE TABLE cuisine_taxonomy (
    code        VARCHAR(24) PRIMARY KEY,
    family      VARCHAR(16) NOT NULL,
    label_ko    VARCHAR(32) NOT NULL,
    label_en    VARCHAR(32) NOT NULL,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    sort_order  SMALLINT    NOT NULL DEFAULT 0
);

-- [5] 레시피 본문
CREATE TABLE recipe (
    id              BIGSERIAL    PRIMARY KEY,
    source          VARCHAR(32)  NOT NULL,
    source_id       VARCHAR(64)  NOT NULL,
    url             TEXT,
    title           VARCHAR(255) NOT NULL,
    description     TEXT,
    image_url       TEXT,
    servings        SMALLINT,
    cook_minutes    SMALLINT,          -- 파싱 실패 시 NULL. 0으로 채우면 랭킹이 망가진다
    difficulty      SMALLINT     CHECK (difficulty BETWEEN 1 AND 5),
    -- 분류 축 (v0.3: 단일 category 문자열에서 다축으로 분리)
    dish_type       VARCHAR(24),                        -- 원본 종류별
    situation       VARCHAR(24)[],                      -- 원본 상황별
    main_ing_cat    VARCHAR(24),                        -- 원본 재료별
    method          VARCHAR(24),                        -- 원본 방법별
    cuisine_family  VARCHAR(16),                        -- 파생 · 거친 축
    cuisine         VARCHAR(24)  REFERENCES cuisine_taxonomy(code),  -- 파생 · 세분 축
    cuisine_conf    REAL,
    view_count      INT          NOT NULL DEFAULT 0,
    rating_avg      REAL,
    rating_count    INT          NOT NULL DEFAULT 0,
    review_count    INT          NOT NULL DEFAULT 0,   -- 인기도 프록시 (02 C-5)
    raw_json        JSONB        NOT NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'raw'
                    CHECK (status IN ('raw','normalized','published','rejected')),
    reject_reason   VARCHAR(64),
    crawled_at      TIMESTAMPTZ  NOT NULL,
    UNIQUE (source, source_id)
);

-- [6] 크롤링 원문. ★불변. 절대 UPDATE 하지 않는다.
--     정규화 규칙이 바뀌면 recipe_ingredient 를 TRUNCATE 하고 여기서 전체 재생성.
CREATE TABLE recipe_ingredient_raw (
    id          BIGSERIAL    PRIMARY KEY,
    recipe_id   BIGINT       NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    group_name  VARCHAR(32),                -- '[재료]' '[양념]' '[고명]' — role 판별 1차 근거
    position    SMALLINT     NOT NULL,
    raw_text    VARCHAR(255) NOT NULL,
    -- 🔴 재적재 멱등. 없으면 `ON CONFLICT DO NOTHING` 이 **아무것도 하지 않고**
    --    로더를 두 번 돌릴 때마다 전량이 배로 늘어난다 — 에러도 안 난다.
    --    실측(09-02): 로더 2회 실행으로 451,862 → 903,724 행.
    UNIQUE (recipe_id, position)
);

-- [7] 정규화 결과. 재생성 가능.
CREATE TABLE recipe_ingredient (
    recipe_id       BIGINT       NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    ingredient_id   INT          NOT NULL REFERENCES ingredient(id),
    raw_id          BIGINT       REFERENCES recipe_ingredient_raw(id) ON DELETE SET NULL,
    quantity        REAL,
    unit            VARCHAR(16),
    quantity_g      REAL,               -- 환산 실패 시 NULL. 0으로 채우지 않는다
    role            VARCHAR(16)  NOT NULL
                    CHECK (role IN ('essential','optional','seasoning','garnish')),
    match_method    VARCHAR(16)  NOT NULL
                    CHECK (match_method IN ('exact','alias','rule','fuzzy','embed','manual')),
    match_score     REAL         NOT NULL,
    PRIMARY KEY (recipe_id, ingredient_id)
);

-- [8] 조리 단계
CREATE TABLE recipe_step (
    recipe_id   BIGINT   NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    step_no     SMALLINT NOT NULL,
    text        TEXT     NOT NULL,
    image_url   TEXT,
    PRIMARY KEY (recipe_id, step_no)
);

-- [9] ★온라인 서빙 전용 비정규화 테이블. 모든 추천이 이것을 읽는다.
CREATE TABLE recipe_feature (
    recipe_id            BIGINT      PRIMARY KEY REFERENCES recipe(id) ON DELETE CASCADE,
    -- 집합 연산용 배열
    essential_ids        INTEGER[]   NOT NULL,   -- role='essential' AND NOT is_staple
    all_ids              INTEGER[]   NOT NULL,   -- 알러지 검사용. 양념·고명 포함
    category_ids         INTEGER[]   NOT NULL,
    -- 스칼라 피처
    n_essential          SMALLINT    NOT NULL,
    n_total              SMALLINT    NOT NULL,
    -- 🔴 정규화가 못 붙인 재료 수 (P3 미매칭). 적재 전에 넣어야 소급이 된다.
    --    n_total > 0 가드는 **전멸**만 잡는다 — 12개 중 11개를 놓쳐도 통과한다.
    --    매칭률 = n_total / (n_total + n_unmatched) 로 부분 실패를 걸러낼 수 있다.
    --    로더가 채우기 전까지는 0 이라 게이트가 발동하지 않는다 (안전한 기본값).
    n_unmatched          SMALLINT    NOT NULL DEFAULT 0,
    flavor_vec           REAL[]      NOT NULL,   -- 길이 6: 매움 짠맛 단맛 신맛 감칠맛 기름짐
    nutrition            JSONB,
    popularity_score     REAL        NOT NULL DEFAULT 0,
    quality_score        REAL        NOT NULL DEFAULT 0,
    season_vec           REAL[],                 -- 길이 12
    cook_minutes         SMALLINT,               -- recipe 에서 복사 (조인 회피)
    difficulty           SMALLINT,
    cuisine_family       VARCHAR(16),            -- 분류축. 🔴 한식 편중으로 캡이 잘 안 걸린다
    dish_type            VARCHAR(24),
    -- 🔑 우연성·다양성 축 (설계 5-3-5). k-means(K≈50) on content_emb.
    --    ① 5-3-2 캡 규칙 대체  ② ③ 폴백 다양성  ③ Thompson 탐색의 arm
    --    분류축(한식 70~80%)과 달리 데이터 분포에 맞춰 균형이 잡힌다 — 실측:
    --      분류축 캡  ILD 0.953 · 근중복 5.6 · 점수유지 89.3%
    --      클러스터  ILD 0.964 · 근중복 3.1 · 점수유지 99.0%
    cluster_id           SMALLINT,
    cluster_version      VARCHAR(16),            -- 재클러스터링 시 배정이 바뀐다
    content_emb          vector(768),
    feature_version      VARCHAR(16) NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 🔴 셋이 어긋나면 **알러지가 뚫린다** (09-03 실측).
    --    essential_ids 가 all_ids 에 없으면, 알러지 검사가 all_ids 만 보므로
    --    알러젠이 든 레시피가 통과한다. 게다가 n_essential=0 이면 coverage 만점이라
    --    **상위로 올라온다.** 에러는 하나도 안 난다.
    CHECK (n_total = cardinality(all_ids)),
    CHECK (n_essential = cardinality(essential_ids)),
    CHECK (essential_ids <@ all_ids),
    -- 합성 피처 격리 (D-17). 접두어를 읽는 쪽에서만 걸렀더니
    -- 'scorer_test' 같은 접미어가 실서빙에 샜다 — 쓰는 쪽에서 막는다.
    CHECK (feature_version ~ '^(v[0-9]|test-)'),
    CHECK (array_length(flavor_vec, 1) = 6),
    CHECK (season_vec IS NULL OR array_length(season_vec, 1) = 12)
);

-- ───────────────────────────────────────────────────────────────
-- GROUP C. 유저
-- ───────────────────────────────────────────────────────────────

-- [11] 규칙 기반 시뮬레이터 페르소나. app_user 가 참조하므로 먼저 생성.
CREATE TABLE sim_persona (
    id             SERIAL      PRIMARY KEY,
    name           VARCHAR(64) NOT NULL UNIQUE,
    description    TEXT,
    params         JSONB       NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [10] 계정. is_simulated 는 분석의 생명줄 — 지표 쿼리에서 반드시 필터링.
CREATE TABLE app_user (
    id            BIGSERIAL   PRIMARY KEY,
    username      VARCHAR(64) NOT NULL UNIQUE,
    display_name  VARCHAR(64),
    is_simulated  BOOLEAN     NOT NULL DEFAULT FALSE,
    persona_id    INT         REFERENCES sim_persona(id),
    -- 🔴 소급 불가 (07 E-3 ⑩). 수료와 동시에 연락이 끊겨 재동의를 받을 수 없다.
    --    NULL 을 허용하는 이유: 계정 생성과 동의 시점이 다르다.
    --    ⚠️ 분석·리포트에 넣기 전 consent_at IS NOT NULL 을 반드시 확인할 것.
    consent_at      TIMESTAMPTZ,
    -- 'v1-min' = 필수 동의만 · 'v1-res' = 연구 보관까지 (S0 ③ · 2단 동의)
    consent_version VARCHAR(16),
    -- 🔴 선택 동의. 8주 이후에도 기록을 남길 수 있는지를 가른다.
    --    필수 동의를 좁게 유지해야 100명이 모이고(LightGBM 성립 하한),
    --    이 체크 하나가 프로젝트 종료 후를 연다.
    consent_research BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (persona_id IS NULL OR is_simulated),  -- 실유저에 페르소나가 붙는 것을 차단
    CHECK (consent_version IS NULL OR consent_at IS NOT NULL),
    -- 동의 안 했는데 연구 보관에 체크된 상태를 막는다
    CHECK (NOT consent_research OR consent_at IS NOT NULL)
);

-- [12] 온보딩 설문 — 콜드스타트의 전부
CREATE TABLE user_preference (
    user_id            BIGINT      PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    spicy_level        SMALLINT    CHECK (spicy_level BETWEEN 0 AND 4),
    sweet_level        SMALLINT    CHECK (sweet_level BETWEEN 0 AND 4),
    salty_level        SMALLINT    CHECK (salty_level BETWEEN 0 AND 4),
    max_cook_minutes   SMALLINT,
    skill_level        SMALLINT    CHECK (skill_level BETWEEN 1 AND 3),
    diet_type          VARCHAR(16),
    goal               VARCHAR(16),
    household_size     SMALLINT,
    pref_cuisines      VARCHAR(16)[],   -- cuisine_family 코드
    pref_dish_types    VARCHAR(24)[],
    onboarding_version VARCHAR(16) NOT NULL DEFAULT 'v1',
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [13] 재료별 선호도. PK 에 source 를 넣어 명시 선호와 행동 학습을 분리 보관.
CREATE TABLE user_ingredient_pref (
    user_id        BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    ingredient_id  INT         NOT NULL REFERENCES ingredient(id) ON DELETE CASCADE,
    source         VARCHAR(16) NOT NULL
                   CHECK (source IN ('onboarding','behavior','explicit')),
    score          REAL        NOT NULL CHECK (score >= -1.0 AND score <= 1.0),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, ingredient_id, source)
);

-- [14] 알러지 = 절대 제약. 선호도와 다른 성질이므로 다른 테이블에 둔다.
CREATE TABLE user_allergy (
    id             BIGSERIAL   PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    ingredient_id  INT         REFERENCES ingredient(id) ON DELETE CASCADE,
    category_id    INT         REFERENCES ingredient_category(id) ON DELETE CASCADE,
    allergen_group VARCHAR(32),                 -- 컬럼 전용 그룹(buckwheat 등) 대응
    severity       VARCHAR(16) NOT NULL DEFAULT 'avoid'
                   CHECK (severity IN ('avoid','allergy')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ingredient_id IS NOT NULL OR category_id IS NOT NULL OR allergen_group IS NOT NULL),
    -- 🔴 오타 하나가 알러지를 통째로 무력화한다 (09-03 실측).
    --    '견과류'·'NUT'·'nuts' 를 넣으면 **삽입이 성공하고 차단 재료가 0종**이 된다.
    --    에러도 없고 온보딩은 성공으로 보인다 — 견과류 알러지 유저에게
    --    아몬드·호두 레시피가 그대로 추천된다. 4갈래 차단 중 둘이 동시에 죽는다.
    CHECK (allergen_group IS NULL OR allergen_group IN (
        'nut','sesame','soy','gluten','egg','dairy','fish','shellfish','peach','buckwheat'))
);

-- [15] 배치 계산 유저 벡터. taste_vec 은 recipe_feature.flavor_vec 과 동일 축.
-- [20b] user_cluster_stat : Thompson 탐색의 사후분포 (설계 5-3-5)
--       유저 100 × 클러스터 50 = 5,000행. 부담이 없다.
--       🔴 이 테이블이 없으면 탐색은 균등 무작위로 폴백한다 (동작은 한다).
CREATE TABLE user_cluster_stat (
    user_id     BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    cluster_id  SMALLINT    NOT NULL,
    n_impress   INT         NOT NULL DEFAULT 0,   -- Beta 의 α+β
    n_positive  INT         NOT NULL DEFAULT 0,   -- Beta 의 α (click 이상)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, cluster_id),
    CHECK (n_positive <= n_impress)
);

CREATE TABLE user_vector (
    user_id        BIGINT      PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    taste_vec      REAL[]      NOT NULL,
    content_emb    vector(768),
    n_events       INT         NOT NULL DEFAULT 0,
    n_positive     INT         NOT NULL DEFAULT 0,
    computed_from  VARCHAR(16) NOT NULL
                   CHECK (computed_from IN ('onboarding','blended','behavior')),
    -- 🔴 온보딩에서 **고른 레시피 원본**. taste_vec 은 이것의 평균이라 결과만
    --    남기면 **다시 계산할 수 없다.** 시드(ingredient_flavor.yaml)가 바뀌면
    --    flavor_vec 이 바뀌고 taste_vec 도 따라 바뀌어야 하는데, 원본이 없으면
    --    유저를 다시 모아야 한다. 실제로 09-02 에 시드 2건을 고쳤다.
    --    seeds/onboarding_recipes.yaml 의 제시 20개 중 고른 것들의 인덱스.
    onboarding_picks SMALLINT[],
    -- 척도 문항 원본 (spicy·salty·sweet 0~4). user_preference 에도 있지만
    -- 여기 스냅샷을 두면 taste_vec 재계산이 이 테이블 하나로 끝난다.
    onboarding_scales SMALLINT[],
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (array_length(taste_vec, 1) = 6),
    CHECK (onboarding_picks IS NULL OR array_length(onboarding_picks, 1) BETWEEN 1 AND 20),
    CHECK (onboarding_scales IS NULL OR array_length(onboarding_scales, 1) = 3)
);

-- [16] 냉장고. expires_at 이 최대 차별화 피처.
CREATE TABLE pantry_item (
    id             BIGSERIAL   PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    ingredient_id  INT         NOT NULL REFERENCES ingredient(id) ON DELETE CASCADE,
    quantity       REAL,
    unit           VARCHAR(16),
    -- 🔴 **구매일**. 소비기한 추정의 기준점이다 (09-02 신설).
    --    `added_at` 과 다르다 — added_at 은 앱에 넣은 시각이고, 마트에서 사고
    --    사흘 뒤에 등록하면 사흘을 공짜로 벌어준다. 실제로는 이미 지난 시간이다.
    --    영수증을 쓰면 구매일이 거기 찍혀 있으므로 정확해진다.
    --    NULL 이면 추정 기준을 added_at 으로 폴백한다.
    purchased_at   DATE,
    -- **소비기한**(use-by). 유통기한(sell-by)이 아니다 — 유통기한은 판매 가능 기한이라
    -- 지나도 먹을 수 있고, 우리가 알고 싶은 것은 "언제까지 먹을 수 있나" 다.
    expires_at     DATE,
    -- 🔑 expires_at 이 유저가 넣은 값인지 시스템 추정치인지 구분한다.
    --    구분하지 않으면 "추정이 유저 입력만큼 유용한가"를 영원히 측정할 수 없다.
    --    추정식:  expires_at = COALESCE(purchased_at, added_at::date)
    --                          + ingredient_shelf_life.days
    expires_at_source VARCHAR(10) NOT NULL DEFAULT 'estimated'
                      CHECK (expires_at_source IN ('user','estimated','unknown')),
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 구매일이 등록일보다 뒤일 수는 없다 (미래 구매)
    CHECK (purchased_at IS NULL OR purchased_at <= (added_at AT TIME ZONE 'UTC')::date + 1),
    -- 🔴 소급 불가 (07 E-3 ③). 소비기한 낭비율 · shelf_life 추정 검증 · 소진 시퀀스가
    --    전부 "언제 넣어 언제 어떻게 뺐나"의 쌍을 요구한다. 삭제하지 않고 tombstone 을
    --    남기면 한 행에 재료의 생애 전체가 담겨 조인 없이 보유기간이 나온다.
    removed_at     TIMESTAMPTZ,
    -- 🔴 3값이다 (S0 ⑤ · 2026-09-02). 2값이면 NULL 이 두 가지를 뜻해 못 나눈다:
    --      NULL      물어보지 않았다 (버튼 없던 시절 · 조용히 사라진 건)
    --      unknown   물었는데 유저가 건너뛰었다
    --    스킵이 폐기 쪽에 몰리면(버린 걸 밝히기 싫어서) MNAR 이라
    --    이 둘을 못 가르면 낭비율의 상한·하한조차 못 낸다. 지금은 공짜, 나중은 소급 불가.
    removed_reason VARCHAR(10)
                   CHECK (removed_reason IN ('consumed','discarded','unknown')),
    CHECK (removed_reason IS NULL OR removed_at IS NOT NULL)
    -- ⚠️ UNIQUE (user_id, ingredient_id) 를 뺐다. 같은 재료를 다시 넣으면 새 행이
    --    생겨야 하기 때문이다. 현재 보유분의 유일성은 03_indexes.sql 의
    --    idx_pantry_active (부분 유니크) 가 강제한다.
);

-- ───────────────────────────────────────────────────────────────
-- GROUP D. 로그 · 관측 (설계 ③ 4층)
-- ───────────────────────────────────────────────────────────────

-- [24] 피처 파생 통계. 코퍼스에서 계산되는 값이라 scoring_config 와 성격이 다르다
--      (저쪽은 사람이 정하는 설정, 이쪽은 배치가 계산하는 통계 — 갱신 주기가 다르다).
--      🔴 f_taste 중심화용 μ 가 여기 산다. μ 가 바뀌면 과거 f_taste 를 재현할 수 없으므로
--      recommendation_log.stats_version 으로 어느 μ 였는지 남긴다 (06 9절 · cluster_version 패턴).
--
--      왜 중심화가 필요한가: flavor_vec 과 taste_vec 이 모두 비음수라 코사인이 [0,1] 에
--      갇힌다. 실측에서 무작위 벡터끼리도 0.77 이 나왔고 우리 값이 0.747 이었다
--      — 무작위와 구분되지 않는다 (bench/flavor_scale.py). 코퍼스 평균을 빼야 한다.
--      🔴 taste_vec 도 **같은 μ** 로 빼야 좌표계가 어긋나지 않는다.
CREATE TABLE feature_stats (
    stats_version SERIAL      PRIMARY KEY,
    flavor_mu     REAL[]      NOT NULL,   -- 길이 6. 축은 flavor_vec 과 동일
    n_recipes     INT         NOT NULL,   -- 몇 건으로 계산했나. 3건 μ 와 4.4만건 μ 는 다르다
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note          TEXT,
    CHECK (array_length(flavor_mu, 1) = 6),
    CHECK (n_recipes >= 0)
);

-- μ 를 아직 계산할 코퍼스가 없다. 자리만 잡고 영벡터로 시작한다
-- (중심화를 끄는 것과 같다). 크롤 적재 후 배치가 진짜 μ 를 넣는다.
INSERT INTO feature_stats (flavor_mu, n_recipes, note)
VALUES (ARRAY[0,0,0,0,0,0]::REAL[], 0, 'bootstrap — 코퍼스 미도착. 중심화 비활성');

-- [25] 크롤 후기 (02 C-5 · 2026-09-02 확정).
--      🔴 **재크롤로 되살릴 수 없다** — 후기는 레시피 본문과 달리 작성자·운영자가
--      지우면 사라지고, 차단 위험이 있으며, 크롤링이 크리티컬 패스라 두 번째 자리가 없다.
--
--      실측(46,552건): 후기 있는 레시피 99.6% · 총 627,610건 · 레시피당 평균 13.5건.
--      작성자 177,875명 중 41.3%가 2개 이상 레시피에 썼다 —
--      **아이템당 상호작용 11.86건**으로, 우리 유저 로그 예상치(0.091)의 130배다.
--      이것이 item-item CF 와 recipe×ingredient 를 여는 유일한 실제 상호작용이다.
--
--      🔴 닉네임은 원문을 저장하지 않는다. 동의를 받을 수 없는 제3자 정보이기 때문이다.
--      HMAC-SHA256(닉네임, salt) 앞 16hex 를 쓴다 — 복원은 불가하지만 재계산은 되므로
--      삭제 요청자가 닉네임을 대면 그 행을 찾아 지울 수 있다. salt 는 .env 에 둔다.
CREATE TABLE recipe_review (
    id          BIGSERIAL   PRIMARY KEY,
    recipe_id   BIGINT      NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    author_hash CHAR(16),                       -- 닉네임 없는 후기는 NULL (실측 0.86%)
    written_at  TIMESTAMPTZ,
    body        TEXT        NOT NULL,
    -- 🔴 `NULLS NOT DISTINCT` 가 없으면 멱등이 깨진다. Postgres 는 UNIQUE 에서
    --    NULL 을 서로 다른 값으로 보므로, 닉네임 파싱이 실패한 후기(실측 10,922건,
    --    author_hash IS NULL)가 제약을 우회해 재적재 때마다 쌓인다.
    --    실측(09-02): 2회 실행으로 5,462행 초과. PG15+ 필요 (현재 16.15).
    UNIQUE NULLS NOT DISTINCT (recipe_id, author_hash, written_at)
);

-- [23] 점수 설정 레지스트리 (07 E-3 ④).
--      🔴 소급 불가. config_hash 를 되돌리는 유일한 경로다. 이 테이블이 없으면
--      과거 요청의 점수를 재현할 수 없다 — 해시는 단방향이다.
--      실효 가중치 = f(base_weights, recommendation_log.warm_alpha) (01 5-2-3).
CREATE TABLE scoring_config (
    config_hash   VARCHAR(32) PRIMARY KEY,
    base_weights  JSONB       NOT NULL,   -- FEATURE_KEYS 17키 전수. w=0 도 명시한다
    penalty_spec  JSONB,                  -- {p_recent, p_cooked, p_avoid} 계수
    n_warm        INT         NOT NULL DEFAULT 20,   -- α = min(1, n_events/n_warm)
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [18] L1 파이프라인 trace. 재현성의 근거.
--      event_log 가 request_id 로 참조하므로 먼저 생성.
CREATE TABLE recommendation_log (
    request_id       UUID        PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    -- 🔴 소급 불가 (07 E-3 ⑥). 규약: c-{user_id}-{uuid4hex12} 는 클라이언트 발급,
    --    g-{user_id}-{YYYYMMDDHHMI} 는 서버 갭 폴백. prefix 로 출처를 가른다 —
    --    섞이면 나중에 신뢰할 수 있는 행만 골라낼 수 없다.
    session_id       VARCHAR(64),
    model_version    VARCHAR(32) NOT NULL,
    mlflow_run_id    VARCHAR(64),
    -- FK 를 붙이지 않는다. 레지스트리 등록이 늦으면 로깅이 실패해서는 안 된다
    --    (⑨ 와 같은 원칙). 등록 보장은 계약 테스트가 한다.
    config_hash      VARCHAR(32),
    -- 🔴 소급 불가 (07 E-3 ④). 실효 w 는 α = min(1, n_events/n_warm) 의 함수라
    --    유저·요청마다 다르다 (01 5-2-3). config_hash 만으로는 점수가 복원되지 않는다.
    warm_alpha       REAL,
    -- 🔴 어느 μ 로 f_taste 를 계산했는가 (feature_stats). μ 가 바뀌면 점수가 바뀐다.
    stats_version    INT,
    pantry_snapshot  INTEGER[]   NOT NULL,
    -- 🔴 소급 불가 (07 E-3 ⑤). pantry_snapshot 은 id 만 담아 f_expiring 의 원값을
    --    검증할 수 없다. [{ingredient_id, quantity, unit, expires_at, expires_at_source}]
    pantry_detail    JSONB,
    allergy_snapshot INTEGER[],
    request_params   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- 🔴 소급 불가 (07 E-3 ⑦). Interleaving 승패 귀속 — 'A' 가 어느 모델이었나.
    --    [{team, model_version, mlflow_run_id}]. 단일 정책이면 NULL.
    policies         JSONB,
    stage_trace      JSONB       NOT NULL,
    candidates       JSONB,
    served           BIGINT[]    NOT NULL,
    total_latency_ms INT         NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 🔴 session_id 접두어 — 소급 불가. 나중에 못 가른다.
    --      c-  실사용자 (클라이언트)
    --      g-  게스트
    --      d-  **개발·디버거·시딩 트래픽** (09-02 신설)
    --    d- 가 없으면 개발자가 디버거로 눌러본 것이 실유저 지표에 섞인다.
    --    is_simulated 는 '가상 유저'만 거르므로 **실유저 ID 로 눌러본 것은 통과한다.**
    CHECK (session_id IS NULL OR session_id ~ '^[cgd]-')
);

-- [17] L2 유저 행동 로그. impression 과 position 은 소급 불가.
CREATE TABLE event_log (
    id           BIGSERIAL   PRIMARY KEY,
    user_id      BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    -- 🔴 FK 를 붙이지 않는다 (07 E-3 ⑨). ON DELETE SET NULL 이면 레시피 1건 삭제로
    --    학습 행이 **에러 없이** 라벨만 남고 아이템을 잃는다. 게다가 event_type='search'
    --    가 recipe_id IS NULL 을 정상값으로 쓰므로 죽은 행과 구분조차 되지 않는다.
    --    append-only 로그에 도메인 테이블의 수명주기를 묶지 않는다 (01:1226 원안).
    recipe_id    BIGINT,
    event_type   VARCHAR(16) NOT NULL
                 CHECK (event_type IN ('impression','click','save','unsave',
                                       'cook','rating','dismiss','search')),
    value        REAL,
    request_id   UUID        REFERENCES recommendation_log(request_id) ON DELETE SET NULL,
    position     SMALLINT,
    -- 🔴 소급 불가 (07 E-3 ⑥). impression 이 이벤트의 95% 라 여기가 비면 세션이 없다.
    session_id   VARCHAR(64),
    context      JSONB,
    -- ─────────────────────────────────────────────────────────
    -- 🔴 이 이벤트를 **무엇이 관측했는가**. DEFAULT 를 두지 않는다 —
    --    기본값이 있으면 새 삽입 경로가 조용히 오분류된다.
    --
    --      served    서버가 응답에 담았다. **본 것이 아니라 보낸 것이다.**
    --      viewport  클라이언트가 실제 화면 노출을 관측했다
    --      client    사용자 행위 보고 (click·cook·save…). 관측 주체가 없다
    --
    --    지금은 프론트 합의가 어려워 impression 을 'served' 로 쓴다(S2 결정).
    --    나중에 'viewport' 로 바꾸면 **두 시대의 impression 은 뜻이 다르다** —
    --    served 는 스크롤해서 안 본 것도 포함한다. 이 열이 없으면 두 시대가
    --    한 테이블에서 섞여 **둘 다 못 쓰게 된다.** 사후 백필은 불가능하다.
    --    전환기에 둘을 동시에 쓰면 r(p) = viewport(p)/served(p) 로 환산계수가 나온다.
    source       VARCHAR(16) NOT NULL
                 CHECK (source IN ('served','viewport','client')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 🔴 session_id 접두어 — 소급 불가. 나중에 못 가른다.
    --      c-  실사용자 (클라이언트)
    --      g-  게스트
    --      d-  **개발·디버거·시딩 트래픽** (09-02 신설)
    --    d- 가 없으면 개발자가 디버거로 눌러본 것이 실유저 지표에 섞인다.
    --    is_simulated 는 '가상 유저'만 거르므로 **실유저 ID 로 눌러본 것은 통과한다.**
    CHECK (session_id IS NULL OR session_id ~ '^[cgd]-'),
    -- 🔴 impression 에 position 이 없으면 그 시대 데이터는 통째로 못 쓴다.
    --    '상위 k 만 잘라 보기' 조차 안 되고 position bias 보정이 불가능하다.
    --    DDL 이 막지 않으면 라이터 버그 하나로 조용히 비어간다.
    CHECK (event_type <> 'impression' OR position IS NOT NULL)
    -- ⚠️ `CHECK (source <> 'served' OR request_id IS NOT NULL)` 은 **넣지 않는다.**
    --    위 FK 가 ON DELETE SET NULL 이라, recommendation_log 행을 지우면 이 열이
    --    NULL 로 바뀌면서 CHECK 를 위반한다 — 즉 **served 노출이 달린 추천 로그는
    --    영원히 삭제 불가**가 된다 (실측: DELETE 시 CheckViolation). 보존기간 만료분
    --    정리가 막히고, 그건 개인정보 파기 의무와 충돌한다.
    --    이 불변식은 라이터가 지킨다 (reco/log/writer.py — served 는 항상 rid 를 싣는다).
);

-- 🔴 재시도·리플레이 멱등. source 를 키에 넣어야 dual 기록(served+viewport)이
--    공존한다 — 빼면 전환기 환산계수를 구할 창이 닫힌다.
--    viewport 는 스크롤 in/out 으로 같은 아이템이 여러 번 발화하므로
--    source 단위 dedup 이 없으면 viewport(p) > served(p) 가 되어 r(p) > 1 이 된다.
CREATE UNIQUE INDEX ux_ev_impression ON event_log (request_id, recipe_id, source)
    WHERE event_type = 'impression' AND request_id IS NOT NULL;

-- [19] L4 데이터 품질 시계열
CREATE TABLE data_quality_snapshot (
    id                      BIGSERIAL   PRIMARY KEY,
    snapshot_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    n_recipes_raw           INT         NOT NULL DEFAULT 0,
    n_recipes_normalized    INT         NOT NULL DEFAULT 0,
    n_recipes_published     INT         NOT NULL DEFAULT 0,
    n_recipes_rejected      INT         NOT NULL DEFAULT 0,
    n_ingredients           INT         NOT NULL DEFAULT 0,
    n_ingredients_verified  INT         NOT NULL DEFAULT 0,
    n_aliases               INT         NOT NULL DEFAULT 0,
    mention_total           BIGINT      NOT NULL DEFAULT 0,
    mention_matched         BIGINT      NOT NULL DEFAULT 0,
    coverage_rate           REAL        NOT NULL DEFAULT 0,
    type_coverage_rate      REAL,                    -- 고유 표현 기준 (설계 4-8)
    match_method_dist       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    queue_pending           INT         NOT NULL DEFAULT 0,
    queue_resolved          INT         NOT NULL DEFAULT 0,
    avg_essential_per_recipe REAL,
    pct_recipes_no_cooktime  REAL
);

-- [20] L4 배치 실행 이력
CREATE TABLE batch_run (
    id            BIGSERIAL   PRIMARY KEY,
    job_name      VARCHAR(64) NOT NULL,
    status        VARCHAR(16) NOT NULL CHECK (status IN ('running','success','failed')),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    input_count   BIGINT,
    output_count  BIGINT,
    params        JSONB,
    error_msg     TEXT,
    mlflow_run_id VARCHAR(64)
);

-- ───────────────────────────────────────────────────────────────
-- GROUP E. 운영 (검수)
-- ───────────────────────────────────────────────────────────────

-- [21] 재료 검수 작업 큐. suggested 가 채워져야 1건 15초가 성립.
CREATE TABLE normalization_queue (
    id            BIGSERIAL    PRIMARY KEY,
    raw_text      VARCHAR(255) NOT NULL UNIQUE,
    freq_count    INT          NOT NULL DEFAULT 0,
    suggested     JSONB,
    resolved_id   INT          REFERENCES ingredient(id) ON DELETE SET NULL,
    action        VARCHAR(16)  CHECK (action IN ('map','new','ignore','split')),
    split_into    INTEGER[],
    assignee      VARCHAR(64),
    status        VARCHAR(16)  NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','in_review','resolved','skipped')),
    source        VARCHAR(16)  NOT NULL DEFAULT 'batch',   -- 'batch' | 'user'
    resolved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- [22] 검수 감사 로그. 20명이 동시에 검수하면 반드시 오류가 섞인다.
CREATE TABLE normalization_audit (
    id          BIGSERIAL    PRIMARY KEY,
    queue_id    BIGINT       REFERENCES normalization_queue(id) ON DELETE SET NULL,
    raw_text    VARCHAR(255) NOT NULL,
    before_id   INT,
    after_id    INT,
    action      VARCHAR(16)  NOT NULL,
    actor       VARCHAR(64)  NOT NULL,
    reverted    BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- [26] 트랙 1 — 오늘의 추천 (사전 계산) · 2026-09-03 확정
--      쓰기: 배치 1일 1회 │ 규모: 유저수 × 20 │ 온라인: ✅✅ 메인화면이 읽는다
-- ═══════════════════════════════════════════════════════════════
-- 추천을 두 트랙으로 나눈 근거는 지연이다 (01 5-6-1) —
-- 사유 1건 25토큰 × 10건을 300ms 에 내려면 833 tok/s 가 필요한데
-- 가장 빠른 로컬 모델이 90.9 tok/s 다. **배치로 밀면 시간 제약이 사라진다.**
--
--   트랙 1  오늘의 추천    배치 1일 1회 · LLM 사유 · 조회 ~30ms   ← 이 테이블
--   트랙 2  잔여 재료      실시간 · 템플릿 사유 · ~58ms
CREATE TABLE daily_recommendation (
    user_id       BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    rank          SMALLINT    NOT NULL CHECK (rank >= 1),
    recipe_id     BIGINT      NOT NULL,   -- 🔴 FK 없음 (event_log 와 같은 이유)
    score         REAL        NOT NULL,
    -- LLM 이 쓴 사유. 실패하면 템플릿으로 떨어지므로 출처를 남긴다 —
    -- "LLM 사유가 템플릿보다 나은가"를 나중에 측정하려면 구분이 필요하다.
    reason        TEXT,
    reason_source VARCHAR(10) NOT NULL DEFAULT 'template'
                  CHECK (reason_source IN ('llm','template','fallback')),
    -- 🔴 **stale 판정의 근거.** 계산 시점의 냉장고를 지문으로 남긴다.
    --    낮에 재료를 쓰면 사유가 어긋나는데("두부가 상하기 전에"인데 두부를 다 씀),
    --    지문이 없으면 그 사실을 알 방법이 없다. 조회 시 현재 pantry 와 비교한다.
    pantry_fingerprint TEXT   NOT NULL,
    -- 점수 재현용. recommendation_log 와 같은 규약이다.
    config_hash   VARCHAR(32),
    stats_version INT,
    batch_run_id  BIGINT      REFERENCES batch_run(id) ON DELETE SET NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, rank)
);
CREATE INDEX idx_dr_user ON daily_recommendation (user_id, rank);
CREATE INDEX idx_dr_batch ON daily_recommendation (batch_run_id);

COMMENT ON TABLE daily_recommendation IS
    '트랙 1(오늘의 추천) 사전 계산 결과. 메인화면이 조회만 한다. '
    '배치가 매일 덮어쓴다 — 이력이 필요하면 recommendation_log 를 본다.';
COMMENT ON COLUMN daily_recommendation.pantry_fingerprint IS
    '계산 시점 냉장고의 지문. 조회 시 현재 값과 다르면 stale 로 표시한다.';
