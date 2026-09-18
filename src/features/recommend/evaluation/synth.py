"""합성 기록 생성기. 실데이터가 없을 때 파이프라인의 유일한 입력입니다 (명세 3.2 의 ① 대안).

뒤의 모든 단계가 이것을 픽스처로 쓰므로 탐색 슬롯, 유형 칸, Interleaving, 미노출 후보,
유저 모드 3종, 제외 대상 줄, 유저 단위 조리, 헤더를 전부 심습니다. 심은 정답(위치별 확률)은
`position_probabilities` 로 되찾을 수 있어 왕복 검사의 기준이 됩니다.

시드가 같으면 결과가 같습니다. 여기서 만든 기록은 전부 `validate` 를 통과합니다.
"""

from __future__ import annotations

import functools
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from features.recommend.enums import FEATURE_KEYS, EventType, UserMode
from features.recommend.evaluation.record import (
    LABEL_VERSION,
    METRIC_VERSION,
    EvalEvent,
    EvalHeader,
    EvalRecord,
    validate,
)
from features.recommend.stage import RankedItem, ScoredCandidate

KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 1, 0, 0, tzinfo=KST)
MODEL_VERSION = "reco-b-linear-v0"
RIVAL_MODEL_VERSION = "reco-b-linear-v1"
CONFIG_HASH = "synth-cfg"
CATALOG_SIZE = 1000
INGREDIENT_POOL = 200
#: 미노출 후보 수. 오프폴리시의 목표 정책이 여기서 다른 항목을 고를 수 있어야 합니다.
UNEXPOSED_CANDIDATES = 10
EXPLORATION_RATIO = {UserMode.COLD: 0.4, UserMode.BLENDED: 0.2, UserMode.WARM: 0.2}
INTERLEAVING_SHARE = 0.2
CUISINE_SLOT_SHARE = 0.3
CUISINE_UNMET_SHARE = 0.1
DEV_SESSION_SHARE = 0.05
SIMULATED_USER_SHARE = 0.1
NOT_REPRODUCIBLE_SHARE = 0.03
COOK_SHARE_OF_POSITIVES = 0.5
EXTRA_COOK_SHARE = 0.3
EVENT_DAYS_MAX = 13.0


@dataclass(frozen=True)
class SynthSpec:
    users: int
    requests: int
    hit_rate: float
    #: 0 이면 위치와 무관. d 면 i 위의 양성 확률이 hit_rate × (1 - d)^(i-1) 입니다.
    position_decay: float = 0.0
    seed: int = 0
    top_k: int = 10


def position_probabilities(spec: SynthSpec) -> list[float]:
    """심은 정답. 위치 i 의 양성 확률입니다."""
    return [spec.hit_rate * (1.0 - spec.position_decay) ** i for i in range(spec.top_k)]


@functools.cache
def _ingredients(recipe_id: int) -> list[int]:
    """레시피마다 고정된 재료 집합. 요청이 달라도 같은 레시피는 같은 재료입니다."""
    rng = random.Random(recipe_id)  # noqa: S311  # 검사용 합성 데이터
    return sorted(rng.sample(range(1, INGREDIENT_POOL + 1), rng.randint(3, 6)))


def _features(rng: random.Random) -> dict[str, float | None]:
    features: dict[str, float | None] = dict.fromkeys(FEATURE_KEYS)
    features["f_coverage"] = round(rng.uniform(0.3, 1.0), 3)
    features["f_missing"] = round(rng.uniform(0.3, 1.0), 3)
    features["f_popularity"] = round(rng.uniform(0.0, 1.0), 3)
    return features


def _candidate(rng: random.Random, recipe_id: int, score: float) -> ScoredCandidate:
    return ScoredCandidate(
        recipe_id=recipe_id, missing_count=0, coverage=1.0, features=_features(rng), score=score
    )


def _items(
    rng: random.Random, pool: list[ScoredCandidate], mode: UserMode, interleaved: bool, top_k: int
) -> list[RankedItem]:
    n_explore = round(top_k * EXPLORATION_RATIO[mode])
    explore_ranks = set(rng.sample(range(1, top_k + 1), n_explore))
    cuisine_rank = None
    if rng.random() < CUISINE_SLOT_SHARE:
        cuisine_rank = min(r for r in range(1, top_k + 1) if r not in explore_ranks)
    items: list[RankedItem] = []
    n_explored = 0
    for index, candidate in enumerate(pool[:top_k]):
        rank = index + 1
        exploring = rank in explore_ranks
        # 탐색 칸끼리 번갈아 채웁니다. 전체 순위로 번갈면 한 경로만 남을 수 있습니다
        source = ("uniform" if n_explored % 2 == 0 else "thompson") if exploring else None
        n_explored += exploring
        items.append(
            RankedItem(
                **candidate.model_dump(),
                final_rank=rank,
                is_exploration=exploring,
                propensity=round(rng.uniform(0.01, 0.5), 4) if exploring else 1.0,
                explore_source=source,
                is_cuisine_slot=rank == cuisine_rank,
                team=("A" if index % 2 == 0 else "B") if interleaved else None,
            )
        )
    return items


