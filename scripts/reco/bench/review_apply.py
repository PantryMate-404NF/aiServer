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

## 못 읽은 행이 있어도 나머지는 반영한다

사전에 없는 이름을 적은 행(오타이거나 아직 등록 안 된 재료)은 **건너뛰고
`review/review_bad.tsv` 로 뺀다.** 예전에는 한 행만 걸려도 `sys.exit` 해서
멀쩡한 별칭까지 통째로 0건 반영이었다 — 800행을 채워도 아무것도 안 들어갔다.

빈 칸을 건너뛰는 것과 같은 취급이다. 둘 다 "아직 판정 안 된 것" 이지 오류가 아니다.
대신 건수와 파일 경로를 크게 찍는다 — 조용히 버리면 채운 사람이 알 수 없다.

## 두 번 나눠 반영해도 안전하다

별칭은 이미 있는 행을 건너뛰고, 도구는 `reviewed:` 블록에 합치며,
신규 후보 목록은 사람이 채워 둔 칸을 보존한다. 예전에는 셋 다 깨졌다.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

#: 저장소 루트 기준. 스크립트를 어디서 돌리든 같은 자리에 쓴다 —
#: 상대경로 "bench/out" 은 scripts/reco 에서 돌릴 때만 맞고, 루트에서
#: 돌리면 9분을 계산한 뒤 마지막 쓰기에서 FileNotFoundError 로 죽는다.
ROOT = Path(__file__).resolve().parents[3]
BENCH_OUT = ROOT / "scripts" / "reco" / "bench" / "out"

#: 검수 작업물은 저장소에 올리지 않는다 (review/README.md). bench/out 은
#: 문서가 인용하는 수치의 기준선이라 커밋하지만, 사람이 채우는 시트는 다르다.
REVIEW_DIR = ROOT / "review"
SHEET = REVIEW_DIR / "review_sheet.tsv"
ALIAS = Path("seeds/ingredient_alias.csv")
NONING = Path("seeds/non_ingredient.yaml")
NEWOUT = BENCH_OUT / "new_ingredients.tsv"

#: 사전에 없는 이름을 적은 행. 반영을 막지 않고 여기로 뺀다.
BADOUT = REVIEW_DIR / "review_bad.tsv"

