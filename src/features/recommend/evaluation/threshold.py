"""임계값을 **고르지 않고 계산한다**.

## 왜 필요한가

4-4-1 의 실패는 개별 실수가 아니라 반복되는 패턴이다.

    "trgm 유사도 0.6 이면 같은 재료로 본다"  →  실측 재현율 0%

숫자를 눈대중으로 정하면 그것이 무엇을 보장하는지 아무도 모른다.
같은 종류의 손으로 고른 숫자가 설계에 아직 여럿 남아 있다.

    MMR λ=0.7 · N_WARM=20 · 캡 3/4/2 · 유사 레시피 0.7 · 중복 제거 임계값

## 대신 하는 것

라벨된 표본을 **캘리브레이션 집합**으로 두고, **목표 정밀도를 만족하는 최저 임계값**을
고른다. 임계값이 "적당해 보여서"가 아니라 **"자동 확정 정밀도 99% 를 보장하는 값"** 이
되고, 커버리지는 그 결과로 나오는 숫자가 된다.

    τ = min{ t : precision(t) ≥ target }

목표 정밀도를 만족하는 t 가 없으면 **`None` 을 돌려준다.** 그것은 실패가 아니라
"이 점수로는 자동 확정이 불가능하다"는 결론이며, 4-4-1 이 실제로 그랬다.
전부 검수 큐로 보내는 것이 정답이다.

## 보수적 보정

유한 표본에서 관측 정밀도는 낙관적이다. `conservative=True` 면 Wilson 하한을 쓴다 —
"관측 정밀도 100%(10건 중 10건)"를 그대로 믿지 않는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float | None
    precision: float
    recall: float
    coverage: float               # 자동 확정된 비율 (= 사람이 안 봐도 되는 비율)
    n_auto: int
    n_total: int
    target: float
    conservative: bool

    @property
    def usable(self) -> bool:
        return self.threshold is not None

    def report(self) -> str:
        if not self.usable:
            return (f"🔴 정밀도 {self.target:.0%} 를 만족하는 임계값이 없다 — "
                    f"이 점수로는 자동 확정 불가. 전부 검수 큐로 보낸다. (n={self.n_total})")
        return (f"τ={self.threshold:.3f} → 정밀도 {self.precision:.1%}"
                f"{' (Wilson 하한)' if self.conservative else ''} · "
                f"재현율 {self.recall:.1%} · 자동확정 {self.n_auto}/{self.n_total} "
                f"({self.coverage:.0%}) · 나머지는 검수")


def _wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    """이항 비율의 Wilson 신뢰구간 하한. n 이 작을 때 낙관을 깎는다."""
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / d)


def calibrate(scores: Sequence[float], labels: Sequence[int],
              target_precision: float = 0.99,
              conservative: bool = True) -> ThresholdResult:
    """목표 정밀도를 보장하는 최저 임계값.

    Args:
        scores: 점수. 클수록 positive 라고 주장하는 값.
        labels: 1 = 실제 positive, 0 = negative.
        target_precision: 자동 확정 구간에서 보장할 정밀도.
        conservative: True 면 Wilson 하한으로 판정 (권장).

    Returns:
        `ThresholdResult`. `threshold is None` 이면 자동 확정 불가.

    가장 낮은 τ 를 고르는 이유는 **커버리지를 최대화**하기 위해서다.
    정밀도 제약을 만족하는 한, 사람이 볼 건수는 적을수록 좋다.
    """
    if len(scores) != len(labels):
        raise ValueError("scores 와 labels 의 길이가 다르다")
    n_total = len(scores)
    n_pos = sum(labels)
    if n_total == 0:
        return ThresholdResult(None, 0.0, 0.0, 0.0, 0, 0, target_precision, conservative)

    pairs = sorted(zip(scores, labels), key=lambda x: -x[0])
    best: ThresholdResult | None = None
    tp = fp = 0
    for i, (s, y) in enumerate(pairs):
        tp += y
        fp += 1 - y
        # 같은 점수가 이어지면 경계를 그 사이에 그을 수 없다
        if i + 1 < len(pairs) and pairs[i + 1][0] == s:
            continue
        n_auto = tp + fp
        prec = _wilson_lower(tp, n_auto) if conservative else tp / n_auto
        if prec >= target_precision:
            best = ThresholdResult(
                threshold=s,
                precision=prec,
                recall=tp / n_pos if n_pos else 0.0,
                coverage=n_auto / n_total,
                n_auto=n_auto, n_total=n_total,
                target=target_precision, conservative=conservative)
    return best or ThresholdResult(None, 0.0, 0.0, 0.0, 0, n_total,
                                   target_precision, conservative)


def min_samples_for(target: float, observed_precision: float = 1.0,
                    z: float = 1.96) -> int:
    """목표 정밀도를 **보장**하려면 라벨이 몇 건 필요한가.

    완벽한 분리(오탐 0)를 얻어도 표본이 적으면 보장할 수 없다.
    `289건 전부 정답` 의 Wilson 하한은 98.7% 이지 100% 가 아니다.

    이 함수가 검수 스프린트의 라벨 목표치를 정한다 — "몇 건 모아야 자동 확정을
    켤 수 있는가"에 숫자로 답한다.
    """
    n = 1
    while n < 100_000:
        k = round(n * observed_precision)
        if _wilson_lower(k, n, z) >= target:
            return n
        n += 1
    return -1
