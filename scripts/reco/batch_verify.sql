-- 정규화 배치(A-2) 결과 검증. make normalize-verify 가 돌립니다.
--
-- 주의: 행 수만 보고 넘어가지 않습니다. 기대치 354,600 은 "매칭된 것만" 의
--    수입니다. 미매칭은 recipe_ingredient 에 아예 들어가지 않아 어느 카운트에도
--    나타나지 않습니다 — 5할이 버려져도 표는 깔끔해 보입니다. 그래서 [1] 이
--    원문 대비 비율을 함께 냅니다. 소급 가능한 형태로 남기는 것은 A-4 의
--    recipe_feature.n_unmatched 입니다.
SET search_path = reco, public;

\echo '[1] 행 수 — 원문 대비 얼마가 남았는가'
SELECT (SELECT count(*) FROM recipe)                 AS recipes,
       (SELECT count(*) FROM recipe_ingredient_raw)  AS raw_rows,
       (SELECT count(*) FROM recipe_ingredient)      AS rows,
       round(100.0 * (SELECT count(*) FROM recipe_ingredient)
                   / NULLIF((SELECT count(*) FROM recipe_ingredient_raw), 0), 1) AS pct_of_raw,
       (SELECT count(DISTINCT recipe_id) FROM recipe_ingredient) AS recipes_with_rows;

\echo ''
\echo '[2] match_method 분포 — exact·alias·rule 셋만 나와야 한다'
-- 주의: fuzzy·embed 가 한 건이라도 있으면 실패입니다. 설계 4-4-1 이 유사도
--    매칭의 자동 확정을 금지합니다 — 후보는 검수자에게 제안될 뿐입니다.
SELECT match_method, count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct,
       round(avg(match_score)::numeric, 3)                AS avg_score
FROM recipe_ingredient GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '[3] role 분포 — garnish 는 0 이어야 한다'
-- 주의: garnish 가 0 이 아니면 role.py 의 GARNISH_BY_POSITION 훅이 켜진
--    것입니다. 원문 group_name 이 451,862행 전부 '기본재료' 라 R1·R2 는 돌 수
--    없고, 위치로 고명을 추측하는 것은 측정된 적 없는 가정입니다.
SELECT role, count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM recipe_ingredient GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '[4] 레시피당 재료 수 분포'
SELECT min(n), round(avg(n)::numeric, 2) AS avg,
       percentile_disc(0.5)  WITHIN GROUP (ORDER BY n) AS p50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY n) AS p95,
       max(n)
FROM (SELECT recipe_id, count(*) n FROM recipe_ingredient GROUP BY 1) t;

\echo ''
\echo '[5] 합격 판정'
-- 한 줄로 통과 여부를 낸다. 눈으로 표 넷을 훑다 놓치는 것을 막습니다.
SELECT CASE WHEN ok THEN '통과' ELSE '실패' END AS verdict, *
FROM (
    SELECT (SELECT count(*) FROM recipe_ingredient) BETWEEN 340000 AND 370000 AS rows_in_range,
           NOT EXISTS (SELECT 1 FROM recipe_ingredient
                       WHERE match_method IN ('fuzzy', 'embed'))              AS no_auto_fuzzy,
           NOT EXISTS (SELECT 1 FROM recipe_ingredient WHERE role = 'garnish') AS no_garnish,
           NOT EXISTS (SELECT 1 FROM recipe_ingredient
                       GROUP BY recipe_id, ingredient_id HAVING count(*) > 1)  AS no_pk_dup
) c, LATERAL (SELECT c.rows_in_range AND c.no_auto_fuzzy
                 AND c.no_garnish AND c.no_pk_dup AS ok) v;
