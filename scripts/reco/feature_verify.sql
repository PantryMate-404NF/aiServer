-- recipe_feature 빌더(A-4) 검증. make feature-verify 가 돌립니다.
--
-- A-4 완료 기준 6줄을 그대로 옮겼습니다. 눈으로 표를 훑는 대신 마지막 절이
-- 한 줄로 판정합니다.
SET search_path = reco, public;

\echo '[1] 완료 기준 6줄'
SELECT k, v, CASE WHEN ok THEN '통과' ELSE '실패' END AS 판정 FROM (
    SELECT '① recipe_feature = 46,353' AS k,
           (SELECT count(*) FROM recipe_feature)::text AS v,
           (SELECT count(*) FROM recipe_feature) = (SELECT count(*) FROM recipe) AS ok
    UNION ALL
    -- 주의: P4 가 이미 staple 을 seasoning 으로 보내지만 SQL 에서 한 번 더 겁니다.
    --    39종 중 15종(쌀·밀가루·물·얼음)은 is_staple 로만 걸립니다. 검수가 role 을
    --    바꾸면 "물이 없어서 못 만드는 레시피" 가 됩니다.
    SELECT '② essential_ids 에 is_staple',
           (SELECT count(*) FROM recipe_feature rf WHERE EXISTS (
                SELECT 1 FROM ingredient i WHERE i.is_staple AND i.id = ANY(rf.essential_ids)))::text,
           NOT EXISTS (SELECT 1 FROM recipe_feature rf WHERE EXISTS (
                SELECT 1 FROM ingredient i WHERE i.is_staple AND i.id = ANY(rf.essential_ids)))
    UNION ALL
    SELECT '③ n_total <> cardinality(all_ids)',
           (SELECT count(*) FROM recipe_feature WHERE n_total <> cardinality(all_ids))::text,
           NOT EXISTS (SELECT 1 FROM recipe_feature WHERE n_total <> cardinality(all_ids))
    UNION ALL
    SELECT '④ n_essential > n_total',
           (SELECT count(*) FROM recipe_feature WHERE n_essential > n_total)::text,
           NOT EXISTS (SELECT 1 FROM recipe_feature WHERE n_essential > n_total)
    UNION ALL
    -- 주의: 0 이면 배치가 값을 안 넘겼다는 뜻입니다. 그러면 D-10 이 미매칭 0 을
    --    정상으로 읽어 조용히 꺼지고, 정규화 실패 레시피가 coverage 만점을 받습니다.
    SELECT '⑤ sum(n_unmatched) > 0',
           (SELECT sum(n_unmatched) FROM recipe_feature)::text,
           (SELECT sum(n_unmatched) FROM recipe_feature) > 0
    UNION ALL
    SELECT '⑥ avg(n_essential) 2.7~3.3',
           (SELECT round(avg(n_essential)::numeric, 2) FROM recipe_feature)::text,
           (SELECT avg(n_essential) FROM recipe_feature) BETWEEN 2.7 AND 3.3
) t;

\echo ''
\echo '[2] D-10 판정 — 필수재료 0개를 미매칭 비율로 가른다'
-- 두 숫자를 따로 냅니다. 하나로 뭉치면 정상(간장계란밥)과 정규화 실패를
-- 구분할 수 없고, Top-20 이 전부 실패로 채워져도 에러가 안 납니다.
SELECT CASE WHEN n_total = 0 THEN '재료 없음 (⓪ 관문이 거름)'
            WHEN n_essential > 0 AND ratio > 0.3 THEN '필수재료 있음 · 미매칭 많음 (아직 안 거름)'
            WHEN n_essential > 0 THEN '정상'
            WHEN ratio > 0.3 THEN '필수재료 0개 · 정규화 실패 (조회에서 빠짐)'
            ELSE '필수재료 0개 · 정상 (coverage 만점)'
       END AS 분류,
       count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM (SELECT n_total, n_essential,
             n_unmatched::real / NULLIF(n_total + n_unmatched, 0) AS ratio
      FROM recipe_feature) t
GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '[3] 미매칭 비율 분포 — D-14 가 A-4 에서 정하라고 남긴 임계값의 근거'
-- 0.3 은 임시값입니다. n_essential >= 1 쪽 임계값은 이 분포를 보고 정합니다.
SELECT bucket, count(*) FILTER (WHERE n_essential = 0) AS "필수0개",
       count(*) FILTER (WHERE n_essential > 0)         AS "필수있음"
FROM (SELECT n_essential,
             width_bucket(n_unmatched::real / NULLIF(n_total + n_unmatched, 0),
                          0, 1, 10) AS bucket
      FROM recipe_feature WHERE n_total > 0) t
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '[4] 조회가 실제로 무엇을 보는가'
-- ⓪ 와 ⓪'' 를 통과해 후보가 될 수 있는 레시피 수입니다.
SELECT count(*) AS 전체,
       count(*) FILTER (WHERE n_total > 0) AS "⓪ 통과",
       count(*) FILTER (WHERE n_total > 0
                          AND (n_essential > 0
                               OR n_unmatched::real
                                  / NULLIF(n_total + n_unmatched, 0) <= 0.3)) AS "⓪'' 까지 통과"
FROM recipe_feature;
