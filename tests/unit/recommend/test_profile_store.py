"""JSON 취향 저장소. 왕복, 샤딩, 원자적 쓰기, 깨진 파일, 제시 목록 읽기."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from features.recommend import profile_store
from features.recommend.engine.persona import TasteEvent, TasteProfile
from features.recommend.engine.taste import FLAVOR_AXES
from features.recommend.enums import EventType
from features.recommend.profile_store import JsonProfileStore

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=KST)
ROOT = Path(__file__).resolve().parents[3]


def sample() -> TasteProfile:
    return TasteProfile(
        user_id=1001,
        picks=(0, 3, 5),
        pick_flavors=((0.1, 0.2, 0.3, 0.4, 0.5, 0.6), (0.0, 0.0, 0.0, None, None, None)),
        scales=(0.75, 1.0, 0.5),
        events=(
            TasteEvent(
                recipe_id=42,
                kind=EventType.COOK,
                at=NOW - timedelta(days=3),
                flavor=(0.5, 0.4, 0.3, 0.2, 0.1, 0.0),
            ),
            TasteEvent(
                recipe_id=7,
                kind=EventType.RATING,
                at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
                flavor=(1.0,) * 6,
                value=5.0,
            ),
        ),
        updated_at=NOW,
    )


def test_round_trip_keeps_everything(tmp_path: Path) -> None:
    store = JsonProfileStore(tmp_path)
    store.save(sample())
    loaded = store.load(1001)
    assert loaded == sample()
    assert loaded is not None and loaded.events[1].at.utcoffset() == timedelta(0)


def test_missing_user_is_none_not_empty(tmp_path: Path) -> None:
    assert JsonProfileStore(tmp_path).load(99) is None


def test_files_are_sharded_by_user_id(tmp_path: Path) -> None:
    store = JsonProfileStore(tmp_path)
    assert store.path(1001) == tmp_path / "e9" / "1001.json"
    assert store.path(256) == tmp_path / "00" / "256.json"
    store.save(sample())
    assert (tmp_path / "e9" / "1001.json").exists()


def test_save_is_atomic_and_overwrites(tmp_path: Path) -> None:
    store = JsonProfileStore(tmp_path)
    store.save(sample())
    store.save(TasteProfile(user_id=1001))
    assert store.load(1001) == TasteProfile(user_id=1001)
    assert not list(tmp_path.rglob("*.tmp"))


def test_a_broken_file_raises_instead_of_becoming_a_cold_user(tmp_path: Path) -> None:
    """조용히 None 을 돌려주면 이력이 사라진 사용자가 되고 아무도 모릅니다."""
    store = JsonProfileStore(tmp_path)
    target = store.path(5)
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="취향 파일"):
        store.load(5)


def test_an_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    store = JsonProfileStore(tmp_path)
    target = store.path(6)
    target.parent.mkdir(parents=True)
    target.write_text('{"schema": 99, "user_id": 6}', encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(6)


def test_presented_flavors_come_from_the_seed_in_engine_axis_order() -> None:
    presented = profile_store.load_presented_flavors(ROOT / "seeds" / "onboarding_recipes.yaml")
    assert len(presented) == 20
    assert all(len(v) == len(FLAVOR_AXES) for v in presented)
    assert all(0.0 <= x <= 1.0 for v in presented for x in v if x is not None)


def test_presented_loader_refuses_a_different_axis_order(tmp_path: Path) -> None:
    """값이 전부 0~1 이라 순서가 어긋나도 어떤 검사에도 안 걸립니다. 여기서만 잡습니다."""
    bad = tmp_path / "onboarding.yaml"
    bad.write_text(
        "axes: [단맛, 짠맛, 매움, 신맛, 감칠맛, 기름짐]\n"
        "presented:\n"
        "  - {name: x, flavor: [0, 0, 0, 0, 0, 0]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="축 순서"):
        profile_store.load_presented_flavors(bad)
