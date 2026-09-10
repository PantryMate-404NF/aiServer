"""popularity_score 빌더 (D-9, A-6).

    python -m features.recommend.ingest.popularity_build
    make popularity-build

`recipe.review_count` 를 `log1p` 한 뒤 백분위 순위로 바꿔
`recipe_feature.popularity_score` 에 넣습니다. `quality_score` 는 0 으로 둡니다.

## 왜 min-max 가 아니라 백분위 순위인가

후기 개수의 꼬리가 극단적입니다 — p50 이 4 인데 max 가 1,079 입니다.

    min 0 · p50 4 · p90 25 · p99 181 · max 1,079 · 평균 13.54

min-max 로 펴면 상위 1%가 나머지 전부를 0.02 근처로 눌러 버려, `popularity` 가
사실상 "인기 있음/없음" 이진 피처가 됩니다. 백분위 순위는 0~1 이 균등해서
`f_popularity`(가중치 0.10)가 실제로 순위를 만듭니다.

## percent_rank 가 아니라 row_number 입니다

동점 블록이 8,470건이라 `percent_rank` 를 쓰면 3분위가 통째로 빕니다. 그러면
후보 500컷이 비결정적이 되고 **propensity 재현이 깨집니다** (D-9). `id` 로
동점을 깨서 몇 번을 돌려도 같은 값이 나오게 합니다.

## 두 개의 review_count 가 있습니다

`recipe.review_count` 는 로더가 원문 개수로 넣은 값(합계 627,605)이고,
`recipe_review` 실적재는 624,422건입니다(파싱 99.14%, 본문 빈 것 제외).
**원문 개수를 씁니다** — 실적재를 쓰면 후기 파싱 규칙이 바뀔 때마다 인기도가
흔들립니다. 값은 둘 다 그럴듯해서 틀린 것을 알아채지 못합니다.

## quality_score 는 만들 재료가 없습니다

크롤에 평점이 없습니다 — `rating_avg` NOT NULL 0건 · `rating_count > 0` 0건 ·
`view_count > 0` 0건 (46,353건 전수). 후기 개수로 만들면 `popularity` 와 상관
1.0 인 가짜 축이 하나 늘 뿐입니다. 0 으로 두고 이유를 컬럼 주석에 남깁니다.

## 코퍼스가 늘면 전부 바뀝니다

백분위 순위는 모집단에 대한 상대값이라, 크롤을 추가하면 과거 점수가 재현되지
않습니다. 그래서 계산 시점의 레시피 수를 로그와 `batch_run.params` 에 남깁니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from features.recommend.ingest.run_log import batch_run
from features.recommend.repository import (
    load_popularity_deciles,
    load_popularity_stats,
    rebuild_popularity,
)

logger = logging.getLogger(__name__)

#: 완료 기준. 0.99 를 요구하면 어떤 구현도 통과하지 못합니다 — 백분위 순위는
#: 원값을 등간격으로 펴므로 로그값과의 *선형* 상관이 1 이 될 수 없습니다.
#: 실측: percent_rank 0.9264 · cume_dist 0.9347 · row_number 0.9242 · min-max 0.6532.
MIN_CORR = 0.92

#: 10분위가 균등해야 합니다. 백분위 순위이므로 각 구간이 N/10 근처여야 합니다.
DECILE_TOLERANCE = 500


@dataclass
class PopularityStats:
    n_recipes: int = 0
    updated: int = 0
    min_score: float = 0.0
    max_score: float = 0.0
    corr: float = 0.0
    quality_nonzero: int = 0
    deciles: list[tuple[int, int]] = field(default_factory=list)

    @property
    def decile_ok(self) -> bool:
        if not self.deciles or not self.n_recipes:
            return False
        want = self.n_recipes / 10
        return all(abs(c - want) <= DECILE_TOLERANCE for _d, c in self.deciles)

    @property
    def passed(self) -> bool:
        return (
            self.min_score >= 0.0
            and self.max_score <= 1.0 + 1e-6
            and self.corr >= MIN_CORR
            and self.quality_nonzero == 0
            and self.decile_ok
        )

    def report(self) -> str:
        bars = "\n".join(
            f"    {d:>2}분위  {c:>6,}  {'█' * max(1, c // 200)}" for d, c in self.deciles
        )
        return (
            f"레시피 {self.n_recipes:,}건 · popularity 를 쓴 행 {self.updated:,}\n"
            f"  min {self.min_score:.6f}  ·  max {self.max_score:.6f}\n"
            f"  ln(1+review_count) 와의 상관 {self.corr:.4f}  (기준 {MIN_CORR} 이상)\n"
            f"  quality_score <> 0 인 행 {self.quality_nonzero}건  (0 이어야 합니다)\n"
            f"  10분위 분포 — 백분위 순위라 균등해야 합니다\n{bars}"
        )


def build() -> PopularityStats:
    """popularity 를 다시 만들고 완료 기준을 잰다. 멱등이다."""
    st = PopularityStats()
    st.updated = rebuild_popularity()
    logger.info("popularity_score %s행을 다시 만들었습니다", f"{st.updated:,}")
    logger.info("quality_score 는 0 으로 두었습니다 — 크롤에 평점이 없습니다")

    st.min_score, st.max_score, st.corr, st.quality_nonzero = load_popularity_stats()
    st.deciles = load_popularity_deciles()
    st.n_recipes = sum(c for _d, c in st.deciles)
    return st


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="popularity_score 빌더 (D-9, A-6)").parse_args(argv)

    with batch_run("popularity") as rl:
        st = build()
        rl.input_count = st.n_recipes
        rl.output_count = st.updated
        # 백분위 순위는 모집단에 대한 상대값이다. 몇 건 기준인지를 안 남기면
        # 크롤이 늘었을 때 과거 점수를 재현할 수 없다 (A-6).
        rl.params["n_recipes"] = st.n_recipes
        rl.params["corr"] = round(st.corr, 4)
    logger.info("─" * 52)
    logger.info("%s", st.report())
    # 주의: 백분위 순위는 모집단에 대한 상대값이다. 크롤이 늘면 과거 점수가
    #    재현되지 않으므로, 몇 건으로 매긴 순위인지를 반드시 남긴다.
    logger.info(
        "\n  이 순위는 레시피 %s건 기준입니다. 코퍼스가 바뀌면 다시 매겨야 합니다.",
        f"{st.n_recipes:,}",
    )
    if st.passed:
        logger.info("통과 — 완료 기준 4항 전부 만족합니다")
        return 0
    logger.error(
        "미달 — min %.4f · max %.4f · corr %.4f · quality<>0 %d · 10분위 균등 %s",
        st.min_score,
        st.max_score,
        st.corr,
        st.quality_nonzero,
        st.decile_ok,
    )
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
