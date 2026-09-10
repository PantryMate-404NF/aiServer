"""Stage 3. MMR 다양성 필터로 개인화 슬롯을 채우고 20% 탐색 슬롯을 무작위 위치에 섞습니다."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from features.recommend.schema import (
    CorpusStats,
    RankConfig,
    ScoredCandidate,
    ServedItem,
    UserContext,
)

# 사전 정보가 없는 요리군의 Beta 분포. 균등 분포라 모든 요리군이 같은 확률로 뽑힙니다.
UNIFORM_PRIOR = (1.0, 1.0)
# 코퍼스 통계에 없는 재료의 IDF. 1 이면 가중치 없는 자카드와 같습니다.
DEFAULT_IDF = 1.0


def rerank(
    scored: Sequence[ScoredCandidate],
    ctx: UserContext,
    corpus: CorpusStats,
    cfg: RankConfig,
    rng: random.Random,
) -> tuple[ServedItem, ...]:
    """최종 Top-K 를 만듭니다. 점수는 바꾸지 않고 순서와 구성만 정합니다.

    개인화 슬롯을 먼저 MMR 로 채우고, 거기 들지 못한 후보에서 탐색 슬롯을 뽑습니다.
    개인화가 어차피 보여 줄 것을 탐색이 다시 고르면 탐색이 아닙니다. 이 순서라야
    개인화 슬롯이 난수와 무관하게 정해집니다.
    """
    ranked = sorted(scored, key=_by_score)
    total = min(ctx.top_k, len(ranked))
    if total == 0:
        return ()
    personal_full = mmr_select(ranked, total, corpus.ingredient_idf, cfg.mmr_lambda)
    shown = {item.candidate.recipe_id for item in personal_full}
    rest = [item for item in ranked if item.candidate.recipe_id not in shown]
    exploration = pick_exploration(rest, ctx, cfg, rng, round(total * cfg.exploration_ratio))
    personal = personal_full[: total - len(exploration)]
    return _mix(personal, exploration, rng)


def mmr_select(
    pool: Sequence[ScoredCandidate], count: int, idf: Mapping[int, float], lambda_: float
) -> list[ScoredCandidate]:
    """점수와 이미 뽑은 것과의 최대 유사도를 맞바꾸며 하나씩 고릅니다.

    뽑을 때마다 남은 후보의 최대 유사도만 갱신하므로 유사도 계산은 후보 수 곱하기 count 번입니다.
    """
    selected: list[ScoredCandidate] = []
    max_sim = [0.0] * len(pool)
    remaining = list(range(len(pool)))
    while remaining and len(selected) < count:
        best = max(
            remaining,
            key=lambda i: (lambda_ * pool[i].score - (1.0 - lambda_) * max_sim[i], -i),
        )
        selected.append(pool[best])
        remaining.remove(best)
        chosen_ids = pool[best].candidate.all_ids
        for i in remaining:
            max_sim[i] = max(max_sim[i], jaccard_idf(pool[i].candidate.all_ids, chosen_ids, idf))
    return selected


def jaccard_idf(left: frozenset[int], right: frozenset[int], idf: Mapping[int, float]) -> float:
    """IDF 로 가중한 자카드 유사도. 흔한 재료(소금, 물)가 겹친다고 비슷하다고 보지 않습니다."""
    union = left | right
    if not union:
        return 0.0
    shared = sum(idf.get(i, DEFAULT_IDF) for i in left & right)
    return shared / sum(idf.get(i, DEFAULT_IDF) for i in union)


def pick_exploration(
    ranked: Sequence[ScoredCandidate],
    ctx: UserContext,
    cfg: RankConfig,
    rng: random.Random,
    count: int,
) -> list[tuple[ScoredCandidate, float]]:
    """비선호 요리군에서 품질 상위 풀을 만들고 절반은 Thompson, 절반은 균등으로 뽑습니다.

    함께 돌려주는 값은 노출 확률의 역수입니다. 오프라인 학습이 탐색 슬롯의 편향을 되돌릴 때 씁니다.
    """
    if count <= 0:
        return []
    pool = [item for item in ranked if _is_novel(item, ctx)]
    pool.sort(key=lambda item: (-(item.candidate.quality_score or 0.0), item.candidate.recipe_id))
    remaining = pool[: cfg.exploration_pool_size]
    priors = ctx.history.cuisine_priors
    picks: list[tuple[ScoredCandidate, float]] = []

    for _ in range(count // 2):
        by_cuisine = _group_by_cuisine(remaining)
        if not by_cuisine:
            break
        cuisines = list(by_cuisine)
        winner = _thompson_winner(cuisines, priors, rng)
        probability = _win_probability(winner, cuisines, priors, rng, cfg.propensity_samples)
        chosen = by_cuisine[winner][0]
        picks.append((chosen, 1.0 / probability))
        remaining.remove(chosen)

    uniform_count = min(count - len(picks), len(remaining))
    if uniform_count > 0:
        inclusion = uniform_count / len(remaining)
        picks.extend((chosen, 1.0 / inclusion) for chosen in rng.sample(remaining, uniform_count))
    return picks


def _is_novel(item: ScoredCandidate, ctx: UserContext) -> bool:
    cuisine = item.candidate.cuisine
    return cuisine is not None and cuisine not in ctx.preferred_cuisines


def _group_by_cuisine(items: Sequence[ScoredCandidate]) -> dict[str, list[ScoredCandidate]]:
    groups: dict[str, list[ScoredCandidate]] = {}
    for item in items:
        if item.candidate.cuisine is not None:
            groups.setdefault(item.candidate.cuisine, []).append(item)
    return groups


def _thompson_winner(
    cuisines: Sequence[str], priors: Mapping[str, tuple[float, float]], rng: random.Random
) -> str:
    """요리군마다 사후 분포에서 하나씩 뽑아 가장 큰 값을 낸 요리군입니다."""
    draws = {cuisine: rng.betavariate(*priors.get(cuisine, UNIFORM_PRIOR)) for cuisine in cuisines}
    return max(cuisines, key=lambda cuisine: draws[cuisine])


def _win_probability(
    winner: str,
    cuisines: Sequence[str],
    priors: Mapping[str, tuple[float, float]],
    rng: random.Random,
    samples: int,
) -> float:
    """winner 가 뽑힐 확률의 몬테카를로 추정. 한 번을 더해 두어 0 이 되지 않습니다."""
    if len(cuisines) == 1:
        return 1.0
    wins = sum(1 for _ in range(samples) if _thompson_winner(cuisines, priors, rng) == winner)
    return (wins + 1) / (samples + 1)


def _mix(
    personal: Sequence[ScoredCandidate],
    exploration: Sequence[tuple[ScoredCandidate, float]],
    rng: random.Random,
) -> tuple[ServedItem, ...]:
    """탐색 아이템을 1~K 위 사이 무작위 위치에 흩뿌립니다.

    자리가 고정되면 사용자가 그 자리를 학습해 건너뛰고, 탐색 슬롯의 노출 확률이 왜곡됩니다.
    """
    total = len(personal) + len(exploration)
    slots = set(rng.sample(range(total), len(exploration)))
    personal_iter = iter(personal)
    exploration_iter = iter(exploration)
    served: list[ServedItem] = []
    for index in range(total):
        if index in slots:
            item, propensity = next(exploration_iter)
            served.append(ServedItem(item, index + 1, True, propensity))
        else:
            served.append(ServedItem(next(personal_iter), index + 1, False, 1.0))
    return tuple(served)


def _by_score(item: ScoredCandidate) -> tuple[float, int]:
    return (-item.score, item.candidate.recipe_id)
