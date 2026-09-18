"""순위·목록 지표 (명세 5절). 표준 라이브러리만 씁니다.

양성이 없는 요청은 `None` 을 돌려주고 호출자가 분모에서 빼며 건수를 보고합니다.
ILD 의 유사도는 엔진의 `jaccard_idf` 그대로입니다. 같은 계산이 두 벌이면 한쪽이 조용히 어긋납니다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from features.recommend.engine.feature import jaccard_idf
from features.recommend.evaluation.record import EvalRecord


def _dcg(gains: Sequence[float], k: int) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def ndcg_at_k(gains: Sequence[float], k: int) -> float | None:
    """gain 은 `final_rank` 순서입니다. 양성이 하나도 없으면 None 입니다."""
    if not any(g > 0 for g in gains):
        return None
    ideal = _dcg(sorted(gains, reverse=True), k)
    return _dcg(gains, k) / ideal if ideal > 0 else 0.0


def recall_at_k(gains: Sequence[float], k: int) -> float | None:
    positives = sum(1 for g in gains if g > 0)
    if positives == 0:
        return None
    return sum(1 for g in gains[:k] if g > 0) / positives


def recall_user_level(served: Sequence[int], cooked: set[int], k: int) -> float | None:
    """상위 K 노출 가운데 유저가 창 안에 조리한 것의 비율. 위치 정보가 없는 계열입니다."""
    if not cooked:
        return None
    return len(set(served[:k]) & cooked) / len(cooked)


def gain_sequence(
    record: EvalRecord, gains: Mapping[int, float], *, include_exploration: bool = True
) -> list[float]:
    """기록의 노출 순서대로 gain 을 늘어놓습니다.

    탐색 제외 변형은 남은 항목의 순서를 그대로 씁니다.
    """
    return [
        gains.get(item.recipe_id, 0.0)
        for item in record.items
        if include_exploration or not item.is_exploration
    ]


def ild(items: Sequence[frozenset[int]], idf: Mapping[int, float]) -> float:
    """모든 쌍의 1 - IDF 가중 자카드 평균. 항목이 하나면 0 입니다."""
    pairs = [1.0 - jaccard_idf(a, b, idf) for i, a in enumerate(items) for b in items[i + 1 :]]
    return sum(pairs) / len(pairs) if pairs else 0.0


def intra_list_distance(items: Sequence[frozenset[int]]) -> float:
    """가중치 없는 ILD. mock 스크립트가 씁니다. IDF 를 비우면 엔진의 자카드와 같습니다."""
    return ild(items, {})


def catalog_coverage(recipe_ids: Iterable[int], catalog_size: int) -> float:
    return len(set(recipe_ids)) / catalog_size


def position_ctr(sequences: Sequence[Sequence[float]]) -> list[float]:
    """위치별 (gain > 0 인 노출 수) / (그 위치의 노출 수)."""
    width = max((len(s) for s in sequences), default=0)
    result: list[float] = []
    for position in range(width):
        shown = [s[position] for s in sequences if len(s) > position]
        result.append(sum(1 for g in shown if g > 0) / len(shown))
    return result


def latency_percentiles(values: Sequence[float]) -> dict[str, float | None]:
    """최근접 순위 백분위. 표본이 없으면 None 입니다."""
    if not values:
        return {"p50": None, "p95": None}
    ordered = sorted(values)

    def at(share: float) -> float:
        return float(ordered[max(0, math.ceil(share * len(ordered)) - 1)])

    return {"p50": at(0.50), "p95": at(0.95)}


def exploration_positions(records: Iterable[EvalRecord]) -> dict[int, int]:
    histogram: dict[int, int] = {}
    for record in records:
        for item in record.items:
            if item.is_exploration:
                histogram[item.final_rank] = histogram.get(item.final_rank, 0) + 1
    return histogram
