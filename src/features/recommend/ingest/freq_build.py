"""ingredient.freq_count 채우기 (A-14).

    python -m features.recommend.ingest.freq_build
    make freq-build

재료가 몇 개의 레시피에 나오는지를 셉니다. 536종 전량이 0 이었습니다.

## 왜 필요한가

**B 지시서가 A 를 선행으로 지목합니다.** `f_cooccur`(가중치 0.10)의 IDF 분모와
MMR 다양성 계산이 둘 다 이 값에 묶여 있습니다.

IDF 는 "흔한 재료일수록 정보가 적다" 를 수치로 만드는 것입니다. 양파가 3만 개
레시피에 나오고 트러플이 5개에 나온다면, 둘을 같이 가진 것이 우연일 확률이
다릅니다. `freq_count` 가 전부 0 이면 그 구분이 사라져 **모든 재료가 똑같이
희귀한 것으로 취급됩니다.**

## 레시피 수이지 언급 수가 아닙니다

한 레시피에 '소금' 이 세 번 나와도 1 로 셉니다. IDF 가 묻는 것은 "몇 개의
문서에 나오는가" 이기 때문입니다.

## 안 나오는 재료는 0 으로 되돌립니다

사전이 좋아져 어떤 재료가 더는 안 잡히면 옛 값이 남습니다. 그러면 IDF 분모가
틀려서, 실제로는 희귀한 재료가 흔한 것으로 오해되어 `f_cooccur` 에서 가중치를
못 받습니다. 값이 그럴듯해서 알아채기 어렵습니다.

## 정규화 뒤에 돌려야 합니다

`recipe_ingredient` 를 세므로, 배치가 끝난 상태여야 맞는 값이 나옵니다.
`make normalize-batch` 다음에 돌리십시오.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from features.recommend.ingest.run_log import batch_run
from features.recommend.repository_ingest import (
    load_freq_stats,
    load_freq_top,
    rebuild_freq_count,
)

logger = logging.getLogger(__name__)

#: 완료 기준. 536종 중 이만큼은 실제 레시피에 나와야 합니다. 이보다 적으면
#: 정규화가 덜 됐거나 사전이 실데이터와 동떨어졌다는 뜻입니다.
MIN_NONZERO = 400


@dataclass
class FreqStats:
    ingredients: int = 0
    nonzero: int = 0
    max_count: int = 0
    avg_count: float = 0.0
    updated: int = 0
    top: list[tuple[str, int, str | None]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.nonzero >= MIN_NONZERO

    def report(self) -> str:
        rows = "\n".join(f"    {n:<14} {c:>6,}  {(p or '?').split('.')[0]}" for n, c, p in self.top)
        return (
            f"재료 {self.ingredients:,}종 · 갱신 {self.updated:,}행\n"
            f"  레시피에 실제로 나오는 것 {self.nonzero:,}종"
            f"  (기준 {MIN_NONZERO}종 이상)\n"
            f"  최댓값 {self.max_count:,} · 평균 {self.avg_count:,.1f}\n"
            f"  상위 재료 — 상식과 맞는지 봅니다\n{rows}"
        )


def build() -> FreqStats:
    """freq_count 를 다시 센다. 멱등이다."""
    st = FreqStats()
    st.updated = rebuild_freq_count()
    st.ingredients, st.nonzero, st.max_count, st.avg_count = load_freq_stats()
    st.top = load_freq_top(10)
    return st


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="ingredient.freq_count 빌더 (A-14)").parse_args(argv)

    with batch_run("freq") as rl:
        st = build()
        rl.input_count = st.ingredients
        rl.output_count = st.nonzero
        rl.params["max_count"] = st.max_count

    logger.info("─" * 52)
    logger.info("%s", st.report())
    if st.passed:
        logger.info("통과 — %s종이 실제 레시피에 나옵니다", f"{st.nonzero:,}")
        return 0
    # 주의: 0 을 반환하면 B 의 f_cooccur 가 모든 재료를 똑같이 희귀한 것으로
    #    보고 돈다. 에러가 아니라 조용히 나쁜 추천이 된다.
    logger.error(
        "미달 — %s종만 나옵니다 (기준 %s종). 정규화를 먼저 돌렸는지 확인하십시오",
        f"{st.nonzero:,}",
        MIN_NONZERO,
    )
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
