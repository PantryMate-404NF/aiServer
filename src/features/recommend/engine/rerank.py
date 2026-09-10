"""③ Re-ranking. MMR 로 개인화 슬롯을 채우고 탐색 슬롯을 무작위 위치에 섞습니다.

탐색 슬롯의 선택과 노출확률은 A 트랙이 이미 만든 `serendipity.mixed_exploration` 과
`explore.exploration_slots` 를 그대로 씁니다. 여기서 다시 구현하면 두 벌이 되고,
`propensity` 의 정의가 갈리는 순간 off-policy 평가가 못 쓰게 됩니다.

개인화 슬롯을 **먼저** 채우고 남은 후보에서 탐색을 뽑습니다. 탐색을 먼저 뽑으면
개인화 결과가 난수에 따라 달라져 같은 입력에 같은 순위라는 성질이 깨지고, 개인화가
어차피 보여 줄 것을 탐색이 다시 고르면 탐색이 아닙니다.

`propensity` 는 확률입니다 (`enums.PROPENSITY_SEMANTICS` = "item"). 아이템이 Top-K
어딘가에 노출될 주변확률이며, 결정적 슬롯은 1.0 입니다.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Mapping, Sequence

from features.recommend.engine import explore, rank, reason, serendipity, taste
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.engine.feature import DEFAULT_IDF
from features.recommend.enums import DEFAULT_WEIGHTS
from features.recommend.policy import RankingPolicy
from features.recommend.stage import RankedItem, ScoredCandidate

SOURCE_UNIFORM = "uniform"
SOURCE_THOMPSON = "thompson"
#: 결정적 슬롯의 노출확률. "편향이 없다"가 아니라 "이 로그로는 보정할 수 없다"는 뜻입니다.
DETERMINISTIC_PROPENSITY = 1.0
#: 노출확률의 하한. 0 이면 IPS 의 분모가 0 이 되어 그 행을 영영 못 씁니다.
MIN_PROPENSITY = 1e-6


def rerank(
    scored: Sequence[ScoredCandidate],
    recipes: Mapping[int, RecipeFeature],
    ctx: UserContext,
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
    *,
    top_k: int = 20,
    weights: Mapping[str, float] | None = None,
) -> list[RankedItem]:
    """최종 Top-K. 점수는 바꾸지 않고 순서와 구성만 정합니다."""
    ranked = sorted(scored, key=lambda item: (-item.score, item.recipe_id))
    total = min(top_k, len(ranked))
    if total == 0:
        return []

    personal_full = mmr_select(
        ranked[: max(policy.mmr_pool_size, total)],
        recipes,
        total,
        corpus.ingredient_idf,
        policy.mmr_lambda,
    )
    shown = {item.recipe_id for item, _ in personal_full}
    rest = [item for item in ranked if item.recipe_id not in shown]
    explored = pick_exploration(rest, ctx, policy, rng, round(total * policy.exploration_ratio))
    personal = personal_full[: total - len(explored)]

    stats = rank.feature_stats(ranked)
    effective = dict(weights or DEFAULT_WEIGHTS)
    slots = explore.exploration_slots(total, len(explored), rng)
    return _assemble(personal, explored, slots, recipes, ctx, corpus, stats, effective)


def mmr_select(
    pool: Sequence[ScoredCandidate],
    recipes: Mapping[int, RecipeFeature],
    count: int,
    idf: Mapping[int, float],
    lambda_: float,
) -> list[tuple[ScoredCandidate, float]]:
    """점수와 이미 뽑은 것과의 최대 유사도를 맞바꾸며 하나씩 고릅니다.

    뽑을 때마다 남은 후보의 최대 유사도만 갱신하므로 유사도 계산은 후보 수 곱하기
    count 번입니다. 후보별 IDF 합을 미리 두면 자카드 분모는 합집합 없이 두 합에서
    교집합만 빼서 나옵니다. 함께 돌려주는 값이 그 아이템에 걸린 다양성 감점입니다.
    """
    ids = [
        recipes.get(item.recipe_id, RecipeFeature(recipe_id=item.recipe_id)).all_ids
        for item in pool
    ]
    totals = [sum(idf.get(i, DEFAULT_IDF) for i in group) for group in ids]
    max_sim = [0.0] * len(pool)
    selected: list[tuple[ScoredCandidate, float]] = []
    remaining = list(range(len(pool)))
    while remaining and len(selected) < count:
        best = max(
            remaining,
            key=lambda i: (lambda_ * pool[i].score - (1.0 - lambda_) * max_sim[i], -i),
        )
        selected.append((pool[best], round(max_sim[best], 6)))
        remaining.remove(best)
        for i in remaining:
            shared = sum(idf.get(x, DEFAULT_IDF) for x in ids[i] & ids[best])
            union = totals[i] + totals[best] - shared
            if union > 0.0:
                max_sim[i] = max(max_sim[i], shared / union)
    return selected


def pick_exploration(
    rest: Sequence[ScoredCandidate],
    ctx: UserContext,
    policy: RankingPolicy,
    rng: random.Random,
    count: int,
) -> list[tuple[ScoredCandidate, float, str]]:
    """개인화에 들지 못한 후보에서 탐색 슬롯을 뽑습니다.

    균등 절반이 모든 후보에 최소 노출확률을 보장하고, 나머지 절반이 클러스터 Thompson
    입니다. 균등이 없으면 Thompson 이 외면한 클러스터는 IPS 로 영원히 평가할 수 없습니다.
    돌려주는 것은 (아이템, 노출확률, 어느 경로가 뽑았는가) 입니다.
    """
    if count <= 0 or not rest:
        return []
    pool = _explorable(rest, policy)
    count = min(count, len(pool) // policy.exploration_min_pool_ratio)
    if count <= 0:
        return []
    stats = serendipity.ClusterStats(
        n=dict(ctx.history.cluster_seen), hits=dict(ctx.history.cluster_hits)
    )
    rows: list[dict[str, object]] = [
        {"recipe_id": item.recipe_id, "score": item.score, "cluster_id": item.cluster_id}
        for item in pool
    ]
    chosen, propensities = serendipity.mixed_exploration(
        rows,
        stats,
        rng,
        k=count,
        uniform_share=policy.uniform_share,
        pool_size=policy.explore_pool_size,
    )
    by_id = {item.recipe_id: item for item in pool}
    uniform_slots = _uniform_count(count, policy)
    picked: list[tuple[ScoredCandidate, float, str]] = []
    for index, row in enumerate(chosen):
        recipe_id = int(str(row["recipe_id"]))
        # mixed_exploration 은 균등 몫을 먼저 채우고 그다음 Thompson 을 붙입니다.
        source = SOURCE_UNIFORM if index < uniform_slots else SOURCE_THOMPSON
        probability = min(1.0, max(MIN_PROPENSITY, propensities.get(recipe_id, MIN_PROPENSITY)))
        picked.append((by_id[recipe_id], probability, source))
    return picked


def reason_context(
    item: ScoredCandidate,
    recipe: RecipeFeature,
    ctx: UserContext,
    corpus: CorpusStats,
) -> dict[str, object]:
    """`reason.build_reason` 이 템플릿을 채울 때 읽는 값.

    없는 키는 그 피처를 사유 후보에서 빼므로, 모르는 값을 지어내지 않고 빼 둡니다.
    """
    values: dict[str, object] = {}
    expiring = sorted(recipe.essential_ids & ctx.expiring_ids)
    named = [corpus.ingredient_names[i] for i in expiring if i in corpus.ingredient_names]
    if named:
        values["expiring_name"] = named[0]
        values["expiring_days"] = EXPIRING_DAYS
    missing_named = [
        corpus.ingredient_names[i] for i in item.missing_ids if i in corpus.ingredient_names
    ]
    if len(item.missing_ids) == 1 and missing_named:
        values["missing_name"] = missing_named[0]
    used = len(recipe.all_ids & ctx.pantry_ids)
    if used:
        values["pantry_used"] = used
    axis = _taste_axis(recipe, ctx, corpus)
    if axis is not None:
        values["taste_axis"] = axis
    liked = sorted(recipe.all_ids & ctx.history.liked_ingredient_ids)
    liked_named = [corpus.ingredient_names[i] for i in liked if i in corpus.ingredient_names]
    if liked_named:
        values["pref_ing"] = liked_named[0]
    if recipe.cuisine is not None:
        values["cuisine"] = recipe.cuisine
    if recipe.dish_type is not None:
        values["dish_type"] = recipe.dish_type
    if recipe.cook_minutes is not None:
        values["cook_minutes"] = recipe.cook_minutes
    return values


#: 임박 재료의 남은 일수. 요청이 D-3 목록을 주므로 문구에도 그 값을 씁니다.
EXPIRING_DAYS = 3


def _taste_axis(recipe: RecipeFeature, ctx: UserContext, corpus: CorpusStats) -> str | None:
    """사유에 쓸 맛 축. 사용자가 평균보다 낮은 쪽이면 강하지 않다는 뜻으로 적습니다."""
    found = taste.dominant_axis(ctx.taste_vec, recipe.flavor_vec, corpus.flavor_mean)
    if found is None:
        return None
    axis, prefers_more = found
    return axis if prefers_more else f"강하지 않은 {axis}"


def _explorable(rest: Sequence[ScoredCandidate], policy: RankingPolicy) -> list[ScoredCandidate]:
    """탐색에 쓸 후보. 점수가 후보군 중위수 아래인 잔여물은 넣지 않습니다.

    탐색 슬롯은 개인화에 못 든 후보에서 뽑으므로, 거르지 않으면 목록 맨 아래 것이
    상위 자리에 섭니다. 탐색은 "덜 좋은 것"이 아니라 "안 보여 줬을 뿐 괜찮은 것"이어야
    합니다. 슬롯 수의 배수만큼 후보가 없으면 슬롯 자체를 줄입니다.
    """
    if not rest:
        return []
    floor = statistics.median([item.score for item in rest])
    kept = [item for item in rest if item.score >= floor]
    return kept[: policy.explore_pool_size]


def _uniform_count(count: int, policy: RankingPolicy) -> int:
    """`serendipity.mixed_exploration` 이 균등에 배정하는 슬롯 수와 같은 계산입니다."""
    if policy.uniform_share <= 0:
        return 0
    return max(1, round(count * policy.uniform_share))


def _assemble(
    personal: Sequence[tuple[ScoredCandidate, float]],
    explored: Sequence[tuple[ScoredCandidate, float, str]],
    slots: Sequence[int],
    recipes: Mapping[int, RecipeFeature],
    ctx: UserContext,
    corpus: CorpusStats,
    stats: Mapping[str, tuple[float, float]],
    weights: Mapping[str, float],
) -> list[RankedItem]:
    """탐색 아이템을 주어진 자리에 끼우고 나머지를 개인화로 채웁니다.

    자리가 고정되면 사용자가 그 자리를 학습해 건너뛰고, 위치별 검사확률 곡선을 구할 수
    없습니다. 그래서 자리는 매 요청 무작위입니다 (`explore.exploration_slots`).
    """
    slot_set = set(slots)
    personal_iter = iter(personal)
    explored_iter = iter(explored)
    items: list[RankedItem] = []
    for index in range(len(personal) + len(explored)):
        if index in slot_set:
            scored, probability, source = next(explored_iter)
            mmr_penalty = 0.0
            is_exploration = True
        else:
            scored, mmr_penalty = next(personal_iter)
            probability = DETERMINISTIC_PROPENSITY
            source = None
            is_exploration = False
        recipe = recipes.get(scored.recipe_id, RecipeFeature(recipe_id=scored.recipe_id))
        keys = rank.top_reasons(scored, dict(weights), dict(stats))
        text, used = reason.build_reason(
            keys, reason_context(scored, recipe, ctx, corpus), is_exploration=is_exploration
        )
        items.append(
            RankedItem(
                **scored.model_dump(),
                final_rank=index + 1,
                reason=text,
                reason_features=used,
                mmr_penalty=mmr_penalty,
                is_exploration=is_exploration,
                propensity=probability,
                explore_source=source,
            )
        )
    return items
