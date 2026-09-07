"""검수 시트 → 시드 반영.

    .venv/bin/python bench/review_apply.py            # 미리보기만
    .venv/bin/python bench/review_apply.py --write    # 실제로 쓴다

`결정` 열을 읽어 세 곳으로 나눠 넣는다:

    재료명  →  seeds/ingredient_alias.csv     (별칭 추가)
    X      →  seeds/non_ingredient.yaml      (도구·용기)
    NEW    →  bench/out/new_ingredients.tsv  (사람이 카테고리를 정해야 한다)

🔴 `NEW` 는 자동으로 `ingredient.csv` 에 넣지 않는다.
   재료를 등록하려면 `category_path`·`is_staple`·`allergen_group` 을 정해야 하는데
   그건 검수 시트 한 칸으로 못 정한다. 별도 목록으로 빼서 다시 본다.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, ".")
from features.recommend.ingest.match import Dictionary  # noqa: E402

SHEET = Path("bench/out/review_sheet.tsv")
ALIAS = Path("seeds/ingredient_alias.csv")
NONING = Path("seeds/non_ingredient.yaml")
NEWOUT = Path("bench/out/new_ingredients.tsv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--sheet", type=Path, default=SHEET)
    a = ap.parse_args()

    if not a.sheet.exists():
        sys.exit(f"시트가 없습니다: {a.sheet}\n  먼저:  .venv/bin/python bench/review_sheet.py")

    d = Dictionary.from_seeds()
    known = {r.strip() for r in Path("seeds/ingredient.csv").read_text(
        encoding="utf-8").splitlines()[1:] if r.strip()}
    known = {r.split(",")[0] for r in known}

    alias_rows, tools, news, bad, blank = [], [], [], [], 0
    with open(a.sheet, encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]   # 안내 주석 건너뛰기
    import io
    with io.StringIO("".join(lines)) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            term, dec = row["표현"].strip(), row["결정"].strip()
            if not dec:
                blank += 1
            elif dec == "X":
                tools.append(term)
            elif dec.upper() == "NEW":
                news.append((row["빈도"], term))
            elif dec in known:
                alias_rows.append((term, dec))
            else:
                bad.append((term, dec))     # 사전에 없는 재료명을 적었다

    print(f"  시트 {a.sheet}")
    print(f"    별칭 추가 {len(alias_rows):>4}종 · 도구 {len(tools):>4}종 · "
          f"신규 후보 {len(news):>4}종 · 미판정 {blank:>4}종")
    if bad:
        print(f"\n  🔴 사전에 없는 재료명을 적었습니다 ({len(bad)}건) — 오타이거나 NEW 여야 합니다:")
        for t, dc in bad[:10]:
            print(f"     {t}  →  {dc!r}")
        if not a.write:
            print("     고친 뒤 다시 돌리세요.")

    if not a.write:
        print("\n  (미리보기입니다. 실제로 쓰려면 --write)")
        return
    if bad:
        sys.exit("\n  🔴 오류를 고친 뒤 --write 하세요.")

    if alias_rows:
        with open(ALIAS, "a", encoding="utf-8") as w:
            for term, dec in alias_rows:
                w.write(f"{term},{dec},review\n")
        print(f"  ✅ {ALIAS} 에 {len(alias_rows)}행 추가")

    if tools:
        txt = NONING.read_text(encoding="utf-8").rstrip()
        txt += "\n\n# ── 검수로 추가 (bench/review_apply.py) ──\nreviewed:\n"
        txt += "".join(f"  - {t}\n" for t in tools)
        NONING.write_text(txt + "", encoding="utf-8")
        print(f"  ✅ {NONING} 에 {len(tools)}종 추가")

    if news:
        NEWOUT.parent.mkdir(exist_ok=True)
        with open(NEWOUT, "w", encoding="utf-8") as w:
            w.write("빈도\t표현\tcategory_path\tis_staple\tis_seasoning\tallergen_group\n")
            for c, t in news:
                w.write(f"{c}\t{t}\t\t\t\t\n")
        print(f"  ✅ {NEWOUT} 에 {len(news)}종 — 🔴 카테고리를 채워야 등록됩니다")

    print("\n  다음:  make validate  &&  .venv/bin/python scripts/reco/coverage.py "
          "raw_data/recipe_raw_data.jsonl --limit 5000")


if __name__ == "__main__":
    main()