#: 사람이 판단해 붙인 별칭의 신뢰도. 시드 작성자가 0.50~0.98 로 쓴 것과 같은 칸이다.
#: 주의: match.py 는 이 값을 읽지 않는다 — 매칭은 적중이면 무조건 확정이다.
#:    검수 대기열 표시용이므로, 낮게 적어도 잘못된 매핑을 막아 주지는 않는다.
REVIEW_CONFIDENCE = "0.95"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--sheet", type=Path, default=SHEET)
    a = ap.parse_args()

    if not a.sheet.exists():
        sys.exit(f"시트가 없습니다: {a.sheet}\n  먼저:  .venv/bin/python bench/review_sheet.py")

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
                bad.append((term, dec, row["빈도"]))   # 사전에 없는 재료명을 적었다

    print(f"  시트 {a.sheet}")
    print(f"    별칭 추가 {len(alias_rows):>4}종 · 도구 {len(tools):>4}종 · "
          f"신규 후보 {len(news):>4}종 · 미판정 {blank:>4}종")
    if bad:
        # 주의: 여기서 멈추지 않는다. 한 행만 걸려도 sys.exit 하던 탓에
        #    800행을 채워도 멀쩡한 별칭 234행까지 0건 반영이었다.
        #    빈 칸과 같은 취급 — 아직 판정 안 된 것이지 오류가 아니다.
        print(f"\n  사전에 없는 재료명 {len(bad)}건 — 건너뜁니다 (오타이거나 아직 미등록):")
        for t, dc, _f in bad[:10]:
            print(f"     {t}  →  {dc!r}")
        if len(bad) > 10:
            print(f"     … 외 {len(bad) - 10}건")

    if not a.write:
        print("\n  (미리보기입니다. 실제로 쓰려면 --write)")
        return

    if alias_rows:
        # 머리줄이 5열(alias,ingredient_name,source,confidence,note)이다.
        # 3필드만 쓰면 confidence 가 None 이 되어 seeds/validate.py 의
        # float(r["confidence"]) 가 TypeError 로 죽는다 — 바로 다음 단계다.
        existing = {
            ln.split(",")[0]: ln.split(",")[1]
            for ln in ALIAS.read_text(encoding="utf-8").splitlines()[1:]
            if ln.strip()
        }
        fresh = [(t, dc) for t, dc in alias_rows if t not in existing]
        dup = [(t, dc) for t, dc in alias_rows if existing.get(t) == dc]
        clash = [(t, dc) for t, dc in alias_rows if t in existing and existing[t] != dc]
        if clash:
            # validate.py 가 '충돌' 로 잡을 것을 미리 보여 준다. 쓰지 않는다.
            print(f"  🔴 이미 다른 재료를 가리키는 별칭 {len(clash)}건 — 건너뜁니다:")
            for t, dc in clash[:5]:
                print(f"     {t}  기존 {existing[t]}  ≠  시트 {dc}")
        if fresh:
            with open(ALIAS, "a", encoding="utf-8") as w:
                for term, dec in fresh:
                    w.write(f"{term},{dec},review,{REVIEW_CONFIDENCE},검수\n")
        print(
            f"  ✅ {ALIAS} 에 {len(fresh)}행 추가"
            + (f" (이미 있던 {len(dup)}행은 건너뜀)" if dup else "")
        )

    if tools:
        # 주의: 매번 `reviewed:` 키를 새로 붙이면 같은 키가 두 번 생기고
        #    PyYAML 이 뒤엣것만 남긴다 — 1회차에 넣은 도구가 통째로 사라진다.
        #    에러도 경고도 없다. 블록이 있으면 그 안에 합친다.
        lines = NONING.read_text(encoding="utf-8").rstrip().splitlines()
        try:
            head = lines.index("reviewed:")
        except ValueError:
            head = None

        if head is None:
            body = lines + ["", "# ── 검수로 추가 (bench/review_apply.py) ──", "reviewed:"]
            have: set[str] = set()
            at = len(body)
        else:
            at = head + 1
            while at < len(lines) and lines[at].startswith("  - "):
                at += 1
            have = {ln[4:].strip() for ln in lines[head + 1 : at]}
            body = lines

        fresh = [t for t in dict.fromkeys(tools) if t not in have]
        NONING.write_text(
            "\n".join(body[:at] + [f"  - {t}" for t in fresh] + body[at:]) + "\n",
            encoding="utf-8",
        )
        print(
            f"  ✅ {NONING} 에 {len(fresh)}종 추가"
            + (f" (이미 있던 {len(tools) - len(fresh)}종은 건너뜀)" if len(fresh) != len(tools) else "")
        )

    if news:
        # 주의: "w" 로 덮어쓰면 1회차에 사람이 채워 둔 category_path·is_seasoning 이
        #    2회차 반영에서 통째로 날아간다. 표현을 키로 합치고 채운 칸은 남긴다.
        NEWOUT.parent.mkdir(parents=True, exist_ok=True)
        head = "빈도\t표현\tcategory_path\tis_staple\tis_seasoning\tallergen_group"
        kept: dict[str, list[str]] = {}
        if NEWOUT.exists():
            for ln in NEWOUT.read_text(encoding="utf-8").splitlines()[1:]:
                if not ln.strip():
                    continue
                col = (ln.split("\t") + [""] * 6)[:6]
                kept[col[1]] = col
        for c, t in news:
            if t in kept:
                kept[t][0] = str(c)          # 빈도만 갱신, 채운 칸은 그대로
            else:
                kept[t] = [str(c), t, "", "", "", ""]
        rows = sorted(kept.values(), key=lambda r: -int(r[0] or 0))
        NEWOUT.write_text(
            head + "\n" + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8"
        )
        filled = sum(1 for r in rows if r[2].strip())
        print(
            f"  ✅ {NEWOUT} 에 {len(rows)}종 (이번 {len(news)}종) — "
            f"🔴 카테고리를 채워야 등록됩니다 (채움 {filled}종)"
        )

    if bad:
        BADOUT.write_text(
            "빈도\t표현\t적은값\n" + "".join(f"{f}\t{t}\t{dc}\n" for t, dc, f in bad),
            encoding="utf-8",
        )
        print(f"  → {BADOUT} 에 미해결 {len(bad)}건 (반영은 막지 않습니다)")

    print("\n  다음:  make validate  &&  .venv/bin/python scripts/reco/coverage.py "
          "raw_data/recipe_raw_data.jsonl --limit 5000")


if __name__ == "__main__":
    main()
