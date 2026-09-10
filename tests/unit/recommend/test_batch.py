#!/usr/bin/env python3
"""배치 러너의 접힘 규칙 검증.   .venv/bin/python -m tests.unit.recommend.test_batch

`recipe_ingredient` 의 기본키가 (recipe_id, ingredient_id) 라 한 레시피에 같은
재료가 두 번 나오면 한 행으로 접힙니다. 무엇을 남길지를 DB 의 ON CONFLICT 에
맡기면 원문 순서에 따라 결과가 달라지므로 파이썬이 먼저 접는데, 그 규칙이
여기서 깨지는지 봅니다.

DB 를 쓰지 않습니다 — 사전을 시드에서 올리고 `_rows_for_recipe` 만 부릅니다.
"""

from __future__ import annotations

import sys

from features.recommend.ingest.batch import BatchStats, _rows_for_recipe
from features.recommend.ingest.match import Dictionary

# (이름, [원문...], 기대) — 기대는 (재료명, role) 의 집합
CASES: list[tuple[str, list[str], set[tuple[str, str]]]] = [
    (
        "역할이 갈리면 우선순위가 높은 쪽이 남는다",
        ["스팸", "스팸 적당량"],
        {("스팸", "essential")},
    ),
    (
        "같은 재료가 같은 역할로 두 번 나와도 한 행이다",
        ["양파 1개", "양파 2개"],
        {("양파", "essential")},
    ),
    (
        "조리도구는 행이 되지 않는다",
        ["도마", "양파 1개"],
        {("양파", "essential")},
    ),
    (
        "사전에 없는 표현은 행이 되지 않는다",
        ["듣도보도못한재료 1개", "양파 1개"],
        {("양파", "essential")},
    ),
    (
        "양념은 양념으로 남는다",
        ["간장 1큰술"],
        {("간장", "seasoning")},
    ),
]


def main() -> int:
    verbose = "-v" in sys.argv
    d = Dictionary.from_seeds()
    fails: list[str] = []

    for name, raws, want in CASES:
        st = BatchStats()
        rows = _rows_for_recipe(1, [(i + 1, i, t) for i, t in enumerate(raws)], d, st)
        got = {(d.id_to_name[r[1]], r[6]) for r in rows}

        errs = []
        if got != want:
            errs.append(f"결과 {sorted(got)} ≠ 기대 {sorted(want)}")
        # 접힘이 제대로 됐다면 같은 재료가 두 행으로 나올 수 없다
        keys = [(r[0], r[1]) for r in rows]
        if len(keys) != len(set(keys)):
            errs.append("기본키 중복 — DB 가 ON CONFLICT 로 삼켰을 것이다")

        if errs:
            fails.append(f"{name}: {' / '.join(errs)}")
        elif verbose:
            print(f"  통과  {name}  → {sorted(got)}")

    print(f"\n배치 접힘 규칙 {len(CASES)}건 중 {len(CASES) - len(fails)}건 통과")
    for f in fails:
        print(f"  실패  {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
