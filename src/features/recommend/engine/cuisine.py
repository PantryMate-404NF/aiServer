"""온보딩에서 고른 음식 유형을 목록에 몇 칸만 반영합니다 (유형 슬롯).

점수는 이미 `f_cuisine`(w=0.04) 으로 유형을 조금 봅니다. 그것만으로 부족한 이유는 가중치가
작아서가 아니라 **초기 사용자의 순위를 재료 매칭이 지배하기 때문**입니다. Σw 의 0.44 가
A군(재료 매칭)이고, 유형이 뒤집을 수 있는 것은 0.04 뿐입니다. 고른 유형의 레시피가 냉장고
재료와 덜 겹치면 Top-K 안에 한 건도 안 들어오고, 사용자는 자기가 고른 문항이 아무 일도
하지 않았다고 읽습니다.

그래서 **점수를 올리지 않고 자리를 몇 칸 뗍니다.** 순위 자체를 건드리면 유형이 사실상
주 순위가 되어, 냉장고에 있는 재료로 만들 수 있는 것을 뒤로 밀어냅니다. 두 방식의 차이는
목록 하나에서 다음과 같습니다.

  가중치를 올린다   고른 유형이 목록 전체를 물들인다. 고른 유형이 하나면 20칸이 한 색이다.
  자리를 뗀다       18칸은 그대로고 2칸이 유형 몫이다. 무엇이 왜 바뀌었는지 한 줄로 설명된다.

## 언제 켜는가

행동이 쌓이기 전(`onboarding` · `blended`)까지입니다. 조리·클릭이 충분히 쌓인
사용자(`behavior`)는 실제로 만든 것이 유형보다 정확한 신호이고, 그때까지도 자리를 떼면
스스로 만든 이력을 온보딩 답변이 밀어냅니다.

## 이미 들어 있으면 떼지 않는다

개인화 목록에 고른 유형이 이미 있으면 그 유형의 칸은 만들지 않습니다. 목적은 "고른 유형이
목록에 보이는 것"이지 "고른 유형을 더 넣는 것"이 아닙니다. 조건 없이 매번 떼면, 점수가
20위 밖인 레시피가 이미 있던 같은 유형의 상위 레시피를 밀어냅니다.

같은 이유로 **뺄 자리도 아무 데나 고르지 않습니다.** 꼬리를 그냥 자르면 하필 그 자리에 있던
다른 유형의 유일한 한 건이 사라져, 한 유형을 넣으려고 다른 유형을 지우게 됩니다
(`trim_for_slots`).

## 무엇을 고르는가

빠진 유형마다 **맛 페르소나에 가장 가까운 것 한 건**입니다. 유형 안에서 아무거나 넣으면
"한식을 골랐더니 안 좋아할 한식이 왔다"가 됩니다. 맛을 모르는 사용자(콜드)는 `f_taste` 가
전부 None 이라 자연히 점수 순이 됩니다. 한 유형에서 두 개째는 가져오지 않습니다 — 목적에
보태는 것이 없는데 개인화 꼬리만 한 줄 더 자릅니다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from features.recommend.engine.context import RecipeFeature, UserContext
from features.recommend.enums import ONBOARDING_CUISINES, UserMode
from features.recommend.policy import RankingPolicy
from features.recommend.stage import ScoredCandidate

#: 맛을 잴 수 없는 후보의 정렬값. 맛을 아는 후보보다 뒤에 섭니다 — 유형 슬롯의 취지가
#: "고른 유형 중 좋아할 만한 것" 이라, 좋아할지 모르는 것을 앞에 둘 이유가 없습니다.
UNKNOWN_TASTE = -1.0


@dataclass(frozen=True)
class CuisineSpec:
    """이 요청에서 유형 슬롯을 몇 칸 쓰는가. 재정렬과 서비스가 같은 값을 보게 합니다."""

    count: int


def cuisine_spec(ctx: UserContext, policy: RankingPolicy, total: int) -> CuisineSpec:
    """유형 슬롯 칸 수. 고른 유형이 없거나 행동이 쌓인 사용자면 0 입니다.

    페르소나 없이 만든 문맥(검사 도구·시뮬레이터)은 온보딩 직후 사용자로 다룹니다 — 그쪽이
    이 슬롯을 확인하려는 경로이기 때문입니다.
    """
    if total <= 0 or not ctx.preferred_cuisines or policy.cuisine_slot_ratio <= 0.0:
        return CuisineSpec(count=0)
    if ctx.persona is not None and ctx.persona.mode is UserMode.WARM:
        return CuisineSpec(count=0)
    wanted = round(total * policy.cuisine_slot_ratio)
    return CuisineSpec(count=max(0, min(wanted, policy.cuisine_slot_max, total)))


def unserved_families(
    shown: Sequence[int], recipes: Mapping[int, RecipeFeature], ctx: UserContext
) -> tuple[str, ...]:
    """고른 유형 가운데 목록에 한 건도 없는 것. 순서는 온보딩 화면 순서입니다.

    탐색이 채운 칸도 셉니다. 탐색은 무작위지만 사용자에게는 그냥 목록의 한 칸이고, 거기에
    이미 그 유형이 서 있는데 또 한 칸을 떼면 같은 유형이 두 번 나옵니다. 난수원은 요청마다
    `rng_seed` 로 고정되므로 같은 요청이면 결과도 같습니다.
    """
    served = {recipes[recipe_id].cuisine for recipe_id in shown if recipe_id in recipes}
    return tuple(
        family
        for family in ONBOARDING_CUISINES
        if family in ctx.preferred_cuisines and family not in served
    )


def pick_cuisine(
    rest: Sequence[ScoredCandidate],
    recipes: Mapping[int, RecipeFeature],
    families: Sequence[str],
    count: int,
) -> list[ScoredCandidate]:
    """개인화·탐색에 들지 못한 후보에서 주어진 유형의 레시피를 뽑습니다.

    `rest` 는 이미 걸러진 후보입니다 - 점수 하한은 탐색과 같은 선(`rerank.worth_showing`)이며
    호출자가 적용합니다. 유형이 맞기만 하면 무엇이든 올린다면 목록 맨 아래 것이 상위 자리에
    서고, 그 칸이 유형을 고른 대가가 됩니다.
    """
    if count <= 0 or not rest or not families:
        return []
    wanted = set(families)
    grouped: dict[str, list[ScoredCandidate]] = {}
    for item in rest:
        family = _family(item, recipes)
        if family in wanted:
            grouped.setdefault(str(family), []).append(item)
    for group in grouped.values():
        group.sort(key=_taste_first)
    return _one_per_family(grouped, count)


def trim_for_slots(
    personal: Sequence[tuple[ScoredCandidate, float]],
    drop: int,
    recipes: Mapping[int, RecipeFeature],
    ctx: UserContext,
) -> list[tuple[ScoredCandidate, float]]:
    """유형 칸이 쓸 자리를 개인화 목록에서 뺍니다. 고른 유형의 마지막 한 건은 건너뜁니다.

    맨 끝부터 빼되, 그 자리가 고른 유형의 유일한 한 건이면 한 칸 앞을 대신 뺍니다. 그러지
    않으면 한 유형에 칸을 떼느라 다른 유형이 목록에서 사라집니다 - 얻은 것과 잃은 것이 같고,
    사용자는 자기가 고른 유형 하나가 왜 없어졌는지 알 길이 없습니다. 실제로 중식이 20위,
    일식이 20위 밖에만 있는 후보에서 일식 칸을 떼자 중식이 통째로 사라졌습니다.

    뺄 수 있는 자리가 없으면(남은 것이 전부 마지막 한 건이면) 그때는 맨 끝을 뺍니다 - 목록
    길이는 지켜야 합니다.
    """
    kept = list(personal)
    for _ in range(max(0, min(drop, len(kept)))):
        kept.pop(_sacrificial(kept, recipes, ctx))
    return kept


def _sacrificial(
    kept: Sequence[tuple[ScoredCandidate, float]],
    recipes: Mapping[int, RecipeFeature],
    ctx: UserContext,
) -> int:
    """뺄 자리. 뒤에서부터 보되 고른 유형의 마지막 한 건은 지나칩니다."""
    counts = Counter(_family(item, recipes) for item, _ in kept)
    for index in range(len(kept) - 1, -1, -1):
        family = _family(kept[index][0], recipes)
        if family not in ctx.preferred_cuisines or counts[family] > 1:
            return index
    return len(kept) - 1


def _family(item: ScoredCandidate, recipes: Mapping[int, RecipeFeature]) -> str | None:
    return recipes.get(item.recipe_id, RecipeFeature(recipe_id=item.recipe_id)).cuisine


def cuisine_slots(filled: int, count: int, taken: Collection[int]) -> list[int]:
    """유형 슬롯의 자리. 목록 중반부터 고르게 놓습니다.

    탐색과 달리 무작위가 아닙니다. 결정적 슬롯이라 노출확률이 1.0 이고, 자리까지 흔들면
    같은 입력에 같은 목록이라는 성질만 잃습니다. 대신 맨 뒤에 몰지 않습니다 — 아무도 보지
    않는 자리에 넣는 것은 반영하지 않은 것과 같습니다.

    탐색이 이미 가져간 자리는 비켜 갑니다. 앞머리(개인화 몫)까지 밀려나야 한다면 그때는
    앞자리도 씁니다 — 슬롯을 통째로 버리는 것보다 낫습니다.
    """
    if count <= 0 or filled <= 0:
        return []
    used = set(taken)
    middle = [p for p in range(filled // 2, filled) if p not in used]
    free = middle or [p for p in range(filled) if p not in used]
    if not free:
        return []
    count = min(count, len(free))
    return sorted(free[(index * len(free)) // count] for index in range(count))


def _taste_first(item: ScoredCandidate) -> tuple[float, float, int]:
    """맛이 가까운 순, 같으면 점수 순, 그래도 같으면 레시피 번호 순입니다."""
    fit = item.features.get("f_taste")
    return (-(UNKNOWN_TASTE if fit is None else fit), -item.score, item.recipe_id)


def _one_per_family(
    grouped: Mapping[str, list[ScoredCandidate]], count: int
) -> list[ScoredCandidate]:
    """유형마다 한 칸까지. 순서는 온보딩 화면 순서라 요청마다 흔들리지 않습니다.

    한 유형에서 두 개째를 가져오지 않습니다. 목적이 "고른 유형이 목록에 보이는 것" 이라
    두 번째부터는 목적에 보태는 것이 없는데, 그 한 칸은 개인화 꼬리를 한 줄 더 잘라 냅니다.
    하필 그 자리에 다른 유형의 유일한 한 건이 있으면 그것이 목록에서 사라집니다 - 한 유형을
    두 번 보여 주려고 다른 유형을 지우는 셈입니다.
    """
    order = [family for family in ONBOARDING_CUISINES if grouped.get(family)]
    return [grouped[family][0] for family in order[:count]]
