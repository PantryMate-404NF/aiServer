"""평가 기록. 불변식은 즉시 실패하고 제외 규칙은 건수로 남습니다 (명세 2.2)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from features.recommend.enums import EventType
from features.recommend.evaluation.record import (
    LABEL_VERSION,
    METRIC_VERSION,
    EvalEvent,
    EvalHeader,
    InvalidRecordError,
    apply_exclusions,
    count_excluded,
    exclusion_reason,
    read_jsonl,
    validate,
    write_jsonl,
)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))


def _header(**kw: object) -> EvalHeader:
    base: dict[str, Any] = {
        "label_version": LABEL_VERSION,
        "metric_version": METRIC_VERSION,
        "catalog_size": 1000,
        "ingredient_idf": {1: 0.1},
        "exported_at": NOW,
        "source": "synth",
    }
    base.update(kw)
    return EvalHeader(**base)


# ── 불변식 6종. 하나씩 깨뜨리면 그 필드 이름을 담아 실패합니다 ──────────
def test_items_outside_candidates_fail(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(candidates=[make_item(1, recipe_id=999)])
    with pytest.raises(InvalidRecordError, match="candidates"):
        validate(record)


def test_final_rank_must_be_contiguous_from_one(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(items=[make_item(1), make_item(3)])
    with pytest.raises(InvalidRecordError, match="final_rank"):
        validate(record)


def test_exploration_needs_propensity_below_one(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(items=[make_item(1, is_exploration=True, propensity=1.0)])
    with pytest.raises(InvalidRecordError, match="propensity"):
        validate(record)


def test_non_exploration_needs_propensity_one(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(items=[make_item(1, propensity=0.5)])
    with pytest.raises(InvalidRecordError, match="propensity"):
        validate(record)


def test_cuisine_slot_is_deterministic(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(
        items=[make_item(1, is_cuisine_slot=True, is_exploration=True, propensity=0.2)]
    )
    with pytest.raises(InvalidRecordError, match="is_cuisine_slot"):
        validate(record)


def test_event_position_must_match_final_rank(make_record: Callable[..., Any]) -> None:
    record = make_record(
        events=[EvalEvent(recipe_id=101, event_type=EventType.CLICK, position=2, created_at=NOW)]
    )
    with pytest.raises(InvalidRecordError, match="position"):
        validate(record)


def test_valid_record_passes_and_keeps_items(
    make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    record = make_record(
        items=[
            make_item(1),
            make_item(2, is_exploration=True, propensity=0.1, explore_source="uniform"),
        ],
        events=[EvalEvent(recipe_id=101, event_type=EventType.CLICK, position=1, created_at=NOW)],
    )
    assert validate(record) is record


# ── 제외 규칙 3종. 파일에서 지우지 않고 사유만 채웁니다 ─────────────────
def test_dev_session_is_excluded(make_record: Callable[..., Any]) -> None:
    assert exclusion_reason(make_record(session_prefix="d")) == "d-session"


def test_simulated_user_is_excluded(make_record: Callable[..., Any]) -> None:
    assert exclusion_reason(make_record(is_simulated=True)) == "simulated_user"


@pytest.mark.parametrize("field", ["config_hash", "warm_alpha", "stats_version"])
def test_missing_reproduction_keys_are_excluded(
    make_record: Callable[..., Any], field: str
) -> None:
    assert exclusion_reason(make_record(**{field: None})) == "not_reproducible"


def test_clean_record_is_not_excluded(make_record: Callable[..., Any]) -> None:
    assert exclusion_reason(make_record()) is None


def test_record_without_items_is_excluded(make_record: Callable[..., Any]) -> None:
    """candidates 를 저장하지 않는 서빙 모드의 행은 평가 분모에 들어가면 안 됩니다."""
    assert exclusion_reason(make_record(items=[])) == "no_items"


def test_include_simulated_keeps_the_other_reasons(make_record: Callable[..., Any]) -> None:
    rows = apply_exclusions(
        [make_record(is_simulated=True), make_record(is_simulated=True, config_hash=None)],
        include_simulated=True,
    )
    assert [r.excluded_reason for r in rows] == [None, "not_reproducible"]


# ── JSONL 왕복. 첫 줄은 헤더, 위반은 줄 번호를 담습니다 ─────────────────
def test_jsonl_round_trip_marks_exclusions(tmp_path: Path, make_record: Callable[..., Any]) -> None:
    path = tmp_path / "eval.jsonl"
    records = [make_record(), make_record(session_prefix="d"), make_record(is_simulated=True)]
    write_jsonl(path, _header(), records)

    header, loaded = read_jsonl(path)

    assert header.catalog_size == 1000
    assert [r.excluded_reason for r in loaded] == [None, "d-session", "simulated_user"]
    assert count_excluded(loaded) == {"d-session": 1, "simulated_user": 1}
    assert loaded[0].items[0].recipe_id == records[0].items[0].recipe_id


def test_invalid_line_reports_its_number(
    tmp_path: Path, make_record: Callable[..., Any], make_item: Callable[..., Any]
) -> None:
    path = tmp_path / "eval.jsonl"
    good = make_record()
    bad = make_record(items=[make_item(1), make_item(3)])
    write_jsonl(path, _header(), [good, bad])

    with pytest.raises(InvalidRecordError) as info:
        read_jsonl(path)
    assert info.value.line == 3
    assert info.value.field == "final_rank"


def test_header_from_other_version_is_refused(
    tmp_path: Path, make_record: Callable[..., Any]
) -> None:
    path = tmp_path / "eval.jsonl"
    write_jsonl(path, _header(label_version=LABEL_VERSION + 1), [make_record()])

    with pytest.raises(InvalidRecordError, match="label_version"):
        read_jsonl(path)


def test_event_window_is_relative_to_record_time(make_record: Callable[..., Any]) -> None:
    """이벤트 시각은 기록 시각과 같은 시간대여야 창 계산이 어긋나지 않습니다."""
    later = NOW + timedelta(days=1)
    record = make_record(
        events=[EvalEvent(recipe_id=101, event_type=EventType.COOK, position=1, created_at=later)]
    )
    assert validate(record).events[0].created_at - record.created_at == timedelta(days=1)
