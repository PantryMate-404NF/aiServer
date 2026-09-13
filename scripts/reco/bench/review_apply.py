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
from features.recommend.ingest.match import Dictionary

#: 저장소 루트 기준. 스크립트를 어디서 돌리든 같은 자리에 쓴다 —
#: 상대경로 "bench/out" 은 scripts/reco 에서 돌릴 때만 맞고, 루트에서
#: 돌리면 9분을 계산한 뒤 마지막 쓰기에서 FileNotFoundError 로 죽는다.
ROOT = Path(__file__).resolve().parents[3]
BENCH_OUT = ROOT / "scripts" / "reco" / "bench" / "out"

SHEET = BENCH_OUT / "review_sheet.tsv"
ALIAS = Path("seeds/ingredient_alias.csv")
NONING = Path("seeds/non_ingredient.yaml")
NEWOUT = BENCH_OUT / "new_ingredients.tsv"


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
    if not lines:
        sys.exit(f"시트가 비어 있습니다: {a.sheet}")

    # 구분자를 머리줄에서 알아본다. 구글 스프레드시트에서 내보내면 쉼표이고,
    # bench/review_sheet.py 가 만든 원본은 탭이다. 둘 다 받는다 —
    # 사람이 어느 도구로 채웠는지에 따라 도구를 갈아타게 만들면,
    # 잘못된 구분자로 읽어 전 행이 한 덩어리가 되고 **조용히 0건 반영**된다.
    delim = "\t" if lines[0].count("\t") >= lines[0].count(",") else ","
    import io
    with io.StringIO("".join(lines)) as f:
        rows = list(csv.DictReader(f, delimiter=delim))
    need = {"표현", "결정"}
    if not rows or not need <= set(rows[0]):
        sys.exit(
            f"시트 머리줄을 못 읽었습니다 (구분자 {delim!r} 로 시도)\n"
            f"  읽은 열: {sorted(rows[0]) if rows else '(없음)'}\n"
            f"  필요한 열: 표현 · 결정"
        )
    print(f"  구분자 {'탭' if delim == chr(9) else '쉼표'} · {len(rows)}행")

    for row in rows:
            term, dec = (row["표현"] or "").strip(), (row["결정"] or "").strip()
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
        NEWOUT.parent.mkdir(parents=True, exist_ok=True)
        with open(NEWOUT, "w", encoding="utf-8") as w:
            w.write("빈도\t표현\tcategory_path\tis_staple\tis_seasoning\tallergen_group\n")
            for c, t in news:
                w.write(f"{c}\t{t}\t\t\t\t\n")
        print(f"  ✅ {NEWOUT} 에 {len(news)}종 — 🔴 카테고리를 채워야 등록됩니다")

    print("\n  다음:  make validate  &&  .venv/bin/python scripts/reco/coverage.py "
          "raw_data/recipe_raw_data.jsonl --limit 5000")


if __name__ == "__main__":
    main()
