#!/usr/bin/env python3
"""시드 데이터 정합성 검증. 적재(migrate) 전에 반드시 통과시킨다.

    python3 seeds/validate.py

FK 위반·고아 참조·중복·충돌을 잡는다. 통과해도 '내용이 옳다'는 뜻은 아니고,
'구조가 깨지지 않았다'는 뜻이다. 내용 검증은 크롤링 데이터로 커버리지를 재야 한다.
"""

import csv
import os
import sys
from collections import Counter, defaultdict

try:
    import yaml
except ImportError:
    sys.exit("pyyaml 필요:  pip install pyyaml")

D = os.path.dirname(os.path.abspath(__file__))
errors, warns = [], []


def E(m):
    errors.append(m)


def W(m):
    warns.append(m)


def load_csv(n):
    return list(csv.DictReader(open(os.path.join(D, n), encoding="utf-8")))


def load_yaml(n):
    return yaml.safe_load(open(os.path.join(D, n), encoding="utf-8"))


# ── 1. 카테고리 트리 ──────────────────────────────────────
cat = load_yaml("ingredient_category.yaml")
paths = {c["path"] for c in cat["categories"]}
for c in cat["categories"]:
    p = c["path"]
    if p.count(".") != c["depth"]:
        E(f"[category] depth 불일치: {p} (depth={c['depth']})")
    if "." in p:
        parent = p.rsplit(".", 1)[0]
        if parent not in paths:
            E(f"[category] 부모 없음: {p} → {parent}")
dups = [p for p, n in Counter(c["path"] for c in cat["categories"]).items() if n > 1]
if dups:
    E(f"[category] path 중복: {dups}")

# 알러지 전개 대상 path 가 실재하는가
for grp, ps in cat["allergen_expansion"].items():
    for p in ps:
        if p not in paths:
            E(f"[allergen] '{grp}' 전개 path 없음: {p}")

# ── 2. 재료 ───────────────────────────────────────────────
ing = load_csv("ingredient.csv")
names = [r["name"] for r in ing]
dups = [n for n, c in Counter(names).items() if c > 1]
if dups:
    E(f"[ingredient] 이름 중복: {dups}")
nameset = set(names)

leaf_paths = paths - {p.rsplit(".", 1)[0] for p in paths if "." in p}
for r in ing:
    if r["category_path"] not in paths:
        E(f"[ingredient] 없는 카테고리: {r['name']} → {r['category_path']}")
    if r["is_staple"] not in ("true", "false"):
        E(f"[ingredient] is_staple 값 오류: {r['name']}")
    if r["is_seasoning"] not in ("true", "false"):
        E(f"[ingredient] is_seasoning 값 오류: {r['name']}")

known_allergens = set(cat["allergen_expansion"]) | set(cat.get("allergen_column_only") or [])
for r in ing:
    a = r["allergen_group"]
    if a and a not in known_allergens:
        E(f"[ingredient] 미정의 알러지 그룹: {r['name']} → {a}")

# 알러지 그룹별 최소 커버리지
for g in known_allergens:
    n = sum(1 for r in ing if r["allergen_group"] == g)
    if n == 0:
        E(f"[allergen] '{g}' 에 속한 재료가 하나도 없음")
    elif n < 3:
        W(f"[allergen] '{g}' 재료 {n}종뿐 — 누락 점검 필요")

# staple 은 양념·조미 또는 기초 곡물이어야 자연스럽다
for r in ing:
    if r["is_staple"] == "true" and not r["category_path"].startswith(
        ("season", "agri.grain", "agri.nutseed")
    ):
        W(f"[staple] 예상 밖 카테고리: {r['name']} ({r['category_path']})")

# ── 3. alias ──────────────────────────────────────────────
al = load_csv("ingredient_alias.csv")
for r in al:
    if r["ingredient_name"] not in nameset:
        E(f"[alias] 없는 재료 참조: {r['alias']} → {r['ingredient_name']}")
    c = float(r["confidence"])
    if not 0 < c <= 1:
        E(f"[alias] confidence 범위 밖: {r['alias']} = {c}")