def _events(
    rng: random.Random, items: list[RankedItem], created_at: datetime, probabilities: list[float]
) -> tuple[list[EvalEvent], list[EvalEvent]]:
    events: list[EvalEvent] = []
    user_events: list[EvalEvent] = []
    for item, probability in zip(items, probabilities, strict=True):
        events.append(
            EvalEvent(
                recipe_id=item.recipe_id,
                event_type=EventType.IMPRESSION,
                position=item.final_rank,
                created_at=created_at,
            )
        )
        if rng.random() >= probability:
            continue
        cooked = rng.random() < COOK_SHARE_OF_POSITIVES
        when = created_at + timedelta(days=rng.uniform(0.0, EVENT_DAYS_MAX))
        events.append(
            EvalEvent(
                recipe_id=item.recipe_id,
                event_type=EventType.COOK if cooked else EventType.CLICK,
                position=item.final_rank,
                created_at=when,
            )
        )
        if cooked:
            user_events.append(
                EvalEvent(recipe_id=item.recipe_id, event_type=EventType.COOK, created_at=when)
            )
    if rng.random() < EXTRA_COOK_SHARE:
        user_events.append(
            EvalEvent(
                recipe_id=rng.randint(1, CATALOG_SIZE),
                event_type=EventType.COOK,
                created_at=created_at + timedelta(days=rng.uniform(0.0, EVENT_DAYS_MAX)),
            )
        )
    return events, user_events


def generate(spec: SynthSpec) -> tuple[EvalHeader, list[EvalRecord]]:
    rng = random.Random(spec.seed)  # noqa: S311  # 검사용 합성 데이터
    modes = list(UserMode)
    probabilities = position_probabilities(spec)
    n_simulated = math.ceil(spec.users * SIMULATED_USER_SHARE)
    records: list[EvalRecord] = []
    for index in range(spec.requests):
        user = index % spec.users
        mode = modes[user % len(modes)]
        created_at = START + timedelta(hours=index)
        recipe_ids = rng.sample(range(1, CATALOG_SIZE + 1), spec.top_k + UNEXPOSED_CANDIDATES)
        pool = [
            _candidate(rng, recipe_id, round(1.0 - rank / len(recipe_ids), 4))
            for rank, recipe_id in enumerate(recipe_ids)
        ]
        interleaved = rng.random() < INTERLEAVING_SHARE
        items = _items(rng, pool, mode, interleaved, spec.top_k)
        events, user_events = _events(rng, items, created_at, probabilities)
        record = EvalRecord(
            request_id=UUID(int=rng.getrandbits(128)),
            model_version=MODEL_VERSION,
            config_hash=None if rng.random() < NOT_REPRODUCIBLE_SHARE else CONFIG_HASH,
            warm_alpha=round(rng.random(), 3),
            stats_version=1,
            created_at=created_at,
            user_hash=f"u{user:04d}",
            session_prefix="d" if rng.random() < DEV_SESSION_SHARE else "c",
            user_mode=mode,
            degraded=False,
            total_latency_ms=rng.randint(10, 60),
            items=items,
            candidates=pool,
            policies=[
                {"team": "A", "model_version": MODEL_VERSION},
                {"team": "B", "model_version": RIVAL_MODEL_VERSION},
            ]
            if interleaved
            else None,
            ingredients={item.recipe_id: _ingredients(item.recipe_id) for item in items},
            events=events,
            user_events=user_events,
            is_simulated=user < n_simulated,
            cuisine_unmet=rng.random() < CUISINE_UNMET_SHARE,
        )
        records.append(validate(record))
    idf_rng = random.Random(spec.seed)  # noqa: S311  # 검사용 합성 데이터
    header = EvalHeader(
        label_version=LABEL_VERSION,
        metric_version=METRIC_VERSION,
        catalog_size=CATALOG_SIZE,
        ingredient_idf={
            i: round(idf_rng.uniform(0.2, 3.0), 3) for i in range(1, INGREDIENT_POOL + 1)
        },
        exported_at=START,
        source="synth",
    )
    return header, records
