"""평가 파트가 읽는 SQL. 읽기만 하고 쓰지 않습니다 (명세 3.2 의 ①, 8절).

SQL 은 `repository*.py` 밖으로 나가지 않습니다(03 의 5절). 평가 축의 모듈은 이 파일이 돌려준
행만 받아 순수 함수로 바꾸므로 DB 없이 검사할 수 있고, 배치 진입점은 `scripts/reco_eval.py` 입니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from infra.db import cursor

_LOG_SQL = """
SELECT rl.request_id, rl.user_id, rl.session_id, rl.model_version, rl.config_hash, rl.warm_alpha,
       rl.stats_version, rl.policies, rl.stage_trace, rl.candidates, rl.served,
       rl.total_latency_ms, rl.created_at, u.is_simulated
FROM recommendation_log rl
JOIN app_user u ON u.id = rl.user_id
WHERE rl.created_at >= %s AND rl.created_at < %s
ORDER BY rl.created_at
"""
_EVENT_SQL = """
SELECT request_id, recipe_id, event_type, value, position, created_at
FROM event_log WHERE request_id = ANY(%s) AND recipe_id IS NOT NULL
"""
_COOK_SQL = """
SELECT user_id, recipe_id, created_at
FROM event_log WHERE event_type = 'cook' AND user_id = ANY(%s) AND created_at >= %s
"""
_INGREDIENT_SQL = "SELECT recipe_id, all_ids FROM recipe_feature WHERE recipe_id = ANY(%s)"
_FREQUENCY_SQL = "SELECT ingredient_id, count(*) FROM recipe_ingredient GROUP BY ingredient_id"
_CATALOG_SQL = "SELECT count(*) FROM recipe_feature"

#: 서버가 목록에 대해 기록하는 이벤트. 여기에 request_id 가 없으면 백엔드 연동 결함입니다.
#: cook·search·unsave 는 목록 밖에서도 일어나므로 request_id 가 없는 것이 정상이라 세지 않습니다.
_ORPHAN_REQUEST_SQL = """
SELECT count(*) FILTER (WHERE request_id IS NULL)::float / NULLIF(count(*), 0)
FROM event_log
WHERE event_type IN ('impression', 'click', 'save', 'dismiss', 'rating')
  AND created_at >= now() - interval '1 day'
"""
_FLAVOR_ALL_ZERO_SQL = """
SELECT count(*) FILTER (WHERE flavor_vec = ARRAY[0,0,0,0,0,0]::real[])::float / NULLIF(count(*), 0)
FROM recipe_feature
"""
_MATCH_METHOD_VIOLATION_SQL = (
    "SELECT count(*) FROM recipe_ingredient WHERE match_method IN ('fuzzy', 'embed')"
)

QUALITY_DB_KEYS = ("orphan_request_ratio", "flavor_all_zero_ratio", "match_method_violation")


@dataclass
class EvalRows:
    """`export.build_records` 의 입력. DB 행을 그대로 담고 모양은 바꾸지 않습니다."""

    logs: list[dict[str, Any]]
    events_by_request: dict[Any, list[dict[str, Any]]] = field(default_factory=dict)
    cooks_by_user: dict[int, list[tuple[int, datetime]]] = field(default_factory=dict)
    ingredients: dict[int, list[int]] = field(default_factory=dict)
    frequency: dict[int, int] = field(default_factory=dict)
    catalog_size: int = 0


def _candidate_ids(row: dict[str, Any]) -> Sequence[int]:
    return [int(c["recipe_id"]) for c in row["candidates"] or []]


def fetch_eval_rows(*, since: datetime, until: datetime) -> EvalRows:
    """기간의 추천 로그와 그에 딸린 이벤트·유저 조리·재료·IDF 재료·카탈로그 크기."""
    with cursor() as cur:
        cur.execute(_LOG_SQL, (since, until))
        columns = [d[0] for d in cur.description or []]
        logs = [dict(zip(columns, values, strict=True)) for values in cur.fetchall()]
        rows = EvalRows(logs=logs)
        if not logs:
            return rows
        cur.execute(_EVENT_SQL, ([r["request_id"] for r in logs],))
        for request_id, recipe_id, event_type, value, position, created_at in cur.fetchall():
            rows.events_by_request.setdefault(request_id, []).append(
                {
                    "recipe_id": recipe_id,
                    "event_type": event_type,
                    "value": value,
                    "position": position,
                    "created_at": created_at,
                }
            )
        cur.execute(_COOK_SQL, (sorted({r["user_id"] for r in logs}), since))
        for user_id, recipe_id, created_at in cur.fetchall():
            rows.cooks_by_user.setdefault(user_id, []).append((recipe_id, created_at))
        # 미노출 후보의 재료도 가져옵니다. 없으면 목표 정책의 MMR 이 그 후보를 최대 다양성으로 봄
        recipe_ids = sorted(
            {
                *(rid for r in logs for rid in r["served"]),
                *(rid for r in logs for rid in _candidate_ids(r)),
            }
        )
        cur.execute(_INGREDIENT_SQL, (recipe_ids,))
        rows.ingredients = {rid: list(ids) for rid, ids in cur.fetchall()}
        cur.execute(_FREQUENCY_SQL)
        rows.frequency = {int(i): int(c) for i, c in cur.fetchall()}
        cur.execute(_CATALOG_SQL)
        rows.catalog_size = int((cur.fetchone() or [0])[0])
    return rows


def quality_items() -> dict[str, float | int | None]:
    """품질 스냅샷의 DB 항목 3종 (명세 8절)."""
    with cursor() as cur:
        values = []
        for sql in (_ORPHAN_REQUEST_SQL, _FLAVOR_ALL_ZERO_SQL, _MATCH_METHOD_VIOLATION_SQL):
            cur.execute(sql)
            row = cur.fetchone()
            values.append(row[0] if row else None)
    return dict(zip(QUALITY_DB_KEYS, values, strict=True))
