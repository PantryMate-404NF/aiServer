"""평가 기록. 추천 요청 1건 = JSONL 1줄 (명세 2.1).

불변식 위반은 줄 번호와 필드를 담아 즉시 실패하고, 제외 규칙은 `excluded_reason` 을
채우고 건수로 남깁니다 (명세 2.2). 제외된 줄을 파일에서 지우지 않는 이유는 제외 비율
자체가 품질 지표이기 때문입니다.

이 모듈은 DB·HTTP 를 모릅니다. 원본 `user_id` 와 냉장고 필드는 여기 오기 전에
export 가 걷어냅니다 (명세 14절).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from features.recommend.enums import EventType, UserMode
from features.recommend.stage import RankedItem, ScoredCandidate

#: 라벨 규칙(명세 2.3)이 바뀌면 올립니다. 다른 버전의 리포트는 비교하지 않습니다.
LABEL_VERSION = 1
#: 지표 정의(명세 5절)가 바뀌면 올립니다.
METRIC_VERSION = 1

EXCLUDED_DEV_SESSION = "d-session"
EXCLUDED_SIMULATED = "simulated_user"
EXCLUDED_NOT_REPRODUCIBLE = "not_reproducible"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvalEvent(_Base):
    recipe_id: int
    event_type: EventType
    value: float | None = None
    position: int | None = None
    created_at: datetime


class EvalHeader(_Base):
    """파일 첫 줄. IDF 는 파일마다 한 번만 싣습니다."""

    label_version: int
    metric_version: int
    catalog_size: int
    ingredient_idf: dict[int, float]
    exported_at: datetime
    source: Literal["db", "synth"]


class EvalRecord(_Base):
    request_id: UUID
    model_version: str
    config_hash: str | None
    warm_alpha: float | None
    stats_version: int | None
    created_at: datetime
    user_hash: str
    session_prefix: Literal["c", "g", "d"] | None
    user_mode: UserMode
    degraded: bool
    total_latency_ms: int
    items: list[RankedItem]
    candidates: list[ScoredCandidate] | None = None
    policies: list[dict[str, Any]] | None = None
    ingredients: dict[int, list[int]]
    events: list[EvalEvent]
    user_events: list[EvalEvent]
    is_simulated: bool
    excluded_reason: str | None = None


class InvalidRecordError(ValueError):
    """불변식 위반. 어느 줄의 어느 필드인지 담습니다."""

    def __init__(self, field: str, message: str, line: int | None = None) -> None:
        self.field = field
        self.message = message
        self.line = line
        where = f"{line}번째 줄 " if line is not None else ""
        super().__init__(f"{where}{field}: {message}")


def validate(record: EvalRecord) -> EvalRecord:
    """불변식 6종 가운데 기록 안에서 확인할 수 있는 5종. 헤더 버전은 `read_jsonl` 이 봅니다."""
    ranks = [item.final_rank for item in record.items]
    if ranks != list(range(1, len(ranks) + 1)):
        raise InvalidRecordError("final_rank", f"1 부터 연속이어야 합니다: {ranks}")
    if record.candidates is not None:
        pool = {c.recipe_id for c in record.candidates}
        outside = [item.recipe_id for item in record.items if item.recipe_id not in pool]
        if outside:
            raise InvalidRecordError("candidates", f"노출 항목이 후보에 없습니다: {outside}")
    for item in record.items:
        if item.is_cuisine_slot and (item.is_exploration or item.propensity != 1.0):
            raise InvalidRecordError(
                "is_cuisine_slot", f"유형 칸은 결정적 슬롯입니다: recipe {item.recipe_id}"
            )
        if item.is_exploration:
            if item.propensity is None or not 0.0 < item.propensity < 1.0:
                raise InvalidRecordError(
                    "propensity", f"탐색 슬롯은 0 < p < 1 이어야 합니다: {item.propensity}"
                )
        elif item.propensity != 1.0:
            raise InvalidRecordError(
                "propensity", f"결정적 슬롯은 p = 1.0 이어야 합니다: {item.propensity}"
            )
    rank_of = {item.recipe_id: item.final_rank for item in record.items}
    for event in record.events:
        expected = rank_of.get(event.recipe_id)
        if event.position is not None and event.position != expected:
            raise InvalidRecordError(
                "position",
                f"recipe {event.recipe_id} 의 position {event.position} 이 "
                f"final_rank {expected} 와 다릅니다",
            )
    return record


def exclusion_reason(record: EvalRecord) -> str | None:
    if record.session_prefix == "d":
        return EXCLUDED_DEV_SESSION
    if record.is_simulated:
        return EXCLUDED_SIMULATED
    if record.config_hash is None or record.warm_alpha is None or record.stats_version is None:
        return EXCLUDED_NOT_REPRODUCIBLE
    return None


def count_excluded(records: Iterable[EvalRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record.excluded_reason is not None:
            counts[record.excluded_reason] = counts.get(record.excluded_reason, 0) + 1
    return counts


def write_jsonl(path: Path, header: EvalHeader, records: Sequence[EvalRecord]) -> None:
    with path.open("w", encoding="utf-8") as out:
        out.write(header.model_dump_json() + "\n")
        for record in records:
            out.write(record.model_dump_json() + "\n")


def read_jsonl(path: Path) -> tuple[EvalHeader, list[EvalRecord]]:
    """줄마다 검증하고 제외 사유를 채웁니다. 위반은 줄 번호와 함께 즉시 실패합니다."""
    with path.open(encoding="utf-8") as src:
        header = EvalHeader.model_validate_json(src.readline())
        if header.label_version != LABEL_VERSION:
            raise InvalidRecordError(
                "label_version", f"{header.label_version} 은 이 코드({LABEL_VERSION})와 다릅니다", 1
            )
        if header.metric_version != METRIC_VERSION:
            raise InvalidRecordError(
                "metric_version",
                f"{header.metric_version} 은 이 코드({METRIC_VERSION})와 다릅니다",
                1,
            )
        records: list[EvalRecord] = []
        for line_no, line in enumerate(src, start=2):
            if not line.strip():
                continue
            record = EvalRecord.model_validate_json(line)
            try:
                validate(record)
            except InvalidRecordError as error:
                raise InvalidRecordError(error.field, error.message, line_no) from error
            record.excluded_reason = exclusion_reason(record)
            records.append(record)
    return header, records
