"""온보딩 음식 유형. 계약 → 페르소나 → 문맥 → 유형 슬롯까지 한 줄로 봅니다.

슬롯이 도는 조건은 "고른 유형이 목록에 한 건도 없을 때" 입니다. 그래서 검사는 대부분 목록
앞머리를 한 유형으로 채우고 다른 유형을 20위 밖에 두는 모양입니다 - 실제 코퍼스의 한식
편중이 그 모양이고, 그 편중이 이 슬롯이 필요한 이유입니다.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from features.recommend import service
from features.recommend.engine import cuisine, mock, rerank
from features.recommend.engine.context import CorpusStats, RecipeFeature, UserContext, build_context
from features.recommend.engine.persona import TasteProfile, derive_persona
from features.recommend.engine.score import score_all
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import (
    CUISINE_LABELS,
    ONBOARDING_CUISINES,
    CuisineFamily,
    UserMode,
    normalize_cuisine,
)
from features.recommend.policy import RankingPolicy
from features.recommend.schema import OnboardingIn, RecommendRequest
from features.recommend.stage import Candidate, RankedItem, ScoredCandidate

ROOT = Path(__file__).resolve().parents[3]
CORPUS = CorpusStats(flavor_mean=(0.5,) * 6)
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))
TOP_K = 20
POOL = 40
KOREAN = CuisineFamily.KOREAN.value
CHINESE = CuisineFamily.CHINESE.value
JAPANESE = CuisineFamily.JAPANESE.value
#: 20위 밖에 두는 중식·일식 자리. `rest` 의 중위 점수 위라 슬롯 후보가 됩니다.
CHINESE_AT = (21, 22)
JAPANESE_AT = (23, 24)


@pytest.fixture
def quiet(policy: RankingPolicy) -> RankingPolicy:
    """탐색을 끈 정책.

    탐색도 `rest` 에서 가져가므로 켜 두면 유형 슬롯이 쓸 후보가 시드에 따라 사라져, 검사가
    재는 것이 슬롯인지 난수인지 알 수 없습니다. 둘이 함께 도는 것은 아래 마지막 검사가 봅니다.
    """
    return replace(policy, exploration_ratio=0.0, cold_exploration_ratio=0.0)


@pytest.fixture
def skewed(
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> tuple[list[ScoredCandidate], dict[int, RecipeFeature]]:
    """앞머리 20건이 전부 한식이고 중식·일식은 20위 밖인 후보 40건."""
    families = dict.fromkeys(CHINESE_AT, CHINESE) | dict.fromkeys(JAPANESE_AT, JAPANESE)
    recipes = {
        i: make_recipe(i, essential=[i * 3, i * 3 + 1], cuisine=families.get(i, KOREAN))
        for i in range(POOL)
    }
    candidates = [make_candidate(i, coverage=1.0 - i / 100, cluster_id=i % 8) for i in range(POOL)]
    scored = score_all(candidates, recipes, make_context(pantry=range(200)), CORPUS, policy)
    return scored, recipes


def run(
    pool: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    ctx: UserContext,
    policy: RankingPolicy,
    seed: int = 7,
) -> list[RankedItem]:
    scored, recipes = pool
    rng = random.Random(seed)  # noqa: S311  # 재현용 시드 RNG. 암호 용도가 아닙니다
    return rerank.rerank(scored, recipes, ctx, CORPUS, policy, rng, top_k=TOP_K)


def slots_of(items: Sequence[RankedItem], recipes: dict[int, RecipeFeature]) -> list[str | None]:
    return [recipes[item.recipe_id].cuisine for item in items if item.is_cuisine_slot]


# ── 계약 → 페르소나 → 문맥 ───────────────────────────────────────
def test_the_onboarding_choice_reaches_the_ranking_context(
    presented: tuple[FlavorVector, ...], policy: RankingPolicy
) -> None:
    """한글 라벨로 받아도 랭킹은 코드로 봅니다. 저장되는 것도 코드입니다."""
    body = OnboardingIn(picks=[0, 1, 2], scales=[2, 2, 2], preferred_cuisines=["중식", "japanese"])
    profile = service.onboarding_profile(
        1, body.picks, body.scales, presented, NOW, body.preferred_cuisines
    )

    ctx = build_context(user_id=1, persona=derive_persona(profile, NOW, policy))

    assert profile.cuisines == (CHINESE, JAPANESE)
    assert ctx.preferred_cuisines == frozenset({CHINESE, JAPANESE})


def test_the_choice_does_not_move_the_taste_vector(
    presented: tuple[FlavorVector, ...], policy: RankingPolicy
) -> None:
    """유형은 맛 6축과 다른 축입니다. 섞으면 문항 하나가 맛 취향을 통째로 움직입니다."""
    made = [
        derive_persona(
            service.onboarding_profile(1, [0, 1, 2], [2, 2, 2], presented, NOW, chosen), NOW, policy
        ).vec
        for chosen in ([], ["한식"], ["양식", "일식"])
    ]

    assert made[0] == made[1] == made[2]


def test_an_unknown_cuisine_is_rejected_rather_than_guessed() -> None:
    """가까운 유형으로 바꿔 넣으면 고르지 않은 음식이 올라오는데 응답은 200 입니다."""
    with pytest.raises(ValueError, match="모르는 음식 유형"):
        OnboardingIn(picks=[0], scales=[2, 2, 2], preferred_cuisines=["프렌치"])
    with pytest.raises(ValueError, match="모르는 음식 유형"):
        service.onboarding_profile(1, [], None, (), NOW, ["프렌치"])


def test_the_context_keeps_an_empty_choice_empty(policy: RankingPolicy) -> None:
    """유형을 지운 사용자에게 페르소나의 옛 선택이 되살아나면 안 됩니다."""
    persona = derive_persona(TasteProfile(user_id=1, cuisines=(KOREAN,)), NOW, policy)

    assert build_context(user_id=1, persona=persona).preferred_cuisines == frozenset({KOREAN})
    assert not build_context(user_id=1, persona=persona, preferred_cuisines=[]).preferred_cuisines


def test_the_family_list_matches_the_taxonomy_seed() -> None:
    """정본은 시드입니다. 갈라지면 고른 유형과 레시피의 유형이 영영 안 만납니다."""
    path = ROOT / "seeds" / "cuisine_taxonomy.yaml"
    families = {
        str(row["family"]) for row in yaml.safe_load(path.read_text(encoding="utf-8"))["taxonomy"]
    }

    assert set(CUISINE_LABELS) == families
    assert set(ONBOARDING_CUISINES) <= families
    assert normalize_cuisine("한식") == KOREAN


@pytest.mark.parametrize(
    ("sent", "code"),
    [
        ("KOREAN", "korean"),
        ("Western", "western"),
        ("JAPANESE", "japanese"),
        ("CHINESE", "chinese"),
        ("ETC", "asian_other"),
        ("etc", "asian_other"),
    ],
)
def test_the_backend_type_codes_are_read_in_any_case(sent: str, code: str) -> None:
    """백엔드는 유형을 대문자로 저장하고 다섯째를 `ETC` 로 적습니다 (2026-09-21 회신)."""
    assert normalize_cuisine(sent) == code
    assert OnboardingIn(picks=["불고기"], preferred_cuisines=[sent]).preferred_cuisines == [code]


def test_an_unknown_type_code_is_still_refused() -> None:
    """`ETC` 는 합의한 대응입니다. 그 밖의 모르는 값을 가까운 유형으로 읽지는 않습니다."""
    assert normalize_cuisine("OTHER") is None
    with pytest.raises(ValueError, match="preferred_cuisines"):
        OnboardingIn(picks=["불고기"], preferred_cuisines=["OTHER"])


# ── 유형 슬롯 ────────────────────────────────────────────────────
def test_a_chosen_cuisine_missing_from_the_list_takes_a_slot(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    _scored, recipes = skewed
    ctx = make_context(preferred_cuisines=frozenset({CHINESE, JAPANESE}))

    items = run(skewed, ctx, quiet)

    assert sorted(slots_of(items, recipes)) == [CHINESE, JAPANESE]
    assert len(items) == TOP_K


def test_a_cuisine_already_in_the_list_does_not_take_a_slot(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """앞머리가 전부 한식이므로 한식을 고른 사용자는 뗄 자리가 없습니다."""
    ctx = make_context(preferred_cuisines=frozenset({KOREAN}))

    assert not [item for item in run(skewed, ctx, quiet) if item.is_cuisine_slot]


def test_no_choice_means_no_slot(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    assert not [item for item in run(skewed, make_context(), quiet) if item.is_cuisine_slot]


def test_a_behavior_user_keeps_the_list_to_the_behaviour(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """조리·클릭이 쌓인 사용자는 실제로 만든 것이 온보딩 답변보다 정확한 신호입니다."""
    onboarded = derive_persona(TasteProfile(user_id=1, cuisines=(CHINESE,)), NOW, quiet)
    warm = replace(onboarded, mode=UserMode.WARM)
    ctx = make_context(preferred_cuisines=frozenset({CHINESE}), persona=warm)

    assert cuisine.cuisine_spec(ctx, quiet, TOP_K).count == 0
    assert not [item for item in run(skewed, ctx, quiet) if item.is_cuisine_slot]


def test_the_slot_is_deterministic_and_is_not_exploration(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """탐색과 한 칸으로 합치면 IPS 의 분모가 섞여 off-policy 평가에서 다시 못 나눕니다."""
    ctx = make_context(preferred_cuisines=frozenset({CHINESE}))

    first = [item for item in run(skewed, ctx, quiet, seed=1) if item.is_cuisine_slot]
    second = [item for item in run(skewed, ctx, quiet, seed=2) if item.is_cuisine_slot]

    assert first and all(item.propensity == 1.0 for item in first)
    assert not any(item.is_exploration for item in first)
    assert [(item.final_rank, item.recipe_id) for item in first] == [
        (item.final_rank, item.recipe_id) for item in second
    ]


def test_the_slot_says_why_it_is_there(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """자리를 뗀 이유와 화면에 적힌 이유가 다르면 목록이 흔들린 것으로만 보입니다."""
    ctx = make_context(preferred_cuisines=frozenset({CHINESE}))

    picked = [item for item in run(skewed, ctx, quiet) if item.is_cuisine_slot]

    assert picked and all("f_cuisine" in item.reason_features for item in picked)
    assert all(CUISINE_LABELS[CHINESE] in item.reason for item in picked)


def test_the_slot_picks_the_recipe_closest_to_the_taste_persona(
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """유형 안에서 아무거나 넣으면 "중식을 골랐더니 안 좋아할 중식이 왔다" 가 됩니다.

    고르는 규칙만 봅니다. 재정렬을 통째로 태우면 맛이 점수에도 들어가, 이 검사가 재는 것이
    맛 정렬인지 점수 정렬인지 구분되지 않습니다.
    """
    # 점수가 같은 넷. 맛만 다르고 둘이 중식입니다 - 중위 점수 걸러내기에 걸리지 않습니다.
    recipes = {
        1: make_recipe(1, essential=[1], cuisine=CHINESE, flavor=[0.1] * 6),
        2: make_recipe(2, essential=[2], cuisine=CHINESE, flavor=[0.9] * 6),
        3: make_recipe(3, essential=[3], cuisine=KOREAN, flavor=[0.9] * 6),
        4: make_recipe(4, essential=[4], cuisine=KOREAN, flavor=[0.1] * 6),
    }
    ctx = make_context(
        pantry=range(10), taste_vec=[0.95] * 6, preferred_cuisines=frozenset({CHINESE})
    )
    rest = score_all([make_candidate(i) for i in recipes], recipes, ctx, CORPUS, quiet)

    picked = cuisine.pick_cuisine(rest, recipes, [CHINESE], 1)

    assert [item.recipe_id for item in picked] == [2]


def test_the_slot_does_not_take_the_head_of_the_list(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """개인화 앞머리는 그대로 둡니다. 유형은 목록의 성격이 아니라 구성의 일부입니다."""
    ctx = make_context(preferred_cuisines=frozenset({CHINESE, JAPANESE}))

    ranks = [item.final_rank for item in run(skewed, ctx, quiet) if item.is_cuisine_slot]

    assert ranks and min(ranks) > TOP_K // 2


def test_the_cap_holds_when_every_cuisine_is_missing(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """다섯 유형을 다 골라도 목록의 절대적인 축이 되지 않습니다."""
    ctx = make_context(preferred_cuisines=frozenset(ONBOARDING_CUISINES))

    items = run(skewed, ctx, quiet)

    assert 0 < sum(item.is_cuisine_slot for item in items) <= quiet.cuisine_slot_max


def test_cuisine_and_exploration_share_the_list(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> None:
    """탐색을 켜 둔 기본 정책. 한 칸이 두 몫을 겸하지 않고 개수도 정책 안입니다."""
    _scored, recipes = skewed
    ctx = make_context(preferred_cuisines=frozenset({CHINESE, JAPANESE}))

    items = run(skewed, ctx, policy)
    served = {recipes[item.recipe_id].cuisine for item in items}

    assert not [item for item in items if item.is_cuisine_slot and item.is_exploration]
    assert sum(item.is_cuisine_slot for item in items) <= policy.cuisine_slot_max
    assert len(items) == TOP_K
    # 탐색이 먼저 가져갔든 슬롯이 넣었든, 고른 유형은 목록 안에 있습니다.
    assert {CHINESE, JAPANESE} <= served


def test_a_cuisine_at_the_truncation_edge_does_not_vanish(
    make_recipe: Callable[..., RecipeFeature],
    make_candidate: Callable[..., Candidate],
    make_context: Callable[..., UserContext],
    quiet: RankingPolicy,
) -> None:
    """유형 칸은 개인화 꼬리를 잘라 만듭니다. 한 유형에 칸을 떼느라 다른 유형의 유일한 한 건이
    잘려 나가면, 고른 유형이 목록에서 사라지는데 에러는 없습니다.

    중식은 개인화 목록의 **맨 끝 자리**에만, 일식은 20위 밖에만 둡니다. 일식 칸을 떼면
    맨 끝이 잘리므로, "이미 있다" 를 자르기 전 목록으로 재면 중식이 사라집니다.
    """
    families = {TOP_K - 1: CHINESE, TOP_K + 2: JAPANESE, TOP_K + 3: JAPANESE}
    recipes = {
        i: make_recipe(i, essential=[i * 3, i * 3 + 1], cuisine=families.get(i, KOREAN))
        for i in range(POOL)
    }
    ctx = make_context(preferred_cuisines=frozenset({CHINESE, JAPANESE}))
    candidates = [make_candidate(i, coverage=1.0 - i / 100, cluster_id=i % 8) for i in range(POOL)]
    scored = score_all(candidates, recipes, make_context(pantry=range(200)), CORPUS, quiet)

    served = {recipes[item.recipe_id].cuisine for item in run((scored, recipes), ctx, quiet)}

    assert {CHINESE, JAPANESE} <= served


# ── 목업 경로 ────────────────────────────────────────────────────
def test_the_mock_reflects_the_saved_onboarding_choice() -> None:
    """프론트가 계약을 확인하는 경로입니다. 실제 구현과 규칙이 달라지면 서빙에서 놀랍니다."""
    user = 90_001
    mock.save_onboarding(
        user, OnboardingIn(picks=[0, 1, 2], scales=[2, 2, 2], preferred_cuisines=["중식", "아시안"])
    )

    request = RecommendRequest(user_id=user, top_k=20, pantry=[], allergies=[])
    items = mock.build_recommendation(request).items
    picked = [item for item in items if item.is_cuisine_slot]

    # 한 칸이 두 몫을 겸하면 노출 확률이 섞여 로그에서 다시 못 나눕니다.
    assert not [item for item in picked if item.is_exploration]
    assert all(item.propensity == 1.0 for item in picked)
    # 유형마다 한 칸까지. 사유는 코드가 아니라 사람이 읽는 이름입니다.
    assert 0 < len(picked) <= 2
    assert {CUISINE_LABELS[CHINESE], CUISINE_LABELS[CuisineFamily.ASIAN_OTHER.value]} == {
        label for item in picked for label in CUISINE_LABELS.values() if label in item.reason
    }


def test_the_mock_gives_no_slot_before_onboarding() -> None:
    """온보딩 전 사용자에게 고른 적 없는 유형의 칸이 생기면 안 됩니다."""
    request = RecommendRequest(user_id=90_002, top_k=20, pantry=[], allergies=[])
    items = mock.build_recommendation(request).items

    assert not [item for item in items if item.is_cuisine_slot]


def test_the_trace_records_a_cuisine_that_never_made_the_list(
    skewed: tuple[list[ScoredCandidate], dict[int, RecipeFeature]],
    make_context: Callable[..., UserContext],
    policy: RankingPolicy,
) -> None:
    """후보에 그 유형이 없어서 못 넣은 것과 이미 있어서 안 넣은 것을 로그에서 갈라야 합니다."""
    scored, recipes = skewed
    ctx = make_context(preferred_cuisines=frozenset({CuisineFamily.WESTERN.value}))
    candidates = [
        Candidate(
            recipe_id=item.recipe_id,
            missing_count=item.missing_count,
            coverage=item.coverage,
            cluster_id=item.cluster_id,
        )
        for item in scored
    ]

    result = service.rank_candidates(candidates, recipes, ctx, CORPUS, policy, top_k=TOP_K)
    params = result.stages[-1].params

    assert params["n_cuisine"] == 0
    assert params["cuisine_unmet"] == CuisineFamily.WESTERN.value
