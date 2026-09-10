"""추천 사유 문구. 블록 점수의 z-salience 로 가장 두드러진 근거 하나를 고릅니다.

LLM 을 쓰지 않습니다. 사유는 점수 계산의 근거여야 하고, 그 근거는 코드에 있어야 재현됩니다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from features.recommend.engine.rank import (
    BLOCK_CTX,
    BLOCK_EXPIRING,
    BLOCK_MATCH,
    BLOCK_QUALITY,
    BLOCK_TASTE,
    BLOCKS,
)
from features.recommend.schema import FLAVOR_AXES
from features.recommend.stage import CorpusStats, RankConfig, ScoredCandidate, UserContext

AXIS_LABELS = {"spicy": "매운맛", "sweet": "단맛", "salty": "짠맛"}
JOSA_PAIRS = {
    "이/가": ("이", "가"),
    "은/는": ("은", "는"),
    "을/를": ("을", "를"),
    "과/와": ("과", "와"),
    "으로/로": ("으로", "로"),
}
HANGUL_BASE = 0xAC00
HANGUL_COUNT = 11172
JONGSEONG_COUNT = 28
JONGSEONG_RIEUL = 8
DEFAULT_REASON = "오늘 만들어 보기 좋은 레시피예요"
MAX_NAMED_INGREDIENTS = 2


@dataclass(frozen=True)
class BlockStats:
    mean: float
    std: float


def block_stats(scored: Sequence[ScoredCandidate]) -> dict[str, BlockStats]:
    """후보군 전체의 블록별 평균과 표준편차. 측정 불가는 빼고 셉니다."""
    stats: dict[str, BlockStats] = {}
    for name in BLOCKS:
        values = [value for item in scored if (value := item.blocks.get(name)) is not None]
        if not values:
            continue
        mean = sum(values) / len(values)
        std = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
        stats[name] = BlockStats(mean, std)
    return stats


def salient_block(
    item: ScoredCandidate,
    stats: Mapping[str, BlockStats],
    exclude: frozenset[str] = frozenset(),
) -> str | None:
    """후보군 평균에서 가장 멀리 위로 벗어난 블록. 편차가 0 이면 원점수로 가립니다.

    값이 0 인 블록은 근거가 될 수 없습니다. 임박 재료를 하나도 안 쓰는 레시피에 임박
    사유를 붙이거나, 그래서 기본 문구로 떨어지는 일을 막습니다.
    """
    best: str | None = None
    best_key = (-math.inf, -math.inf)
    for name in BLOCKS:
        value = item.blocks.get(name)
        if value is None or value <= 0.0 or name in exclude:
            continue
        stat = stats.get(name)
        z = (value - stat.mean) / stat.std if stat is not None and stat.std > 0 else 0.0
        if (z, value) > best_key:
            best, best_key = name, (z, value)
    return best


def explain(
    item: ScoredCandidate,
    ctx: UserContext,
    corpus: CorpusStats,
    stats: Mapping[str, BlockStats],
    cfg: RankConfig,
    *,
    is_exploration: bool,
) -> str:
    """사람이 읽는 한 문장. 값이 비어도 자리표시자가 남지 않게 각 갈래가 완결된 문장을 냅니다.

    맛 블록이 가장 두드러져도 사용자 취향이 코퍼스 평균과 다를 바 없으면 맛을 근거로
    대지 않습니다. 전부 "보통" 인 사용자에게 "좋아하시는 짠맛" 은 근거 없는 말입니다.
    """
    if is_exploration:
        return _explain_exploration(item)
    block = salient_block(item, stats)
    if block == BLOCK_TASTE and not _taste_is_meaningful(ctx, corpus, cfg):
        block = salient_block(item, stats, exclude=frozenset({BLOCK_TASTE}))
    if block == BLOCK_MATCH:
        return _explain_match(item, corpus)
    if block == BLOCK_EXPIRING:
        return _explain_expiring(item, ctx, corpus)
    if block == BLOCK_TASTE:
        return _explain_taste(item, ctx, corpus)
    if block == BLOCK_QUALITY:
        return "많은 분들이 만족한 인기 레시피예요"
    if block == BLOCK_CTX:
        return _explain_context(item)
    return DEFAULT_REASON


def attach_josa(word: str, pair: str) -> str:
    """받침 유무로 조사를 고릅니다. 한글이 아닌 글자로 끝나면 받침 없는 쪽을 씁니다."""
    with_batchim, without_batchim = JOSA_PAIRS[pair]
    if not word:
        return without_batchim
    code = ord(word[-1]) - HANGUL_BASE
    if not 0 <= code < HANGUL_COUNT:
        return word + without_batchim
    jongseong = code % JONGSEONG_COUNT
    if jongseong == 0:
        return word + without_batchim
    if pair == "으로/로" and jongseong == JONGSEONG_RIEUL:
        return word + without_batchim
    return word + with_batchim


def _explain_exploration(item: ScoredCandidate) -> str:
    cuisine = item.candidate.cuisine
    if cuisine is None:
        return "평소와 다른 새로운 요리에 도전해 보세요"
    return f"평소와 다른 {cuisine} 요리예요. 새로운 맛에 도전해 보세요"


def _explain_match(item: ScoredCandidate, corpus: CorpusStats) -> str:
    if not item.missing_ids:
        return "지금 있는 재료만으로 바로 만들 수 있어요"
    known = _known_names(item.missing_ids, corpus)
    total = len(item.missing_ids)
    if not known:
        return f"재료 {total}가지만 더 있으면 완성돼요"
    if total > len(known):
        return f"{', '.join(known)} 등 {total}가지만 더 있으면 완성돼요"
    return f"{', '.join(known)}만 더 있으면 완성돼요"


def _explain_expiring(item: ScoredCandidate, ctx: UserContext, corpus: CorpusStats) -> str:
    used = sorted(item.candidate.essential_ids & ctx.expiring_ids)
    if not used:
        return DEFAULT_REASON
    known = _known_names(used, corpus)
    if not known:
        return f"곧 소비기한이 끝나는 재료 {len(used)}개를 쓸 수 있어요"
    subject = ", ".join(known) if len(used) == len(known) else f"{', '.join(known)} 등"
    return f"{attach_josa(subject, '이/가')} 소비기한이 얼마 안 남아 먼저 쓰기 좋아요"


def _explain_taste(item: ScoredCandidate, ctx: UserContext, corpus: CorpusStats) -> str:
    """사용자가 그 맛을 평균보다 좋아하면 '살아 있다', 덜 좋아하면 '강하지 않다'고 말합니다.

    둘 다 정합이지만 방향이 반대입니다. 한 문구로 쓰면 매운맛 0 인 사용자에게
    "좋아하시는 매운맛" 이라고 말하게 됩니다.
    """
    axis, prefers_more = _dominant_axis(item, ctx, corpus)
    label = attach_josa(AXIS_LABELS[axis], "이/가")
    if prefers_more:
        return f"좋아하시는 {label} 잘 살아 있는 레시피예요"
    return f"{label} 강하지 않아 부담 없이 드실 수 있어요"


def _taste_is_meaningful(ctx: UserContext, corpus: CorpusStats, cfg: RankConfig) -> bool:
    """어느 축이든 사용자 취향이 코퍼스 평균에서 반 단계 이상 떨어져 있는지."""
    if corpus.flavor_mean is None:
        return False
    deviation = max(abs(u - m) for u, m in zip(ctx.taste_vec, corpus.flavor_mean, strict=True))
    return deviation >= cfg.taste_reason_min_deviation


def _dominant_axis(
    item: ScoredCandidate, ctx: UserContext, corpus: CorpusStats
) -> tuple[str, bool]:
    """유사도에 가장 크게 기여한 축과, 그 축에서 사용자가 평균보다 높은 쪽인지.

    기여는 중심화된 사용자·레시피 벡터의 곱입니다. 둘 다 평균 아래여도 곱은
    양수이므로 방향은 따로 돌려줍니다.
    """
    mean = corpus.flavor_mean or (0.0, 0.0, 0.0)
    contributions = [
        (user - center) * (recipe - center)
        for user, recipe, center in zip(ctx.taste_vec, item.candidate.flavor_vec, mean, strict=True)
    ]
    index = max(range(len(FLAVOR_AXES)), key=lambda i: contributions[i])
    return FLAVOR_AXES[index], ctx.taste_vec[index] >= mean[index]


def _explain_context(item: ScoredCandidate) -> str:
    minutes = item.candidate.cook_minutes
    if minutes is None:
        return "짧은 시간에 완성할 수 있어요"
    return f"{minutes}분이면 완성돼요"


def _known_names(ids: Sequence[int], corpus: CorpusStats) -> list[str]:
    """알려진 이름만 최대 두 개. 하나도 모르면 빈 목록입니다."""
    known = [corpus.ingredient_names[i] for i in ids if i in corpus.ingredient_names]
    return known[:MAX_NAMED_INGREDIENTS]