conf = defaultdict(set)
for r in al:
    conf[r["alias"]].add(r["ingredient_name"])
for a, tgts in conf.items():
    if len(tgts) > 1:
        E(f"[alias] 충돌 — '{a}' 가 {sorted(tgts)} 로 동시 매핑")
# alias 가 다른 재료의 정식명과 같으면 위험
for r in al:
    if r["alias"] in nameset and r["alias"] != r["ingredient_name"]:
        E(f"[alias] 정식 재료명을 alias 로 씀: '{r['alias']}' → {r['ingredient_name']}")

# ── 4. 단위 환산 ──────────────────────────────────────────
uw = load_csv("ingredient_unit_weight.csv")
mu = load_yaml("measure_units.yaml")
measure_units = set(mu["volume_ml"]) | set(mu["weight_g"])
for r in uw:
    if r["ingredient_name"] not in nameset:
        E(f"[unit] 없는 재료 참조: {r['ingredient_name']}")
    g = float(r["grams_per_unit"])
    if g <= 0:
        E(f"[unit] 무게 0 이하: {r['ingredient_name']} {r['unit']}")
    if g > 3000:
        W(f"[unit] 비정상적으로 큼: {r['ingredient_name']} 1{r['unit']} = {g}g")
dups = [k for k, c in Counter((r["ingredient_name"], r["unit"]) for r in uw).items() if c > 1]
if dups:
    E(f"[unit] (재료,단위) 중복: {dups}")

# ── 5. 위험쌍 ─────────────────────────────────────────────
cp = load_yaml("confusable_pairs.yaml")
for pair in cp["pairs"]:
    for n in pair:
        if n not in nameset:
            E(f"[confusable] 없는 재료: {n} (쌍 {pair})")

# ── 6. 요리 계열 ──────────────────────────────────────────
ct = load_yaml("cuisine_taxonomy.yaml")
codes = [t["code"] for t in ct["taxonomy"]]
if len(codes) != len(set(codes)):
    E("[cuisine] code 중복")
fams = {t["family"] for t in ct["taxonomy"]}
for t in ct["taxonomy"]:
    if t["family"] not in fams:
        E(f"[cuisine] family 오류: {t['code']}")
for fam, ings in ct["rules_draft"]["by_signature_ingredients"].items():
    for n in ings:
        if n not in nameset:
            W(f"[cuisine] 시그니처 재료가 사전에 없음: {n} ({fam})")

# ── 7. 수식어 화이트리스트 ────────────────────────────────
mw = load_yaml("modifier_whitelist.yaml")
allmods = [m for k, v in mw.items() if k != "do_not_remove_examples" for m in v]
for m in allmods:
    for n in nameset:
        if n != m and n.startswith(m) and len(n) - len(m) <= 1:
            W(f"[modifier] '{m}' 제거 시 '{n}' 이 깨질 수 있음")

# ── 8. 소비기한 ───────────────────────────────────────────
# f_expiring(가중치 0.15)이 유저 입력 없이 동작하려면 전 재료에 값이 있어야 한다.
shelf = load_yaml("ingredient_shelf_life.yaml")
sdef = {x["path"]: x for x in shelf["defaults"]}
sovr = {x["name"]: x for x in shelf["overrides"]}
for n in sovr:
    if n not in set(names):
        E(f"소비기한 override 재료 없음: {n}")
for p_ in sdef:
    if p_ not in paths:
        E(f"소비기한 default 카테고리 없음: {p_}")


def _shelf(row):
    if row["name"] in sovr:
        return sovr[row["name"]]
    parts = row["category_path"].split(".")
    for i in range(len(parts), 0, -1):
        hit = sdef.get(".".join(parts[:i]))
        if hit:
            return hit
    return None


_unres = [r["name"] for r in ing if _shelf(r) is None]
if _unres:
    E(f"소비기한 미해소 {len(_unres)}건: {_unres[:5]}")

