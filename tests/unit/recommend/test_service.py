"""②③ 조립. 계약 준수, 로그에 남는 값, 12인 프로필 전수 통과."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from features.recommend import service
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext
from features.recommend.engine.persona import (
    PersonaSource,
    TasteProfile,
    cold_persona,
    derive_persona,
)
from features.recommend.engine.rank import check_trace_params, keep_candidates, merge_served_detail
from features.recommend.engine.rerank import exploration_ratio
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import CANDIDATE_KEEP, FEATURE_KEYS, EventType, Stage, UserMode
from features.recommend.policy import POLICY_ID, RankingPolicy, with_trace_extra
from features.recommend.profile_store import JsonProfileStore
from features.recommend.schema import EventIn, OnboardingIn
from features.recommend.stage import Candidate


def _profile(profiles: list[dict[str, Any]], user_id: int) -> dict[str, Any]:
    return next(row for row in profiles if row["user_id"] == user_id)


def test_pipeline_meets_the_stage_contract(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    profile = _profile(personas, 1005)
    ctx = context_for(profile)
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(
        candidates, recipes, ctx, corpus, policy, rng, top_k=20, rng_seed=7
    )

    assert len(result.items) == 20
    assert [item.final_rank for item in result.items] == list(range(1, 21))
    assert len({item.recipe_id for item in result.items}) == 20
    for item in result.items:
        assert set(item.features) == set(FEATURE_KEYS)
        assert 0.0 <= item.score <= 1.0
        assert item.propensity is not None and 0.0 < item.propensity <= 1.0


def test_trace_params_carry_every_frozen_key(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """값이 아니라 정의가 소급 불가입니다. 키가 없으면 propensity 를 재구성할 수 없습니다."""
    ctx = context_for(_profile(personas, 1002))
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng, rng_seed=3)

    for stage in result.stages:
        assert check_trace_params(stage.params) == []
        assert stage.params["policy_id"] == POLICY_ID
        assert stage.params["rng_seed"] == 3
    assert [stage.name for stage in result.stages] == [Stage.RANKING, Stage.RERANK]


def test_score_stats_show_the_spread(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """랭커가 조용히 납작해지는 것을 이 값으로 알아챕니다."""
    ctx = context_for(_profile(personas, 1005))
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    stats = result.stages[0].score_stats
    assert stats["min"] <= stats["p50"] <= stats["max"]
    assert stats["max"] > stats["min"]


def test_served_items_survive_the_candidate_cut(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """탐색 아이템은 상위 50 밖으로 떨어질 수 있고, 그것이 propensity != 1 인 유일한 행입니다."""
    ctx = context_for(_profile(personas, 1005))
    candidates = retrieve(ctx.pantry_ids, max_minutes=ctx.max_cook_minutes)
    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)
    served = [item.recipe_id for item in result.items]

    merged = merge_served_detail(result.scored, result.items)
    kept = keep_candidates(merged, served)

    assert set(served) <= {row.recipe_id for row in kept}
    assert len(kept) >= min(CANDIDATE_KEEP["real"], len(result.scored))
    explored = {item.recipe_id for item in result.items if item.is_exploration}
    logged = {row.recipe_id for row in kept if getattr(row, "propensity", 1.0) not in (None, 1.0)}
    assert explored <= logged


def test_every_profile_is_served(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """후보가 모자란 프로필은 완화 계획을 따라 다시 조회하면 채워져야 합니다."""
    for profile in personas:
        ctx = context_for(profile)
        top_k = int(profile.get("top_k", 20))
        candidates = _retrieve_with_fallback(retrieve, ctx, policy, top_k)

        result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng, top_k=top_k)

        assert len(result.items) == min(top_k, len(candidates)), profile["user_id"]
        assert len(result.items) >= 10, profile["user_id"]


def test_allergy_cut_is_the_retrieval_stage_not_the_ranking(
    personas: list[dict[str, Any]],
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    context_for: Callable[..., UserContext],
    retrieve: Callable[..., list[Candidate]],
    allergy_ids: Callable[[Iterable[str]], frozenset[int]],
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """제외는 ① 에서만 합니다. ② 는 걸러진 뒤의 후보만 봅니다."""
    profile = _profile(personas, 1006)
    banned = allergy_ids(profile["allergy_group_codes"])
    ctx = context_for(profile)
    candidates = retrieve(ctx.pantry_ids, allergy=banned, max_minutes=ctx.max_cook_minutes)

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    assert result.items
    assert all(not (recipes[item.recipe_id].all_ids & banned) for item in result.items)


def test_counters_are_isolated_from_ranking() -> None:
    """로그 실패를 삼키되 반드시 셉니다. 카운터는 프로세스 메모리입니다."""
    service.reset_counters()
    service.bump("log_write_failed")
    service.bump("log_write_failed", 2)

    assert service.counters()["log_write_failed"] == 3

    service.reset_counters()
    assert service.counters() == {}


def _retrieve_with_fallback(
    retrieve: Callable[..., list[Candidate]],
    ctx: UserContext,
    policy: RankingPolicy,
    top_k: int,
) -> list[Candidate]:
    """`engine/candidate.py` 의 계획대로 다시 조회합니다. 운영에서는 repository 가 합니다."""
    from features.recommend.engine import candidate as plan_module

    plan = plan_module.first_plan(policy)
    rows = retrieve(ctx.pantry_ids, max_missing=plan.max_missing, max_minutes=ctx.max_cook_minutes)
    ratio = exploration_ratio(ctx, policy)
    while True:
        nxt = plan_module.next_plan(plan, len(rows), policy, top_k, ratio)
        if nxt is None:
            return plan_module.dedupe(rows)
        plan = nxt
        wider = retrieve(
            ctx.pantry_ids,
            max_missing=plan.max_missing,
            max_minutes=ctx.max_cook_minutes,
            ignore_missing=plan.stage == plan_module.FALLBACK_POPULARITY,
        )
        rows = plan_module.dedupe([*rows, *wider])


def test_fingerprint_changes_with_the_policy(policy: RankingPolicy) -> None:
    """지문이 같으면 그때의 계산을 되살릴 수 있어야 합니다. 값 하나만 달라도 달라집니다."""
    assert policy.fingerprint() == RankingPolicy().fingerprint()
    assert policy.fingerprint() != RankingPolicy(mmr_lambda=0.8).fingerprint()
    assert policy.fingerprint() != policy.fingerprint({"f_coverage": 0.9})


def test_policy_knobs_stay_within_their_meaning(policy: RankingPolicy) -> None:
    assert 0.0 < policy.uniform_share <= 1.0
    assert 0.0 < policy.exploration_ratio < 1.0
    assert policy.max_missing < policy.max_missing_relaxed
    assert policy.mmr_pool_size <= policy.candidate_limit
    with pytest.raises(KeyError):
        _ = policy.trace_params(top_k=20, n_explore=4, rng_seed=0, max_missing_final=2)["없는키"]


# ─────────────────────────────────────────────────────────────────
# 취향 페르소나 서비스
# ─────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=KST)


@pytest.fixture
def persona_service(
    tmp_path: Path, presented: tuple[FlavorVector, ...], policy: RankingPolicy
) -> service.PersonaService:
    return service.PersonaService(JsonProfileStore(tmp_path), presented, policy)


def test_onboarding_keeps_the_originals_and_derives_from_picks(
    persona_service: service.PersonaService, presented: tuple[FlavorVector, ...]
) -> None:
    made = persona_service.save_onboarding(1, picks=[0, 3], scales=[4, 0, 2], now=NOW)

    stored = persona_service.store.load(1)
    assert stored is not None
    assert stored.picks == (0, 3) and stored.pick_flavors == (presented[0], presented[3])
    assert stored.scales == (1.0, 0.0, 0.5)
    assert made.prior_source is PersonaSource.PICKS
    assert made.vec == pytest.approx(
        tuple((a + b) / 2 for a, b in zip(presented[0], presented[3], strict=True))
    )


def test_onboarding_rejects_an_index_outside_the_presented_list(
    persona_service: service.PersonaService,
) -> None:
    service.reset_counters()
    with pytest.raises(ValueError, match="제시 목록 밖"):
        persona_service.save_onboarding(1, picks=[0, 20], scales=[2, 2, 2], now=NOW)
    assert service.counters()["persona_pick_out_of_range"] == 1
    assert persona_service.store.load(1) is None


def test_re_onboarding_keeps_recorded_events(persona_service: service.PersonaService) -> None:
    persona_service.save_onboarding(1, picks=[0], scales=None, now=NOW)
    events = [EventIn(user_id=1, event_type=EventType.COOK, recipe_id=42)]
    persona_service.record_events(events, lambda _: (0.9,) * 6, NOW)

    persona_service.save_onboarding(1, picks=[1, 2], scales=[1, 1, 1], now=NOW + timedelta(days=1))

    stored = persona_service.store.load(1)
    assert stored is not None and len(stored.events) == 1 and stored.picks == (1, 2)


def test_record_events_stores_positive_signals_and_counts_the_rest(
    persona_service: service.PersonaService,
) -> None:
    service.reset_counters()
    flavors: dict[int, FlavorVector] = {1: (1.0,) * 6, 2: (0.0,) * 6}
    events = [
        EventIn(user_id=7, event_type=EventType.COOK, recipe_id=1),
        EventIn(user_id=7, event_type=EventType.DISMISS, recipe_id=2),
        EventIn(user_id=7, event_type=EventType.CLICK, recipe_id=999),
        EventIn(user_id=7, event_type=EventType.SEARCH, recipe_id=None),
        EventIn(user_id=8, event_type=EventType.RATING, recipe_id=2, value=5.0),
    ]

    stored = persona_service.record_events(events, flavors.get, NOW)

    assert stored == 2
    counts = service.counters()
    assert counts["persona_event_ignored"] == 1
    assert counts["persona_recipe_unknown"] == 1
    assert counts["persona_events_stored"] == 2
    seven = persona_service.store.load(7)
    assert seven is not None and [e.recipe_id for e in seven.events] == [1]
    # 사전 취향 없이 조리 한 건(무게 1.0)은 3축 척도 무게(6.0)에 못 미쳐 아직 '행동' 이 아닙니다.
    assert persona_service.persona_for(7, NOW).mode is UserMode.BLENDED
    assert persona_service.persona_for(8, NOW).vec == (0.0,) * 6


def test_record_events_prunes_to_the_policy_limits_keeping_the_newest(
    tmp_path: Path, presented: tuple[FlavorVector, ...]
) -> None:
    policy = RankingPolicy(persona_max_events=3)
    svc = service.PersonaService(JsonProfileStore(tmp_path), presented, policy)
    events = [EventIn(user_id=1, event_type=EventType.COOK, recipe_id=i) for i in range(5)]

    svc.record_events(events, lambda _: (0.5,) * 6, NOW)

    stored = svc.store.load(1)
    assert stored is not None and [e.recipe_id for e in stored.events] == [2, 3, 4]


def test_persona_for_an_unknown_user_is_cold_and_counted(
    persona_service: service.PersonaService,
) -> None:
    service.reset_counters()
    made = persona_service.persona_for(12345, NOW)
    assert made.is_cold and made.prior_source is PersonaSource.NONE
    assert service.counters()["persona_missing"] == 1


def test_an_unreadable_profile_degrades_to_cold_and_is_counted(
    persona_service: service.PersonaService, tmp_path: Path
) -> None:
    """저장소는 예외를 올리고 서빙은 받아서 셉니다. 추천이 파일 하나 때문에 죽지 않습니다."""
    service.reset_counters()
    target = JsonProfileStore(tmp_path).path(3)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{broken", encoding="utf-8")

    made = persona_service.persona_for(3, NOW)

    assert made.is_cold
    assert service.counters()["persona_profile_unreadable"] == 1


def test_trace_carries_the_persona_source(
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
    make_context: Callable[..., UserContext],
) -> None:
    """평가가 취향 유무로 갈라 볼 수 있어야 합니다. 동결 키 10종은 그대로입니다."""
    made = derive_persona(TasteProfile(user_id=1, scales=(0.5, 0.5, 0.5)), NOW, policy)
    ctx = make_context(pantry=range(1, 61), persona=made, taste_vec=made.vec)
    candidates = [
        Candidate(recipe_id=r, missing_count=0, missing_ids=[], coverage=1.0)
        for r in list(recipes)[:30]
    ]

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    params = result.stages[-1].params or {}
    assert check_trace_params(params) == []
    assert params["persona_source"] == "scales" and params["persona_mode"] == "onboarding"
    assert params["persona_events"] == 0


def test_extra_trace_keys_cannot_shadow_a_frozen_key(policy: RankingPolicy) -> None:
    params = policy.trace_params(top_k=20, n_explore=0, rng_seed=1, max_missing_final=2)
    assert with_trace_extra(params, {"persona_source": "picks"})["persona_source"] == "picks"
    with pytest.raises(ValueError, match="동결 키"):
        with_trace_extra(params, {"top_k": 5})


# ─────────────────────────────────────────────────────────────────
# 3회차 복기에서 찾은 구멍
# ─────────────────────────────────────────────────────────────────
def test_onboarding_rejects_a_scale_outside_the_contract_instead_of_clamping(
    persona_service: service.PersonaService,
) -> None:
    """잘라 넣으면 5 는 4 와 같아지고 아무도 모릅니다.

    계약(0~4)을 바꾸는 곳은 SCALE_MAX 하나입니다.
    """
    service.reset_counters()
    with pytest.raises(ValueError, match="척도"):
        persona_service.save_onboarding(1, picks=[0, 1, 2], scales=[5, 0, 0], now=NOW)
    assert service.counters()["persona_scale_out_of_range"] == 1
    assert persona_service.store.load(1) is None


def test_scale_max_matches_the_onboarding_contract() -> None:
    """계약의 상한과 서비스의 상한이 갈라지면 모든 척도가 한 칸씩 조용히 밀립니다(G-24)."""
    top = int(service.SCALE_MAX)
    assert OnboardingIn(picks=[0], scales=[top, top, top]).scales == [top, top, top]
    with pytest.raises(ValidationError):
        OnboardingIn(picks=[0], scales=[top + 1, 0, 0])


def test_duplicate_picks_count_once_and_a_short_list_is_counted(
    persona_service: service.PersonaService, presented: tuple[FlavorVector, ...]
) -> None:
    """계약은 개수를 프론트의 일로 둡니다. 서버는 거부하지 않고 세어 드러냅니다."""
    service.reset_counters()
    made = persona_service.save_onboarding(1, picks=[3, 3, 3], scales=[2, 2, 2], now=NOW)

    stored = persona_service.store.load(1)
    assert stored is not None and stored.picks == (3,)
    assert made.vec == presented[3]
    counts = service.counters()
    assert counts["persona_pick_duplicate"] == 2
    assert counts["persona_picks_under_min"] == 1


def test_unknown_flavor_bad_rating_and_repeats_are_counted_not_stored(
    persona_service: service.PersonaService,
) -> None:
    """맛을 전부 모르는 레시피, 범위 밖·값 없는 별점, 한 배치 안의 같은 이벤트는 저장하지 않습니다.

    저장하지 않되 각각 다른 이름으로 셉니다.
    """
    service.reset_counters()
    flavors: dict[int, FlavorVector] = {1: (0.5,) * 6, 2: (None,) * 6}
    events = [
        EventIn(user_id=1, event_type=EventType.COOK, recipe_id=2),
        EventIn(user_id=1, event_type=EventType.RATING, recipe_id=1, value=180.0),
        EventIn(user_id=1, event_type=EventType.RATING, recipe_id=2),
        EventIn(user_id=1, event_type=EventType.COOK, recipe_id=1),
        EventIn(user_id=1, event_type=EventType.COOK, recipe_id=1),
    ]

    stored = persona_service.record_events(events, flavors.get, NOW)

    counts = service.counters()
    assert stored == 1
    assert counts["persona_recipe_unknown"] == 1
    assert counts["persona_event_invalid"] == 2
    assert counts["persona_event_duplicate"] == 1
    made = persona_service.persona_for(1, NOW)
    assert made.n_events == 1 and made.behavior_weight == pytest.approx(1.0)


def test_record_events_rejects_a_naive_clock_before_counting_anything(
    persona_service: service.PersonaService,
) -> None:
    service.reset_counters()
    events = [EventIn(user_id=1, event_type=EventType.DISMISS, recipe_id=1)]
    with pytest.raises(ValueError, match="시간대"):
        persona_service.record_events(events, lambda _: (0.5,) * 6, datetime(2026, 9, 11, 12, 0))
    assert service.counters() == {}


def test_events_carry_the_server_clock_and_age_out_on_the_next_save(
    persona_service: service.PersonaService,
) -> None:
    """`EventIn` 에 시각이 없으므로 수신 시각이 곧 이벤트 시각입니다.

    상한(730일)을 넘긴 것은 다음 저장에서 사라집니다.
    """
    long_ago = NOW - timedelta(days=800)
    events = [EventIn(user_id=1, event_type=EventType.COOK, recipe_id=1)]
    persona_service.record_events(events, lambda _: (0.5,) * 6, long_ago)
    stored = persona_service.store.load(1)
    assert stored is not None and stored.events[0].at == long_ago

    persona_service.save_onboarding(1, picks=[0, 1, 2], scales=None, now=NOW)

    again = persona_service.store.load(1)
    assert again is not None and again.events == ()


def test_concurrent_batches_for_one_user_lose_nothing(
    persona_service: service.PersonaService,
) -> None:
    """같은 사용자의 배치가 여러 스레드에서 동시에 와도 읽고-합치고-쓰기가 겹치지 않습니다.

    잠금 없이는 Windows 에서 임시 파일 이름이 겹쳐 160번 중 119번이 예외였고, 예외가 없어도
    나중 쓰기가 앞 쓰기를 지웠습니다.
    """
    errors: list[Exception] = []

    def worker(offset: int) -> None:
        try:
            for i in range(10):
                events = [EventIn(user_id=1, event_type=EventType.COOK, recipe_id=offset * 100 + i)]
                persona_service.record_events(events, lambda _: (0.5,) * 6, NOW)
        except Exception as error:  # 스레드 안의 예외를 본 스레드의 검사로 옮깁니다.
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    stored = persona_service.store.load(1)
    assert errors == []
    assert stored is not None and len(stored.events) == 40


def test_a_cold_user_has_no_taste_signal_and_the_wider_exploration(
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
    make_context: Callable[..., UserContext],
) -> None:
    """취향이 없으면 맛 피처는 전부 비고(Zero-Drop) 탐색이 8칸입니다. 임의 취향을 넣지 않습니다."""
    cold = cold_persona()
    ctx = make_context(pantry=range(1, 61), persona=cold, taste_vec=cold.vec)
    candidates = [
        Candidate(recipe_id=r, missing_count=0, missing_ids=[], coverage=1.0)
        for r in list(recipes)[:60]
    ]

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    assert all(item.features["f_taste"] is None for item in result.items)
    assert sum(item.is_exploration for item in result.items) == round(
        20 * policy.cold_exploration_ratio
    )
    params = result.stages[-1].params or {}
    assert params["persona_source"] == "none"
    # 이 후보들에는 군집이 없습니다. 계약대로 균등 폴백이 일어났고, 그것이 로그에 남습니다.
    assert params["explore_fallback"] == "uniform"
    assert result.stages[-1].dropped["explore_shortfall"] == 0


def test_exploration_shortfall_and_uniform_fallback_are_visible(
    recipes: dict[int, RecipeFeature],
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
    make_context: Callable[..., UserContext],
) -> None:
    """후보가 모자라 탐색이 줄면 셈과 추적에 남습니다. 24건이면 4칸을 다 채우지 못합니다(F-04)."""
    service.reset_counters()
    ctx = make_context(pantry=range(1, 61))
    candidates = [
        Candidate(recipe_id=r, missing_count=0, missing_ids=[], coverage=1.0)
        for r in list(recipes)[:24]
    ]

    result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)

    n_explore = sum(item.is_exploration for item in result.items)
    wanted = round(20 * policy.exploration_ratio)
    assert 0 < n_explore < wanted
    assert result.stages[-1].dropped["explore_shortfall"] == wanted - n_explore
    counts = service.counters()
    assert counts["explore_shortfall"] == wanted - n_explore
    assert counts["explore_uniform_fallback"] == 1
