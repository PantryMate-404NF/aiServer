"""온보딩 제시 목록을 내려주는 라우트. 시드 하나만 읽습니다.

## 왜 라우트가 필요한가

이 목록은 화면이 그대로 그리는 20개인데, 지금까지 내려주는 경로가 없어서 화면이
자기 쪽에 베껴 두고 있었습니다. 저장소가 다르면 검사가 건너가지 못하므로, 우리가
목록을 바꿔도 화면이 모르는 채로 남습니다.

목록은 바뀝니다 — 시드에 교체 후보가 따로 들어 있습니다. 그래서 베껴 두는 것이
아니라 그리기 직전에 받아 가는 것이 맞습니다.

## 맛 6축은 일부러 안 내보냅니다

맛 값이 밖으로 나가면 부르는 쪽이 그것을 사본으로 들게 되고, 우리 시드가 바뀌면
그 사본이 조용히 낡습니다. 지금 고치려는 문제와 같은 모양입니다. 점수는 우리가
내고 화면은 이름만 있으면 됩니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from features.recommend.enums import ONBOARDING_CUISINES, normalize_cuisine
from features.recommend.profile_store import PRESENTED_PATH, load_presented_menu

SEED = Path(__file__).resolve().parents[3] / "seeds" / "onboarding_recipes.yaml"


@pytest.fixture(scope="module")
def seed() -> dict[str, object]:
    return yaml.safe_load(SEED.read_text(encoding="utf-8"))


def test_the_module_constant_points_at_the_real_seed() -> None:
    """경로가 어긋나면 라우트가 엉뚱한 파일을 내려줍니다."""
    assert PRESENTED_PATH == SEED
    assert PRESENTED_PATH.exists()


def test_names_and_order_match_the_seed(seed: dict[str, object]) -> None:
    """순서까지 같아야 합니다. 화면이 보는 순서가 곧 시드 순서입니다."""
    presented = seed["presented"]
    assert isinstance(presented, list)
    _, rows = load_presented_menu()
    assert [name for name, _ in rows] == [entry["name"] for entry in presented]


def test_the_list_version_comes_from_the_seed(seed: dict[str, object]) -> None:
    """판 번호가 없으면 부르는 쪽이 목록이 바뀐 것을 알 수 없습니다."""
    version, _ = load_presented_menu()
    assert version == seed["version"]


def test_every_family_is_a_known_code() -> None:
    """한글 라벨이 아니라 코드로 내려줍니다. 화면 문구는 부르는 쪽이 정합니다."""
    _, rows = load_presented_menu()
    assert {family for _, family in rows} <= set(ONBOARDING_CUISINES)


def test_an_unknown_family_is_rejected_not_guessed(tmp_path: Path) -> None:
    """모르는 계열을 가까운 것으로 짐작하면 그 음식만 조용히 다른 유형이 됩니다."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump({"version": 9, "presented": [{"name": "무언가", "family": "프렌치"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="모르는 계열"):
        load_presented_menu(bad)


def test_the_route_answers_with_the_whole_list() -> None:
    client = pytest.importorskip("fastapi.testclient")
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER
    from main import create_app

    header = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}
    with client.TestClient(create_app(), headers=header) as http:
        response = http.get("/v1/onboarding/presented")
    assert response.status_code == 200
    body = response.json()
    _, rows = load_presented_menu()
    assert body["list_version"] == load_presented_menu()[0]
    assert [item["name"] for item in body["items"]] == [name for name, _ in rows]


def test_the_route_does_not_leak_the_flavour_axes() -> None:
    """맛 값을 내보내면 부르는 쪽이 사본을 들게 되고 그 사본이 낡습니다."""
    client = pytest.importorskip("fastapi.testclient")
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER
    from main import create_app

    header = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}
    with client.TestClient(create_app(), headers=header) as http:
        body = http.get("/v1/onboarding/presented").json()
    assert set(body["items"][0]) == {"name", "family"}
    assert "flavor" not in body["items"][0]
    assert "items" not in body["items"][0]


def test_the_korean_label_in_the_seed_still_normalises() -> None:
    """시드는 한글로 적고 라우트는 코드로 내려줍니다. 둘을 잇는 함수가 있어야 합니다."""
    assert normalize_cuisine("한식") == "korean"
