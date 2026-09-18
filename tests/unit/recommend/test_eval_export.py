"""내보내기의 순수 부분. DB 행을 가명화·필드 제거해 평가 기록으로 (명세 2.1, 14절)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from features.recommend.enums import FEATURE_KEYS, EventType, UserMode
from features.recommend.evaluation import export
from features.recommend.evaluation.record import InvalidRecordError, exclusion_reason, validate
from features.recommend.repository_eval import EvalRows

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))
SALT = "test-salt"
OPTIONS = export.ExportOptions(salt=SALT)


def _candidate(recipe_id: int, *, rank: int | None = None, **extra: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "recipe_id": recipe_id,
        "missing_count": 0,
        "missing_ids": [],
        "coverage": 1.0,
        "cluster_id": None,
        "features": dict.fromkeys(FEATURE_KEYS) | {"f_coverage": 0.5},
        "score": 0.5,
        "penalty": 1.0,
    }
    if rank is not None:
        row |= {"final_rank": rank, "propensity": 1.0, "is_exploration": False} | extra
    return row


def _row(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "request_id": uuid4(),
        "user_id": 42,
        "session_id": "c-42-abc",
        "model_version": "reco-b-linear-v0",
        "config_hash": "cfg",
        "warm_alpha": 0.3,
        "stats_version": 1,
        "policies": None,
        "stage_trace": {
            "trace_version": "v1",
            "stages": [
                {
                    "name": "rerank",
                    "in_count": 3,
                    "out_count": 2,
                    "latency_ms": 1,
                    "params": {"cuisine_unmet": "korean"},
                }
            ],
            "totals": {"latency_ms": 20, "degraded": False, "user_mode": "blended"},
        },
        # 노출 2개(final_rank 있음) + 미노출 1개. 순서는 일부러 뒤섞습니다
        "candidates": [_candidate(3), _candidate(2, rank=2), _candidate(1, rank=1)],
        "served": [1, 2],
        "total_latency_ms": 20,
        "created_at": NOW,
        "is_simulated": False,
        "pantry_snapshot": [7, 8],
        "pantry_detail": [{"ingredient_id": 7}],
        "allergy_snapshot": [9],
    }
    base.update(overrides)
    return base


def test_record_drops_identity_and_pantry_and_orders_items() -> None:
    row = _row()
    events = [
        {"recipe_id": 2, "event_type": "click", "value": None, "position": 2, "created_at": NOW}
    ]
    record = export.build_record(
        row,
        events=events,
        user_cooks=[(5, NOW)],
        ingredients={1: [10, 11], 2: [11]},
        options=OPTIONS,
    )

    assert record.user_hash == export.pseudonymize(42, SALT)
    assert record.user_hash != "42"
    dumped = record.model_dump_json()
    for forbidden in ('"user_id"', "pantry_snapshot", "pantry_detail", "allergy_snapshot"):
        assert forbidden not in dumped
    assert [i.recipe_id for i in record.items] == [1, 2]
    assert record.candidates is None
    assert record.session_prefix == "c"
    assert record.user_mode == UserMode.BLENDED
    assert record.cuisine_unmet is True
    assert record.events[0].event_type == EventType.CLICK
    assert record.user_events[0].recipe_id == 5
    assert record.ingredients == {1: [10, 11], 2: [11]}
    assert validate(record) is record


def test_with_candidates_keeps_the_unexposed_pool() -> None:
    record = export.build_record(
        _row(),
        events=[],
        user_cooks=[],
        ingredients={1: [10], 3: [12]},
        options=export.ExportOptions(salt=SALT, with_candidates=True),
    )
    assert record.candidates is not None
    assert {c.recipe_id for c in record.candidates} == {1, 2, 3}
    # 미노출 후보의 재료도 실려야 목표 정책의 MMR 이 그 후보를 최대 다양성으로 보지 않습니다
    assert record.ingredients == {1: [10], 2: [], 3: [12]}


def test_missing_user_mode_falls_back_to_onboarding() -> None:
    row = _row(stage_trace={"trace_version": "v1", "stages": [], "totals": {"latency_ms": 1}})
    record = export.build_record(row, events=[], user_cooks=[], ingredients={}, options=OPTIONS)
    assert record.user_mode == UserMode.COLD
    assert record.cuisine_unmet is False
    # JSONB null 도 온보딩입니다. 한 행 때문에 배치 전체가 죽지 않습니다
    row = _row(stage_trace={"trace_version": "v1", "stages": [], "totals": {"user_mode": None}})
    assert (
        export.build_record(
            row, events=[], user_cooks=[], ingredients={}, options=OPTIONS
        ).user_mode
        == UserMode.COLD
    )


def test_row_without_candidates_becomes_an_excluded_record() -> None:
    """load_test 모드는 candidates 를 저장하지 않습니다. 평가 분모에 들어가면 안 됩니다."""
    record = export.build_record(
        _row(candidates=None), events=[], user_cooks=[], ingredients={}, options=OPTIONS
    )
    assert record.items == []
    assert exclusion_reason(record) == "no_items"


def test_served_mismatch_fails_fast() -> None:
    with pytest.raises(InvalidRecordError, match="served"):
        export.build_record(
            _row(served=[2, 1]), events=[], user_cooks=[], ingredients={}, options=OPTIONS
        )


def test_build_records_assembles_header_from_rows() -> None:
    rows = EvalRows(logs=[_row()], ingredients={1: [10]}, frequency={10: 2}, catalog_size=4)
    header, records = export.build_records(rows, OPTIONS, exported_at=NOW)
    assert header.catalog_size == 4
    assert header.ingredient_idf == {10: pytest.approx(math.log(2))}
    assert len(records) == 1


def test_session_prefix_is_only_the_first_letter() -> None:
    record = export.build_record(
        _row(session_id="d-42-x"), events=[], user_cooks=[], ingredients={}, options=OPTIONS
    )
    assert record.session_prefix == "d"
    record = export.build_record(
        _row(session_id=None), events=[], user_cooks=[], ingredients={}, options=OPTIONS
    )
    assert record.session_prefix is None


def test_pseudonymize_is_stable_and_salted() -> None:
    assert export.pseudonymize(1, SALT) == export.pseudonymize(1, SALT)
    assert export.pseudonymize(1, SALT) != export.pseudonymize(1, "other")
    assert len(export.pseudonymize(1, SALT)) == 16


def test_idf_from_document_frequency() -> None:
    idf = export.idf_from_frequency({10: 2, 11: 4}, n_recipes=4)
    assert idf[10] == pytest.approx(math.log(2))
    assert idf[11] == pytest.approx(0.0)


def test_header_stamps_versions_and_source() -> None:
    header = export.build_header(catalog_size=100, idf={1: 0.5}, exported_at=NOW)
    assert header.source == "db"
    assert header.catalog_size == 100