# 주의: 값이 전부 같으면 피처가 상수가 되어 랭킹에 기여하지 못한다
_days = sorted({_shelf(r)["days"] for r in ing if _shelf(r)})
if len(_days) < 6:
    E(f"소비기한 값 종류가 {len(_days)}종뿐 — f_expiring 이 사실상 상수가 된다")
_short = [r["name"] for r in ing if _shelf(r) and _shelf(r)["days"] <= 3]
if len(_short) < 20:
    W(f"3일 이하 재료가 {len(_short)}종뿐 — 임박 추천이 거의 발동하지 않는다")

_by = {}
for r in ing:
    _by[_shelf(r)["days"]] = _by.get(_shelf(r)["days"], 0) + 1
_SUMMARY_SHELF = (
    f"  소비기한 {len(_days)}종 · 3일 이하 {len(_short)}종 · "
    f"override {len(sovr)}종 (전 재료 해소 ✓)"
)

# ── 9. 구조 매칭 (P3) 회귀 검사 ───────────────────────────
# 4-4-1 에서 퍼지 매칭이 임계값 0.6 에 재현율 0% 였다. 구조 매칭이 그 자리를
# 대신하므로, confusable 이 자동확정으로 새는 순간 즉시 실패해야 한다.
import os as _os  # noqa: E402  # 바로 위에서 sys.path 를 세운 뒤라야 import 가 됩니다
import sys as _sys  # noqa: E402  # 바로 위에서 sys.path 를 세운 뒤라야 import 가 됩니다

_sys.path.insert(0, _os.path.dirname(D) or ".")
# 바로 위에서 sys.path 를 세운 뒤라야 아래 import 가 됩니다.
from features.recommend.ingest.head import HeadIndex  # noqa: E402  # sys.path 설정 이후

_wlf = load_yaml("modifier_whitelist.yaml")
_wl = {x for k, v in _wlf.items() if k != "do_not_remove_examples" for x in (v or [])}
_conf = [
    (x[0], x[1])
    for x in load_yaml("confusable_pairs.yaml")["pairs"]
    if isinstance(x, (list, tuple))
]
_H = HeadIndex(names)

_leak = [(a, b) for a, b in _conf if _H.relation(a, b, _wl) in ("same", "rule")]
if _leak:
    E(f"🔴 confusable 이 구조매칭을 통과한다 (자동확정되면 안 됨): {_leak}")

_gen = [
    (m + b, b)
    for m in sorted(_wl)
    for b in ("대파", "새우", "돼지고기", "양파", "두부", "닭고기", "표고버섯")
]
_gen = [(a, b) for a, b in _gen if a not in _H.names]
_fail = [(a, b) for a, b in _gen if _H.relation(a, b, _wl) not in ("same", "rule")]
if _fail:
    E(f"수식어 변형이 자동확정되지 않는다 {len(_fail)}건: {_fail[:5]}")

_SUMMARY_P3 = (
    f"  구조매칭 핵심어 {len(_H.heads)}종 · "
    f"confusable 차단 {len(_conf)}/{len(_conf)} · "
    f"수식어 변형 통과 {len(_gen)}/{len(_gen)}"
)

# ── 결과 ──────────────────────────────────────────────────
print(
    f"카테고리 {len(cat['categories'])} · 재료 {len(ing)} · alias {len(al)} · "
    f"단위환산 {len(uw)} · 위험쌍 {len(cp['pairs'])} · 요리계열 {len(codes)}"
)
print(_SUMMARY_SHELF)
print(_SUMMARY_P3)
print(
    f"  staple {sum(1 for r in ing if r['is_staple'] == 'true')} · "
    f"seasoning {sum(1 for r in ing if r['is_seasoning'] == 'true')}"
)
for w in warns:
    print(f"  WARN  {w}")
for e in errors:
    print(f"  ERROR {e}")
print(
    f"\n{'✅ 통과' if not errors else f'❌ 오류 {len(errors)}건'}"
    f"{f' (경고 {len(warns)}건)' if warns else ''}"
)
sys.exit(1 if errors else 0)
