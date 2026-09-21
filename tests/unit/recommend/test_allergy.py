"""알레르기 라벨 → 막을 재료. 조용히 새는 자리를 검사로 막습니다.

여기가 틀리면 에러가 나지 않습니다. 추천은 200 으로 나가고 알레르기 재료가 섞입니다.
그래서 "무엇이 막히는가" 만이 아니라 **"무엇이 빠져나갈 수 있는가"** 를 봅니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from features.recommend.engine import allergy
from features.recommend.enums import ALLERGEN_GROUPS

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "seeds" / "ingredient.csv"
BACKEND = ROOT / "tests" / "fixtures" / "recommend" / "backend_ingredients.json"


@pytest.fixture(scope="module")
def names() -> dict[int, str]:
    """백엔드 재료 사전의 사본(2026-09-21 덤프 148종). 덤프 자체는 git 에 없습니다."""
    rows = json.loads(BACKEND.read_text(encoding="utf-8"))["ingredients"]
    return {int(r["ingredient_id"]): str(r["name"]) for r in rows}


@pytest.fixture(scope="module")
def groups(names: dict[int, str]) -> dict[int, str]:
    mapped, _ = allergy.ingredient_groups(names, allergy.load_seed_allergen_groups(SEED))
    return mapped


def _blocked(labels: list[str], names: dict[int, str], groups: dict[int, str]) -> set[str]:
    return {names[i] for i in allergy.resolve(labels, names, groups).blocked_ids}


# ── 재료가 시드에 닿는가 ─────────────────────────────────────────────────────


def test_every_backend_ingredient_reaches_the_seed(names: dict[int, str]) -> None:
    """닿지 못한 재료는 알레르기 군이 **조용히 비어** 하드컷을 빠져나갑니다.

    09-21 에 `통깨` 가 그랬습니다. 이 검사가 빨개지면 `BACKEND_NAME_ALIASES` 에 그 이름을
    더하거나 시드에 재료를 더합니다 — 검사를 느슨하게 하지 않습니다.
    """
    _, unmapped = allergy.ingredient_groups(names, allergy.load_seed_allergen_groups(SEED))
    assert unmapped == ()


def test_every_alias_points_at_a_real_seed_name() -> None:
    """별칭의 오른쪽이 시드에 없으면 별칭이 있어도 닿지 못합니다."""
    seed = allergy.load_seed_allergen_groups(SEED)
    assert [v for v in allergy.BACKEND_NAME_ALIASES.values() if v not in seed] == []


def test_sesame_blocks_the_seed_that_is_in_a_third_of_the_recipes(
    names: dict[int, str], groups: dict[int, str]
) -> None:
    """회귀: `통깨` 는 레시피의 32.8% 에 들어가는데 시드 이름(참깨)과 달라 군이 비어 있었습니다."""
    assert {"통깨", "참기름", "들기름", "들깨가루"} <= _blocked(["참깨"], names, groups)


# ── 백엔드가 보내는 모양 ─────────────────────────────────────────────────────


def test_the_backend_shape_milk_and_peanut(names: dict[int, str], groups: dict[int, str]) -> None:
    """`["우유", "땅콩"]` — 2026-09-21 유재현이 전한 백엔드의 전달 모양입니다."""
    blocked = _blocked(["우유", "땅콩"], names, groups)

    assert {"우유", "버터", "생크림", "연유"} <= blocked
    assert {"치즈", "요거트"} <= blocked, "별칭을 거쳐야 닿는 이름(체다치즈 · 플레인요거트)"
    assert {"땅콩", "견과류"} <= blocked
    assert "두부" not in blocked and "달걀" not in blocked


def test_a_label_blocks_its_whole_group_on_purpose(
    names: dict[int, str], groups: dict[int, str]
) -> None:
    """넓게 막습니다. 덜 막아서 생기는 일과 더 막아서 생기는 일의 무게가 다릅니다."""
    assert {"호두", "아몬드"} <= _blocked(["땅콩"], names, groups)
    assert {"바지락", "굴", "김치"} <= _blocked(["새우"], names, groups), "김치는 젓갈 때문"
    assert {"오징어", "진미채"} <= _blocked(["오징어"], names, groups)


def test_processed_meats_follow_the_meat(names: dict[int, str], groups: dict[int, str]) -> None:
    """시드에 군이 없는 것은 이름으로 막습니다."""
    assert _blocked(["돼지고기"], names, groups) == {"돼지고기", "베이컨", "소시지", "햄", "스팸"}
    assert _blocked(["토마토"], names, groups) == {"토마토", "토마토소스", "케첩"}


# ── 빠져나갈 수 있는 자리 ────────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["아황산류", "MSG", "고수", "매운 것"])
def test_an_unknown_label_is_returned_not_dropped(
    label: str, names: dict[int, str], groups: dict[int, str]
) -> None:
    """조용히 버리면 그 사용자는 보호받지 못하는데 응답은 200 입니다."""
    resolved = allergy.resolve(["우유", label], names, groups)

    assert resolved.unknown_labels == (label,)
    assert resolved.blocked_ids, "아는 라벨은 그대로 막는다"


def test_short_fragments_do_not_leak_into_other_foods() -> None:
    """'밀' 은 '메밀' 을, '콩' 은 '땅콩' 을, '게' 는 무엇이든 잡습니다. 조각으로 안 씁니다."""
    names = {1: "메밀면", 2: "밀가루", 3: "땅콩", 4: "콩나물", 5: "게맛살", 6: "가게"}
    groups = {1: "buckwheat", 2: "gluten", 3: "nut", 4: "soy", 5: "fish", 6: ""}

    def blocked(label: str) -> set[str]:
        return {names[i] for i in allergy.resolve([label], names, groups).blocked_ids}

    assert blocked("밀") == {"밀가루"}
    assert blocked("콩") == {"콩나물"}
    assert "가게" not in blocked("게")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 우유 ", "우유"),
        ("우유 알레르기", "우유"),
        ("땅콩알러지", "땅콩"),
        ("DAIRY", "dairy"),
        ("알레르기", "알레르기"),
    ],
)
def test_labels_are_read_the_way_people_write_them(raw: str, expected: str) -> None:
    assert allergy.normalize_label(raw) == expected


def test_the_engine_group_codes_are_labels_too(
    names: dict[int, str], groups: dict[int, str]
) -> None:
    """`OnboardingIn.allergy_groups` 의 영문 코드도 같은 함수가 받습니다. 길은 하나입니다."""
    assert set(ALLERGEN_GROUPS) <= set(allergy.LABEL_RULES)
    assert _blocked(["dairy"], names, groups) == _blocked(["우유"], names, groups)


def test_name_rules_reach_an_ingredient_the_seed_does_not_know() -> None:
    """군을 몰라도 이름은 압니다. 시드에 없는 새 재료도 이름 규칙에는 걸립니다."""
    names = {1: "칵테일새우", 2: "양파"}

    assert allergy.resolve(["새우"], names, {}).blocked_ids == frozenset({1})


# ── 재료 목록이 틀렸을 때 — 제목이 두 번째 방어선 ───────────────────────────


def test_a_recipe_whose_rows_miss_the_allergen_is_caught_by_its_title(
    names: dict[int, str], groups: dict[int, str]
) -> None:
    """회귀: 새우 알레르기 사용자의 1위가 「고추장 건새우볶음」 이었습니다 (09-21 실데이터).

    그 레시피(recipe_id 1194)의 재료 행에는 건새우가 없습니다 — 마늘·고춧가루·고추장뿐입니다.
    재료 id 로만 막으면 이 구멍은 어떤 라벨 표로도 메워지지 않습니다.
    """
    by_name = {v: k for k, v in names.items()}
    rows = frozenset({by_name["고춧가루"], by_name["참기름"], by_name["고추장"]})
    shrimp = allergy.resolve(["새우"], names, groups)

    assert not (rows & shrimp.blocked_ids), "전제: 재료 행만 보면 안 걸린다"
    assert allergy.blocks(shrimp, rows, "고추장 건새우볶음 : 밑반찬 하나로 공기밥 뚝딱")
    assert not allergy.blocks(shrimp, rows, "고추장 멸치볶음")


@pytest.mark.parametrize(
    ("label", "title"),
    [
        ("달걀", "잔치국수 : 계란말이잔치국수"),
        ("새우", "쉬림프 오일 파스타"),
        ("땅콩", "진한 땅콩의맛 // 피넛버터쿠키!"),
        ("우유", "단호박 콘치즈"),
        ("돼지고기", "매콤 제육볶음"),
        ("해산물", "문어초무침"),
    ],
)
def test_the_title_is_read_in_the_words_people_use(
    label: str, title: str, names: dict[int, str], groups: dict[int, str]
) -> None:
    """재료 사전의 이름만으로는 모자랍니다 — 제목은 계란 · 쉬림프 · 피넛 이라고 적습니다."""
    assert allergy.blocks(allergy.resolve([label], names, groups), frozenset(), title)


def test_potato_soup_is_not_a_pine_nut_dish(names: dict[int, str], groups: dict[int, str]) -> None:
    """한 글자 조각은 다른 낱말 속에 들어갑니다. 「감잣국」 의 '잣' 은 잣이 아닙니다."""
    nuts = allergy.resolve(["견과류"], names, groups)

    assert not allergy.blocks(nuts, frozenset(), "황태 감잣국 / 황태 해장국")
    assert allergy.blocks(nuts, frozenset(), "고소한 잣죽 끓이기")


def test_ingredient_overlap_blocks_without_any_help_from_the_title(
    names: dict[int, str], groups: dict[int, str]
) -> None:
    by_name = {v: k for k, v in names.items()}
    dairy = allergy.resolve(["우유"], names, groups)

    assert allergy.blocks(dairy, frozenset({by_name["버터"]}), "초간단 감자구이")


def test_without_an_allergy_nothing_is_blocked_by_its_title() -> None:
    assert not allergy.blocks(allergy.AllergyResolution(), frozenset({1, 2}), "새우 크림 파스타")


def test_no_labels_block_nothing(names: dict[int, str], groups: dict[int, str]) -> None:
    resolved = allergy.resolve(["", "  "], names, groups)

    assert resolved.blocked_ids == frozenset() and resolved.unknown_labels == ()


def test_the_result_says_which_label_blocked_what(
    names: dict[int, str], groups: dict[int, str]
) -> None:
    """로그와 검수를 위해 라벨별 결과를 남깁니다."""
    resolved = allergy.resolve(["달걀", "밀"], names, groups)

    assert {names[i] for i in resolved.by_label["달걀"]} == {"달걀", "마요네즈", "메추리알"}
    assert "밀가루" in {names[i] for i in resolved.by_label["밀"]}
    assert resolved.blocked_ids == resolved.by_label["달걀"] | resolved.by_label["밀"]
