"""① Retrieval — 메모리의 사전에서 후보를 뽑습니다.

레시피의 정본이 백엔드로 가면서(2026-09-21) 후보 조회도 DB 의 함수(`repository.retrieve`)에서
메모리로 옮겼습니다. 규칙은 같습니다 — 냉장고와 필수 재료가 하나라도 겹치고, 부족한 필수 재료가
k 이하이고, 조리시간 상한 안이고, 알레르기에 걸리지 않는 것. 모자라면 k 를 풀고 그래도 모자라면
인기순입니다(`candidate.py` 의 사다리).

알레르기는 **여기서** 거릅니다. 뒤 단계는 점수와 순서만 정하므로 여기서 새면 그대로 서빙됩니다.
재료 id 만 보지 않고 제목도 봅니다 — 백엔드 표본에 제목에는 있는데 재료 행에는 없는 레시피가
있었습니다(새우 2.8% · 오징어 7.8% · 땅콩 12.9%, `engine/allergy.py`).

자르는 자리의 동점은 레시피 번호로 가르지 않습니다. 충족률이 같은 후보가 상한(500)보다 많으면
번호가 낮은 레시피만 영원히 후보가 됩니다. 인기 점수로 먼저 가르고, 그래도 같으면 사용자마다
다른 순서(`score.tie_break`)로 가릅니다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from features.recommend.engine import candidate as plans
from features.recommend.engine.allergy import AllergyResolution, blocks
from features.recommend.engine.catalog import Catalog
from features.recommend.engine.context import RecipeFeature, UserContext
from features.recommend.engine.score import tie_break
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate

#: 사용자마다 다른 순서를 만드는 곱셈 해시. 정수 연산뿐이라 실행마다 같습니다(`score.tie_break`
#: 과 같은 이유 — 난수도 파이썬 `hash()` 도 쓰지 않습니다).
_MIX = 0x9E3779B1
_MASK = 0xFFFFFFFF


@dataclass(frozen=True)
class Retrieved:
    """후보와, 그것이 어느 단계에서 어떻게 걸러져 나왔는가."""

    candidates: list[Candidate]
    stage: str
    max_missing: int
    #: 탈락 사유별 건수. 추적의 `filters` 로 나갑니다.
    filters: dict[str, int]
    scanned: int


def retrieve(
    catalog: Catalog,
    ctx: UserContext,
    allergies: AllergyResolution,
    policy: RankingPolicy,
    top_k: int,
    exploration_ratio: float,
) -> Retrieved:
    """사다리를 따라 후보가 찰 때까지 다시 뽑습니다. 마지막으로 뽑은 단계의 기록을 돌려줍니다."""
    plan = plans.first_plan(policy, pantry_is_bare=not ctx.has_own_ingredients)
    found = _collect(catalog, ctx, allergies, policy, plan)
    rows = found.candidates
    while (step := plans.next_plan(plan, len(rows), policy, top_k, exploration_ratio)) is not None:
        plan = step
        found = _collect(catalog, ctx, allergies, policy, plan)
        # 앞 단계의 후보를 앞에 둡니다. 부족이 적은 것이 자리를 먼저 잡습니다.
        rows = plans.dedupe([*rows, *found.candidates])
    return Retrieved(rows, plan.stage, plan.max_missing, found.filters, found.scanned)


def _collect(
    catalog: Catalog,
    ctx: UserContext,
    allergies: AllergyResolution,
    policy: RankingPolicy,
    plan: plans.RetrievalPlan,
) -> Retrieved:
    by_popularity = plan.stage == plans.FALLBACK_POPULARITY
    overlap: Counter[int] = Counter()
    for ingredient_id in ctx.pantry_ids:
        overlap.update(catalog.by_essential.get(ingredient_id, ()))
    pool = catalog.recipes.keys() if by_popularity else overlap.keys()

    filters = {"allergy_cut": 0, "cooktime_cut": 0, "missing_gt_k": 0}
    # 순서의 열쇠를 이 자리에서 바로 만듭니다. 후보 수천 건마다 함수를 부르면 그 호출 비용이
    # 조회 시간의 절반이었습니다(2026-09-22 실측). 열쇠 끝의 레시피 번호는 앞이 전부 같을 때
    # 비교가 레시피 객체까지 가지 않게 막는 것이지 순서를 정하려는 것이 아닙니다.
    salt = tie_break(ctx.user_id, 0)
    limit = ctx.max_cook_minutes
    passed: list[tuple[float, int, float, int, int, RecipeFeature, int]] = []
    for recipe_id in pool:
        recipe = catalog.recipes[recipe_id]
        total = len(recipe.essential_ids)
        missing = total - overlap.get(recipe_id, 0)
        if not by_popularity and missing > plan.max_missing:
            filters["missing_gt_k"] += 1
            continue
        if limit is not None and recipe.cook_minutes is not None and recipe.cook_minutes > limit:
            filters["cooktime_cut"] += 1
            continue
        popularity = -(recipe.popularity_score or 0.0)
        shuffle = ((recipe_id ^ salt) * _MIX) & _MASK
        if by_popularity:
            passed.append((popularity, 0, 0.0, shuffle, recipe_id, recipe, missing))
        else:
            coverage = -(total - missing) / total
            passed.append((coverage, missing, popularity, shuffle, recipe_id, recipe, missing))
    if not by_popularity:
        filters["no_overlap"] = len(catalog.recipes) - len(overlap)

    # 알레르기는 순서를 정한 뒤 **앞에서부터 상한이 찰 때까지만** 봅니다. 제목까지 훑는 검사라
    # 수천 건에 다 하면 조회가 지연을 지배합니다(2026-09-22 실측 p50 135ms). 상한 밖의 후보는
    # 어차피 서빙되지 않으므로 검사하지 않아도 새지 않습니다. `allergy_cut` 은 그래서 "본 것 중에
    # 걸러진 수" 입니다 — 사전 전체에서 걸러질 수가 아닙니다.
    passed.sort()
    candidates: list[Candidate] = []
    for *_key, recipe, missing in passed:
        if len(candidates) >= policy.candidate_limit:
            break
        if blocks(allergies, recipe.all_ids, recipe.title):
            filters["allergy_cut"] += 1
        else:
            candidates.append(_candidate(recipe, missing, ctx))
    return Retrieved(candidates, plan.stage, plan.max_missing, filters, len(catalog.recipes))


def _candidate(recipe: RecipeFeature, missing: int, ctx: UserContext) -> Candidate:
    total = len(recipe.essential_ids)
    lacking = sorted(recipe.essential_ids - ctx.pantry_ids)
    return Candidate(
        recipe_id=recipe.recipe_id,
        missing_count=missing,
        missing_ids=lacking,
        coverage=(total - missing) / total,
        cluster_id=None,
    )
