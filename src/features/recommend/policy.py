"""랭킹 정책 손잡이. 가중치는 `enums.DEFAULT_WEIGHTS` 가 갖고, 여기는 그 밖의 값입니다.

가중치와 달리 이 값들은 피처가 아니라 **절차**를 정합니다. 완화를 언제 할지, 다양성을
얼마나 줄지, 탐색을 몇 칸 어떻게 채울지입니다. `trace_params()` 가 여기서 나오며
`enums.REQUIRED_TRACE_PARAMS` 가 요구하는 키를 채웁니다. 이 값이 로그에 남지 않으면
나중에 propensity 를 재구성할 수 없습니다.

⬜ 03 의 2절은 튜닝 상수를 `config.py` 로 빼라고 합니다. `config.py` 는 세 트랙이
   함께 쓰는 파일이라 병합 뒤에 한 번에 옮깁니다. 그때까지 기본값은 여기 한 곳입니다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from features.recommend.enums import DEFAULT_WEIGHTS, PROPENSITY_SEMANTICS

#: 정책 자체의 이름. 가중치가 아니라 절차가 바뀌면 올립니다.
POLICY_ID = "reco-b-linear-v0"


@dataclass(frozen=True)
class RankingPolicy:
    """값은 전부 착수 추정치입니다 (예시값, 실제 데이터로 대체 필요).

    Mock 후보 500건에서 정한 값이라 실데이터에서 다시 맞춥니다. 바꾸면
    `fingerprint()` 가 달라져 그 이전 로그와 섞이지 않습니다.
    """

    # ── 감점 (곱연산) ────────────────────────────────────────
    penalty_recent: float = 0.7
    penalty_cooked: float = 0.5
    avoid_multiplier: float = 2.0
    avoid_cap: float = 0.8
    # ── 맛 ───────────────────────────────────────────────────
    #: 사용자 취향이 코퍼스 평균에서 이만큼(온보딩 한 단계) 떨어져야 맛을 전폭 반영합니다.
    taste_min_norm: float = 0.25
    # ── ① 후보 완화 ──────────────────────────────────────────
    max_missing: int = 2
    max_missing_relaxed: int = 4
    min_candidates: int = 20
    candidate_limit: int = 500
    # ── ③ 재정렬 ─────────────────────────────────────────────
    mmr_lambda: float = 0.7
    #: MMR 은 점수 상위 이만큼만 봅니다. 500건 전부를 보면 재정렬이 지연시간을 지배합니다.
    mmr_pool_size: int = 200
    exploration_ratio: float = 0.2
    #: 탐색 풀 크기. A 트랙 설계 5-3-3 의 상위 200 과 같습니다.
    explore_pool_size: int = 200
    #: 탐색에 쓸 후보가 슬롯 수의 이 배수보다 적으면 슬롯을 줄입니다. 억지로 채우면
    #: 목록 맨 아래 것이 상위 자리에 섭니다.
    exploration_min_pool_ratio: int = 2
    #: 균등에 배정할 탐색 슬롯 비율. 나머지가 클러스터 Thompson 입니다 (설계 5-3-5).
    uniform_share: float = 0.5
    #: Thompson 노출확률의 몬테카를로 반복 수.
    propensity_mc: int = 200
    # ── 취향 페르소나 (결정 기록 2026-09-11) ──────────────────
    #: 고른 음식으로 만든 사전 취향을 조리 이벤트 몇 건과 같은 무게로 볼지.
    picks_prior_weight: float = 12.0
    #: 직접 적은 3축 척도는 자기 보고라 그 절반입니다.
    scales_prior_weight: float = 6.0
    #: 이벤트 무게가 절반이 되는 경과 일수. 0 이하면 감쇠를 끕니다.
    persona_half_life_days: float = 90.0
    #: 주기 친화도의 세기(0~1). 0 이면 그 주기를 보지 않습니다. 1 이면 정반대 시기의
    #: 이벤트가 사라지므로 권하지 않습니다.
    season_cycle_strength: float = 0.5
    weekly_cycle_strength: float = 0.0
    daily_cycle_strength: float = 0.0
    #: 저장소가 남기는 이벤트 상한. 기본값에서는 잘리는 이벤트의 무게가 0.4% 이하라 결과에는
    #: 영향이 없고 파일 크기만 정합니다. 반감기를 상한 근처로 늘리면 결과에도 닿습니다.
    persona_max_events: int = 2000
    persona_max_event_age_days: int = 730
    #: 취향을 전혀 모르는 사용자에게 쓰는 탐색 비율. 목록을 다양하게 만듭니다.
    cold_exploration_ratio: float = 0.4

    def __post_init__(self) -> None:
        """페르소나 손잡이의 범위. 벗어나면 예외 없이 모델이 뒤집히거나(세기 > 1) 0 으로 나눕니다"""
        for name in ("picks_prior_weight", "scales_prior_weight"):
            weight = getattr(self, name)
            if not (math.isfinite(weight) and weight > 0.0):
                raise ValueError(f"{name} 은 0 보다 큰 유한한 수여야 합니다: {weight}")
        for name in ("season_cycle_strength", "weekly_cycle_strength", "daily_cycle_strength"):
            strength = getattr(self, name)
            if not 0.0 <= strength <= 1.0:
                raise ValueError(f"{name} 은 0~1 이어야 합니다: {strength}")
        if not math.isfinite(self.persona_half_life_days):
            raise ValueError(
                f"persona_half_life_days 는 유한한 수여야 합니다: {self.persona_half_life_days}"
            )
        if self.persona_max_events < 1 or self.persona_max_event_age_days < 1:
            raise ValueError(
                "persona_max_events 와 persona_max_event_age_days 는 1 이상이어야 합니다"
            )
        if not 0.0 < self.cold_exploration_ratio < 1.0:
            raise ValueError(
                f"cold_exploration_ratio 는 0 과 1 사이여야 합니다: {self.cold_exploration_ratio}"
            )

    def fingerprint(self, weights: dict[str, float] | None = None) -> str:
        """정책과 가중치 조합의 지문. 서빙 로그가 이 값으로 그때의 계산을 되살립니다."""
        body = {"policy": asdict(self), "weights": weights or DEFAULT_WEIGHTS}
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(payload).hexdigest()[:32]

    def trace_params(
        self,
        *,
        top_k: int,
        n_explore: int,
        rng_seed: int,
        max_missing_final: int,
        serving_mode: str = "real",
    ) -> dict[str, Any]:
        """`StageInfo.params` 에 실을 값. `REQUIRED_TRACE_PARAMS` 를 전부 채웁니다.

        요청마다 다른 값(페르소나 출처 등)은 `with_trace_extra()` 로 덧붙입니다.
        """
        return {
            "policy_id": POLICY_ID,
            "propensity_semantics": PROPENSITY_SEMANTICS,
            "explore_pool_size": self.explore_pool_size,
            "uniform_share": self.uniform_share,
            "propensity_mc": self.propensity_mc,
            "rng_seed": rng_seed,
            "max_missing_final": max_missing_final,
            "top_k": top_k,
            "n_explore": n_explore,
            "serving_mode": serving_mode,
        }


def with_trace_extra(params: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """추적 파라미터에 요청별 값을 덧붙입니다. 동결 키는 덮지 못합니다.

    덮어써지면 로그의 `top_k` 같은 값이 조용히 바뀌어 재현이 틀어집니다.
    """
    clash = sorted(set(extra) & set(params))
    if clash:
        raise ValueError(f"동결 키를 덮어쓸 수 없습니다: {clash}")
    return {**params, **extra}
