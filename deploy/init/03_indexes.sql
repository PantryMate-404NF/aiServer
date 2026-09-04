-- ★ 이 파일의 모든 객체는 reco 스키마에 생성된다.
--   확장 타입(vector·ltree)을 쓰기 위해 public 을 search_path 에 함께 둔다.
SET search_path TO reco, public;

-- ═══════════════════════════════════════════════════════════════
-- 인덱스. 스키마와 분리해 두면 대량 적재 시 DROP → 적재 → 재생성이 쉽다.
-- ═══════════════════════════════════════════════════════════════

-- ── 재료 온톨로지 ──────────────────────────────────────────────
CREATE INDEX idx_cat_path      ON ingredient_category USING GIST (path);
CREATE INDEX idx_cat_parent    ON ingredient_category (parent_id);

CREATE INDEX idx_ing_freq      ON ingredient (freq_count DESC);
CREATE INDEX idx_ing_category  ON ingredient (category_id);
CREATE INDEX idx_ing_staple    ON ingredient (id) WHERE is_staple;      -- 부분 인덱스
CREATE INDEX idx_ing_allergen  ON ingredient (allergen_group) WHERE allergen_group IS NOT NULL;
CREATE INDEX idx_ing_name_trgm ON ingredient USING GIN (name      gin_trgm_ops);
CREATE INDEX idx_ing_jamo_trgm ON ingredient USING GIN (name_jamo gin_trgm_ops);

CREATE INDEX idx_alias_trgm    ON ingredient_alias USING GIN (alias      gin_trgm_ops);
CREATE INDEX idx_alias_jamo    ON ingredient_alias USING GIN (alias_jamo gin_trgm_ops);
CREATE INDEX idx_alias_ing     ON ingredient_alias (ingredient_id);
CREATE INDEX idx_alias_exact   ON ingredient_alias (alias);             -- L1 완전일치

-- ── 레시피 ─────────────────────────────────────────────────────
CREATE INDEX idx_recipe_status   ON recipe (status);
CREATE INDEX idx_recipe_cuisine  ON recipe (cuisine_family) WHERE cuisine_family IS NOT NULL;
CREATE INDEX idx_recipe_crawled  ON recipe (crawled_at DESC);

CREATE INDEX idx_rir_recipe ON recipe_ingredient_raw (recipe_id);
CREATE INDEX idx_rir_text   ON recipe_ingredient_raw (raw_text);        -- 빈도 집계용

CREATE INDEX idx_ri_ing     ON recipe_ingredient (ingredient_id);
CREATE INDEX idx_ri_method  ON recipe_ingredient (match_method);        -- 품질 집계용
CREATE INDEX idx_ri_role    ON recipe_ingredient (role);

-- ★ 온라인 경로. 이 3개가 Retrieval 성능을 결정한다.
CREATE INDEX idx_rf_essential ON recipe_feature USING GIN (essential_ids gin__int_ops);
CREATE INDEX idx_rf_all       ON recipe_feature USING GIN (all_ids       gin__int_ops);
CREATE INDEX idx_rf_pop       ON recipe_feature (popularity_score DESC);
CREATE INDEX idx_rf_cuisine   ON recipe_feature (cuisine_family) WHERE cuisine_family IS NOT NULL;
CREATE INDEX idx_rf_version   ON recipe_feature (feature_version);      -- 부분 재계산용
-- 필수재료가 전부 staple 인 레시피. && 로는 못 잡히므로 별도 경로가 필요하다.
CREATE INDEX idx_rf_no_ess    ON recipe_feature (popularity_score DESC)
                              WHERE cardinality(essential_ids) = 0;

-- HNSW 는 데이터가 적재된 뒤 만드는 편이 훨씬 빠르다.
-- 배치가 recipe_feature 를 채운 뒤 db/post_index.sql 로 별도 실행한다.
-- CREATE INDEX idx_rf_emb ON recipe_feature USING hnsw (content_emb vector_cosine_ops);

