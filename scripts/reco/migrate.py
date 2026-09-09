#!/usr/bin/env python3
"""seeds/ → PostgreSQL 적재.

    python3 db/migrate.py --dry-run    DB 없이 파싱·참조 해석만 검증
    python3 db/migrate.py              적재 (idempotent)
    python3 db/migrate.py --reset      시드 테이블 비우고 재적재
    python3 db/migrate.py --verify     적재 결과만 확인

적재 순서는 FK 의존 순서다 (설계 2-11). 바꾸면 FK 위반으로 전부 실패한다.

시드 파일이 SoT 다. DB 를 직접 수정하지 않는다 — 검수 UI 가 DB 를 쓰더라도
반드시 CSV 로 export 해서 커밋해야 재현성이 유지된다.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "seeds"

try:
    import yaml
except ImportError:
    sys.exit("pyyaml 필요:  make install TRACK=A|B|C")


# ─────────────────────────────────────────────────────────────────
# 로딩
# ─────────────────────────────────────────────────────────────────
def load_yaml(name: str):
    with open(SEEDS / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_csv(name: str) -> list[dict]:
    with open(SEEDS / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def as_bool(v: str) -> bool:
    return str(v).strip().lower() == "true"


def jamo(v):
    """한글 음절 → NFD 자모 분해. trgm 해상도를 올린다 (설계 4-4).
    자동 매칭용이 아니라 검수 큐 후보 제안·자동완성 전용이다."""
    return unicodedata.normalize("NFD", v) if v else None


def nullable(v):
    """빈 문자열은 NULL 이다. 0 이나 '' 로 채우지 않는다."""
    v = (v or "").strip()
    return v if v else None


# ─────────────────────────────────────────────────────────────────
# 적재 계획 — DB 없이 만들 수 있어야 한다 (--dry-run 의 근거)
# ─────────────────────────────────────────────────────────────────
class Plan:
    """시드를 읽어 삽입 가능한 형태로 변환하고 참조를 검증한다."""

    def __init__(self):
        self.errors: list[str] = []
        self.cat = load_yaml("ingredient_category.yaml")
        self.ing = load_csv("ingredient.csv")
        self.alias = load_csv("ingredient_alias.csv")
        self.unit = load_csv("ingredient_unit_weight.csv")
        self.cuisine = load_yaml("cuisine_taxonomy.yaml")
        self.shelf = load_yaml("ingredient_shelf_life.yaml")

        self.categories = self.cat["categories"]
        self.paths = {c["path"] for c in self.categories}
        self.ing_names = {r["name"] for r in self.ing}

        # 소비기한 해소 — 최장 접두 매칭. 전 재료가 값을 가져야 한다.
        self.shelf_def = {x["path"]: x for x in self.shelf["defaults"]}
        self.shelf_ovr = {x["name"]: x for x in self.shelf["overrides"]}
        self.shelf_of: dict[str, dict] = {}
        for r in self.ing:
            self.shelf_of[r["name"]] = self._resolve_shelf(r)

        self._check()

    def _resolve_shelf(self, row: dict) -> dict:
        """override(재료명) > default(카테고리 최장 접두). 없으면 {}."""
        if row["name"] in self.shelf_ovr:
            return self.shelf_ovr[row["name"]]
        parts = row["category_path"].split(".")
        for i in range(len(parts), 0, -1):
            hit = self.shelf_def.get(".".join(parts[:i]))
            if hit:
                return hit
        return {}

    def _err(self, m: str):
        self.errors.append(m)

    def _check(self):
        # 카테고리: 부모가 실재하는가
        for c in self.categories:
            p = c["path"]
            if "." in p and p.rsplit(".", 1)[0] not in self.paths:
                self._err(f"category 부모 없음: {p}")

        # 재료: 카테고리 참조
        for r in self.ing:
            if r["category_path"] not in self.paths:
                self._err(f"ingredient 카테고리 없음: {r['name']} → {r['category_path']}")

        # alias / unit: 재료 참조
        for r in self.alias:
            if r["ingredient_name"] not in self.ing_names:
                self._err(f"alias 재료 없음: {r['alias']} → {r['ingredient_name']}")
        for r in self.unit:
            if r["ingredient_name"] not in self.ing_names:
                self._err(f"unit_weight 재료 없음: {r['ingredient_name']}")

        # 소비기한: override 가 실재하는 재료를 가리키는가 · 전 재료가 해소되는가
        for n in self.shelf_ovr:
            if n not in self.ing_names:
                self._err(f"shelf_life override 재료 없음: {n}")
        for p in self.shelf_def:
            if p not in self.paths:
                self._err(f"shelf_life default 카테고리 없음: {p}")
        unresolved = [n for n, v in self.shelf_of.items() if not v]
        if unresolved:
            self._err(f"소비기한 미해소 {len(unresolved)}건: {unresolved[:5]}")

    # ── 삽입 순서대로 (테이블명, 행수) ──────────────────────────
    def summary(self) -> list[tuple[str, int]]:
        return [
            ("ingredient_category", len(self.categories)),
            ("ingredient", len(self.ing)),
            ("ingredient_alias", len(self.alias)),
            ("ingredient_unit_weight", len(self.unit)),
            ("cuisine_taxonomy", len(self.cuisine["taxonomy"])),
        ]

    def stats(self) -> dict:
        staple = [r["name"] for r in self.ing if as_bool(r["is_staple"])]
        seasoning = sum(1 for r in self.ing if as_bool(r["is_seasoning"]))
        allergens: dict[str, int] = {}
        for r in self.ing:
            g = nullable(r["allergen_group"])
            if g:
                allergens[g] = allergens.get(g, 0) + 1
        return {
            "staple": staple,
            "seasoning": seasoning,
            "allergens": allergens,
            "unit_targets": len({r["ingredient_name"] for r in self.unit}),
            "shelf_short": sum(1 for v in self.shelf_of.values() if v.get("days", 999) <= 3),
            "shelf_override": len(self.shelf_ovr),
            "max_depth": max(c["depth"] for c in self.categories),
        }


# ─────────────────────────────────────────────────────────────────
# 적재
# ─────────────────────────────────────────────────────────────────
SEED_TABLES = [  # TRUNCATE 순서 (역의존)
    "ingredient_unit_weight",
    "ingredient_alias",
    "ingredient_substitute",
    "ingredient",
    "ingredient_category",
    "cuisine_taxonomy",
]


def connect(url: str):
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg 필요:  uv sync")
    conn = psycopg.connect(url)
    # URL 에 options 가 없어도 항상 reco 스키마를 보게 한다.
    # 공유 DB 에서 public 의 동명 테이블을 건드리는 사고를 막는 안전장치다.
    with conn.cursor() as c:
        c.execute("SET search_path TO reco, public")
    conn.commit()
    return conn


#: 주의: CASCADE 로 같이 날아가는 테이블. 여기 행이 있으면 reset 을 막는다.
#: 3인 병렬에서 누가 무심코 `make seed-reset` 을 치면 남의 하루가 사라진다.
_CASCADE_VICTIMS = [
    ("recipe_ingredient", "A 트랙의 정규화 결과 (46,353건 배치 = 10분)"),
    ("pantry_item", "유저 냉장고"),
    ("user_allergy", "유저 알러지 — 온보딩 재수집 불가"),
    ("user_ingredient_pref", "유저 기피 재료 — 온보딩 재수집 불가"),
    ("normalization_queue", "검수 큐 — 사람이 판정한 결과"),
]


def do_reset(cur, force: bool = False):
    """시드 테이블을 비운다.

    🔴 `TRUNCATE ... CASCADE` 라 `ingredient` 를 참조하는 **운영·유저 테이블이
       같이 날아간다.** 그래서 행이 있으면 멈춘다 — 3인 병렬에서 이것 하나로
       남의 작업이 사라질 수 있다.
    """
    blocked = []
    for tbl, why in _CASCADE_VICTIMS:
        cur.execute(f"SELECT count(*) FROM {tbl}")
        n = cur.fetchone()[0]
        if n:
            blocked.append(f"    {tbl:22} {n:>9,}행   {why}")
    if blocked and not force:
        print("\n  🔴 seed-reset 을 멈춥니다 — CASCADE 로 아래가 같이 지워집니다.\n")
        print("\n".join(blocked))
        print("\n  정말 지우려면:  make seed-reset FORCE=1")
        print("  시드만 갱신하려면:  make seed   (덮어쓰기, idempotent)\n")
        raise SystemExit(1)
    cur.execute(f"TRUNCATE {', '.join(SEED_TABLES)} RESTART IDENTITY CASCADE")
    print("  reset: 시드 테이블 비움 (CASCADE)")


def load(cur, plan: Plan):
    # psycopg3 의 executemany 는 파이프라인으로 묶어 보낸다.
    # psycopg2 의 execute_batch(page_size=...) 가 하던 일이 기본 동작이다.

    # ── 1. 카테고리 : depth 오름차순으로 넣고 path 로 부모를 해석 ──
    cat_id: dict[str, int] = {}
    for depth in range(plan.stats()["max_depth"] + 1):
        for c in (x for x in plan.categories if x["depth"] == depth):
            parent = cat_id.get(c["path"].rsplit(".", 1)[0]) if "." in c["path"] else None
            cur.execute(
                """INSERT INTO ingredient_category (name, parent_id, depth, path)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (path) DO UPDATE
                     SET name = EXCLUDED.name, parent_id = EXCLUDED.parent_id,
                         depth = EXCLUDED.depth
                   RETURNING id""",
                (c["name"], parent, c["depth"], c["path"]),
            )
            cat_id[c["path"]] = cur.fetchone()[0]
    print(f"  ingredient_category      {len(cat_id):>5}")

    # ── 2. 재료 ────────────────────────────────────────────────
    ing_id: dict[str, int] = {}
    for r in plan.ing:
        cur.execute(
            """INSERT INTO ingredient
                 (name, category_id, is_staple, is_seasoning, allergen_group, note, name_jamo,
                  shelf_life_days, storage_default)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE
                 SET category_id    = EXCLUDED.category_id,
                     is_staple      = EXCLUDED.is_staple,
                     is_seasoning   = EXCLUDED.is_seasoning,
                     allergen_group = EXCLUDED.allergen_group,
                     note           = EXCLUDED.note,
                     name_jamo      = EXCLUDED.name_jamo,
                     shelf_life_days = EXCLUDED.shelf_life_days,
                     storage_default = EXCLUDED.storage_default
               RETURNING id""",
            (r["name"], cat_id[r["category_path"]], as_bool(r["is_staple"]),
             as_bool(r["is_seasoning"]), nullable(r["allergen_group"]),
             nullable(r.get("note")), jamo(r["name"]),
             plan.shelf_of[r["name"]].get("days"),
             plan.shelf_of[r["name"]].get("storage")),
        )
        ing_id[r["name"]] = cur.fetchone()[0]
    print(f"  ingredient               {len(ing_id):>5}")

    # ── 3. alias ───────────────────────────────────────────────
    cur.executemany(
        """INSERT INTO ingredient_alias (alias, ingredient_id, source, confidence, note, alias_jamo)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (alias, ingredient_id) DO UPDATE
             SET source = EXCLUDED.source, confidence = EXCLUDED.confidence,
                 note = EXCLUDED.note, alias_jamo = EXCLUDED.alias_jamo""",
        [(r["alias"], ing_id[r["ingredient_name"]], r["source"],
          float(r["confidence"]), nullable(r.get("note")), jamo(r["alias"]))
         for r in plan.alias])
    print(f"  ingredient_alias         {len(plan.alias):>5}")

    # ── 4. 단위 환산 ───────────────────────────────────────────
    cur.executemany(
        """INSERT INTO ingredient_unit_weight
             (ingredient_id, unit, grams_per_unit, source, confidence, note)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (ingredient_id, unit) DO UPDATE
             SET grams_per_unit = EXCLUDED.grams_per_unit,
                 source = EXCLUDED.source, confidence = EXCLUDED.confidence,
                 note = EXCLUDED.note""",
        [(ing_id[r["ingredient_name"]], r["unit"], float(r["grams_per_unit"]),
          r["source"], float(r["confidence"]), nullable(r.get("note")))
         for r in plan.unit])
    print(f"  ingredient_unit_weight   {len(plan.unit):>5}")

    # ── 5. 요리 계열 ───────────────────────────────────────────
    cur.executemany(
        """INSERT INTO cuisine_taxonomy (code, family, label_ko, label_en, active, sort_order)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (code) DO UPDATE
             SET family = EXCLUDED.family, label_ko = EXCLUDED.label_ko,
                 label_en = EXCLUDED.label_en, active = EXCLUDED.active,
                 sort_order = EXCLUDED.sort_order""",
        [(t["code"], t["family"], t["label_ko"], t["label_en"],
          bool(t["active"]), int(t["sort_order"])) for t in plan.cuisine["taxonomy"]])
    print(f"  cuisine_taxonomy         {len(plan.cuisine['taxonomy']):>5}")


def verify(cur):
    """적재 후 구조가 실제로 동작하는지 확인한다. 행 수 세기가 아니라 기능 확인이다."""
    ok = True

    def check(label, sql, want=None, gt=None):
        nonlocal ok
        cur.execute(sql)
        got = cur.fetchone()[0]
        good = (got == want) if want is not None else (got > gt)
        print(f"  {'✓' if good else '✗'} {label}: {got}")
        ok = ok and good

    cur.execute("SELECT current_schema()")
    print(f"  · 대상 스키마: {cur.fetchone()[0]}")
    check("재료 수", "SELECT count(*) FROM ingredient", gt=400)
    check("staple 수", "SELECT count(*) FROM ingredient WHERE is_staple", gt=20)
    check("고아 재료(카테고리 없음)", "SELECT count(*) FROM ingredient WHERE category_id IS NULL", want=0)
    check("고아 카테고리(부모 유실)",
          "SELECT count(*) FROM ingredient_category c WHERE c.depth > 0 AND c.parent_id IS NULL", want=0)

    # ltree 전개가 실제로 되는가 — 알러지 하드 컷의 생명줄
    check("자모 분해 적재", "SELECT count(*) FROM ingredient WHERE name_jamo IS NOT NULL", gt=400)
    check("소비기한 미적재", "SELECT count(*) FROM ingredient WHERE shelf_life_days IS NULL", want=0)
    check("ltree 견과류 전개",
          """SELECT count(*) FROM ingredient i
             JOIN ingredient_category c ON c.id = i.category_id
             WHERE c.path <@ 'agri.nutseed'::ltree""", gt=10)

    # staple 함수가 결정 2 를 실제로 적용하는가
    cur.execute("SELECT cardinality(user_pantry_ids(-1))")
    n = cur.fetchone()[0]
    print(f"  {'✓' if n > 20 else '✗'} user_pantry_ids(없는유저) = staple {n}종")
    ok = ok and n > 20

    return ok


# ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 없이 파싱·참조 검증만")
    ap.add_argument("--reset", action="store_true", help="시드 테이블 비우고 재적재")
    ap.add_argument("--verify", action="store_true", help="적재 결과만 확인")
    ap.add_argument("--url", default=os.environ.get(
        "DATABASE_URL", "postgresql://reco:reco@localhost:5432/recodb"))
    ap.add_argument("--schema", default="reco", help="대상 스키마 (기본 reco)")
    a = ap.parse_args()

    if a.verify:
        conn = connect(a.url)
        with conn, conn.cursor() as cur:
            sys.exit(0 if verify(cur) else 1)

    plan = Plan()

    print(f"seeds/ 읽음 — {SEEDS}")
    for t, n in plan.summary():
        print(f"  {t:<24} {n:>5}")
    st = plan.stats()
    print(f"\n  staple {len(st['staple'])}종 · seasoning {st['seasoning']}종 "
          f"· 단위환산 {st['unit_targets']}종 · 소비기한 3일↓ {st['shelf_short']}종 · 계층 깊이 {st['max_depth']}")
    print(f"  알러지 그룹: {', '.join(f'{k}={v}' for k, v in sorted(st['allergens'].items()))}")

    if plan.errors:
        print(f"\n❌ 참조 오류 {len(plan.errors)}건")
        for e in plan.errors[:20]:
            print(f"  {e}")
        sys.exit(1)

    if a.dry_run:
        print("\n✅ dry-run 통과 — 파싱·참조 해석 이상 없음 (DB 미접속)")
        return

    conn = connect(a.url)
    with conn, conn.cursor() as cur:
        print(f"\n적재 → {a.url.rsplit('@', 1)[-1]}")
        if a.reset:
            do_reset(cur, force=os.environ.get('FORCE') == '1')
        load(cur, plan)
        print()
        if not verify(cur):
            conn.rollback()
            sys.exit("❌ 검증 실패 — 롤백")
    print("\n✅ 적재 완료")


if __name__ == "__main__":
    main()
