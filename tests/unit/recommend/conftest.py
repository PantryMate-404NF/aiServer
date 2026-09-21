"""추천 단위 테스트가 공유하는 Mock 픽스처. DB 없이 tests/fixtures/recommend 만 읽습니다."""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from features.recommend import service
from features.recommend.engine import taste
from features.recommend.engine.context import (
    CorpusStats,
    RecipeFeature,
    UserContext,
    UserHistory,
    build_context,
)
from features.recommend.engine.persona import derive_persona
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import FEATURE_KEYS, CuisineFamily, UserMode
from features.recommend.evaluation.record import EvalRecord
from features.recommend.policy import RankingPolicy
from features.recommend.profile_store import load_presented_flavors
from features.recommend.stage import Candidate, RankedItem

ROOT = Path(__file__).resolve().parents[3]
#: 고정 시각. 페르소나의 감쇠·주기 계산이 검사 실행 날짜에 따라 달라지지 않게 합니다.
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recommend"


@pytest.fixture(scope="session")
def catalog() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "catalog.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def personas() -> list[dict[str, Any]]:
    files = sorted((FIXTURE_DIR / "personas").glob("persona_*.json"))
    return [json.loads(file.read_text(encoding="utf-8")) for file in files]


@pytest.fixture(scope="session")
def recipes(catalog: dict[str, Any]) -> dict[int, RecipeFeature]:
    """`recipe_feature` 를 대신합니다. 맛은 A 트랙과 같은 6축입니다."""
    return {
        int(row["recipe_id"]): RecipeFeature(
            recipe_id=int(row["recipe_id"]),
            title=str(row["title"]),
            essential_ids=frozenset(row["essential_ids"]),
            all_ids=frozenset(row["all_ids"]),
            flavor_vec=taste.as_vector(row["flavor_vec"]),
            popularity_score=row["popularity_score"],
            quality_score=row["quality_score"],
            cook_minutes=row["cook_minutes"],
            cuisine=row["cuisine"],
            product_ids=tuple(row["product_ids"]),
        )
        for row in catalog["recipes"]
    }


@pytest.fixture(scope="session")
def clusters(catalog: dict[str, Any]) -> dict[int, int]:
    return {int(row["recipe_id"]): int(row["cluster_id"]) for row in catalog["recipes"]}


@pytest.fixture(scope="session")
def corpus(catalog: dict[str, Any], recipes: dict[int, RecipeFeature]) -> CorpusStats:
    """`feature_stats` 가 줄 통계를 Mock 풀에서 직접 계산합니다."""
    pool = list(recipes.values())
    mean = [sum(_axis(r, i) for r in pool) / len(pool) for i in range(taste.AXIS_COUNT)]
    frequency: dict[int, int] = {}
    for recipe in pool:
        for ingredient in recipe.all_ids:
            frequency[ingredient] = frequency.get(ingredient, 0) + 1
    return CorpusStats(
        flavor_mean=taste.as_vector(mean),
        ingredient_idf={i: math.log(len(pool) / count) for i, count in frequency.items()},
        ingredient_names={int(key): name for key, name in catalog["ingredients"].items()},
    )


def _axis(recipe: RecipeFeature, index: int) -> float:
    value = recipe.flavor_vec[index]
    return 0.0 if value is None else value


@pytest.fixture
def policy() -> RankingPolicy:
    return RankingPolicy()


@pytest.fixture(scope="session")
def presented() -> tuple[FlavorVector, ...]:
    """온보딩 제시 목록의 6축. 실제 시드 파일을 읽습니다 - 픽스처의 picks 가 그 인덱스입니다."""
    return load_presented_flavors(ROOT / "seeds" / "onboarding_recipes.yaml")


@pytest.fixture
def rng() -> random.Random:
    return random.SystemRandom()


@pytest.fixture(scope="session")
def allergy_ids(catalog: dict[str, Any]) -> Callable[[Iterable[str]], frozenset[int]]:
    """알레르기 코드군을 재료 ID 집합으로 풉니다. 운영에서는 SQL 이 전개합니다."""
    groups: dict[str, list[int]] = catalog["allergen_groups"]

    def resolve(codes: Iterable[str]) -> frozenset[int]:
        return frozenset(i for code in codes for i in groups.get(code, []))

    return resolve


