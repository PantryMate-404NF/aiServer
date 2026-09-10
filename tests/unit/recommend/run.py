#!/usr/bin/env python3
"""fixture 기반 P1·P2 검증.   .venv/bin/python -m tests.unit.recommend.run [-v]"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

from features.recommend.ingest.parse import normalize

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "responses" / "samples.yaml"


def close(a, b) -> bool:
    if a is None or b is None:
        return a is b or a == b
    return abs(float(a) - float(b)) < 1e-6


def main() -> int:
    verbose = "-v" in sys.argv
    cases = yaml.safe_load(FIX.read_text(encoding="utf-8"))["cases"]
    fails: list[tuple[str, str]] = []
    by_tag: Counter[str] = Counter()
    fail_tag: Counter[str] = Counter()

    for c in cases:
        raw, tag = c["raw"], c.get("tag", "?")
        by_tag[tag] += 1
        got = normalize(raw)
        errs = []

        if "count" in c and len(got) != c["count"]:
            errs.append(f"개수 {len(got)} ≠ {c['count']}")
        if not got:
            if "name" in c:
                errs.append("결과 없음")
        else:
            g = got[0]
            if "name" in c and g.name != c["name"]:
                errs.append(f"name '{g.name}' ≠ '{c['name']}'")
            if "qty" in c and not close(g.quantity, c["qty"]):
                errs.append(f"qty {g.quantity} ≠ {c['qty']}")
            if "unit" in c and g.unit != c["unit"]:
                errs.append(f"unit '{g.unit}' ≠ '{c['unit']}'")
            if "note" in c and g.note != c["note"]:
                errs.append(f"note '{g.note}' ≠ '{c['note']}'")
            if "optional" in c and g.is_optional_hint != c["optional"]:
                errs.append(f"optional {g.is_optional_hint} ≠ {c['optional']}")
            if "ambiguous" in c and g.is_ambiguous_qty != c["ambiguous"]:
                errs.append(f"ambiguous {g.is_ambiguous_qty} ≠ {c['ambiguous']}")
            if "subs" in c and g.substitutes != c["subs"]:
                errs.append(f"subs {g.substitutes} ≠ {c['subs']}")
            if "mods" in c and g.modifiers != c["mods"]:
                errs.append(f"mods {g.modifiers} ≠ {c['mods']}")
            if "split_candidate" in c and g.split_candidate != c["split_candidate"]:
                errs.append(f"split_candidate {g.split_candidate} ≠ {c['split_candidate']}")

        if errs:
            fails.append((raw, " · ".join(errs)))
            fail_tag[tag] += 1
        elif verbose:
            g = got[0] if got else None
            print(
                f"  ✓ {raw!r:<34} {g.name if g else '—'} "
                f"{g.quantity if g else ''} {g.unit or '' if g else ''}"
            )

    print(f"\n{'분류':<16}{'전체':>5}{'실패':>6}")
    for t, n in by_tag.most_common():
        f = fail_tag.get(t, 0)
        mark = "" if not f else "  ←"
        print(f"  {t:<14}{n:>5}{f:>6}{mark}")

    if fails:
        print(f"\n❌ {len(fails)}/{len(cases)} 실패")
        for raw, e in fails[:25]:
            print(f"  {raw!r:<36} {e}")
        return 1
    print(f"\n✅ {len(cases)}건 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
