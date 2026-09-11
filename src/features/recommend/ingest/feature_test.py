"""회귀 게이트 — A-4·A-5·A-6 의 검증을 한 명령으로 묶는다 (A-8).

    python -m features.recommend.ingest.feature_test
    make feature-test

체크 8개가 전부 초록이어야 `exit 0` 입니다. 하나라도 깨지면 `exit 1` 이고,
어느 것이 왜 깨졌는지 한 줄로 나옵니다.

## 게이트는 돌려야 일합니다

만들어 놓고 아무도 안 돌리면 존재만 하고 일하지 않습니다. 이 저장소에 이미
그런 게이트가 있었습니다 — `coverage.py` 가 `--min` 없이는 항상 0 을 반환해
커버리지가 떨어져도 계속 초록이었습니다.

그래서 **스키마나 배치를 건드렸으면 이것을 돌린다**를 규약으로 합니다.
B·C 트랙에도 같은 내용을 알립니다.

## 체크 목록

    ① recipe_feature 행 수 = recipe 행 수
    ② essential_ids 에 is_staple 재료가 없다
    ③ n_total = cardinality(all_ids) · n_essential = cardinality(essential_ids)
       · essential_ids ⊆ all_ids
    ④ flavor_vec 길이가 전행 6
    ⑤ feature_stats 최신 μ 길이 6 · n_recipes > 0
    ⑥ popularity_score 가 0~1 안에 있고 10분위가 균등
    ⑦ 실배치 행에 `test-` 접두어가 없다
    ⑧ 중심화 판별력 (A-5 게이트 재실행)

③ 은 DB 의 CHECK 제약과 겹칩니다. 겹치는 것이 맞습니다 — 제약은 새로 들어오는
행만 막고, 이 게이트는 **이미 들어 있는 행**을 봅니다. 제약을 나중에 고치거나
`ALTER TABLE ... DROP CONSTRAINT` 로 잠깐 풀면 그 사이에 들어온 행이 남습니다.

## ⑦ 이 필요한 이유

`tests/integration/test_smoke.py` 가 같은 `recipe_feature` 에 합성 행을 넣습니다
(`feature_version='test-smoke'`). 정상 종료 시 지우지만 crash·Ctrl+C 면 남고,
`make smoke-big` 은 50,000행입니다.

조회에는 `feature_version NOT LIKE 'test-%'` 필터가 있어(⓪', 09-03) 실서빙에
새지는 않습니다. 다만 남아 있으면 ① 의 행 수가 부풀고 통계가 오염됩니다.
`idx_rf_version` 인덱스가 이미 있어 확인 비용은 0 입니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from features.recommend.ingest.flavor_check import check as flavor_check
from features.recommend.repository_ingest import (
    load_gate_counts,
    load_gate_stats,
    load_popularity_deciles,
)

logger = logging.getLogger(__name__)

#: 10분위가 이 폭 안에 들어야 균등으로 봅니다 (A-6 과 같은 기준).
DECILE_TOLERANCE = 500


@dataclass
class Check:
    n: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class GateResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def run_gate() -> GateResult:
    """체크 8개를 돌린다. 하나라도 깨지면 passed 가 False 다."""
    res = GateResult()
    (
        n_recipe,
        n_feature,
        staple_in_ess,
        n_total_mismatch,
        n_ess_mismatch,
        ess_not_subset,
        bad_flavor_len,
        pop_out_of_range,
        test_rows,
        quality_nonzero,
    ) = load_gate_counts()

    res.checks.append(
        Check(
            "①",
            "recipe_feature 행 수 = recipe 행 수",
            n_feature == n_recipe,
            f"{n_feature:,} / {n_recipe:,}",
        )
    )
    res.checks.append(
        Check(
            "②", "essential_ids 에 is_staple 재료 없음", staple_in_ess == 0, f"{staple_in_ess:,}건"
        )
    )
    # 주의: 셋을 한 체크로 묶는다. 따로 두면 하나만 보고 넘어가는데, 셋이
    #    어긋나면 알러지가 뚫린다 — essential_ids 가 all_ids 에 없으면
    #    알러지 검사가 all_ids 만 보므로 알러젠이 든 레시피가 통과한다.
    bad3 = n_total_mismatch + n_ess_mismatch + ess_not_subset
    res.checks.append(
        Check(
            "③",
            "배열과 개수가 맞고 essential ⊆ all",
            bad3 == 0,
            f"n_total {n_total_mismatch} · n_essential {n_ess_mismatch}"
            f" · 부분집합 아님 {ess_not_subset}",
        )
    )
    res.checks.append(
        Check("④", "flavor_vec 길이 6", bad_flavor_len == 0, f"어긋난 행 {bad_flavor_len:,}")
    )

    stats = load_gate_stats()
    if stats is None:
        res.checks.append(Check("⑤", "feature_stats 최신 μ", False, "행이 없습니다"))
    else:
        version, mu_len, n_rec = stats
        res.checks.append(
            Check(
                "⑤",
                "feature_stats 최신 μ 길이 6 · n_recipes > 0",
                mu_len == 6 and n_rec > 0,
                f"stats_version {version} · 길이 {mu_len} · {n_rec:,}건",
            )
        )

    deciles = load_popularity_deciles()
    total = sum(c for _d, c in deciles)
    want = total / 10 if total else 0
    even = bool(deciles) and all(abs(c - want) <= DECILE_TOLERANCE for _d, c in deciles)
    res.checks.append(
        Check(
            "⑥",
            "popularity 0~1 · 10분위 균등",
            pop_out_of_range == 0 and even,
            f"범위 밖 {pop_out_of_range}건 · 분위 {len(deciles)}개"
            f" · 최대 편차 {max((abs(c - want) for _d, c in deciles), default=0):.0f}",
        )
    )
    res.checks.append(
        Check("⑦", "실배치 행에 test- 접두어 없음", test_rows == 0, f"{test_rows:,}건")
    )

    fc = flavor_check()
    res.checks.append(
        Check(
            "⑧",
            "중심화 판별력 (A-5 게이트)",
            fc.passed,
            " · ".join(
                f"{x.name} {x.raw_gap:.3f}→{x.centered_gap:.3f}" for x in fc.labels if not x.skipped
            ),
        )
    )

    # 게이트는 아니지만 함께 찍는다 — A-6 이 0 으로 두기로 한 값이다.
    if quality_nonzero:
        logger.warning(
            "quality_score <> 0 인 행이 %s건 있습니다 (A-6 은 0 으로 정했습니다)",
            f"{quality_nonzero:,}",
        )
    return res


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="피처 회귀 게이트 (A-8)").parse_args(argv)

    res = run_gate()
    for c in res.checks:
        logger.info("  %s %s %-42s %s", "통과" if c.ok else "실패", c.n, c.name, c.detail)
    logger.info("%s", "─" * 72)
    if res.passed:
        logger.info("통과 — 체크 %d개 전부 초록", len(res.checks))
        return 0
    logger.error("실패 %d개 — %s", len(res.failed), " · ".join(c.n for c in res.failed))
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
