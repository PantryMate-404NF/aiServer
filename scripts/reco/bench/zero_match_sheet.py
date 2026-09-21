"""재료가 하나도 안 붙은 레시피의 검수 시트.

    .venv/bin/python scripts/reco/bench/zero_match_sheet.py

## 보통 검수 시트와 무엇이 다른가

`review_sheet.py` 는 **자주 나오는 표현**을 빈도순으로 냅니다. 이 시트는
**레시피를 통째로 죽이는 표현**을 냅니다.

원본에 재료가 적혀 있는데 하나도 못 붙인 레시피가 있습니다. 그 레시피는
`recipe_feature` 에 재료 집합이 비어 맛 6축이 전부 0 이 되고, 조회의 재료 조건에
한 번도 걸리지 않아 **사실상 없는 레시피**가 됩니다.

그래서 순서가 빈도가 아니라 **"이 표현 하나를 고치면 살아나는 레시피 수"** 입니다.
자주 나오지 않아도 그 레시피의 유일한 재료면 값이 큽니다.

## 검수자가 하는 일

`결정` 칸에 셋 중 하나를 적습니다. 비우면 판단 보류입니다.

    재료명   기존 재료에 매핑합니다.  예) 황치즈 -> 체다치즈
    X       재료가 아닙니다 (도구·용기·소모품)
    NEW     사전에 없는 새 재료입니다

## 후보를 믿지 마십시오

문자 유사도라 뜻이 아니라 글자가 비슷한 것이 올라옵니다. 참고일 뿐입니다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from features.recommend.ingest.match import Dictionary, match  # noqa: E402
from features.recommend.ingest.preprocess import non_ingredient_kind  # noqa: E402
from infra.db import cursor  # noqa: E402

OUT = ROOT / "review" / "zero_match_sheet.csv"

_SOURCE_SQL = """
SELECT id, title, raw_json->'ingredient_names'
FROM   recipe
WHERE  status = 'raw'
  AND  jsonb_array_length(COALESCE(raw_json->'ingredient_names', '[]'::jsonb)) > 0
ORDER BY id
"""

#: 전체 코퍼스에서의 등장 횟수. 드문 표현인지 흔한 표현인지 가릅니다.
_GLOBAL_SQL = """
SELECT x, count(*)
FROM  (SELECT jsonb_array_elements_text(raw_json->'ingredient_names') x FROM recipe) t
GROUP BY 1
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    with cursor() as cur:
        cur.execute(_SOURCE_SQL)
        recipes = [(int(i), str(t), list(names or [])) for i, t, names in cur.fetchall()]
        cur.execute(_GLOBAL_SQL)
        overall = {str(x): int(n) for x, n in cur.fetchall()}

    # 표현 하나가 몇 개의 레시피를 막고 있는가. 같은 레시피 안의 중복은 한 번으로 셉니다.
    blocking: Counter[str] = Counter()
    example: dict[str, str] = {}
    for _, title, names in recipes:
        for name in dict.fromkeys(names):
            blocking[name] += 1
            example.setdefault(name, title)

    dictionary = Dictionary.from_seeds()

    # 재료가 하나뿐인 레시피는 그 표현만 고치면 바로 살아납니다. 먼저 보여 줍니다.
    only_one: defaultdict[str, int] = defaultdict(int)
    for _, _, names in recipes:
        uniq = list(dict.fromkeys(names))
        if len(uniq) == 1:
            only_one[uniq[0]] += 1

    rows = sorted(blocking.items(), key=lambda kv: (-only_one[kv[0]], -kv[1], kv[0]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "순위",
                "단독복구",
                "막는레시피",
                "전체빈도",
                "표현",
                "결정",
                "후보1",
                "후보2",
                "후보3",
                "비고",
                "레시피예시",
            ]
        )
        for rank, (text, n_block) in enumerate(rows, 1):
            result = match(text, dictionary)
            suggested = [n for n, _, _ in result.suggested[:3]]
            suggested += [""] * (3 - len(suggested))

            kind = non_ingredient_kind(text)
            decided = "X" if kind else ""
            if kind:
                note = f"이미 {kind} 로 분류됨"
            elif result.blocked_by:
                note = f"구조 차단: {result.blocked_by}"
            elif not suggested[0]:
                note = "닮은 이름 없음 — NEW 이거나 재료가 아닐 가능성이 큽니다"
            else:
                note = ""

            writer.writerow(
                [
                    rank,
                    only_one[text],
                    n_block,
                    overall.get(text, 0),
                    text,
                    decided,
                    *suggested,
                    note,
                    example[text][:40],
                ]
            )

    n_single = sum(1 for _, _, names in recipes if len(dict.fromkeys(names)) == 1)
    print(f"  레시피 {len(recipes)}건 · 표현 {len(rows)}종")
    print(f"  그중 재료가 하나뿐이라 표현 한 개로 살아나는 레시피: {n_single}건")
    print(f"  → {args.out}")


if __name__ == "__main__":
    main()
