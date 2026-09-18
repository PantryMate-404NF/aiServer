"""품질 스냅샷. 기대 None 패턴, 수집 실패의 null, 파일 추가 (명세 8절)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from features.recommend.enums import FEATURE_KEYS
from features.recommend.evaluation import quality, synth


def test_feature_none_ratio_matches_expected_pattern_on_synth() -> None:
    """합성 기록은 재료 3개만 값이 있어 즉시 가능 5개 중 2개가 100% None 이라 경보가 납니다."""
    _, records = synth.generate(synth.SynthSpec(users=5, requests=20, hit_rate=0.3, seed=1))
    row = quality.snapshot(records, health=None, health_error="skipped")

    assert set(row["feature_none_ratio"]) == set(FEATURE_KEYS)
    assert row["feature_none_ratio"]["f_coverage"] == 0.0
    assert row["feature_none_ratio"]["f_cuisine"] == 1.0
    assert any("f_time_fit" in alert for alert in row["alerts"])
    assert not any("f_cuisine" in alert for alert in row["alerts"])


def test_health_failure_is_null_with_an_alert(make_record: Callable[..., Any]) -> None:
    row = quality.snapshot([make_record()], health=None, health_error="connection refused")
    assert row["health_counters"] is None
    assert any("health" in alert for alert in row["alerts"])


def test_health_payload_is_kept_raw_with_collection_time(make_record: Callable[..., Any]) -> None:
    row = quality.snapshot([make_record()], health={"status": "ok", "db": True}, health_error=None)
    assert row["health_counters"]["payload"] == {"status": "ok", "db": True}
    assert "collected_at" in row["health_counters"]


def test_db_items_are_null_without_db(make_record: Callable[..., Any]) -> None:
    row = quality.snapshot([make_record()], health=None, health_error="skipped")
    for key in ("orphan_request_ratio", "flavor_all_zero_ratio", "match_method_violation"):
        assert row[key] is None
    assert row["orphan_position_ratio"] == 0.0


def test_counts_and_positions_from_records() -> None:
    _, records = synth.generate(synth.SynthSpec(users=10, requests=60, hit_rate=0.3, seed=2))
    row = quality.snapshot(records, health=None, health_error="skipped")

    assert set(row["excluded_counts"]) <= {"d-session", "simulated_user", "not_reproducible"}
    assert row["degraded_ratio"] == 0.0
    assert sum(row["exploration_positions"].values()) > 0
    assert 0.0 <= row["cuisine_unmet_ratio"] <= 1.0


def test_append_writes_one_json_line(tmp_path: Path, make_record: Callable[..., Any]) -> None:
    out = tmp_path / "2026-09-18.jsonl"
    row = quality.snapshot([make_record()], health=None, health_error="skipped")
    quality.append(out, row)
    quality.append(out, row)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["orphan_position_ratio"] == 0.0
