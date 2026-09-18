"""내보내기 (명세 3.2 의 ①, 14절). DB 행을 요청 1건 = JSONL 1줄로 바꿉니다.

이 모듈은 순수 함수만 둡니다. SQL 은 `repository_eval.py` 에 있고 배치 진입점은
`scripts/reco_eval.py export` 입니다. 원본 `user_id` 는 HMAC 가명으로, `session_id` 는 접두어만,
냉장고·알레르기 필드는 화이트리스트에 없어 여기 오지 못합니다.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from features.recommend.enums import EventType, UserMode
from features.recommend.evaluation.record import (
    LABEL_VERSION,
    METRIC_VERSION,
    EvalEvent,
    EvalHeader,
    EvalRecord,
    InvalidRecordError,
    validate,
)
from features.recommend.repository_eval import EvalRows
from features.recommend.stage import RankedItem, ScoredCandidate

#: 후기 작성자 해시와 같은 방식(HMAC-SHA256 앞 16 hex). salt 는 평가 전용입니다
HASH_HEX_LENGTH = 16


@dataclass(frozen=True)
class ExportOptions:
    salt: str
    with_candidates: bool = False


def pseudonymize(user_id: int, salt: str) -> str:
    return hmac.new(salt.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()[
        :HASH_HEX_LENGTH
    ]


def idf_from_frequency(frequency: Mapping[int, int], n_recipes: int) -> dict[int, float]:
    """mock 스크립트의 `build_corpus` 와 같은 식입니다: log(N / df)."""
    return {i: math.log(n_recipes / c) for i, c in frequency.items() if c > 0}


def build_header(
    *, catalog_size: int, idf: Mapping[int, float], exported_at: datetime
) -> EvalHeader:
    return EvalHeader(
        label_version=LABEL_VERSION,
        metric_version=METRIC_VERSION,
        catalog_size=catalog_size,
        ingredient_idf=dict(idf),
        exported_at=exported_at,
        source="db",
    )


def _cuisine_unmet(trace: Mapping[str, Any]) -> bool:
    return any(
        "cuisine_unmet" in (stage.get("params") or {}) for stage in trace.get("stages") or []
    )


def build_record(
    row: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    user_cooks: Sequence[tuple[int, datetime]],
    ingredients: Mapping[int, Sequence[int]],
    options: ExportOptions,
) -> EvalRecord:
    """DB 행 하나를 평가 기록으로.

    노출 항목은 `candidates` 의 `final_rank` 가 있는 원소입니다. `served` 와 다르면 로그가
    깨진 것이라 즉시 실패합니다. `candidates` 가 비어 노출 항목이 없는 행은 제외 규칙
    (`no_items`)이 맡습니다.
    """
    pool = [
        RankedItem.model_validate(c) if "final_rank" in c else ScoredCandidate.model_validate(c)
        for c in row["candidates"] or []
    ]
    items = sorted((c for c in pool if isinstance(c, RankedItem)), key=lambda i: i.final_rank)
    served = list(row["served"])
    if items and [i.recipe_id for i in items] != served:
        raise InvalidRecordError(
            "served",
            f"candidates 의 노출분 {[i.recipe_id for i in items]} 이 served {served} 와 다릅니다",
        )
    trace = row["stage_trace"] or {}
    totals = trace.get("totals") or {}
    session_id = row.get("session_id")
    with_ingredients = pool if options.with_candidates else items
    return validate(
        EvalRecord(
            request_id=row["request_id"],
            model_version=row["model_version"],
            config_hash=row.get("config_hash"),
            warm_alpha=row.get("warm_alpha"),
            stats_version=row.get("stats_version"),
            created_at=row["created_at"],
            user_hash=pseudonymize(row["user_id"], options.salt),
            session_prefix=session_id[0] if session_id else None,
            # 없거나 null 이면 온보딩입니다. 리포트의 세그먼트가 그것을 보입니다 (작업 기록 A-04)
            user_mode=UserMode(totals.get("user_mode") or UserMode.COLD.value),
            degraded=bool(totals.get("degraded") or False),
            total_latency_ms=row["total_latency_ms"],
            items=items,
            candidates=pool if options.with_candidates else None,
            policies=row.get("policies"),
            ingredients={
                c.recipe_id: list(ingredients.get(c.recipe_id, [])) for c in with_ingredients
            },
            events=[
                EvalEvent(
                    recipe_id=e["recipe_id"],
                    event_type=e["event_type"],
                    value=e.get("value"),
                    position=e.get("position"),
                    created_at=e["created_at"],
                )
                for e in events
            ],
            user_events=[
                EvalEvent(recipe_id=rid, event_type=EventType.COOK, created_at=at)
                for rid, at in user_cooks
            ],
            is_simulated=bool(row["is_simulated"]),
            cuisine_unmet=_cuisine_unmet(trace),
        )
    )


def build_records(
    rows: EvalRows, options: ExportOptions, *, exported_at: datetime
) -> tuple[EvalHeader, list[EvalRecord]]:
    records = [
        build_record(
            row,
            events=rows.events_by_request.get(row["request_id"], []),
            user_cooks=rows.cooks_by_user.get(row["user_id"], []),
            ingredients=rows.ingredients,
            options=options,
        )
        for row in rows.logs
    ]
    header = build_header(
        catalog_size=rows.catalog_size,
        idf=idf_from_frequency(rows.frequency, rows.catalog_size),
        exported_at=exported_at,
    )
    return header, records
