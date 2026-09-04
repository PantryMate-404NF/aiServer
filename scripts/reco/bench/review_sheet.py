"""검수 시트 생성 — 스프레드시트에서 바로 판단할 수 있는 TSV.

    .venv/bin/python bench/review_sheet.py --top 300

검수 큐 화면(S5)이 아직 없으므로 시트로 대신한다. 50~300종이면 시트가 더 빠르다.

## 검수자가 하는 일

`결정` 열에 셋 중 하나를 적는다:

    재료명        기존 재료에 매핑한다.  예) 매실액 → 매실청
    X            재료가 아니다 (도구·용기·소모품)
    NEW          사전에 없는 새 재료다. 새로 등록한다

비우면 "판단 보류" 다. 나중에 다시 본다.

## 🔴 후보를 믿지 마라

문자 유사도라 **뜻이 아니라 글자가 비슷한 것**이 올라온다 —
`물`→`물엿`, `깨소금`→`소고기`. 그래서 자동 확정을 금지했다 (설계 4-4-1).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")
from features.recommend.ingest.match import Dictionary, match  # noqa: E402
from features.recommend.ingest.preprocess import non_ingredient_kind  # noqa: E402

OUT = Path("bench/out/review_sheet.tsv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--src", type=Path, default=Path("bench/out/unmatched.tsv"))
    a = ap.parse_args()

    rows = []
    with open(a.src, encoding="utf-8") as f:
        next(f)
        for line in f:
            c, t = line.rstrip("\n").split("\t", 1)
            rows.append((int(c), t))

    d = Dictionary.from_seeds()
    total_un = sum(c for c, _ in rows)
    cum = 0

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as w:
        w.write("# 결정 칸에 셋 중 하나를 적으세요 — 후보는 참고일 뿐 고를 필요 없습니다\n")
        w.write("#   재료명   우리 사전(536종)에 있는 이름. 예) 매실액 → 매실청\n")
        w.write("#   X        재료가 아님 (도구·용기·소모품)\n")
        w.write("#   NEW      사전에 없는 새 재료. 예) 물 · 밥 · 육수\n")
        w.write("# 양념/식재료 같은 분류는 적지 않습니다 — 역할은 P4 가 자동 판정합니다\n")
        w.write("# 쓸 수 있는 이름 목록:  bench/out/dictionary.tsv\n")
        w.write("순위\t빈도\t누적%\t표현\t결정\t후보1\t후보2\t후보3\t비고\n")
        for i, (c, t) in enumerate(rows[:a.top], 1):
            cum += c
            r = match(t, d)
            sug = [n for n, _, _ in r.suggested[:3]]
            sug += [""] * (3 - len(sug))
            # 이미 도구 목록에 있으면 미리 채워 준다
            note = ""
            k = non_ingredient_kind(t)
            pre = "X" if k else ""
            if k:
                note = f"이미 {k} 로 분류됨"
            elif r.blocked_by:
                note = f"구조 차단: {r.blocked_by}"
            w.write(f"{i}\t{c}\t{100*cum/total_un:.1f}\t{t}\t{pre}\t"
                    f"{sug[0]}\t{sug[1]}\t{sug[2]}\t{note}\n")

    # 검수자가 "쓸 수 있는 이름" 을 찾아볼 수 있게 사전을 함께 낸다
    import csv as _csv
    dic = Path("bench/out/dictionary.tsv")
    with open("seeds/ingredient.csv", encoding="utf-8") as f, \
         open(dic, "w", encoding="utf-8") as w:
        w.write("재료명\t카테고리\t기본양념\n")
        for r in sorted(_csv.DictReader(f), key=lambda x: x["name"]):
            w.write(f"{r['name']}\t{r['category_path']}\t{r['is_staple']}\n")

    print(f"  → {OUT}  ({a.top}종)")
    print(f"  → {dic}  (쓸 수 있는 재료 이름 536종)")
    print(f"     상위 {a.top}종이 미매칭의 {100*cum/total_un:.1f}% 를 덮는다")
    print()
    print("  스프레드시트로 열어 `결정` 열만 채우세요:")
    print("     재료명  기존 재료에 매핑   ·   X  재료 아님   ·   NEW  새 재료")
    print()
    print("  다 하면:  .venv/bin/python bench/review_apply.py")


if __name__ == "__main__":
    main()
