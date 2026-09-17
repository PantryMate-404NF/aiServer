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
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from features.recommend.ingest.run_log import batch_run
from features.recommend.repository_ingest import (
    load_feature_quality,
    mark_recipe_status,
    rebuild_recipe_features,
    set_unmatched_counts,
)

logger = logging.getLogger(__name__)

#: 사전(seeds/)의 지문 6자리. 사전이 바뀌면 값이 저절로 바뀝니다.
SEEDS = Path(__file__).resolve().parents[4] / "seeds"


#: recipe_feature 에 영향이 없다고 확인된 시드. 이것만 지문에서 뺍니다.
#:
#: 주의: 기본이 '포함' 입니다. 새 시드 파일이 생기면 저절로 지문에 들어갑니다 —
#:    모르는 파일을 빼면 조용히 낡고, 넣으면 헛된 판 번호가 하나 올라갈 뿐입니다.
#:    실패 비용이 비대칭이라 넓은 쪽을 기본으로 둡니다.
#:
#: 09-17 에 실측으로 정했습니다. main 이 onboarding_recipes.yaml 을 고쳤을 때
#: 지문이 뒤집혔는데 그 파일은 정규화 경로에 없습니다 — 헛된 전량 재계산이
#: 될 뻔했습니다. ingest 경로가 읽는지를 기준으로 갈랐습니다.
#:
#: 같은 날 cuisine_taxonomy.yaml 을 뺐다가 되돌렸습니다. "recipe.cuisine 이라
#: recipe_feature 가 아니다" 라고 적었는데 `recipe_feature.cuisine_family` 가
#: 실재합니다. 오늘은 채우는 코드가 없어 값이 전건 NULL 이라 무해하지만,
#: G-30 이 그 칸을 채우는 순간 조용히 틀립니다 — 사전이 바뀌어도 판 번호가
#: 안 움직이는 그 실패입니다. 컬럼이 있으면 포함이 기본입니다.
_UNRELATED = frozenset(
    {
        # 온보딩 taste_vec. profile_store·schema 만 읽습니다
        "onboarding_recipes.yaml",
        # 측정용 라벨. ingredient_substitute 는 0행이고 아무도 안 읽습니다
        "substitutable_pairs.yaml",
        # 소비기한. effective_expiry 로 가고 recipe_feature 에 안 옵니다
        "ingredient_shelf_life.yaml",
    }
)


def _seed_fingerprint() -> str:
    """사전 파일들의 내용 해시 앞 6자리.

    주의: 이걸 안 붙이고 "v1" 로 굳히면 조용히 틀립니다. 검수를 반영해
       재정규화한 2회차 피처가 1회차와 **같은 판 번호**를 달고 나가서,
       B·C 가 캐시한 옛 값과 새 값을 구분할 방법이 없습니다. 에러가 아니라
       점수만 조금씩 어긋나므로 아무도 알아채지 못합니다.

       사람이 기억해서 올리는 규칙으로 두지 않고 사전에서 끌어냅니다.
    """
    h = hashlib.sha256()
    for f in sorted(SEEDS.glob("*.yaml")) + sorted(SEEDS.glob("*.csv")):
        if f.name in _UNRELATED:
            continue
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:6]


#: 조회에 나가는 산출물임을 표시합니다. `test-` 로 시작하면 조회가 자동으로
#: 뺍니다 — B·C 가 넣는 합성 피처와 섞이지 않게 하는 격리 장치입니다.
#:
#: 스키마 제약이 `^(v[0-9]|test-)` 이고 VARCHAR(16) 이라 `v1-abc123`(9자)이 들어갑니다.
FEATURE_VERSION = f"v1-{_seed_fingerprint()}"

#: D-14 가 남겨 둔 몫 — **필수재료가 있는데** 미매칭이 많은 레시피를 세는 데만 씁니다.
#: 임시값입니다. 실제 분포를 보고 조정하며, 조정해도 다시 만들면 되므로 소급됩니다.
#:
#: 주의: 필수재료가 0건인 쪽에는 쓰지 않습니다. 거기서는 미매칭 1건도 판정 불가라
#:    조회(04_functions.sql 의 ⓪'')가 `n_unmatched = 0` 으로 가릅니다. 09-17 에
#:    조회만 조이고 이 파일을 안 고쳐서, `make feature-build` 가 "통과 1,498 ·
#:    실패 1,241" 이라고 찍는 동안 실제로는 59 · 2,680 이었습니다. 1,439건이
#:    틀린 채로 C 의 품질 화면에 그려질 뻔했습니다 — 두 판정식은 한 뜻이어야 합니다.
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
            f" (미매칭 > 0 — 조회의 ⓪'' 와 같은 식)\n"
            f"  필수재료는 있으나 미매칭이 많음 {self.dirty_with_essential:,}건"
            f"  (아직 거르지 않습니다 — D-14 가 A-4 분포를 보고 정하라고 남긴 몫)"
        )


def _zero_essential_failed(n_unmatched: int) -> bool:
    """필수재료가 0건인 레시피가 조회에서 빠지는가 (D-10).

    **조회의 ⓪''(04_functions.sql) 와 같은 식이어야 합니다.** 거기서 통과시키는
    조건이 `n_essential > 0 OR n_unmatched = 0` 이므로 여기도 미매칭 유무로 가릅니다.

    비율을 쓰지 않는 이유: 필수를 하나도 못 찾았는데 미매칭이 남아 있으면
    "양념만으로 되는 요리" 가 아니라 "아직 못 읽은 재료 안에 필수가 있다" 는
    뜻입니다. 비율은 필수가 1건 이상일 때 "얼마나 놓쳤나" 를 재는 값이라
    이 자리에서는 뜻이 다릅니다.
    """
    return n_unmatched > 0


def _dirty_with_essential(n_total: int, n_unmatched: int) -> bool:
    """필수재료는 있는데 미매칭이 많은가. 세기만 하고 거르지 않습니다 (D-14).

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
        if n_ess == 0:
            # 조회가 쓰는 식과 같아야 한다 (⓪''). 비율이 아니라 미매칭 유무다
            if _zero_essential_failed(n_unm):
                st.zero_essential_failed += 1
            else:
                st.zero_essential_ok += 1
        elif _dirty_with_essential(n_total, n_unm):
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
