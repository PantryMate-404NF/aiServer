"""쌓인 두 JSONL 을 이어 평가표를 내는 오프라인 평가 (`evaluation/offline.py`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from features.recommend.enums import FEATURE_KEYS
from features.recommend.evaluation import offline


def item(
    recipe_id: int, rank: int, *, exploration: bool = False, propensity: float = 1.0
) -> dict[str, Any]:
    features = dict.fromkeys(FEATURE_KEYS)
    features["f_coverage"] = 1.0
    return {
        "recipe_id": recipe_id,
        "final_rank": rank,
        "is_exploration": exploration,
        "is_cuisine_slot": False,
        "propensity": propensity,
        "features": features,
    }


def recommendation(
    request_id: str,
    user_id: int,
    items: list[dict[str, Any]],
    *,
    degraded: bool = False,
    day: str = "2026-09-22",
) -> dict[str, Any]:
    return {
        "log": {
            "request_id": request_id,
            "user_id": user_id,
            "model_version": "reco-b-linear-v0",
            "created_at": f"{day}T10:00:00+09:00",
            "total_latency_ms": 80,
            "served": [i["recipe_id"] for i in items],
            "stage_trace": {
                "stages": [
                    {
                        "name": "retrieval",
                        "fallback": None,
                        "params": {"allergy_labels_unknown": ""},
                    },
                    {"name": "ranking"},
                    {"name": "rerank", "dropped": {"same_dish": 3}},
                ],
                "totals": {"degraded": degraded},
            },
        },
        "items": items,
    }


def event(request_id: str | None, recipe_id: int, kind: str = "click") -> dict[str, Any]:
    return {"user_id": 1, "event_type": kind, "recipe_id": recipe_id, "request_id": request_id}


TWO_LISTS = [
    recommendation(
        "r1", 1, [item(10, 1), item(11, 2), item(12, 3, exploration=True, propensity=0.25)]
    ),
    recommendation(
        "r2",
        2,
        [item(20, 1), item(21, 2), item(22, 3, exploration=True, propensity=0.5)],
        degraded=True,
    ),
]


def test_events_are_joined_by_request_id_and_recipe() -> None:
    report = offline.evaluate(
        TWO_LISTS,
        [event("r1", 10), event("r1", 12), event("r2", 99), event(None, 20), event("nope", 20)],
    )

    assert (report.events_linked, report.events_unlinked, report.events_orphan) == (2, 1, 2)
    assert report.recommendations == 2 and report.impressions == 6 and report.users == 2


def test_slot_rates_and_the_ips_correction() -> None:
    report = offline.evaluate(
        TWO_LISTS, [event("r1", 10), event("r1", 12, "click"), event("r2", 21, "cook")]
    )

    personal, exploration = report.by_slot["personal"], report.by_slot["exploration"]
    assert (personal.impressions, personal.clicks, personal.cooks) == (4, 1, 1)
    assert personal.ctr == pytest.approx(0.25) and personal.ips_ctr == pytest.approx(0.25)
    assert (exploration.impressions, exploration.clicks) == (2, 1)
    assert exploration.ctr == pytest.approx(0.5)
    # 자기 정규화 IPS: (1/0.25) / (1/0.25 + 1/0.5) = 4 / 6
    assert exploration.ips_ctr == pytest.approx(4 / 6)
    assert exploration.mean_propensity == pytest.approx(0.375)


def test_rates_without_impressions_are_unknown_not_zero() -> None:
    report = offline.evaluate(TWO_LISTS, [])

    assert report.by_slot["cuisine"].ctr is None
    assert report.by_slot["personal"].ctr == 0.0


def test_dead_features_carry_their_weight_and_the_rank_curve_is_per_position() -> None:
    report = offline.evaluate(TWO_LISTS, [event("r1", 10), event("r2", 20)])

    assert "f_coverage" not in report.dead_features
    assert {"f_ing_pref", "f_cooccur"} <= set(report.dead_features)
    assert report.dead_weight == pytest.approx(
        sum(offline.DEFAULT_WEIGHTS[k] for k in report.dead_features)
    )
    assert report.by_rank[1] == (2, 2, 1.0) and report.by_rank[2] == (2, 0, 0.0)


def test_engine_state_is_read_from_the_trace() -> None:
    report = offline.evaluate(TWO_LISTS, [])

    assert report.degraded_ratio == pytest.approx(0.5)
    assert report.fallback_stages == {"none": 2}
    assert report.same_dish_per_request == pytest.approx(3.0)
    assert report.latency_p50_ms == pytest.approx(80.0)
    assert report.by_day == {"2026-09-22": (2, 6, 0.0)}


def test_the_daily_files_are_read_within_the_window_and_broken_lines_are_skipped(
    tmp_path: Path,
) -> None:
    (tmp_path / "recommendations-20260921.jsonl").write_text(
        json.dumps(recommendation("old", 1, [item(1, 1)], day="2026-09-21")) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "recommendations-20260922.jsonl").write_text(
        json.dumps(TWO_LISTS[0]) + "\n{broken\n" + json.dumps(TWO_LISTS[1]) + "\n", encoding="utf-8"
    )
    (tmp_path / "events-20260922.jsonl").write_text(
        json.dumps(event("r1", 10)) + "\n", encoding="utf-8"
    )

    everything = offline.load_logs(tmp_path)
    recent = offline.load_logs(tmp_path, since=offline.date(2026, 9, 22))

    assert len(everything[0]) == 3 and len(everything[1]) == 1
    assert [r["log"]["request_id"] for r in recent[0]] == ["r1", "r2"]


def test_render_is_a_readable_table() -> None:
    text = offline.render(offline.evaluate(TWO_LISTS, [event("r1", 10)]))

    assert "칸별 반응" in text and "personal" in text and "꺼진 신호" in text
    assert "2026-09-22" in text
