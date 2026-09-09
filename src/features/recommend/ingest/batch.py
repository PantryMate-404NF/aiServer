"""정규화 배치 러너 — recipe_ingredient_raw 를 recipe_ingredient 로 (설계 4, A-2).

    python -m features.recommend.ingest.batch --limit 2000 --truncate
    python -m features.recommend.ingest.batch --dry-run

P1 전처리 → P2 분해 → P3 매칭 → P4 역할 판정을 DB 전량에 관통시킵니다.
`scripts/reco/coverage.py` 와 같은 파이프라인이고, 입력이 파일에서 DB 로
바뀐 것뿐입니다. coverage 는 재기만 하고 여기는 결과를 씁니다.

## 재개 기능을 만들지 않습니다

실측으로 2,000 레시피(원문 18,936행)가 CPU 22.6초입니다(P1P2 20.0 · P3 2.6 ·
P4 0.01). 451,862행 전량이 약 9분입니다. 재개 로직이 틀렸을 때 치르는 값이
9분보다 비쌉니다. 대신 청크마다 진행을 찍고, `--truncate` 로 언제든 전량을
다시 만듭니다.

## 한 레시피에 같은 재료가 두 번 나옵니다

`recipe_ingredient` 의 기본키가 (recipe_id, ingredient_id) 라 겹치면 한 행으로
접힙니다. 실측 2,941 레시피에서 577건(19.6%)이 겹쳤고 그중 3건은 역할이 서로
달랐습니다 — '간장' 이 재료칸(essential)과 양념칸(seasoning)에 모두 있는 식입니다.

DB 의 `ON CONFLICT DO NOTHING` 에 맡기면 먼저 온 행이 이기므로 position 순서에
따라 결과가 바뀝니다. 에러도 안 나고, 실행마다 달라질 수도 있습니다. 그래서
파이썬에서 역할 우선순위로 먼저 접고, DB 의 ON CONFLICT 는 안전망으로만 둡니다.

## 사전은 DB 에서 올립니다

`Dictionary.from_seeds()` 가 아니라 `from_db()` 입니다. 시드 파일의 행 순서로
매긴 id 와 DB 의 serial id 가 지금은 우연히 전부 일치하지만(실측 불일치 0/536),
검수(A-10)로 재료가 중간에 한 줄 들어가는 순간 그 아래가 전부 1씩 밀립니다.
밀린 id 도 유효한 외래키라 INSERT 는 성공하고, 추천은 '돼지고기' 자리에
'닭고기' 를 넣은 채 에러 없이 계속 돕니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from config import get_settings
from features.recommend.enums import IngredientRole
from features.recommend.ingest.match import Dictionary, match
from features.recommend.ingest.parse import normalize
from features.recommend.ingest.role import judge
from features.recommend.repository import (
    insert_recipe_ingredients,
    load_raw_ingredients,
    load_recipe_ids,
    truncate_recipe_ingredient,
)

logger = logging.getLogger(__name__)

#: 같은 재료가 두 역할로 잡혔을 때 무엇을 남기는가. 낮을수록 이깁니다.
#: 재료칸에 있던 것을 양념으로 깎아내리면 필수재료 수가 줄어 추천이 그 레시피를
#: 실제보다 쉽게 봅니다. 그래서 essential 을 가장 위에 둡니다.
_ROLE_RANK = {
    IngredientRole.ESSENTIAL: 0,
    IngredientRole.SEASONING: 1,
    IngredientRole.GARNISH: 2,
    IngredientRole.OPTIONAL: 3,
}

#: recipe_ingredient 한 행. 컬럼 순서는 repository 의 INSERT 문과 같습니다.
Row = tuple[int, int, int, float | None, str | None, None, str, str, float]


@dataclass
class BatchStats:
    """무엇을 몇 건 처리했는가. 끝나고 한 번 출력합니다."""

    recipes: int = 0
    raw_rows: int = 0
    mentions: int = 0
    matched: int = 0
    unmatched: int = 0
    non_ingredient: int = 0
    collapsed: int = 0
    prepared: int = 0
    written: int = 0

    @property
    def coverage(self) -> float:
        """도구를 뺀 언급 중 매칭된 비율. coverage.py 의 mention 과 같은 정의입니다."""
        d = self.matched + self.unmatched
        return self.matched / d if d else 0.0

    def report(self) -> str:
        return (
            f"레시피 {self.recipes:,}건 · 원문 {self.raw_rows:,}행\n"
            f"  언급 {self.mentions:,} = 매칭 {self.matched:,}"
            f" + 미매칭 {self.unmatched:,} + 도구 {self.non_ingredient:,}\n"
            f"  매칭 커버리지 {self.coverage:.3f}\n"
            f"  중복 접힘 {self.collapsed:,}건 → 쓸 행 {self.prepared:,}"
            f" · 실제로 쓴 행 {self.written:,}"
        )


def _chunks(items: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _rows_for_recipe(
    recipe_id: int,
    raws: Sequence[tuple[int, int, str]],
    d: Dictionary,
    st: BatchStats,
) -> list[Row]:
    """한 레시피의 원문 행들을 recipe_ingredient 행으로 바꿉니다.

    Args:
        raws: (raw_id, position, raw_text) 를 position 순으로.

    같은 재료가 두 번 나오면 `_ROLE_RANK` 가 낮은 쪽만 남깁니다. 같은 등급이면
    먼저 나온 것을 남깁니다 — 원문 순서가 유일하게 재현 가능한 기준입니다.
    """
    best: dict[int, tuple[int, Row]] = {}
    n_total = len(raws)
    for pos, (raw_id, _position, raw_text) in enumerate(raws):
        for p in normalize(raw_text):
            st.mentions += 1
            # 도마·냄비는 재료가 아니라 커버리지 분모에서 뺍니다. 남겨 두면
            # 미매칭으로 잡혀 커버리지가 실제보다 낮게 보입니다.
            if p.is_non_ingredient:
                st.non_ingredient += 1
                continue
            m = match(p.name, d)
            if m.ingredient_id is None or m.method is None:
                st.unmatched += 1
                continue
            st.matched += 1
            r = judge(p, m, d, pos=pos, n_total=n_total)
            if r.role is None:
                continue
            rank = _ROLE_RANK[r.role]
            # quantity_g 는 None 입니다. P5 수량환산이 아직 없고, 0 으로 채우면
            # "환산 실패" 와 "정말 0g" 이 구분되지 않습니다 (DDL 주석).
            row: Row = (
                recipe_id,
                m.ingredient_id,
                raw_id,
                p.quantity,
                p.unit,
                None,
                r.role.value,
                m.method.value,
                m.score,
            )
            prev = best.get(m.ingredient_id)
            if prev is None:
                best[m.ingredient_id] = (rank, row)
            else:
                st.collapsed += 1
                if rank < prev[0]:
                    best[m.ingredient_id] = (rank, row)
    return [row for _, row in best.values()]


def run(limit: int | None = None, truncate: bool = False, dry_run: bool = False) -> BatchStats:
    """배치 한 번. 통계를 돌려줍니다.

    `dry_run` 이면 파이프라인은 전부 돌리되 DB 에 쓰지 않습니다 — 커버리지가
    떨어졌는지를 쓰기 전에 볼 수 있어야 하기 때문입니다.
    """
    d = Dictionary.from_db()
    ids = load_recipe_ids(limit)
    st = BatchStats()
    logger.info(
        "레시피 %s건 · 사전 %s종%s",
        f"{len(ids):,}",
        f"{len(d.names):,}",
        " · dry-run (쓰지 않습니다)" if dry_run else "",
    )

    if truncate:
        if dry_run:
            logger.info("dry-run 이라 --truncate 를 건너뜁니다")
        else:
            truncate_recipe_ingredient()
            logger.info("recipe_ingredient 를 비웠습니다")

    size = get_settings().ingest_batch
    for chunk in _chunks(ids, size):
        raw = load_raw_ingredients(chunk)
        st.raw_rows += len(raw)

        by_recipe: dict[int, list[tuple[int, int, str]]] = {}
        for recipe_id, raw_id, position, raw_text in raw:
            by_recipe.setdefault(recipe_id, []).append((raw_id, position, raw_text))

        rows: list[Row] = []
        for recipe_id, raws in by_recipe.items():
            rows.extend(_rows_for_recipe(recipe_id, raws, d, st))
        st.recipes += len(chunk)
        st.prepared += len(rows)
        if not dry_run:
            st.written += insert_recipe_ingredients(rows)

        logger.info(
            "  %s/%s 레시피 · 누적 %s행", f"{st.recipes:,}", f"{len(ids):,}", f"{st.prepared:,}"
        )
    return st


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="재료 정규화 배치 (설계 4, A-2)")
    ap.add_argument("--limit", type=int, help="앞에서 N개 레시피만")
    ap.add_argument("--truncate", action="store_true", help="쓰기 전에 recipe_ingredient 를 비운다")
    ap.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 통계만")
    a = ap.parse_args(argv)

    st = run(limit=a.limit, truncate=a.truncate, dry_run=a.dry_run)
    logger.info("─" * 52)
    logger.info("%s", st.report())
    # 주의: 매칭이 0 이면 사전 로딩이 깨진 것이다. 0 을 반환하면 Makefile 이
    #    초록으로 넘어가고 A-4 가 빈 테이블 위에서 돈다.
    if st.mentions and not st.matched:
        logger.error("매칭 0건 — 사전 로딩을 확인하십시오")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
