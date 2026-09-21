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
    #: 온보딩에서 고른 음식 유형으로 채우는 칸의 비율(Top-K 대비). 0 이면 유형 슬롯을 끕니다.
    #: 0.1 은 Top-20 에서 두 칸입니다 — 목록의 성격은 그대로 두고 고른 유형이 보이게 하는 값입니다.
    cuisine_slot_ratio: float = 0.1
    #: 그 칸의 절대 상한. top_k 를 크게 부르는 디버거·시뮬에서 유형이 목록을 덮지 않게 막습니다.
    cuisine_slot_max: int = 2
    #: 한 목록에 같은 요리의 판본을 몇 건까지 둘지(`engine/dish.py`). 0 이면 묶지 않습니다.
    #: 실데이터에서 묶기 전에는 Top-20 에 감자조림이 여섯 건 들었습니다(2026-09-21).
    max_per_dish: int = 1
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
        """손잡이의 범위. 벗어나면 예외 없이 모델이 뒤집히거나 0 으로 나누거나 로그가 틀립니다."""
        for name in (
            "penalty_recent",
            "penalty_cooked",
            "avoid_cap",
            "mmr_lambda",
            "uniform_share",
            "exploration_ratio",
            "cold_exploration_ratio",
            "cuisine_slot_ratio",
        ):
            share = getattr(self, name)
            if not 0.0 <= share <= 1.0:
                raise ValueError(f"{name} 은 0~1 이어야 합니다: {share}")
        for name in ("avoid_multiplier", "taste_min_norm"):
            if not (math.isfinite(getattr(self, name)) and getattr(self, name) >= 0.0):
                raise ValueError(f"{name} 은 0 이상의 유한한 수여야 합니다: {getattr(self, name)}")
        for name in (
            "min_candidates",
            "candidate_limit",
            "mmr_pool_size",
            "explore_pool_size",
            "exploration_min_pool_ratio",
            "propensity_mc",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 은 1 이상이어야 합니다: {getattr(self, name)}")
        if self.cuisine_slot_max < 0:
            raise ValueError(f"cuisine_slot_max 는 0 이상이어야 합니다: {self.cuisine_slot_max}")
        if self.max_per_dish < 0:
            raise ValueError(f"max_per_dish 는 0 이상이어야 합니다: {self.max_per_dish}")
        if not 0 <= self.max_missing <= self.max_missing_relaxed:
            raise ValueError(
                f"max_missing({self.max_missing}) 은 0 이상이고 "
                f"max_missing_relaxed({self.max_missing_relaxed}) 이하여야 합니다"
            )
        if max(self.mmr_pool_size, self.explore_pool_size) > self.candidate_limit:
            raise ValueError("MMR 풀과 탐색 풀은 후보 조회 상한(candidate_limit)을 넘지 못합니다")
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
        feature_version: str | None = None,
        cluster_version: str | None = None,
    ) -> dict[str, Any]:
        """`StageInfo.params` 에 실을 값. `REQUIRED_TRACE_PARAMS` 를 전부 채웁니다.

        요청마다 다른 값(페르소나 출처 등)은 `with_trace_extra()` 로 덧붙입니다. 배치 판 번호
        둘은 호출자가 `repository.load_batch_versions()` 로 읽어 넘깁니다 — 여기서 DB 를 보면
        정책이 순수하지 않게 되고, 목업 서빙 동안은 None 이 맞습니다.
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
            "feature_version": feature_version,
            "cluster_version": cluster_version,
        }


def with_trace_extra(params: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """추적 파라미터에 요청별 값을 덧붙입니다. 동결 키는 덮지 못합니다.

    덮어써지면 로그의 `top_k` 같은 값이 조용히 바뀌어 재현이 틀어집니다.
    """
    clash = sorted(set(extra) & set(params))
    if clash:
        raise ValueError(f"동결 키를 덮어쓸 수 없습니다: {clash}")
    return {**params, **extra}
