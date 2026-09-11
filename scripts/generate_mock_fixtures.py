"""12인 가상 사용자와 Mock 레시피 풀을 만들어 tests/fixtures/recommend 에 씁니다.

실행: uv run python scripts/generate_mock_fixtures.py

DB 가 없는 동안 `recipe_feature` 와 사용자 프로필을 대신합니다. 레시피의 맛은 A 트랙과
같은 **6축**(매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐)이고, 사용자 취향은 온보딩이 지금
받는 **앞 3축**만 채웁니다. 나머지 축은 랭킹에서 None 으로 들어가 계산에서 빠집니다.

난수 대신 키의 해시를 쓰므로 언제 돌려도 같은 파일이 나옵니다. 값을 바꾸려면 SEED 를 올립니다.
사용자 프로필에는 백엔드가 보낼 법한 비정형 값(문자열 가구원 수, 규약 밖 필드)을 일부러 섞어 둡니다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

SEED = "reco-mock-v1"
OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "recommend"
RECIPE_COUNT = 120
PRODUCT_ID_BASE = 100
# 이 번호부터는 조미료입니다. 필수 재료로는 뽑지 않습니다.
SEASONING_ID_START = 45
# 20건에 하나는 품질 점수가 비어 있습니다. Zero-Drop 경로를 밟게 하기 위함입니다.
MISSING_QUALITY_EVERY = 20
# 우연성 탐색이 쓰는 클러스터 수. A 트랙 설계의 50개를 축소한 값입니다.
CLUSTER_COUNT = 8

INGREDIENTS: dict[int, str] = {
    1: "양파",
    2: "대파",
    3: "마늘",
    4: "감자",
    5: "당근",
    6: "애호박",
    7: "양배추",
    8: "시금치",
    9: "버섯",
    10: "두부",
    11: "계란",
    12: "우유",
    13: "치즈",
    14: "돼지고기",
    15: "소고기",
    16: "닭고기",
    17: "새우",
    18: "오징어",
    19: "고등어",
    20: "참치캔",
    21: "김치",
    22: "쌀",
    23: "밀가루",
    24: "스파게티면",
    25: "라면사리",
    26: "떡",
    27: "어묵",
    28: "햄",
    29: "베이컨",
    30: "토마토",
    31: "파프리카",
    32: "오이",
    33: "상추",
    34: "콩나물",
    35: "무",
    36: "고추",
    37: "땅콩",
    38: "호두",
    39: "복숭아",
    40: "사과",
    41: "바지락",
    42: "게맛살",
    43: "메밀면",
    44: "잣",
    45: "간장",
    46: "고추장",
    47: "된장",
    48: "설탕",
    49: "식용유",
    50: "참기름",
    51: "고춧가루",
    52: "소금",
    53: "후추",
    54: "케첩",
    55: "마요네즈",
    56: "카레가루",
    57: "버터",
    58: "생크림",
    59: "요거트",
    60: "꿀",
}
MAIN_INGREDIENT_IDS = [i for i in INGREDIENTS if i < SEASONING_ID_START]

ALLERGEN_GROUPS: dict[str, list[int]] = {
    "EGG": [11],
    "MILK": [12, 13, 57, 58, 59],
    "WHEAT": [23, 24, 25],
    "PORK": [14, 28, 29],
    "BEEF": [15],
    "CHICKEN": [16],
    "SHRIMP": [17],
    "SQUID": [18],
    "MACKEREL": [19],
    "SOYBEAN": [10, 45, 47],
    "TOMATO": [30, 54],
    "PEANUT": [37],
    "WALNUT": [38],
    "PEACH": [39],
    "SHELLFISH": [41],
    "CRAB": [42],
    "BUCKWHEAT": [43],
    "PINE_NUT": [44],
}


@dataclass(frozen=True)
class CuisineSpec:
    dishes: list[str]
    seasonings: list[int]
    #: 6축 (매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐).
    #: A 트랙 recipe_feature.flavor_vec 과 같은 순서입니다.
    flavor: tuple[float, float, float, float, float, float]


CUISINES: dict[str, CuisineSpec] = {
    "한식": CuisineSpec(
        ["볶음", "찌개", "국", "무침", "조림", "전", "덮밥"],
        [45, 46, 47, 50, 51, 52],
        (0.65, 0.6, 0.35, 0.3, 0.7, 0.45),
    ),
    "중식": CuisineSpec(
        ["볶음밥", "탕", "덮밥", "튀김"], [45, 48, 49, 53, 36], (0.55, 0.65, 0.45, 0.35, 0.75, 0.7)
    ),
    "일식": CuisineSpec(
        ["덮밥", "구이", "조림", "우동"], [45, 48, 52], (0.2, 0.55, 0.5, 0.3, 0.65, 0.3)
    ),
    "양식": CuisineSpec(
        ["파스타", "샐러드", "스테이크", "그라탕", "리소토"],
        [52, 53, 57, 49, 13],
        (0.2, 0.5, 0.4, 0.4, 0.55, 0.6),
    ),
    "분식": CuisineSpec(
        ["떡볶이", "김밥", "볶음", "전골"], [46, 48, 51, 45], (0.75, 0.6, 0.55, 0.35, 0.6, 0.55)
    ),
}
COOK_MINUTES_OPTIONS = [10, 15, 20, 25, 30, 40, 45, 60, 90]

PERSONAS: list[dict[str, object]] = [
    {
        "user_id": 1001,
        "pantry_ingredient_ids": [1, 2, 3, 11, 21, 22, 25, 46, 51, 52],
        "expiring_ingredient_ids": [11, 21],
        "taste_preference": {"spicy_level": 4, "sweet_level": 1, "salty_level": 3},
        "household_size": 1,
        "preferred_cuisines": ["한식", "분식"],
        "max_cook_minutes": 30,
        "allergy_group_codes": [],
        "top_k": 20,
    },
    {
        "user_id": 1002,
        "pantry_ingredient_ids": [1, 2, 3, 4, 5, 6, 9, 10, 14, 15, 21, 22, 45, 46, 47, 50, 51, 52],
        "expiring_ingredient_ids": [6, 10, 14],
        "taste_preference": {"spicy_level": 2, "sweet_level": 2, "salty_level": 2},
        "household_size": "4인 가구",
        "preferred_cuisines": ["한식"],
        "max_cook_minutes": 60,
        "allergy_group_codes": ["EGG"],
        "top_k": 20,
    },
    {
        "user_id": 1003,
        "pantry_ingredient_ids": [1, 3, 11, 16, 24, 30, 31, 33, 49, 52, 53, 54],
        "expiring_ingredient_ids": [33],
        "taste_preference": {"spicy_level": 1, "sweet_level": 3, "salty_level": 2},
        "household_size": 2,
        "preferred_cuisines": ["양식", "일식"],
        "max_cook_minutes": None,
        "allergy_group_codes": ["MILK"],
        "top_k": 20,
    },
    {
        "user_id": 1004,
        # 고른 음식이 없는 사용자. 3축 척도로만 취향을 만드는 경로를 검사합니다.
        "onboarding_picks": [],
        "pantry_ingredient_ids": [11, 52],
        "expiring_ingredient_ids": [],
        "taste_preference": {"spicy_level": 2, "sweet_level": 2, "salty_level": 2},
        "household_size": 1,
        "preferred_cuisines": [],
        "max_cook_minutes": 20,
        "allergy_group_codes": [],
        "top_k": 20,
    },
    {
        "user_id": 1005,
        "pantry_ingredient_ids": [*range(1, 23), 45, 46, 47, 48, 49, 50, 51, 52, 53],
        "expiring_ingredient_ids": [],
        "taste_preference": {"spicy_level": 3, "sweet_level": 2, "salty_level": 3},
        "household_size": 3,
        "preferred_cuisines": "한식, 양식",
        "max_cook_minutes": 45,
        "allergy_group_codes": [],
        "top_k": 20,
    },
    {
        "user_id": 1006,
        "pantry_ingredient_ids": [1, 2, 3, 4, 5, 9, 10, 14, 16, 22, 34, 35, 45, 47, 52],
        "expiring_ingredient_ids": [34, 35],
        "taste_preference": {"spicy_level": 2, "sweet_level": 1, "salty_level": 2},
        "household_size": 3,
        "preferred_cuisines": ["한식", "중식"],
        "max_cook_minutes": 40,
        "allergy_group_codes": ["SHRIMP", "SQUID", "SHELLFISH", "CRAB", "MACKEREL"],
        "top_k": 20,
    },
    {
        "user_id": 1007,
        "pantry_ingredient_ids": [1, 3, 4, 5, 6, 7, 8, 9, 10, 30, 31, 32, 33, 34, 45, 47, 50, 52],
        "expiring_ingredient_ids": [8, 33],
        "taste_preference": {"spicy_level": 1, "sweet_level": 2, "salty_level": 1},
        "household_size": 1,
        "preferred_cuisines": ["한식", "양식"],
        "max_cook_minutes": 30,
        "allergy_group_codes": [],
        "top_k": 20,
    },
    {
        "user_id": 1008,
        "pantry_ingredient_ids": [11, 12, 13, 23, 24, 29, 30, 48, 49, 57, 58, 60],
        "expiring_ingredient_ids": [12, 58],
        "taste_preference": {"spicy_level": 0, "sweet_level": 4, "salty_level": 1},
        "household_size": 2,
        "preferred_cuisines": ["양식"],
        "max_cook_minutes": 45,
        "allergy_group_codes": [],
        "top_k": 20,
    },
    {
        "user_id": 1009,
        "pantry_ingredient_ids": [1, 2, 3, 11, 15, 17, 18, 22, 36, 45, 48, 49, 52, 53],
        "expiring_ingredient_ids": [17, 18],
        "taste_preference": {"spicy_level": 0, "sweet_level": 2, "salty_level": 4},
        "household_size": 2,
        "preferred_cuisines": ["일식", "중식"],
        "max_cook_minutes": None,
        "allergy_group_codes": [],
        "top_k": 10,
    },
    {
        "user_id": 1010,
        "pantry_ingredient_ids": [4, 5, 9, 14, 16, 22, 26, 27, 45, 46, 48, 51, 52, 56],
        "expiring_ingredient_ids": [26, 27],
        "taste_preference": {"spicy_level": 3, "sweet_level": 3, "salty_level": 2},
        "household_size": "가구원 3명",
        "preferred_cuisines": ["분식", "한식"],
        "max_cook_minutes": 30,
        "allergy_group_codes": [],
        "top_k": 20,
    },
    {
        "user_id": 1011,
        "pantry_ingredient_ids": [1, 1, 2, 3, 11, 14, 21, 22, 22, 45, 46, 51, 52],
        "expiring_ingredient_ids": [14, 99],
        "taste_preference": {"spicy_level": 3, "sweet_level": 2, "salty_level": 3},
        "household_size": 2,
        "preferred_cuisines": ["한식"],
        "max_cook_minutes": 60,
        "allergy_group_codes": [],
        "top_k": 50,
        "nickname": "요리초보",
        "app_version": "2.3.1",
    },
    {
        "user_id": 1012,
        # 온보딩을 하지 않은 사용자. 취향 없이 다양한 목록을 내는 경로를 검사합니다.
        "onboarding_picks": [],
        "pantry_ingredient_ids": [45, 46, 47, 48, 49, 50, 51, 52, 53],
        "expiring_ingredient_ids": [],
        "taste_preference": None,
        "household_size": 1,
        "preferred_cuisines": ["한식"],
        "max_cook_minutes": None,
        "allergy_group_codes": ["WHEAT", "PEANUT"],
        "top_k": 20,
    },
]


def unit(key: str) -> float:
    """키마다 고정된 0 이상 1 미만의 값. 난수 대신 씁니다."""
    digest = hashlib.blake2b(f"{SEED}:{key}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def pick[T](key: str, options: Sequence[T]) -> T:
    return options[int(unit(key) * len(options))]


def sample[T](key: str, options: Sequence[T], count: int) -> list[T]:
    """겹치지 않게 count 개. 키별 해시로 정렬하므로 섞임이 고정됩니다."""
    ordered = sorted(options, key=lambda option: unit(f"{key}:{option}"))
    return ordered[:count]


def between(key: str, low: float, high: float) -> float:
    return low + (high - low) * unit(key)


def build_recipe(index: int) -> dict[str, object]:
    key = f"recipe:{index}"
    cuisine = pick(f"{key}:cuisine", list(CUISINES))
    spec = CUISINES[cuisine]

    essential_count = 2 + int(unit(f"{key}:essential_count") * 3)
    essential = sample(f"{key}:essential", MAIN_INGREDIENT_IDS, essential_count)
    other_mains = [i for i in MAIN_INGREDIENT_IDS if i not in essential]
    extra_mains = sample(f"{key}:extra", other_mains, 1 + int(unit(f"{key}:extra_count") * 3))
    seasoning_count = 1 + int(unit(f"{key}:seasoning_count") * 3)
    extra_seasonings = sample(f"{key}:seasoning", spec.seasonings, seasoning_count)

    flavor = [
        round(min(1.0, max(0.0, base + between(f"{key}:flavor:{axis}", -0.2, 0.2))), 3)
        for axis, base in enumerate(spec.flavor)
    ]
    has_quality = index % MISSING_QUALITY_EVERY != 0
    product_count = int(unit(f"{key}:products") * 3) if unit(f"{key}:has_products") < 0.5 else 0
    return {
        "recipe_id": index,
        "title": f"{INGREDIENTS[essential[0]]} {pick(f'{key}:dish', spec.dishes)}",
        "cuisine": cuisine,
        # 우연성 탐색의 축. A 트랙 recipe_feature.cluster_id 를 흉내 냅니다.
        "cluster_id": int(unit(f"{key}:cluster") * CLUSTER_COUNT),
        "essential_ids": sorted(essential),
        "all_ids": sorted({*essential, *extra_mains, *extra_seasonings}),
        "flavor_vec": flavor,
        "popularity_score": round(between(f"{key}:popularity", 0.0, 1.0) ** 1.5, 3),
        "quality_score": round(between(f"{key}:quality", 0.3, 1.0), 3) if has_quality else None,
        "cook_minutes": pick(f"{key}:minutes", COOK_MINUTES_OPTIONS),
        "product_ids": [index * PRODUCT_ID_BASE + offset for offset in range(1, product_count + 1)],
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # 어느 OS 에서 돌려도 같은 바이트가 나오게 줄 끝을 고정합니다.
    path.write_text(text, encoding="utf-8", newline="\n")


PICK_COUNT = 4
PRESENTED_PATH = Path(__file__).resolve().parents[1] / "seeds" / "onboarding_recipes.yaml"


def picks_for(preference: dict[str, int], presented: list[list[float]]) -> list[int]:
    """3축 척도와 앞 3축이 가장 가까운 제시 음식 네 개. 결정적입니다.

    실제 사용자는 손으로 고르지만, 가상 사용자는 자기 척도와 어긋나지 않는 음식을 고른 것으로
    둡니다. 척도와 고른 음식이 다른 경우는 검사가 따로 만듭니다.
    """
    target = [
        preference["spicy_level"] / 4,
        preference["salty_level"] / 4,
        preference["sweet_level"] / 4,
    ]
    ranked = sorted(
        range(len(presented)),
        key=lambda i: (
            math.dist(target, presented[i][:3]),
            i,
        ),
    )
    return sorted(ranked[:PICK_COUNT])


def with_picks(persona: dict[str, object], presented: list[list[float]]) -> dict[str, object]:
    if "onboarding_picks" in persona:
        return persona
    preference = persona["taste_preference"]
    if not isinstance(preference, dict):
        raise TypeError(f"고른 음식이 없으면 척도가 있어야 합니다: {persona['user_id']}")
    return {**persona, "onboarding_picks": picks_for(preference, presented)}


def main() -> None:
    presented_doc = yaml.safe_load(PRESENTED_PATH.read_text(encoding="utf-8"))
    presented = [[float(v) for v in entry["flavor"]] for entry in presented_doc["presented"]]
    catalog = {
        "seed": SEED,
        "ingredients": {str(i): name for i, name in INGREDIENTS.items()},
        "allergen_groups": ALLERGEN_GROUPS,
        "cuisines": list(CUISINES),
        "recipes": [build_recipe(index) for index in range(1, RECIPE_COUNT + 1)],
    }
    write_json(OUT_DIR / "catalog.json", catalog)
    for number, persona in enumerate(PERSONAS, start=1):
        write_json(
            OUT_DIR / "personas" / f"persona_{number:02d}.json", with_picks(persona, presented)
        )
    print(f"wrote {RECIPE_COUNT} recipes and {len(PERSONAS)} personas to {OUT_DIR}")


if __name__ == "__main__":
    main()
