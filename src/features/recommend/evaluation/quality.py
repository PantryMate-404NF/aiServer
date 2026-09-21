"""품질 스냅샷 (명세 8절). 하루 1행을 계산해 JSONL 에 추가합니다.

DB 항목은 호출자가 `repository_eval.quality_items()` 결과를 넘겼을 때만 값이 있고 없으면 `null`
입니다. `null` 과 0 은 다릅니다. `/v1/health` 카운터는 누적하지 않고 원값을 수집 시각과 함께 둡니다.
재시작으로 0 이 되는 것이 보여야 유실이 보입니다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from features.recommend.enums import FEATURE_KEYS
from features.recommend.evaluation.metrics import exploration_positions
from features.recommend.evaluation.record import EvalRecord, apply_exclusions, count_excluded
from features.recommend.repository_eval import QUALITY_DB_KEYS

#: 측정 수단이나 데이터가 없어 100% None 이 정상인 피처 (명세 8절)
EXPECTED_ALL_NONE = frozenset(
    {"f_cuisine", "f_dish_type", "f_quality", "f_content", "f_ing_cf", "f_group_pref"}
)
#: 즉시 계산 가능해 0% None 이 정상인 피처
EXPECTED_NO_NONE = frozenset(
    {"f_coverage", "f_missing", "f_popularity", "f_time_fit", "f_skill_fit"}
)
#: 수집 한 번에 재시도 없이 실패를 기록합니다. 스냅샷은 매일 다시 돌므로 재시도가 곧 다음 실행입니다
HEALTH_TIMEOUT_SEC = 5.0


def fetch_health(url: str) -> tuple[dict[str, Any] | None, str | None]:
    """`/v1/health` 원값. 실패는 예외가 아니라 사유 문자열입니다."""
    try:
        response = httpx.get(url, timeout=HEALTH_TIMEOUT_SEC)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        return None, f"{type(error).__name__}: {error}"
    return payload, None


def _feature_none_ratio(records: Sequence[EvalRecord]) -> dict[str, float | None]:
    items = [item for r in records for item in r.items]
    if not items:
        return dict.fromkeys(FEATURE_KEYS)
    return {
        key: sum(1 for item in items if item.features.get(key) is None) / len(items)
        for key in FEATURE_KEYS
    }


def _feature_alerts(ratios: Mapping[str, float | None]) -> list[str]:
    alerts = []
    for key in sorted(EXPECTED_ALL_NONE):
        ratio = ratios[key]
        if ratio is not None and ratio < 1.0:
            alerts.append(
                f"{key}: None 100% 가 정상인데 {ratio:.0%}. 수단이 생겼으면 명세 8절을 갱신"
            )
    for key in sorted(EXPECTED_NO_NONE):
        ratio = ratios[key]
        if ratio is not None and ratio > 0.0:
            alerts.append(f"{key}: None 0% 가 정상인데 {ratio:.0%}")
    return alerts


def snapshot(
    records: Sequence[EvalRecord],
    *,
    health: Mapping[str, Any] | None,
    health_error: str | None,
    db: Mapping[str, float | int | None] | None = None,
) -> dict[str, Any]:
    collected_at = datetime.now(UTC).isoformat()
    rows = apply_exclusions(records)
    events = [e for r in rows for e in r.events]
    ratios = _feature_none_ratio(rows)
    alerts = _feature_alerts(ratios)
    if health is None:
        alerts.append(f"health: 수집 실패 ({health_error})")
    db_values = {key: (db or {}).get(key) for key in QUALITY_DB_KEYS}
    if db_values["orphan_request_ratio"]:
        alerts.append(
            "orphan_request_ratio > 0: 백엔드가 request_id 를 붙이지 않는 목록 이벤트가 있음"
        )
    if db_values["match_method_violation"]:
        alerts.append("match_method_violation > 0: fuzzy·embed 매칭은 규약 위반")
    orphan_position = sum(1 for e in events if e.position is None) / len(events) if events else 0.0
    if orphan_position > 0.0:
        alerts.append(f"orphan_position_ratio {orphan_position:.1%}: position 없는 이벤트")
    return {
        "collected_at": collected_at,
        "n_records": len(rows),
        **db_values,
        "orphan_position_ratio": orphan_position,
        "feature_none_ratio": ratios,
        "health_counters": {"collected_at": collected_at, "payload": dict(health)}
        if health is not None
        else None,
        "degraded_ratio": sum(1 for r in rows if r.degraded) / len(rows) if rows else None,
        "excluded_counts": count_excluded(rows),
        "exploration_positions": exploration_positions(rows),
        "cuisine_unmet_ratio": sum(1 for r in rows if r.cuisine_unmet) / len(rows)
        if rows
        else None,
        "alerts": alerts,
    }


def append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
