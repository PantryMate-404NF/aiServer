"""추천 단위 테스트가 공유하는 Mock 픽스처. DB 없이 tests/fixtures/recommend 만 읽습니다."""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from features.recommend.schema import CorpusStats, RankConfig, RecipeCandidate, UserContext

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recommend"


@pytest.fixture(scope="session")
def catalog() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "catalog.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def personas() -> list[dict[str, Any]]:
    files = sorted((FIXTURE_DIR / "personas").glob("persona_*.json"))
    return [json.loads(file.read_text(encoding="utf-8")) for file in files]


@pytest.fixture(scope="session")
def pool(catalog: dict[str, Any]) -> list[RecipeCandidate]:
    return [RecipeCandidate.model_validate(row) for row in catalog["recipes"]]


@pytest.fixture(scope="session")
def corpus(catalog: dict[str, Any], pool: list[RecipeCandidate]) -> CorpusStats:
    """feature_stats 가 줄 통계를 Mock 풀에서 직접 계산합니다."""
    axes = zip(*(recipe.flavor_vec for recipe in pool), strict=True)
    mean = [sum(axis) / len(pool) for axis in axes]
    frequency: dict[int, int] = {}
    for recipe in pool:
        for ingredient in recipe.all_ids:
            frequency[ingredient] = frequency.get(ingredient, 0) + 1
    return CorpusStats(
        flavor_mean=(mean[0], mean[1], mean[2]),
        ingredient_idf={i: math.log(len(pool) / count) for i, count in frequency.items()},
        ingredient_names={int(key): name for key, name in catalog["ingredients"].items()},
    )


@pytest.fixture
def cfg() -> RankConfig:
    return RankConfig()


@pytest.fixture
def rng() -> random.Random:
    return random.SystemRandom()


@pytest.fixture(scope="session")
def allergy_ids(catalog: dict[str, Any]) -> Callable[[Iterable[str]], frozenset[int]]:
    """알레르기 코드군을 카탈로그로 재료 ID 집합으로 풉니다. 운영에서는 repository 가 합니다."""
    groups: dict[str, list[int]] = catalog["allergen_groups"]

    def resolve(codes: Iterable[str]) -> frozenset[int]:
        return frozenset(i for code in codes for i in groups.get(code, []))

    return resolve


@pytest.fixture(scope="session")
def make_recipe() -> Callable[..., RecipeCandidate]:
    """손으로 만드는 레시피. 지정하지 않은 값은 중간값입니다."""

    def build(
        recipe_id: int,
        essential: Iterable[int] = (),
        extra: Iterable[int] = (),
        **overrides: object,
    ) -> RecipeCandidate:
        essential_ids = frozenset(essential)
        fields: dict[str, object] = {
            "recipe_id": recipe_id,
            "title": f"레시피 {recipe_id}",
            "essential_ids": essential_ids,
            "all_ids": essential_ids | frozenset(extra),
            "flavor_vec": (0.5, 0.5, 0.5),
            "popularity_score": 0.5,
            "quality_score": 0.5,
            "cook_minutes": 30,
            "cuisine": "한식",
        }
        fields.update(overrides)
        return RecipeCandidate.model_validate(fields)

    return build


@pytest.fixture(scope="session")
def make_context() -> Callable[..., UserContext]:
    """손으로 만드는 문맥. 요청 파싱을 거치지 않고 엔진 입력을 바로 만듭니다."""

    def build(
        pantry: Iterable[int] = (),
        expiring: Iterable[int] = (),
        taste: tuple[float, float, float] = (0.5, 0.5, 0.5),
        **overrides: object,
    ) -> UserContext:
        fields: dict[str, object] = {
            "user_id": 1,
            "pantry_ids": frozenset(pantry),
            "expiring_ids": frozenset(expiring),
            "taste_vec": taste,
            "top_k": 20,
        }
        fields.update(overrides)
        return UserContext(**fields)

    return build
