"""B·C 트랙용 골든 픽스처 — DB 없이 실데이터로 테스트하게 (A-9).

    python -m features.recommend.ingest.golden          # 만든다
    python -m features.recommend.ingest.golden --check   # 계약이 맞는지 본다
    make golden-build · make golden-check

실제 `recipe_feature` 에서 30건을 뽑아 `tests/fixtures/recommend/feature_golden.json`
에 담습니다. B 의 스코어러와 C 의 평가 하네스가 **DB 없이** 이 파일로 돕니다.

## 경계 케이스를 반드시 넣습니다

정상 케이스만 담으면 안 됩니다. B 의 스코어러가 아래를 만나면 예외가 아니라
**NaN 이나 만점을 조용히** 냅니다.

    n_essential = 0      2,739건 (5.9%)   coverage 가 1.0 만점이 된다
    flavor_vec 전부 0    1,046건 (2.3%)   코사인 분모가 0 이 된다
    cook_minutes NULL    2,072건 (4.5%)   시간 필터·조리시간 피처가 비교를 못 한다
    n_unmatched 높음     2,615건 (5.6%)   정규화가 반쯤 실패한 레시피
    n_total = 0          1,006건 (2.2%)   재료가 하나도 안 붙었다

픽스처에 이게 없으면 그 버그는 **실데이터에서만** 나타나고, 그때는 원인이
A 인지 B 인지 아무도 모릅니다.

## 계약이 바뀌면 이 파일도 바뀝니다

`--check` 가 픽스처의 키와 `recipe_feature` 의 현재 컬럼을 대조합니다.
어긋나면 **A 가 계약을 바꿨다는 신호**입니다. 그때 B·C 에게 알립니다.

## 왜 recipe_id 순으로 고정하나

실행마다 다른 레시피가 뽑히면 픽스처로 쓴 테스트도 실행마다 달라집니다.
조건마다 `ORDER BY recipe_id LIMIT n` 으로 고정합니다.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from features.recommend.repository_ingest import (
    load_feature_columns,
    load_golden_rows,
    load_latest_mu,
)

logger = logging.getLogger(__name__)

#: 저장소 루트 기준. src/features/recommend/ingest/golden.py 에서 4단계 위입니다.
FIXTURE = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "recommend" / "feature_golden.json"
)

#: 픽스처에 담는 컬럼. repository_ingest 의 _GOLDEN_COLS 와 순서가 같아야 합니다.
KEYS = (
    "recipe_id",
    "essential_ids",
    "all_ids",
    "category_ids",
    "n_essential",
    "n_total",
    "n_unmatched",
    "flavor_vec",
    "popularity_score",
    "quality_score",
    "cook_minutes",
    "difficulty",
    "cluster_id",
    "feature_version",
)

#: (구분, repository_ingest.GOLDEN_CONDS 의 키, 건수). 합이 30 입니다.
#:
#: 주의: 조건에 겹침이 있습니다 — n_total=0 이면 n_essential 도 0 입니다.
#:    뒤 조건에서 앞 것을 빼 같은 레시피가 두 번 들어가지 않게 합니다.
BUCKETS: tuple[tuple[str, str, int], ...] = (
    ("정상", "normal", 15),
    ("필수재료 0개", "zero_essential", 5),
    ("미매칭 많음", "many_unmatched", 5),
    ("맛 벡터 전부 0", "zero_flavor", 3),
    ("조리시간 없음", "no_cooktime", 2),
)


@dataclass
class GoldenStats:
    rows: int = 0
    by_bucket: dict[str, int] = field(default_factory=dict)
    mu_version: int = 0

    def report(self) -> str:
        b = "\n".join(f"    {k:<16} {v}건" for k, v in self.by_bucket.items())
        return (
            f"레시피 {self.rows}건 · stats_version {self.mu_version}\n"
            f"  구분별\n{b}\n  파일: {FIXTURE.relative_to(FIXTURE.parents[3])}"
        )


def build() -> GoldenStats:
    """실 DB 에서 뽑아 픽스처를 다시 만든다. 멱등이다."""
    st = GoldenStats()
    seen: set[int] = set()
    out: list[dict[str, object]] = []

    for name, kind, n in BUCKETS:
        picked = 0
        # 겹침을 빼려고 넉넉히 뽑은 뒤 앞에서부터 채운다
        for row in load_golden_rows(kind, n * 4):
            rid = int(row[0])
            if rid in seen:
                continue
            seen.add(rid)
            out.append(dict(zip(KEYS, row, strict=True)))
            picked += 1
            if picked >= n:
                break
        st.by_bucket[name] = picked
        if picked < n:
            logger.warning("%s 구분이 %d건뿐입니다 (목표 %d건)", name, picked, n)

    mu = load_latest_mu()
    if mu is None:
        raise RuntimeError("feature_stats 가 비어 있습니다 — flavor-build 를 먼저 돌리십시오")
    st.mu_version, flavor_mu, n_recipes = mu

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(
            {
                "note": "실제 recipe_feature 에서 뽑은 골든 픽스처. A 가 계약을 "
                "바꾸면 이 파일도 바뀝니다 (A-9).",
                "stats_version": st.mu_version,
                "flavor_mu": [round(float(x), 6) for x in flavor_mu],
                "mu_n_recipes": n_recipes,
                "keys": list(KEYS),
                "recipes": out,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    st.rows = len(out)
    return st


def check() -> list[str]:
    """픽스처가 현재 계약과 맞는지 본다. 어긋난 것을 문장으로 돌려준다."""
    problems: list[str] = []
    if not FIXTURE.exists():
        return [f"픽스처가 없습니다: {FIXTURE}"]

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recipes = data.get("recipes") or []

    # ① 키가 recipe_feature 의 현재 컬럼에 전부 있는가
    cols = set(load_feature_columns())
    missing = [k for k in data.get("keys", []) if k not in cols]
    if missing:
        problems.append(f"recipe_feature 에 없는 키: {missing} — A 가 계약을 바꿨습니다")

    # ② 행마다 키가 같은가
    want = set(data.get("keys", []))
    for r in recipes:
        if set(r) != want:
            problems.append(f"recipe_id {r.get('recipe_id')} 의 키가 다릅니다")
            break

    # ③ 경계 케이스가 들어 있는가. 정상만 있으면 픽스처의 뜻이 없다.
    n_zero_ess = sum(1 for r in recipes if r["n_total"] > 0 and r["n_essential"] == 0)
    n_zero_flavor = sum(1 for r in recipes if not any(r["flavor_vec"]))
    n_no_time = sum(1 for r in recipes if r["cook_minutes"] is None)
    if n_zero_ess < 5:
        problems.append(f"필수재료 0개 케이스가 {n_zero_ess}건입니다 (5건 이상이어야 합니다)")
    if n_zero_flavor < 1:
        problems.append("맛 벡터가 전부 0 인 케이스가 없습니다")
    if n_no_time < 1:
        problems.append("조리시간이 없는 케이스가 없습니다")

    # ④ μ 가 길이 6 인가
    if len(data.get("flavor_mu") or []) != 6:
        problems.append("flavor_mu 가 길이 6 이 아닙니다")

    logger.info(
        "레시피 %d건 · 필수재료 0개 %d · 맛 0 %d · 조리시간 없음 %d · stats_version %s",
        len(recipes),
        n_zero_ess,
        n_zero_flavor,
        n_no_time,
        data.get("stats_version"),
    )
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="골든 픽스처 (A-9)")
    ap.add_argument("--check", action="store_true", help="만들지 않고 계약만 대조한다")
    a = ap.parse_args(argv)

    if a.check:
        problems = check()
        if not problems:
            logger.info("통과 — 픽스처가 현재 계약과 맞습니다")
            return 0
        for p in problems:
            logger.error("  %s", p)
        return 1

    st = build()
    logger.info("%s", st.report())
    problems = check()
    if problems:
        for p in problems:
            logger.error("  %s", p)
        return 1
    logger.info("통과 — 경계 케이스가 전부 들어 있습니다")
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
