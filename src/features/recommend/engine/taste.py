"""6축 맛 벡터. 축이 비어 있어도 계산이 되도록 축 단위로 결측을 다룹니다.

축 순서와 개수는 A 트랙의 `ingest/flavor.py` 와 `recipe_feature.flavor_vec` 을 따릅니다.
사용자 취향은 `engine/persona.py` 가 고른 음식과 행동에서 만듭니다. 직접 적은 3축 척도로만
만들면 뒤 3축이 None 입니다. **None 인 축은
분자와 분모에서 함께 빼며**, 0 으로 채우지 않습니다. 0 은 "그 맛이 없다"이고 None 은
"모른다"라서, 채우면 신맛을 모르는 사람이 신맛을 싫어하는 사람이 됩니다.

값이 없던 축에 데이터가 오면 이 파일을 고치지 않아도 그대로 켜집니다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: A 트랙 `ingest/flavor.py` 의 AXES 와 같은 순서입니다. 라벨은 사유 문구에도 씁니다.
FLAVOR_AXES: tuple[str, ...] = ("매움", "짠맛", "단맛", "신맛", "감칠맛", "기름짐")
AXIS_COUNT = len(FLAVOR_AXES)
#: 0 으로 나누는 것을 막는 값입니다. 점수에 보이는 영향은 없습니다.
EPSILON = 1e-9

FlavorVector = tuple[float | None, ...]


def as_vector(values: Sequence[float | None] | None) -> FlavorVector:
    """어떤 길이로 와도 6축으로 맞춥니다. 모자라면 None 으로 늘리고 넘치면 자릅니다."""
    if values is None:
        return (None,) * AXIS_COUNT
    taken = list(values[:AXIS_COUNT])
    taken.extend([None] * (AXIS_COUNT - len(taken)))
    return tuple(taken)


def shared_axes(*vectors: FlavorVector) -> tuple[int, ...]:
    """모든 벡터에서 값이 있는 축의 번호. 계산은 이 축들 위에서만 합니다."""
    return tuple(
        i for i in range(AXIS_COUNT) if all(v[i] is not None for v in vectors if v is not None)
    )


def centered_cosine(
    user: FlavorVector,
    recipe: FlavorVector,
    corpus_mean: FlavorVector | None,
    min_norm: float = 0.0,
) -> float | None:
    """코퍼스 평균을 양쪽에서 뺀 뒤의 코사인을 0~1 로 옮깁니다. 못 재면 None 입니다.

    빼지 않으면 모든 값이 양수라 무엇을 넣어도 0.77 근처로 몰립니다(A 트랙 DDL 주석과
    같은 이유입니다). 평균이 없거나, 공통 축이 없거나, 어느 한쪽이 평균과 같아 방향이
    없으면 측정 불가입니다.

    코사인은 크기를 버리므로 평균에서 0.03 떨어진 사용자도 방향만으로 전폭 반영됩니다.
    사용자 벡터의 거리가 min_norm 에 못 미치면 그 비율만큼 0.5 쪽으로 눌러 잡음을 줄입니다.
    """
    if corpus_mean is None:
        return None
    axes = shared_axes(user, recipe, corpus_mean)
    if not axes:
        return None
    user_delta = [_value(user[i]) - _value(corpus_mean[i]) for i in axes]
    recipe_delta = [_value(recipe[i]) - _value(corpus_mean[i]) for i in axes]
    user_norm = math.sqrt(sum(x * x for x in user_delta))
    recipe_norm = math.sqrt(sum(x * x for x in recipe_delta))
    if user_norm < EPSILON or recipe_norm < EPSILON:
        return None
    dot = sum(a * b for a, b in zip(user_delta, recipe_delta, strict=True))
    similarity = (dot / (user_norm * recipe_norm) + 1.0) / 2.0
    confidence = 1.0 if min_norm <= 0.0 else min(1.0, user_norm / min_norm)
    return _clamp(0.5 + (similarity - 0.5) * confidence)


def dominant_axis(
    user: FlavorVector, recipe: FlavorVector, corpus_mean: FlavorVector | None
) -> tuple[str, bool] | None:
    """유사도에 가장 크게 기여한 축의 이름과, 그 축에서 사용자가 평균보다 높은 쪽인지.

    기여는 중심화된 두 값의 곱입니다. 둘 다 평균 아래여도 곱은 양수이므로 방향을 따로
    돌려줍니다. 한 문구로 쓰면 매운맛을 싫어하는 사람에게 "좋아하시는 매운맛"이라고 말하게 됩니다.
    """
    if corpus_mean is None:
        return None
    axes = shared_axes(user, recipe, corpus_mean)
    if not axes:
        return None
    best = max(
        axes,
        key=lambda i: (
            (_value(user[i]) - _value(corpus_mean[i]))
            * (_value(recipe[i]) - _value(corpus_mean[i]))
        ),
    )
    return FLAVOR_AXES[best], _value(user[best]) >= _value(corpus_mean[best])


def _value(item: float | None) -> float:
    """공통 축만 넘어오므로 None 이 아닙니다. 타입 검사를 위한 좁히기입니다."""
    return 0.0 if item is None else item


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
