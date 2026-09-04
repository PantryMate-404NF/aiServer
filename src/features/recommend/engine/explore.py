"""Exploration 슬롯과 Interleaving — 편향 없는 학습 신호를 만드는 두 장치.

둘 다 **지금 안 하면 나중에 소급할 수 없다.** 과거 로그에 없는 무작위성은
사후에 만들어낼 수 없기 때문이다.
"""
from __future__ import annotations

import random
from typing import Any, Sequence


def exploration_slots(top_k: int, n: int, rng: random.Random) -> list[int]:
    """무작위 삽입 위치 n개를 고른다.

    🔴 **위치를 고정하면(기존 6위·14위) position bias 곡선을 구할 수 없다.** *(v1.9 수정)*

    고정 위치에서 얻는 것은 "위치 6 과 14 의 검사확률 비" 하나뿐이다.
    IPS/SNIPS 에 필요한 것은 위치 1~20 전체의 검사확률 곡선인데,
    두 점으로는 곡선이 그려지지 않는다.

    위치를 매 요청 무작위로 뽑으면 무작위 아이템이 모든 위치에 균등 분포하고,
    **위치별 CTR 이 곧 검사확률 곡선**이 된다. 실험 `ranker-lgbm-v2-ipw` (설계 6-9 #8)
    가 비로소 성립한다.

    비용은 동일하다 — 삽입 개수는 그대로다.
    """
    n = min(n, top_k)
    return sorted(rng.sample(range(top_k), n))


def propensity_of(position: int, explore_positions: Sequence[int],
                  pool_size: int, top_k: int, n_explore: int) -> float:
    """이 위치의 아이템이 노출될 확률. IPS 의 분모다.

    - exploration 슬롯: 풀에서 균등 추출 → `n_explore / (top_k · pool_size)` 근사
    - 결정적 슬롯: 로그 정책이 결정적이므로 1.0

    결정적 슬롯의 1.0 은 "편향이 없다"는 뜻이 아니라 **"이 로그로는 보정할 수 없다"**
    는 뜻이다. 그래서 exploration 슬롯이 유일한 보정 근거가 된다 (설계 5-3-3).
    """
    if position in explore_positions:
        return max(1e-6, n_explore / max(1, top_k * pool_size))
    return 1.0


# 🔴 **이 함수를 서빙 경로에서 쓰지 말 것** (v2.9).
#
#    위 식은 **(아이템, 위치)** 확률이고, 실제로 쓰는
#    `serendipity.mixed_exploration()` 은 **아이템** 확률을 돌려준다 —
#    `p_uniform = n_uniform / len(pool)`. 같은 조건에서 값이 10배 다르다
#    (0.0005 vs 0.005). 두 정의로 찍힌 로그가 섞이면 IPS 가 사후에 구분되지 않는다.
#
#    현재 이 함수는 **어디에서도 호출되지 않고** `rank/__init__.py` 에도 없다.
#    남겨 둔 이유는 position bias 곡선을 따로 추정할 때 참고식으로 쓰기 위해서다.
#    서빙 propensity 의 정본은 `mixed_exploration` 의 반환값 하나뿐이다.


def interleave(a: Sequence[Any], b: Sequence[Any], rng: random.Random,
               top_k: int, key=lambda x: x) -> list[tuple[Any, str]]:
    """Team-Draft Interleaving (Radlinski et al.).

    **유저 100명에서 A/B 테스트는 검정력이 없다.** 50명 vs 50명, 각 20 impression 으로
    CTR 차이를 검출하는 것은 불가능하다 (설계 5-7-2 계산).

    Interleaving 은 같은 유저에게 두 랭커의 결과를 섞은 **한 목록**을 보여주고
    클릭이 어느 팀 것인지로 승패를 센다. 유저 간 분산이 사라져 필요 표본이
    1~2 자릿수 줄어든다.

    공정성의 핵심은 **매 라운드 동전을 던져 선공을 정하는 것**이다.
    번갈아 고정하면 A 가 항상 1위를 가져가 position bias 가 팀에 붙는다.

    Returns:
        [(item, 'A'|'B'), ...] — 길이 top_k
    """
    ia = ib = 0
    seen: set = set()
    out: list[tuple[Any, str]] = []
    la, lb = list(a), list(b)

    while len(out) < top_k and (ia < len(la) or ib < len(lb)):
        first_is_a = rng.random() < 0.5
        for team, lst, idx_name in (("A", la, "ia"), ("B", lb, "ib")) if first_is_a \
                else (("B", lb, "ib"), ("A", la, "ia")):
            idx = ia if idx_name == "ia" else ib
            while idx < len(lst) and key(lst[idx]) in seen:
                idx += 1
            if idx < len(lst) and len(out) < top_k:
                seen.add(key(lst[idx]))
                out.append((lst[idx], team))
                idx += 1
            if idx_name == "ia":
                ia = idx
            else:
                ib = idx
    return out