@pytest.fixture(scope="session")
def retrieve(
    recipes: dict[int, RecipeFeature], clusters: dict[int, int]
) -> Callable[..., list[Candidate]]:
    """A 트랙 SQL 함수 `retrieve_candidates` 의 조건을 파이썬으로 흉내 냅니다.

    DB 가 붙으면 `repository.retrieve` 가 이 자리를 대신합니다. 조건이 어긋나면
    Mock 결과와 서빙 결과가 조용히 갈라지므로 SQL 과 같은 순서로 적어 둡니다.
    """

    def run(
        pantry: Iterable[int],
        *,
        allergy: Iterable[int] = (),
        max_missing: int = 2,
        max_minutes: int | None = None,
        limit: int = 500,
        ignore_missing: bool = False,
    ) -> list[Candidate]:
        pantry_set, allergy_set = frozenset(pantry), frozenset(allergy)
        found: list[tuple[int, float, Candidate]] = []
        for recipe in recipes.values():
            if recipe.all_ids & allergy_set:
                continue
            if max_minutes is not None and (recipe.cook_minutes or 0) > max_minutes:
                continue
            missing = sorted(recipe.essential_ids - pantry_set)
            if not ignore_missing:
                if recipe.essential_ids and not (recipe.essential_ids & pantry_set):
                    continue
                if len(missing) > max_missing:
                    continue
            total = len(recipe.essential_ids)
            found.append(
                (
                    len(missing),
                    -(recipe.popularity_score or 0.0),
                    Candidate(
                        recipe_id=recipe.recipe_id,
                        missing_count=len(missing),
                        missing_ids=missing,
                        coverage=1.0 if total == 0 else (total - len(missing)) / total,
                        cluster_id=clusters.get(recipe.recipe_id),
                    ),
                )
            )
        found.sort(key=lambda row: (row[0], row[1], row[2].recipe_id))
        return [row[2] for row in found[:limit]]

    return run


@pytest.fixture
def context_for(
    allergy_ids: Callable[[Iterable[str]], frozenset[int]],
    policy: RankingPolicy,
    presented: tuple[FlavorVector, ...],
) -> Callable[..., UserContext]:
    """가상 사용자 프로필을 랭킹 문맥으로 바꿉니다.

    고른 음식(`onboarding_picks`, 제시 목록 인덱스)이 있으면 그 6축 평균이 취향이고, 없으면
    3축 척도, 그것도 없으면 취향 없는 사용자입니다. 이벤트는 없습니다.
    """

    def build(profile: dict[str, Any], history: UserHistory | None = None) -> UserContext:
        preference = profile.get("taste_preference") or {}
        onboarding = [
            preference.get("spicy_level"),
            preference.get("salty_level"),
            preference.get("sweet_level"),
        ]
        scales = None if any(v is None for v in onboarding) else [int(v) for v in onboarding]
        taste_profile = service.onboarding_profile(
            int(profile["user_id"]), profile.get("onboarding_picks", []), scales, presented, NOW
        )
        return build_context(
            user_id=int(profile["user_id"]),
            persona=derive_persona(taste_profile, NOW, policy),
            pantry_ids=profile.get("pantry_ingredient_ids", []),
            expiring_ids=profile.get("expiring_ingredient_ids", []),
            history=history or UserHistory(),
            max_cook_minutes=profile.get("max_cook_minutes"),
            preferred_cuisines=_cuisines(profile.get("preferred_cuisines", [])),
        )

    return build


def _cuisines(raw: object) -> list[str]:
    """백엔드가 보낼 법한 모양을 그대로 받습니다. 코드로 맞추는 것은 `build_context` 가 합니다."""
    if isinstance(raw, str):
        return [part.strip() for part in raw.replace("/", ",").split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if item]
    return []


