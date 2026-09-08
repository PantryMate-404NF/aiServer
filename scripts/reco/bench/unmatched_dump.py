"""미매칭 표현을 빈도순으로 덤프한다 — 조리도구 필터·사전 보강의 입력.

    .venv/bin/python bench/unmatched_dump.py raw_data/recipe_raw_data.jsonl

전량 커버리지 측정이 12분 걸리므로 결과를 파일로 남겨 재사용한다.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from features.recommend.ingest.match import Dictionary, match
from features.recommend.ingest.parse import normalize  # noqa: E402

OUT = Path("bench/out/unmatched.tsv")


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "raw_data/recipe_raw_data.jsonl")
    d = Dictionary.from_seeds()
    cnt: collections.Counter[str] = collections.Counter()
    total = matched = 0

    with open(src, encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for g in rec.get("ingredient_groups") or []:
                for it in g.get("items") or []:
                    txt = it.get("raw_text") or it.get("name") or ""
                    for p in normalize(txt):
                        if p.is_non_ingredient:      # 도구·용기는 분모에서 뺀다
                            continue
                        total += 1
                        # 🔴 MatchResult 는 미매칭일 때도 객체를 돌려준다.
                        #    `.matched` 로 봐야 한다 — 객체 truthiness 를 보면
                        #    항상 참이라 미매칭이 0 으로 나온다 (실제로 그랬다).
                        if match(p.name, d).matched:
                            matched += 1
                        else:
                            cnt[p.name] += 1
            if (i + 1) % 10000 == 0:
                print(f"  … {i+1:,}건", flush=True)

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as w:
        w.write("count\tterm\n")
        for term, c in cnt.most_common():
            w.write(f"{c}\t{term}\n")

    cum = 0
    tot_un = sum(cnt.values())
    marks = {}
    for k, (term, c) in enumerate(cnt.most_common(), 1):
        cum += c
        for p in (50, 70, 80, 90):
            if p not in marks and cum / tot_un >= p / 100:
                marks[p] = k

    print(f"\n  언급 {total:,} · 매칭 {matched:,} ({100*matched/total:.1f}%)")
    print(f"  미매칭 고유 {len(cnt):,}종 · 총 {tot_un:,}건")
    print(f"\n  🔑 누적 빈도 — 몇 종만 잡으면 되는가")
    for p in (50, 70, 80, 90):
        if p in marks:
            print(f"    상위 {marks[p]:>5,}종 → 미매칭의 {p}%")
    print(f"\n  → {OUT}")


if __name__ == "__main__":
    main()
