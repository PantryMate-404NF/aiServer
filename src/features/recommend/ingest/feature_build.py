"""recipe_feature 빌더 — 정규화 결과를 조회용 피처로 접는다 (설계 4, A-4).

    python -m features.recommend.ingest.feature_build
    make feature-build

`recipe_ingredient` 345,892행을 레시피 46,353건으로 접어 `recipe_feature` 를
채웁니다. 조회(`retrieve_for_user`)가 읽는 것은 이 테이블뿐이라, 여기가 틀리면
추천 전체가 틀립니다.

## 멱등입니다

같은 입력으로 몇 번을 돌려도 결과가 같습니다. 그래서 필수재료 규칙을 바꾸면
배치를 다시 돌리지 않고 이것만 다시 돌려도 됩니다 — 11분이 아니라 몇 초입니다.

## n_unmatched 는 배치에서 받아옵니다

P3 가 못 붙인 재료 수는 `recipe_ingredient` 에 흔적이 없습니다. 못 붙었으니
행이 아예 없기 때문입니다. 그래서 배치가 세어 둔 것을 받아 채웁니다.

이 값이 없으면 D-10 이 동작하지 않습니다. 필수재료 0개 레시피에는 정상
(간장계란밥)과 정규화 실패가 섞여 있는데, 조회는 둘 다 coverage 만점을 주고
항상 후보에 넣습니다. Top-20 이 전부 실패한 레시피로 채워져도 에러가 안 납니다.
가르는 유일한 근거가 미매칭 비율입니다.

혼자 돌릴 때는 이미 들어 있는 값을 그대로 둡니다. 0 으로 덮으면 배치가 세어
놓은 것이 조용히 사라집니다.

## is_staple 을 두 겹으로 겁니다

P4 가 이미 staple 을 seasoning 으로 보내지만 SQL 에서 한 번 더 겁니다. 39종
중 15종(쌀·밀가루·전분·물·얼음 등)은 `is_staple` 로만 걸리고 `is_seasoning`
에는 없습니다. 검수 alias 나 manual override 가 role 을 바꾸는 순간 물과 쌀이
`essential_ids` 에 들어가고, 그러면 "물이 없어서 못 만드는 레시피" 가 됩니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from features.recommend.ingest.run_log import batch_run
from features.recommend.repository_ingest import (
    load_feature_quality,
    mark_recipe_status,
    rebuild_recipe_features,
    set_unmatched_counts,
)

logger = logging.getLogger(__name__)

#: 조회에 나가는 산출물임을 표시합니다. `test-` 로 시작하면 조회가 자동으로
#: 뺍니다 — B·C 가 넣는 합성 피처와 섞이지 않게 하는 격리 장치입니다.
FEATURE_VERSION = "v1"

#: D-10 — 미매칭 비율이 이 값을 넘으면 정규화 실패로 봅니다.
#: 임시값입니다. 실제 분포를 보고 조정하며, 조정해도 다시 만들면 되므로
#: 소급됩니다. 근거 없이 굳히지 않기 위해 상수로 빼 둡니다.
UNMATCHED_FAIL_RATIO = 0.3


@dataclass
class BuildStats:
    """무엇이 몇 건인가. D-10 이 요구하는 두 숫자를 따로 셉니다."""

    recipes: int = 0
    no_ingredient: int = 0
    zero_essential_ok: int = 0
    zero_essential_failed: int = 0
    unmatched_rows: int = 0
    status_rows: int = 0
    #: 필수재료는 있는데 미매칭이 많은 레시피. D-14 가 "A-4 에서 분포를 보고
    #: 따로 정한다" 고 남겨 둔 몫이라 지금은 세기만 하고 거르지 않습니다.
    dirty_with_essential: int = 0

    def report(self) -> str:
        z = self.zero_essential_ok + self.zero_essential_failed
        return (
            f"레시피 {self.recipes:,}건\n"
            f"  재료가 하나도 안 붙음 {self.no_ingredient:,}건"
            f"  (조회의 n_total > 0 관문이 걸러냅니다)\n"
            f"  필수재료 0개 {z:,}건 = "
            f"정상 {self.zero_essential_ok:,} + 정규화 실패 {self.zero_essential_failed:,}\n"
            f"    → 조회에서 빠지는 것은 실패 {self.zero_essential_failed:,}건입니다"
            f" (미매칭 비율 > {UNMATCHED_FAIL_RATIO})\n"
            f"  필수재료는 있으나 미매칭이 많음 {self.dirty_with_essential:,}건"
            f"  (아직 거르지 않습니다 — D-14 가 A-4 분포를 보고 정하라고 남긴 몫)"
        )


def _is_dirty(n_total: int, n_unmatched: int) -> bool:
    """미매칭이 전체의 일정 비율을 넘는가 (D-10 의 판정식).

    분모가 0 이면(붙은 것도 못 붙인 것도 없으면) 판정하지 않습니다. 그런
    레시피는 원문 자체가 비어 있다는 뜻이라 정규화의 잘못이 아니고,
    조회의 `n_total > 0` 관문이 따로 걸러냅니다.
    """
    d = n_total + n_unmatched
    return d > 0 and n_unmatched / d > UNMATCHED_FAIL_RATIO


def build(
    unmatched: Mapping[int, int] | None = None,
    scope: Sequence[int] | None = None,
    version: str = FEATURE_VERSION,
) -> BuildStats:
    """피처를 다시 만든다. 통계를 돌려준다.

    Args:
        unmatched: 레시피별 P3 미매칭 수. 배치가 방금 센 것을 넘깁니다.
            None 이면 이미 들어 있는 값을 그대로 둡니다 — 혼자 돌릴 때는
            새로 셀 방법이 없고, 0 으로 덮으면 D-10 이 조용히 꺼집니다.
        scope: 배치가 실제로 처리한 레시피. 이 범위 밖의 n_unmatched 는
            건드리지 않습니다. `--limit` 부분 실행이 나머지 값을 지우면
            그 값은 배치 메모리에만 있었으므로 복구되지 않습니다.
    """
    st = BuildStats()
    st.recipes = rebuild_recipe_features(version)
    logger.info("recipe_feature %s건을 다시 만들었습니다 (version=%s)", f"{st.recipes:,}", version)

    if unmatched is None:
        logger.info("n_unmatched 는 그대로 둡니다 — 배치가 넘겨준 값이 없습니다")
    else:
        st.unmatched_rows = set_unmatched_counts(unmatched, scope or list(unmatched))
        logger.info("n_unmatched %s행을 갱신했습니다", f"{st.unmatched_rows:,}")

    # ── D-10·D-14 — 두 숫자를 따로 센다 ─────────────────────
    # 주의: 판정은 n_essential = 0 에만 적용한다. 전 레시피에 걸면 20.1% 가
    #    사라진다 (D-14 실측 2,962/14,735). 필수재료가 있는데 미매칭이 많은
    #    쪽은 세기만 하고, 임계값은 이 분포를 보고 따로 정한다.
    for _rid, n_ess, n_total, n_unm in load_feature_quality():
        if n_total == 0:
            st.no_ingredient += 1
            continue  # 원문이 빈 것. ⓪ 관문이 따로 거른다
        dirty = _is_dirty(n_total, n_unm)
        if n_ess == 0:
            if dirty:
                st.zero_essential_failed += 1
            else:
                st.zero_essential_ok += 1
        elif dirty:
            st.dirty_with_essential += 1

    st.status_rows = mark_recipe_status()
    logger.info("recipe.status %s건을 'normalized' 로 올렸습니다", f"{st.status_rows:,}")
    return st


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="recipe_feature 빌더 (설계 4, A-4)")
    ap.add_argument("--version", default=FEATURE_VERSION, help="feature_version 값")
    a = ap.parse_args(argv)

    # 주의: test- 는 조회가 통째로 빼는 접두어다. 전 행에 찍으면 추천 카탈로그가
    #    0건이 되는데, 빌더는 성공으로 끝나고 조회도 에러 없이 빈 목록을 준다.
    #    합성 피처는 B·C 가 자기 행만 넣는 것이지 전량을 덮는 일이 아니다.
    if a.version.startswith("test-"):
        logger.error(
            "--version %s 은 전 행을 조회에서 빼 버립니다. 실배치는 v1 같은 값을 씁니다",
            a.version,
        )
        return 1

    with batch_run("feature", {"version": a.version}) as rl:
        st = build(version=a.version)
        rl.input_count = st.recipes
        rl.output_count = st.recipes
        rl.params["zero_essential_failed"] = st.zero_essential_failed
        rl.params["dirty_with_essential"] = st.dirty_with_essential
    logger.info("─" * 52)
    logger.info("%s", st.report())
    # 주의: 피처 행 수는 recipe 테이블 행 수라 항상 46,353 이다 — 0 이 되는 일이
    #    없어서 그것으로는 아무것도 못 잡는다. 재료가 붙은 레시피가 하나도 없는
    #    것이 실제 사고 신호다. 0 을 반환하면 A-5 가 빈 테이블 위에서 돈다.
    if st.recipes and st.no_ingredient == st.recipes:
        logger.error("재료가 붙은 레시피가 0건 — recipe_ingredient 를 확인하십시오")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
