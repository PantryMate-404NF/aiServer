"""오프폴리시 추정 (명세 7절). item-position IPS 가정의 SNIPS 와 지원 진단.

보상은 항목별로 더해지고 `propensity` 는 그 항목이 노출될 주변 확률입니다. 탐색 항목이 다른
항목의 반응을 빼앗는 슬레이트 효과는 이 추정기가 보지 못하며 리포트가 그것을 적습니다.
`usable=False` 는 예외가 아니라 결과이고 진단 수치와 분리할 수 없게 함께 돌려줍니다.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from features.recommend.engine.context import RecipeFeature
from features.recommend.engine.rerank import mmr_select
from features.recommend.engine.score import weighted_score
from features.recommend.evaluation.labels import gains
from features.recommend.evaluation.record import EvalRecord
from features.recommend.policy import RankingPolicy

TargetPolicy = Callable[[EvalRecord], list[int]]
#: 유효 표본 수가 유저 수의 이 비율 밑이면 쓸 수 없습니다 (예시값, 실제 데이터로 대체 필요)
ESS_MIN_SHARE = 0.1


@dataclass(frozen=True)
class SourceDiagnostic:
    n: int
    ess: float
    snips: float | None


@dataclass(frozen=True)
class OffPolicyEstimate:
    target: str
    snips: float | None
    ess: float
    n_users: int
    unsupported_ratio: float
    propensity_below_one_ratio: float
    exposures_needed: int
    by_source: dict[str, SourceDiagnostic]
    usable: bool
    reasons: list[str] = field(default_factory=list)


def _ess(weights: Sequence[float]) -> float:
    return sum(weights) ** 2 / sum(w * w for w in weights) if weights else 0.0


def _snips(weights: Sequence[float], rewards: Sequence[float]) -> float | None:
    total = sum(weights)
    if total == 0.0:
        return None
    return sum(w * g for w, g in zip(weights, rewards, strict=True)) / total


def logged_policy(record: EvalRecord) -> list[int]:
    """로그 정책 자신. 노출 순서 그대로입니다."""
    return [item.recipe_id for item in record.items]


def weight_swap_policy(
    weights: Mapping[str, float],
    *,
    idf: Mapping[int, float],
    lambda_: float = RankingPolicy().mmr_lambda,
) -> TargetPolicy:
    """저장된 17개 특성값에 새 가중치를 곱하고 로그의 penalty 를 곱한 뒤 MMR 까지 태웁니다.

    탐색 슬롯은 넣지 않습니다. 후보의 재료는 기록에 노출분만 있으므로 미노출 후보는
    재료가 빈 것으로 두며, 그 차이는 리포트가 적습니다.
    """

    def target(record: EvalRecord) -> list[int]:
        pool = record.candidates if record.candidates is not None else list(record.items)
        rescored = [
            c.model_copy(update={"score": weighted_score(c.features, weights) * c.penalty})
            for c in pool
        ]
        recipes = {
            rid: RecipeFeature(recipe_id=rid, all_ids=frozenset(ids))
            for rid, ids in record.ingredients.items()
        }
        chosen = mmr_select(rescored, recipes, count=len(record.items), idf=idf, lambda_=lambda_)
        return [c.recipe_id for c, _ in chosen]

    return target


def estimate(
    records: Sequence[EvalRecord],
    target: TargetPolicy,
    *,
    name: str,
    labels: Sequence[Mapping[int, float]] | None = None,
) -> OffPolicyEstimate:
    """labels 는 records 와 같은 순서의 gain 입니다. 없으면 여기서 계산합니다."""
    weights: list[float] = []
    rewards: list[float] = []
    per_source: dict[str, tuple[list[float], list[float]]] = {}
    users: set[str] = set()
    n_target = n_unsupported = n_served = n_below_one = 0
    label_list = labels if labels is not None else [gains(r) for r in records]
    for record, label in zip(records, label_list, strict=True):
        if record.excluded_reason is not None:
            continue
        users.add(record.user_hash)
        served = {item.recipe_id: item for item in record.items}
        n_served += len(served)
        n_below_one += sum(1 for item in record.items if (item.propensity or 1.0) < 1.0)
        for recipe_id in target(record)[: len(record.items)]:
            n_target += 1
            item = served.get(recipe_id)
            if item is None:
                n_unsupported += 1
                continue
            w = 1.0 / (item.propensity or 1.0)
            weights.append(w)
            rewards.append(label.get(recipe_id, 0.0))
            if item.explore_source:
                bucket = per_source.setdefault(item.explore_source, ([], []))
                bucket[0].append(w)
                bucket[1].append(label.get(recipe_id, 0.0))
    ess = _ess(weights)
    threshold = ESS_MIN_SHARE * len(users)
    unsupported_ratio = n_unsupported / n_target if n_target else 0.0
    reasons: list[str] = []
    if not users:
        reasons.append("평가할 기록이 없음")
    if unsupported_ratio > 0.0:
        reasons.append(f"목표 정책 상위 K 의 {unsupported_ratio:.1%} 가 로그에 미지원")
    if users and ess < threshold:
        reasons.append(f"ESS {ess:.1f} < 유저 수의 {ESS_MIN_SHARE:.0%} ({threshold:.1f})")
    return OffPolicyEstimate(
        target=name,
        snips=_snips(weights, rewards),
        ess=ess,
        n_users=len(users),
        unsupported_ratio=unsupported_ratio,
        propensity_below_one_ratio=n_below_one / n_served if n_served else 0.0,
        # ponytail: 노출 1건이 ESS 를 최대 1 올린다는 상한으로 역산. 가중치 분포를 보지 않습니다
        exposures_needed=max(0, math.ceil(threshold - ess)),
        by_source={
            source: SourceDiagnostic(n=len(ws), ess=_ess(ws), snips=_snips(ws, gs))
            for source, (ws, gs) in per_source.items()
        },
        usable=not reasons,
        reasons=reasons,
    )
