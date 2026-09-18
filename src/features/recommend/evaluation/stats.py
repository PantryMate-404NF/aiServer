"""정책 비교와 판정 (명세 6절). 표준 라이브러리만 씁니다.

통계 단위는 유저입니다. 한 유저의 요청들은 독립이 아니므로 유저를 복원 추출하고 그 유저의
요청 전부를 가져옵니다. 판정은 사전 등록 지표(nDCG@10 의 서빙 − coverage baseline) 하나로만
하고, 표본 부족은 예외가 아니라 "보류" 라는 결과입니다.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from features.recommend.evaluation.labels import gains
from features.recommend.evaluation.metrics import gain_sequence, ndcg_at_k, nearest_rank
from features.recommend.evaluation.record import EvalRecord

Baseline = Literal["popularity", "random", "coverage"]
BASELINES: tuple[Baseline, ...] = ("popularity", "random", "coverage")
#: 판정에 필요한 최소 유저 수 (예시값, 실제 데이터로 대체 필요)
MIN_USERS = 20
#: 부트스트랩 리샘플 횟수 (예시값, 실제 데이터로 대체 필요)
RESAMPLES = 1000
#: 검출력 계산의 목표 효과 크기 (예시값, 실제 데이터로 대체 필요)
TARGET_EFFECT = 0.02
Z_ALPHA = 1.96  # 양측 5%
Z_POWER = 0.8416  # 검출력 80%
CI_LOW, CI_HIGH = 0.025, 0.975

Labels = Sequence[Mapping[int, float]]


@dataclass(frozen=True)
class Estimate:
    mean: float
    ci95: tuple[float, float]
    n_users: int
    #: 리샘플 통계량의 표준편차. 검출력 계산이 이 값을 씁니다
    se: float


@dataclass(frozen=True)
class Interleaving:
    pairs: int
    win_rate: float
    p_value: float


@dataclass(frozen=True)
class Verdict:
    status: Literal["pass", "hold", "fail"]
    reason: str


def baseline_sequence(
    record: EvalRecord, labels: Mapping[int, float], kind: Baseline, *, seed: int
) -> list[float]:
    """노출 목록을 순서만 바꿔 같은 라벨로 gain 을 늘어놓습니다."""
    items = list(record.items)
    if kind == "random":
        # 기록마다 다른 순열이어야 합니다. 시드 하나로 만들면 모든 기록이 같은 순열을 받습니다
        random.Random(f"{seed}:{record.request_id}").shuffle(items)  # noqa: S311  # 검사용 난수
    else:
        feature = "f_popularity" if kind == "popularity" else "f_coverage"
        # None 은 뒤로. 값이 같으면 원래 순서를 지킵니다
        items.sort(
            key=lambda i: (i.features.get(feature) is None, -(i.features.get(feature) or 0.0))
        )
    return [labels.get(item.recipe_id, 0.0) for item in items]


def ndcg_by_user(
    records: Sequence[EvalRecord],
    k: int,
    *,
    labels: Labels | None = None,
    baseline: Baseline | None = None,
    seed: int = 0,
    include_exploration: bool = True,
) -> dict[str, list[float]]:
    """유저별 nDCG@K 목록. 양성이 없는 요청과 제외된 기록은 뺍니다.

    labels 는 records 와 같은 순서입니다.
    """
    result: dict[str, list[float]] = {}
    label_list = labels if labels is not None else [gains(r) for r in records]
    for record, label in zip(records, label_list, strict=True):
        if record.excluded_reason is not None:
            continue
        sequence = (
            baseline_sequence(record, label, baseline, seed=seed)
            if baseline
            else gain_sequence(record, label, include_exploration=include_exploration)
        )
        value = ndcg_at_k(sequence, k)
        if value is not None:
            result.setdefault(record.user_hash, []).append(value)
    return result


def _totals(values_by_user: Mapping[str, Sequence[float]]) -> dict[str, tuple[float, int]]:
    """유저별 (합, 건수). 리샘플마다 값을 다시 모으지 않고 이 둘만 더합니다."""
    return {user: (sum(values), len(values)) for user, values in values_by_user.items()}


def _pooled_mean(totals: Mapping[str, tuple[float, int]], users: Sequence[str]) -> float:
    total = sum(totals[u][0] for u in users)
    count = sum(totals[u][1] for u in users)
    return total / count


def _estimate(samples: list[float], observed: float, n_users: int) -> Estimate:
    samples.sort()
    return Estimate(
        mean=observed,
        ci95=(nearest_rank(samples, CI_LOW), nearest_rank(samples, CI_HIGH)),
        n_users=n_users,
        se=statistics.pstdev(samples) if len(samples) > 1 else 0.0,
    )


def bootstrap(
    values_by_user: Mapping[str, Sequence[float]], *, resamples: int = RESAMPLES, seed: int = 0
) -> Estimate | None:
    """유저를 복원 추출해 그 유저의 요청 전부를 가져온 평균의 백분위 95% CI. 유저가 없으면 None."""
    users = sorted(values_by_user)
    if not users:
        return None
    totals = _totals(values_by_user)
    rng = random.Random(seed)  # noqa: S311  # 검사용 난수
    samples = [_pooled_mean(totals, rng.choices(users, k=len(users))) for _ in range(resamples)]
    return _estimate(samples, _pooled_mean(totals, users), len(users))


def paired_bootstrap(
    a: Mapping[str, Sequence[float]],
    b: Mapping[str, Sequence[float]],
    *,
    resamples: int = RESAMPLES,
    seed: int = 0,
) -> Estimate | None:
    """같은 유저 집합에서 두 순서의 지표 차이(a − b)를 리샘플합니다. 겹치는 유저가 없으면 None."""
    users = sorted(set(a) & set(b))
    if not users:
        return None
    ta, tb = _totals(a), _totals(b)
    rng = random.Random(seed)  # noqa: S311  # 검사용 난수
    samples = []
    for _ in range(resamples):
        sample = rng.choices(users, k=len(users))
        samples.append(_pooled_mean(ta, sample) - _pooled_mean(tb, sample))
    return _estimate(samples, _pooled_mean(ta, users) - _pooled_mean(tb, users), len(users))


def _binomial_two_sided(k: int, n: int) -> float:
    """p = 0.5 인 정확 양측 검정. 관측 확률 이하인 결과의 확률을 전부 더합니다."""
    probabilities = [math.comb(n, i) / (1 << n) for i in range(n + 1)]
    return min(1.0, sum(p for p in probabilities if p <= probabilities[k] + 1e-12))


def interleaving(
    records: Sequence[EvalRecord], *, labels: Labels | None = None
) -> Interleaving | None:
    """gain > 0 인 항목의 team 으로 요청별 승패를 매기고 유저별 다수결로 승률을 냅니다."""
    votes: Counter[str] = Counter()
    pairs = 0
    label_list = labels if labels is not None else [gains(r) for r in records]
    for record, label in zip(records, label_list, strict=True):
        if not record.policies or record.excluded_reason is not None:
            continue
        credit: Counter[str] = Counter()
        for item in record.items:
            if item.team in ("A", "B") and label.get(item.recipe_id, 0.0) > 0:
                credit[item.team] += 1
        if credit["A"] == credit["B"]:
            continue
        pairs += 1
        votes[record.user_hash] += 1 if credit["A"] > credit["B"] else -1
    decided = [v for v in votes.values() if v != 0]
    if not decided:
        return None
    wins = sum(1 for v in decided if v > 0)
    return Interleaving(
        pairs=pairs, win_rate=wins / len(decided), p_value=_binomial_two_sided(wins, len(decided))
    )


def users_needed(sd: float, effect: float = TARGET_EFFECT) -> int:
    """대응 차이의 표준편차로 목표 효과 크기를 검출하는 데 필요한 유저 수."""
    return math.ceil(((Z_ALPHA + Z_POWER) * sd / effect) ** 2)


def min_detectable(sd: float, n_users: int) -> float:
    return (Z_ALPHA + Z_POWER) * sd / math.sqrt(n_users)


def power(diff: Estimate | None) -> dict[str, object]:
    """대응 부트스트랩이 실제로 낸 산포로 검출력을 냅니다. 판정과 같은 통계량이어야 합니다."""
    if diff is None or diff.n_users == 0 or diff.se == 0.0:
        return {"min_detectable": None, "users_needed_for": {str(TARGET_EFFECT): None}}
    sd = diff.se * math.sqrt(diff.n_users)  # 표준오차 → 유저 단위 표준편차
    return {
        "min_detectable": min_detectable(sd, diff.n_users),
        "users_needed_for": {str(TARGET_EFFECT): users_needed(sd)},
    }


def verdict(diff: Estimate | None, *, min_users: int = MIN_USERS) -> Verdict:
    """사전 등록 지표의 대응 차이 하나로만 판정합니다 (명세 6.3)."""
    if diff is None:
        return Verdict("hold", "비교할 유저가 없음")
    low, high = diff.ci95
    if diff.n_users < min_users:
        return Verdict("hold", f"유저 {diff.n_users}명 < {min_users}명")
    if high < 0.0:
        return Verdict("fail", f"CI 상한 {high:.4f} < 0")
    if low > 0.0:
        return Verdict("pass", f"CI 하한 {low:.4f} > 0")
    return Verdict("hold", f"CI [{low:.4f}, {high:.4f}] 가 0 을 포함")
