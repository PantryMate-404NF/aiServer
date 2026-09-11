"""flavor_vec 빌더 + 코퍼스 평균 μ (설계 2-5-1, A-5).

    python -m features.recommend.ingest.flavor_build
    make flavor-build

`recipe_feature.flavor_vec` 6축(매움·짠맛·단맛·신맛·감칠맛·기름짐)을 채우고,
전량 평균 μ 를 `feature_stats` 에 새 `stats_version` 으로 넣습니다.

## 원값을 저장합니다

μ 를 미리 빼서 넣지 않습니다. 그렇게 하면 `CHECK(길이 6)` 은 통과하고 값도
그럴듯해서 배치가 초록으로 끝납니다. 그런데 유저의 `taste_vec` 은 원좌표계라
`f_taste` 가 좌표계가 어긋난 채 조용히 돌고, 스코어러가 규약대로 μ 를 한 번 더
빼면 두 번 빠집니다.

빼기는 읽는 쪽이 합니다 — `flavor_vec` 과 `taste_vec` **양쪽에 같은 μ** 로.

## μ 는 전량으로 계산합니다

표본이 작으면 값이 15~20% 움직입니다. 부분 적재 상태에서 확정하면 그 뒤의
중심화가 전부 틀어지므로, 처리한 레시피 수를 `feature_stats.n_recipes` 에
함께 남겨 나중에 "몇 건으로 잰 μ 인가" 를 알 수 있게 합니다.

## 강도는 수량 숫자를 쓰지 않습니다

P5 수량환산이 없어 `quantity` 를 신뢰할 수 없습니다. 대신 단위의 *종류*
(`g` 인가 `큰술` 인가), 제목 등장 여부, 목록 앞쪽인지만 봅니다.
`is_ambiguous_qty` 는 `unit` 에서 정확히 복원됩니다 — P2 가 모호 수량일 때
단위 자리에 그 표현('약간'·'적당량')을 그대로 넣기 때문입니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from config import get_settings
from features.recommend.enums import IngredientRole
from features.recommend.ingest.flavor import N_AXIS, FlavorTable, aggregate, intensity
from features.recommend.ingest.parse import _units
from features.recommend.ingest.run_log import batch_run
from features.recommend.repository_ingest import (
    insert_feature_stats,
    load_all_flavor_vectors,
    load_flavor_source,
    load_recipe_ids,
    set_flavor_vectors,
)
from features.recommend.stage import ParsedIngredient

logger = logging.getLogger(__name__)

#: (title, 재료명, 분류경로, role, unit, position, 원문행수)
FlavorRow = tuple[str | None, str, str | None, str | None, str | None, int, int]


@dataclass
class FlavorStats:
    recipes: int = 0
    written: int = 0
    all_zero: int = 0
    stats_version: int = 0
    mu: list[float] | None = None

    def report(self) -> str:
        mu = " ".join(f"{v:.3f}" for v in (self.mu or []))
        return (
            f"레시피 {self.recipes:,}건 · 6축을 쓴 행 {self.written:,}\n"
            f"  전부 0 인 레시피 {self.all_zero:,}건"
            f"  (맛 사전에 없는 재료만으로 이뤄진 경우입니다)\n"
            f"  코퍼스 평균 μ  [{mu}]  → "
            + (
                f"stats_version {self.stats_version}"
                if self.stats_version
                else "저장 안 함 (부분 실행)"
            )
        )


def _chunks(items: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _vector_for_recipe(
    rows: Sequence[FlavorRow], ft: FlavorTable, ambiguous: set[str]
) -> list[float]:
    """한 레시피의 재료 행들 → 6축.

    Args:
        rows: (title, 재료명, 분류경로, role, unit, position, 원문행수)

    강도를 벡터에 곱한 뒤 역할 가중으로 모읍니다. 곱하는 순서가 중요합니다 —
    모으고 나서 곱하면 레시피 안에서 어느 재료가 강했는지가 사라집니다.
    """
    items: list[tuple[list[float], IngredientRole | None]] = []
    for title, name, cat, role_s, unit, position, n_total in rows:
        role = IngredientRole(role_s) if role_s else None
        p = ParsedIngredient(
            raw_text="",
            name=name,
            unit=unit,
            is_ambiguous_qty=bool(unit) and unit in ambiguous,
        )
        k = intensity(p, name, title or "", position or 0, n_total or 0, role)
        items.append(([v * k for v in ft.of(name, cat)], role))
    return aggregate(items, "role_w")


def build(limit: int | None = None, note: str = "A-5 flavor_vec 1회차") -> FlavorStats:
    """flavor_vec 을 채우고 μ 를 새 stats_version 으로 넣는다. 멱등이다."""
    ft = FlavorTable.from_seeds()
    _alias, ambiguous, _alts = _units()
    ids = load_recipe_ids(limit)
    st = FlavorStats()
    logger.info("레시피 %s건 · 맛 사전 로드 완료", f"{len(ids):,}")

    size = get_settings().ingest_batch
    for chunk in _chunks(ids, size):
        by_recipe: dict[int, list[FlavorRow]] = {}
        for rid, *rest in load_flavor_source(chunk):
            by_recipe.setdefault(rid, []).append(tuple(rest))

        vecs = {rid: _vector_for_recipe(rows, ft, ambiguous) for rid, rows in by_recipe.items()}
        st.written += set_flavor_vectors(vecs)
        st.recipes += len(chunk)
        logger.info("  %s/%s 레시피", f"{st.recipes:,}", f"{len(ids):,}")

    # ── 코퍼스 평균. 전량으로만 잰다 ────────────────────────
    rows = load_all_flavor_vectors()
    n = len(rows)
    st.all_zero = sum(1 for _rid, v in rows if not any(v))
    st.mu = [sum(v[k] for _rid, v in rows) / n for k in range(N_AXIS)] if n else [0.0] * N_AXIS

    # 주의: 부분 실행에서는 μ 를 확정하지 않는다. 이번에 안 건드린 레시피는
    #    영벡터로 남아 있어 평균을 0 쪽으로 끌어내린다. 표본이 작으면 값이
    #    15~20% 움직이는데(A-5), 그 μ 로 중심화하면 뒤가 전부 틀어진다.
    #    그런데 feature_stats 행은 멀쩡해 보이고 배치도 초록으로 끝난다.
    if limit is not None:
        logger.warning(
            "부분 실행이라 μ 를 저장하지 않았습니다 — 영벡터 %s건이 평균을 끌어내립니다",
            f"{st.all_zero:,}",
        )
        return st

    st.stats_version = insert_feature_stats(st.mu, n, note)
    return st


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="flavor_vec 빌더 (설계 2-5-1, A-5)")
    ap.add_argument("--limit", type=int, help="앞에서 N개 레시피만 (μ 는 전량으로 잽니다)")
    ap.add_argument("--note", default="A-5 flavor_vec 1회차", help="feature_stats.note")
    a = ap.parse_args(argv)

    with batch_run("flavor", {"limit": a.limit, "note": a.note}) as rl:
        st = build(limit=a.limit, note=a.note)
        rl.input_count = st.recipes
        rl.output_count = st.written
        rl.params["stats_version"] = st.stats_version
        rl.params["all_zero"] = st.all_zero
    logger.info("─" * 52)
    logger.info("%s", st.report())
    # 주의: 전부 0 이면 맛 사전 로딩이 깨진 것이다. 0 을 반환하면 스코어러가
    #    영벡터 위에서 돌고, f_taste 는 모든 후보에 같은 값을 줘 사실상 꺼진다.
    if st.recipes and st.all_zero == len(load_all_flavor_vectors()):
        logger.error("6축이 전부 0 — seeds/flavor_table.yaml 로딩을 확인하십시오")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
