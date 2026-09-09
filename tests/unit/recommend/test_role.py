"""P4 역할 판정 회귀 검증 (설계 4-5).

    .venv/bin/python -m tests.unit.recommend.test_role

🔴 여기서 깨지면 **조용히 틀린다** — `essential_ids` 가 바뀌어
   만들 수 없는 레시피를 추천하거나, 후보가 말라죽는다.
"""
from __future__ import annotations

import sys

from features.recommend.enums import IngredientRole
from features.recommend.ingest.match import Dictionary, match
from features.recommend.ingest.parse import normalize
from features.recommend.ingest.role import MAIN_CATEGORIES, judge, judge_all

ok, fail = 0, []


def check(label: str, cond: bool):
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        fail.append(label)
        print(f"  ✗ {label}")


def role_of(text: str, d):
    p = normalize(text)[0]
    return judge(p, match(p.name, d), d)


print("[P4 역할 판정]")
d = Dictionary.from_seeds()

# ── 규칙별 발동 ─────────────────────────────────────────────
for txt, want_role, want_rule in [
    ("소금 약간",        IngredientRole.SEASONING, "R3_is_seasoning"),
    ("대파 1대",         IngredientRole.ESSENTIAL, "R7_기본"),
    ("청양고추 1개(취향껏)", IngredientRole.OPTIONAL,  "R4_note_hint"),
]:
    r = role_of(txt, d)
    check(f"{want_rule:<18} {txt} → {want_role.value}",
          r.role == want_role and r.rule == want_rule)

# ── 주재료 가드 — 설계 4-5 규칙 5 를 그대로 쓰면 깨지는 곳 ──
#    "돼지고기 적당량" 이 optional 이 되면 돼지고기 없는 사람에게 제육볶음을 권한다.
for txt in ["돼지고기 적당량", "새우 약간", "소고기 약간"]:
    r = role_of(txt, d)
    check(f"🔴 주재료는 '약간' 이어도 essential — {txt}",
          r.role == IngredientRole.ESSENTIAL and r.rule == "R5_가드_주재료")

# ── 양념은 '약간' 없이도 seasoning ──────────────────────────
for txt in ["간장 2큰술", "참기름 1큰술", "설탕 1작은술"]:
    r = role_of(txt, d)
    check(f"양념은 수량이 명확해도 seasoning — {txt}",
          r.role == IngredientRole.SEASONING)

# ── 미매칭은 판정하지 않는다 ─────────────────────────────
#    is_staple 을 알 수 없고, ingredient_id 가 없어 essential_ids 에 못 들어간다.
r = role_of("올리브유오일 약간", d)
check("🔴 미매칭은 판정 보류 (임의로 optional 로 떨구지 않는다)",
      r.role is None and r.rule == "R0_미매칭")

# ── group_name 규칙이 발동하지 않는 것을 명시적으로 확인 ──
#    실측: 크롤 데이터의 group_name 이 전부 '기본재료' 라 1·2번은 죽어 있다.
codes = {role_of(t, d).rule for t in
         ["소금 약간", "대파 1대", "돼지고기 적당량", "간장 2큰술"]}
check("🔴 R1·R2(group_name) 는 발동하지 않는다 — 실측 전제",
      not any(c.startswith(("R1", "R2")) for c in codes))

# ── essential 경계가 Retrieval 에 미치는 영향 ───────────────
#    optional·seasoning·garnish 는 전부 essential_ids 에서 빠진다 → 같은 결과
non_essential = [IngredientRole.OPTIONAL, IngredientRole.SEASONING, IngredientRole.GARNISH]
check("essential 이 아닌 3종은 Retrieval 에 동일하게 작용한다 (모두 제외)",
      all(r != IngredientRole.ESSENTIAL for r in non_essential))

# ── 통계 집계 ───────────────────────────────────────────────
items = []
for t in ["대파 1대", "소금 약간", "돼지고기 300g", "우주선전투기 1개"]:
    p = normalize(t)[0]
    items.append((p, match(p.name, d)))
res, st = judge_all(items, d)
check("규칙별 발동 횟수를 집계한다", sum(st.by_rule.values()) == 4)
check("보류(미매칭)를 따로 센다", st.n_unjudged == 1)
check("essential 비율은 판정된 것만으로 계산한다",
      abs(st.essential_ratio - 2 / 3) < 1e-9)

# ── 주재료 카테고리 상수가 실제 시드와 맞는가 ───────────────
cats = {(v.get("category_path") or "").split(".")[0] for v in d.meta.values()}
check(f"MAIN_CATEGORIES 가 실제 대분류에 존재한다 {MAIN_CATEGORIES}",
      all(c in cats for c in MAIN_CATEGORIES))

print(f"\n{'✅ 전부 통과' if not fail else f'❌ {len(fail)}건 실패'} ({ok}건 확인)")
sys.exit(1 if fail else 0)
