-- 적재 검증. 시뮬 시드 유저(is_simulated AND username LIKE 'sim_u%')만 본다.
-- is_simulated 만으로 걸면 make smoke 의 합성 유저(smoketest_*)가 섞여 건수가 어긋난다 (09-14 실측 +8·+4·+71).
SET search_path TO reco, public;
\echo '--- 테이블별 건수'
SELECT 'app_user' t, count(*) FROM app_user WHERE is_simulated AND username LIKE 'sim_u%'
UNION ALL SELECT 'user_preference', count(*) FROM user_preference p JOIN app_user u ON u.id=p.user_id WHERE u.is_simulated AND u.username LIKE 'sim_u%'
UNION ALL SELECT 'user_vector', count(*) FROM user_vector v JOIN app_user u ON u.id=v.user_id WHERE u.is_simulated AND u.username LIKE 'sim_u%'
UNION ALL SELECT 'user_allergy', count(*) FROM user_allergy a JOIN app_user u ON u.id=a.user_id WHERE u.is_simulated AND u.username LIKE 'sim_u%'
UNION ALL SELECT 'pantry_item', count(*) FROM pantry_item p JOIN app_user u ON u.id=p.user_id WHERE u.is_simulated AND u.username LIKE 'sim_u%'
UNION ALL SELECT 'event_log', count(*) FROM event_log e JOIN app_user u ON u.id=e.user_id WHERE u.is_simulated AND u.username LIKE 'sim_u%';

\echo '--- 집단 × 모드 (cold/blended/warm) 분포'
SELECT sp.name AS persona, v.computed_from, count(*) AS n, round(avg(v.n_events),1) AS avg_events
FROM user_vector v JOIN app_user u ON u.id=v.user_id JOIN sim_persona sp ON sp.id=u.persona_id
GROUP BY 1,2 ORDER BY 1,2;

\echo '--- 시나리오 후보: 이벤트 많은 유저 상위 10 (냉장고 보유 여부 포함)'
SELECT u.id, u.display_name, sp.name AS persona, v.n_events, v.computed_from,
       (SELECT count(*) FROM pantry_item p WHERE p.user_id=u.id AND p.removed_at IS NULL) AS pantry_n,
       (SELECT count(*) FROM event_log e WHERE e.user_id=u.id AND e.event_type='cook') AS cooks
FROM app_user u JOIN user_vector v ON v.user_id=u.id JOIN sim_persona sp ON sp.id=u.persona_id
WHERE u.is_simulated AND u.username LIKE 'sim_u%' ORDER BY v.n_events DESC, u.id LIMIT 10;

\echo '--- 후보 조회 가능 여부 (냉장고 있는 유저 중 retrieve_for_user 가 1건 이상 내는 비율)'
SELECT count(*) FILTER (WHERE n > 0) AS users_with_candidates, count(*) AS users_with_pantry
FROM (SELECT u.id, (SELECT count(*) FROM retrieve_for_user(u.id, 2, NULL, 50)) AS n
      FROM app_user u WHERE u.is_simulated AND u.username LIKE 'sim_u%'
        AND EXISTS (SELECT 1 FROM pantry_item p WHERE p.user_id=u.id AND p.removed_at IS NULL)) s;
