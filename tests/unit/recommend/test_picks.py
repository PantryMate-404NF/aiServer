"""고른 음식을 이름으로 남기는 계약.

## 왜 인덱스를 버렸는가

`picks` 는 점수에 쓰이지 않습니다. 점수는 `pick_flavors`(맛 6축)가 냅니다.
`picks` 를 남기는 이유는 하나뿐입니다 — 재료 시드가 바뀌어 음식의 맛 값이 달라졌을 때
**다시 계산하기 위해서**입니다. 실제로 9/2 에 시드 2건을 고쳐 돈까스 기름짐이
0.45 에서 0.69 로 바뀐 적이 있습니다.

그런데 그 원본이 인덱스였습니다. 제시 목록은 교체 후보 20개가 파일에 따로 들어 있어
바뀔 것을 전제로 만들어졌고, 바뀌면 같은 숫자가 다른 음식을 가리킵니다. 즉
**`picks` 를 쓰려고 하는 바로 그 상황에서 틀립니다.** 에러는 나지 않습니다.

이름은 목록이 어떻게 바뀌어도 그 음식입니다.

## 인덱스도 계속 받습니다

우리 시뮬과 검사가 그 모양으로 쓰고 있습니다. 어느 쪽이든 목록 밖이면 거부합니다 —
짐작해서 채우면 고르지 않은 음식이 그 사용자의 취향이 됩니다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from features.recommend.profile_store import (
    PRESENTED_PATH,
    SCHEMA_VERSION,
    JsonProfileStore,
    load_presented_flavors,
    load_presented_names,
)
from features.recommend.service import onboarding_profile

NOW = datetime(2026, 9, 20, tzinfo=UTC)


Flavors = tuple[tuple[float | None, ...], ...]


@pytest.fixture(scope="module")
def presented() -> Flavors:
    return load_presented_flavors(PRESENTED_PATH)


@pytest.fixture(scope="module")
def names() -> tuple[str, ...]:
    return load_presented_names()


def test_a_name_is_stored_not_a_position(presented: Flavors, names: tuple[str, ...]) -> None:
    profile = onboarding_profile(1, ["불고기", "김치찌개"], None, presented, NOW)
    assert profile.picks == ("불고기", "김치찌개")


def test_an_index_still_works_and_is_stored_as_a_name(
    presented: Flavors, names: tuple[str, ...]
) -> None:
    """시뮬·검사가 쓰는 모양입니다. 저장되는 것은 똑같이 이름입니다."""
    by_index = onboarding_profile(1, [2, 3], None, presented, NOW)
    by_name = onboarding_profile(1, [names[2], names[3]], None, presented, NOW)
    assert by_index.picks == by_name.picks
    assert by_index.pick_flavors == by_name.pick_flavors


def test_the_name_follows_the_food_when_the_list_moves(
    presented: Flavors, names: tuple[str, ...]
) -> None:
    """이것이 인덱스를 버린 이유입니다.

    목록의 순서만 뒤집어도 같은 인덱스는 다른 음식을 가리킵니다. 이름은 그대로입니다.
    """
    flipped_names = tuple(reversed(names))
    flipped_flavors = tuple(reversed(presented))

    before = onboarding_profile(1, ["불고기"], None, presented, NOW)
    after = onboarding_profile(
        1, ["불고기"], None, flipped_flavors, NOW, presented_names=flipped_names
    )
    assert before.picks == after.picks == ("불고기",)
    assert before.pick_flavors == after.pick_flavors, "같은 음식이면 맛도 같아야 합니다"

    # 같은 시나리오를 인덱스로 하면 다른 음식이 됩니다.
    index_of_bulgogi = names.index("불고기")
    moved = onboarding_profile(
        1, [index_of_bulgogi], None, flipped_flavors, NOW, presented_names=flipped_names
    )
    assert moved.picks != ("불고기",), "인덱스는 목록이 바뀌면 다른 음식을 가리킵니다"


def test_an_unknown_name_is_rejected_rather_than_guessed(presented: Flavors) -> None:
    """가까운 음식으로 바꿔 넣으면 고르지 않은 것이 취향이 되고 응답은 200 입니다."""
    with pytest.raises(ValueError, match="제시 목록에 없는 음식"):
        onboarding_profile(1, ["없는음식"], None, presented, NOW)


def test_an_out_of_range_index_is_rejected(presented: Flavors) -> None:
    with pytest.raises(ValueError, match="제시 목록 밖의 인덱스"):
        onboarding_profile(1, [len(presented)], None, presented, NOW)


def test_the_names_and_the_flavours_must_come_from_the_same_list(presented: Flavors) -> None:
    """개수가 어긋나면 배선이 잘못된 것입니다. 조용히 넘기면 이름이 밀립니다."""
    with pytest.raises(ValueError, match="개수가 다릅니다"):
        onboarding_profile(1, ["불고기"], None, presented[:5], NOW)


def test_the_file_keeps_names_and_round_trips(tmp_path: Path, presented: Flavors) -> None:
    store = JsonProfileStore(tmp_path)
    store.save(onboarding_profile(7, ["불고기", "돈까스"], None, presented, NOW))

    raw = json.loads(next(tmp_path.rglob("*.json")).read_text(encoding="utf-8"))
    assert raw["schema"] == SCHEMA_VERSION
    assert raw["picks"] == ["불고기", "돈까스"]

    back = store.load(7)
    assert back is not None and back.picks == ("불고기", "돈까스")


def test_an_older_file_with_indexes_is_converted_on_read(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    """판 2 까지는 인덱스를 적었습니다. 지금 목록으로 옮겨 담습니다."""
    store = JsonProfileStore(tmp_path)
    store.save(onboarding_profile(9, ["불고기"], None, load_presented_flavors(PRESENTED_PATH), NOW))

    path = next(tmp_path.rglob("*.json"))
    old = json.loads(path.read_text(encoding="utf-8"))
    old["schema"] = 2
    old["picks"] = [names.index("불고기")]
    path.write_text(json.dumps(old), encoding="utf-8")

    back = store.load(9)
    assert back is not None and back.picks == ("불고기",)


def test_an_older_file_with_an_impossible_index_is_rejected(tmp_path: Path) -> None:
    """지금 목록으로 해석할 수 없는 값을 짐작해 채우면 그 사용자만 조용히 틀립니다."""
    store = JsonProfileStore(tmp_path)
    store.save(
        onboarding_profile(11, ["불고기"], None, load_presented_flavors(PRESENTED_PATH), NOW)
    )

    path = next(tmp_path.rglob("*.json"))
    old = json.loads(path.read_text(encoding="utf-8"))
    old["schema"] = 2
    old["picks"] = [999]
    path.write_text(json.dumps(old), encoding="utf-8")

    with pytest.raises(ValueError, match="이름으로 옮길 수 없습니다"):
        store.load(11)
