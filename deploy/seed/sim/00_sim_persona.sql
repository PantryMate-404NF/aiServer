-- 생성물. scripts/sim/convert_planning_data.py 가 만든다.
-- 손으로 고치지 않는다.
SET search_path TO reco, public;
INSERT INTO sim_persona (name, description, params) VALUES
  ('sim_funnel_A', '기획 v0.4 A집단: 첫구매 → 팬트리·레시피·상품탐색 → 장바구니 → 재구매',
   '{"funnel": "A", "uses_pantry": true, "uses_recipe": true, "source": "planning_v0.4"}'::jsonb),
  ('sim_funnel_B',
   '기획 v0.4 B집단: 첫구매 → 상품탐색만 → 장바구니 → 재구매 (팬트리·레시피 미사용)',
   '{"funnel": "B", "uses_pantry": false, "uses_recipe": false, "source": "planning_v0.4"}'::jsonb)
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, params = EXCLUDED.params;
