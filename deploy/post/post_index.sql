SET search_path TO reco, public;

-- 대량 적재 후 별도 실행 (make post-index)
-- HNSW 는 빈 테이블에 만들면 이후 INSERT 마다 느려진다. 데이터를 채운 뒤 한 번에 만든다.

-- 🔴 HNSW 그래프는 maintenance_work_mem 안에서 만들어진다. 넘치면 훨씬 느린
--    on-disk 경로로 떨어진다. 필요량은 대략 최종 인덱스 크기이고,
--    레시피 4.4만 기준 idx_rf_emb 가 약 1 GB 다 (06 2절 실측).
--    전역을 크게 잡으면 autovacuum 워커마다 물리므로 여기서 세션 단위로만 올린다.
SET maintenance_work_mem = '1GB';

-- 병렬 빌드. 리더 + 워커 수만큼 프로세스를 쓰므로 CPU 코어와 맞춘다 (06 8-D).
SET max_parallel_maintenance_workers = 3;

CREATE INDEX IF NOT EXISTS idx_rf_emb  ON recipe_feature USING hnsw (content_emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ing_emb ON ingredient      USING hnsw (embedding   vector_cosine_ops);
ANALYZE recipe_feature;
ANALYZE ingredient;
