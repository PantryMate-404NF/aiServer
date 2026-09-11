"""설정값이 실제 계산에 닿는지 봅니다. 안 닿아도 에러가 나지 않는 자리들입니다.

여기 있는 검사는 전부 **에러 없이 조용히 틀리던 것**의 회귀 방지입니다. 값이
기본값으로 대체되거나 확인 없이 참으로 나가도 예외가 오르지 않으므로, 검사가
없으면 다음에 손잡이를 바꾼 사람이 바뀌지 않은 결과를 보게 됩니다.
"""

from __future__ import annotations

import inspect
import random
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from features.recommend.engine import mock, rerank, serendipity
from features.recommend.engine.context import (
    CorpusStats,
    RecipeFeature,
    UserContext,
    build_context,
)
from features.recommend.engine.persona import TasteProfile, derive_persona
from features.recommend.engine.score import score_all
from features.recommend.engine.serendipity import ClusterStats
from features.recommend.policy import RankingPolicy
from features.recommend.router import health as health_endpoint
from features.recommend.stage import Candidate

CORPUS = CorpusStats(flavor_mean=(0.5,) * 6)
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))


# ─────────────────────────────────────────────────────────────────
# /health 는 DB 를 확인하고 답합니다
# ─────────────────────────────────────────────────────────────────
def test_health_reports_the_measured_db_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 가 죽었으면 죽었다고 답합니다. 확인 없이 초록을 내보내면 장애를 못 봅니다."""
    for probed in (True, False):
        monkeypatch.setattr("infra.db.healthy", lambda value=probed: value)
        assert health_endpoint().db is probed


def test_health_payload_takes_no_default_for_the_db_flag() -> None:
    """`db_ok` 에 기본값이 생기면 확인을 빠뜨려도 `HealthOut.db` 가 True 로 나갑니다."""
    parameter = inspect.signature(mock.health_payload).parameters["db_ok"]
    assert parameter.default is inspect.Parameter.empty


# ─────────────────────────────────────────────────────────────────
# 추적에 싣는 MC 반복 수가 실제로 쓰입니다
# ─────────────────────────────────────────────────────────────────
def test_mixed_exploration_forwards_mc_to_thompson(
    monkeypatch: pytest.MonkeyPatch, rng: random.Random
) -> None:
    seen: list[int] = []

    def spy(
        clusters: Sequence[int], stats: ClusterStats, k: int, mc: int = 200, seed: int = 0
    ) -> dict[int, float]:
        seen.append(mc)
        return {}

    monkeypatch.setattr(serendipity, "thompson_propensity", spy)
    rows: list[dict[str, Any]] = [
        {"recipe_id": i, "score": 1.0 - i / 100, "cluster_id": i % 4} for i in range(40)
    ]
    serendipity.mixed_exploration(rows, ClusterStats(), rng, k=4, mc=37)
    assert seen == [37]


def test_rerank_uses_the_mc_it_writes_to_the_trace(
    monkeypatch: pytest.MonkeyPatch,
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
    rng: random.Random,
) -> None:
    """`policy.trace_params()` 가 적는 값과 탐색이 실제로 쓰는 값이 같아야 합니다.

    다르면 off-policy 평가의 분모가 조용히 틀립니다. 확률과 그 역수처럼 둘 다
    양수라 섞여도 에러가 나지 않습니다.
    """
    policy = RankingPolicy(propensity_mc=37)
    seen: list[int] = []

    original = serendipity.mixed_exploration

    def spy(*args: object, **kwargs: object) -> tuple[list[dict[str, Any]], dict[int, float]]:
        seen.append(int(str(kwargs["mc"])))
        return original(*args, **kwargs)  # type: ignore[arg-type]  # 원본에 그대로 넘깁니다

    monkeypatch.setattr(serendipity, "mixed_exploration", spy)

    recipes = {i: make_recipe(i, essential=[i * 3, i * 3 + 1]) for i in range(40)}
    candidates = [make_candidate(i, coverage=1.0 - i / 100, cluster_id=i % 5) for i in range(40)]
    ctx = make_context(pantry=range(200))
    scored = score_all(candidates, recipes, ctx, CORPUS, policy)
    rerank.rerank(scored, recipes, ctx, CORPUS, policy, rng, top_k=20)

    assert seen, "탐색 슬롯이 하나도 안 뽑혀 검사가 공허합니다"
    logged = policy.trace_params(top_k=20, n_explore=len(seen), rng_seed=1, max_missing_final=2)
    assert set(seen) == {policy.propensity_mc} == {logged["propensity_mc"]}


# ─────────────────────────────────────────────────────────────────
# 문맥은 페르소나를 반드시 받습니다
# ─────────────────────────────────────────────────────────────────
def test_build_context_requires_a_persona() -> None:
    """기본값이 생기면 빠뜨린 호출자가 조용히 취향 없는 사용자가 됩니다. 목록이 통째로 바뀝니다."""
    with pytest.raises(TypeError):
        build_context(user_id=1)  # type: ignore[call-arg]  # 인자 누락을 확인하는 검사입니다


def test_context_taste_is_exactly_the_persona_vector() -> None:
    """같은 값을 두 곳에 두지 않습니다(D-27). 문맥의 맛은 페르소나 그대로입니다."""
    profile = TasteProfile(user_id=1, pick_flavors=((0.1, 0.2, 0.3, 0.4, 0.5, 0.6),))
    made = derive_persona(profile, NOW, RankingPolicy())
    ctx = build_context(user_id=1, persona=made)
    assert ctx.taste_vec == made.vec
    assert ctx.persona is made