-- ── 유저 ───────────────────────────────────────────────────────
CREATE INDEX idx_user_sim     ON app_user (is_simulated);
CREATE INDEX idx_uip_ing      ON user_ingredient_pref (ingredient_id);
CREATE INDEX idx_allergy_user ON user_allergy (user_id);
-- 🔴 현재 보유분의 유일성. 02_schema.sql 에서 뺀 UNIQUE 를 여기서 대신한다.
--    부분 인덱스라 이력이 쌓여도 담는 것은 활성 행뿐이다 (유저 100명 기준 약 2,000행).
CREATE UNIQUE INDEX idx_pantry_active ON pantry_item (user_id, ingredient_id)
                                      WHERE removed_at IS NULL;
CREATE INDEX idx_pantry_user  ON pantry_item (user_id)
                               WHERE removed_at IS NULL;
CREATE INDEX idx_pantry_exp   ON pantry_item (user_id, expires_at)
                               WHERE expires_at IS NOT NULL AND removed_at IS NULL;
-- 이력 분석용 (폐기율 · 평균 보유기간). 활성 행은 담지 않는다.
CREATE INDEX idx_pantry_hist  ON pantry_item (ingredient_id, removed_reason)
                               WHERE removed_at IS NOT NULL;

-- ── 로그 ───────────────────────────────────────────────────────
CREATE INDEX idx_ev_user    ON event_log (user_id, created_at DESC);
-- 세션 재구성 (07 E-3 ⑥). 클라이언트 발급 라벨과 30분 갭 휴리스틱의 ARI 검증,
-- 그리고 시퀀스 모델의 입력 단위가 된다.
CREATE INDEX idx_ev_session ON event_log (session_id, created_at)
                             WHERE session_id IS NOT NULL;
CREATE INDEX idx_ev_request ON event_log (request_id);
CREATE INDEX idx_ev_type    ON event_log (event_type, created_at DESC);
CREATE INDEX idx_ev_recipe  ON event_log (recipe_id, event_type);

CREATE INDEX idx_rl_user    ON recommendation_log (user_id, created_at DESC);
CREATE INDEX idx_rl_model   ON recommendation_log (model_version, created_at DESC);
CREATE INDEX idx_rl_created ON recommendation_log (created_at DESC);
CREATE INDEX idx_rl_mlflow  ON recommendation_log (mlflow_run_id)
                             WHERE mlflow_run_id IS NOT NULL;

CREATE INDEX idx_dq_time    ON data_quality_snapshot (snapshot_at DESC);
CREATE INDEX idx_batch_job  ON batch_run (job_name, started_at DESC);

-- ── 운영 ───────────────────────────────────────────────────────
CREATE INDEX idx_nq_work     ON normalization_queue (status, freq_count DESC);
CREATE INDEX idx_nq_assignee ON normalization_queue (assignee, status);
CREATE INDEX idx_nq_trgm     ON normalization_queue USING GIN (raw_text gin_trgm_ops);
CREATE INDEX idx_na_queue    ON normalization_audit (queue_id);
CREATE INDEX idx_na_actor    ON normalization_audit (actor, created_at DESC);

-- 우연성·다양성 (설계 5-3-5) — 클러스터 쿼터가 후보 500건을 그룹핑할 때
CREATE INDEX IF NOT EXISTS idx_rf_cluster ON recipe_feature (cluster_id)
                                          WHERE cluster_id IS NOT NULL;

-- ── 크롤 후기 (02 C-5) ─────────────────────────────────────────
-- author_hash: 같은 사람이 쓴 여러 후기를 묶는다 — item-item CF 의 입력
CREATE INDEX idx_review_author ON recipe_review (author_hash)
                                WHERE author_hash IS NOT NULL;
CREATE INDEX idx_review_recipe ON recipe_review (recipe_id);