@pytest.fixture(scope="session")
def make_recipe() -> Callable[..., RecipeFeature]:
    """손으로 만드는 레시피 피처. 지정하지 않은 축은 None 입니다."""

    def build(
        recipe_id: int,
        essential: Iterable[int] = (),
        extra: Iterable[int] = (),
        flavor: Sequence[float | None] | None = None,
        **overrides: object,
    ) -> RecipeFeature:
        essential_ids = frozenset(essential)
        fields: dict[str, Any] = {
            "recipe_id": recipe_id,
            "title": f"레시피 {recipe_id}",
            "essential_ids": essential_ids,
            "all_ids": essential_ids | frozenset(extra),
            "flavor_vec": taste.as_vector(flavor),
            "popularity_score": 0.5,
            "quality_score": 0.5,
            "cook_minutes": 30,
            "cuisine": CuisineFamily.KOREAN.value,
        }
        fields.update(overrides)
        return RecipeFeature(**fields)

    return build


@pytest.fixture(scope="session")
def make_candidate() -> Callable[..., Candidate]:
    def build(recipe_id: int, **overrides: object) -> Candidate:
        fields: dict[str, Any] = {
            "recipe_id": recipe_id,
            "missing_count": 0,
            "missing_ids": [],
            "coverage": 1.0,
            "cluster_id": recipe_id % 4,
        }
        fields.update(overrides)
        return Candidate(**fields)

    return build


@pytest.fixture(scope="session")
def make_context() -> Callable[..., UserContext]:
    """손으로 만드는 문맥. 프로필 파싱을 거치지 않고 랭킹 입력을 바로 만듭니다."""

    def build(
        pantry: Iterable[int] = (),
        expiring: Iterable[int] = (),
        taste_vec: Sequence[float | None] | None = None,
        **overrides: object,
    ) -> UserContext:
        fields: dict[str, Any] = {
            "user_id": 1,
            "pantry_ids": frozenset(pantry),
            "expiring_ids": frozenset(expiring),
            "taste_vec": taste.as_vector(taste_vec),
        }
        fields.update(overrides)
        return UserContext(**fields)

    return build


# ─────────────────────────────────────────────────────────────────
# 평가 파이프라인 픽스처. 노출 항목과 기록을 최소 인자로 만듭니다.
# ─────────────────────────────────────────────────────────────────
def _eval_features() -> dict[str, float | None]:
    """17개 피처 전부를 담습니다. 재료·인기만 값이 있고 나머지는 None 입니다."""
    base: dict[str, float | None] = dict.fromkeys(FEATURE_KEYS)
    base.update({"f_coverage": 0.5, "f_missing": 0.5, "f_popularity": 0.5})
    return base


@pytest.fixture
def make_item() -> Callable[..., Any]:
    """`RankedItem` 한 개. rank 와 recipe_id 만 주면 결정적 슬롯(propensity 1.0)입니다."""

    def build(rank: int, recipe_id: int | None = None, **kw: object) -> RankedItem:
        return RankedItem(
            recipe_id=recipe_id if recipe_id is not None else 100 + rank,
            missing_count=0,
            coverage=1.0,
            features=kw.pop("features", None) or _eval_features(),
            score=kw.pop("score", 1.0 / rank),
            final_rank=rank,
            propensity=kw.pop("propensity", 1.0),
            **kw,
        )

    return build


@pytest.fixture
def make_record(make_item: Callable[..., Any]) -> Callable[..., Any]:
    """`EvalRecord` 한 건. items 를 안 주면 결정적 노출 5개입니다."""

    def build(**kw: object) -> EvalRecord:
        items = kw.pop("items", None)
        if (
            items is None
        ):  # 빈 목록은 빈 목록입니다. falsy 로 기본값을 주면 no_items 를 검사할 수 없습니다
            items = [make_item(rank) for rank in range(1, 6)]
        defaults: dict[str, Any] = {
            "request_id": uuid4(),
            "model_version": "reco-b-linear-v0",
            "config_hash": "cfg0",
            "warm_alpha": 0.0,
            "stats_version": 1,
            "created_at": NOW,
            "user_hash": "u0",
            "session_prefix": "c",
            "user_mode": UserMode.BLENDED,
            "degraded": False,
            "total_latency_ms": 20,
            "items": items,
            "ingredients": {item.recipe_id: [item.recipe_id, 1] for item in items},
            "events": [],
            "user_events": [],
            "is_simulated": False,
        }
        defaults.update(kw)
        return EvalRecord(**defaults)

    return build
