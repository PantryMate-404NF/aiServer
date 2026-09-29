"""오프라인 평가 — 쌓인 두 JSONL(추천 · 이벤트)을 `request_id` 로 이어 반응과 신호 상태를 냅니다.

관리자 페이지(`diagnosis.py`)는 서버가 뜬 뒤의 누적을 봅니다. 이것은 `RECO_LOG_DIR` 의 파일을
읽으므로 재시작과 무관하고 날짜별로 잘라 볼 수 있습니다. 읽는 것은 실서빙이 남긴 그대로이고
계산은 전부 여기서 합니다 — 파일의 모양이 바뀌면 여기가 깨지고 그것이 맞습니다.

내는 것은 넷입니다.

- **칸별 반응** — 개인화 · 탐색 · 유형 칸의 노출 · 클릭 · 저장 · 조리와 그 비율. 탐색 칸은
  노출확률이 1 보다 작아 그대로 세면 과소평가됩니다. 자기 정규화 IPS(sum(y/p) / sum(1/p))를
  함께 냅니다.
- **순위별 반응** — 몇 번째 칸이 얼마나 눌리는가. 위치 편향의 원재료입니다.
- **신호 상태** — 노출 항목에서 값이 없는 비율. 가중치가 있는데 95% 넘게 없으면 꺼진 신호입니다.
- **엔진 상태** — 후보 부족 비율 · 사다리 단계 · 판본 탈락 · 지연 · 모르는 알레르기 라벨.

반응이 없는 기간의 비율은 0 이 아니라 None 입니다. "아직 모른다" 를 0% 로 적으면 그 숫자가
어디선가 임계값과 비교됩니다.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from features.recommend.enums import DEFAULT_WEIGHTS, FEATURE_KEYS

#: 노출 항목의 이 비율 이상에서 값이 없으면 꺼진 신호입니다(`diagnosis.DEAD_FEATURE_RATIO` 와 같음).
DEAD_FEATURE_RATIO = 0.95
SLOTS = ("personal", "exploration", "cuisine")
REACTIONS = ("click", "save", "cook")


@dataclass(frozen=True)
class SlotStats:
    impressions: int
    clicks: int
    saves: int
    cooks: int
    ctr: float | None
    save_rate: float | None
    cook_rate: float | None
    #: 자기 정규화 IPS 클릭률. 노출확률이 전부 1 이면 ctr 과 같습니다.
    ips_ctr: float | None
    mean_propensity: float | None


@dataclass(frozen=True)
class OfflineReport:
    first_at: datetime | None
    last_at: datetime | None
    recommendations: int
    impressions: int
    users: int
    events: int
    events_linked: int
    events_unlinked: int
    events_orphan: int
    by_slot: Mapping[str, SlotStats]
    #: 순위(1부터) → (노출, 클릭, 클릭률)
    by_rank: Mapping[int, tuple[int, int, float | None]]
    #: 신호 → 노출 항목에서 값이 없는 비율
    feature_none_ratio: Mapping[str, float]
    dead_features: tuple[str, ...]
    dead_weight: float
    degraded_ratio: float | None
    fallback_stages: Mapping[str, int]
    same_dish_per_request: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    unknown_allergy_requests: int
    model_versions: Mapping[str, int]
    by_day: Mapping[str, tuple[int, int, float | None]] = field(default_factory=dict)


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """한 줄에 하나. 깨진 줄은 건너뜁니다 — 쓰다 만 마지막 줄이 있을 수 있습니다."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def load_logs(
    log_dir: Path, since: date | None = None, until: date | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`recommendations-YYYYMMDD.jsonl` · `events-YYYYMMDD.jsonl` 을 날짜로 걸러 읽습니다."""

    def wanted(path: Path, prefix: str) -> bool:
        stamp = path.stem.removeprefix(prefix)
        try:
            day = datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            return False
        return (since is None or day >= since) and (until is None or day <= until)

    recommendations = [
        record
        for path in sorted(log_dir.glob("recommendations-*.jsonl"))
        if wanted(path, "recommendations-")
        for record in read_jsonl(path)
    ]
    events = [
        record
        for path in sorted(log_dir.glob("events-*.jsonl"))
        if wanted(path, "events-")
        for record in read_jsonl(path)
    ]
    return recommendations, events


def slot_of(item: Mapping[str, Any]) -> str:
    if item.get("is_exploration"):
        return "exploration"
    if item.get("is_cuisine_slot"):
        return "cuisine"
    return "personal"


def evaluate(
    recommendations: Sequence[Mapping[str, Any]], events: Iterable[Mapping[str, Any]]
) -> OfflineReport:
    """순수 함수. 두 목록을 잇고 셉니다."""
    by_request: dict[str, Mapping[str, Any]] = {}
    impressions_by_slot: Counter[str] = Counter()
    ips_weight: dict[str, float] = defaultdict(float)
    propensity_sum: dict[str, float] = defaultdict(float)
    by_rank_shown: Counter[int] = Counter()
    none_count: Counter[str] = Counter()
    shown = 0
    users: set[int] = set()
    degraded = 0
    fallbacks: Counter[str] = Counter()
    same_dish: list[int] = []
    latencies: list[float] = []
    unknown_allergy = 0
    versions: Counter[str] = Counter()
    per_day: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # 요청 · 노출 · 클릭
    stamps: list[datetime] = []

    for record in recommendations:
        log = record.get("log") or {}
        items = record.get("items") or []
        request_id = str(log.get("request_id"))
        by_request[request_id] = record
        users.add(int(log.get("user_id", 0)))
        versions[str(log.get("model_version"))] += 1
        created = _parse(log.get("created_at"))
        day = created.date().isoformat() if created else "unknown"
        if created:
            stamps.append(created)
        per_day[day][0] += 1
        if (latency := log.get("total_latency_ms")) is not None:
            latencies.append(float(latency))
        trace = log.get("stage_trace") or {}
        totals = trace.get("totals") or {}
        degraded += 1 if totals.get("degraded") else 0
        stages = trace.get("stages") or []
        if stages:
            fallbacks[str(stages[0].get("fallback") or "none")] += 1
            params = stages[0].get("params") or {}
            if params.get("allergy_labels_unknown"):
                unknown_allergy += 1
            dropped = stages[-1].get("dropped") or {}
            if "same_dish" in dropped:
                same_dish.append(int(dropped["same_dish"]))
        for item in items:
            slot = slot_of(item)
            shown += 1
            impressions_by_slot[slot] += 1
            per_day[day][1] += 1
            propensity = float(item.get("propensity") or 1.0)
            propensity_sum[slot] += propensity
            ips_weight[slot] += 1.0 / propensity if propensity > 0 else 0.0
            by_rank_shown[int(item.get("final_rank") or 0)] += 1
            features = item.get("features") or {}
            for key in FEATURE_KEYS:
                if features.get(key) is None:
                    none_count[key] += 1

    reactions: dict[str, Counter[str]] = {slot: Counter() for slot in SLOTS}
    ips_clicks: dict[str, float] = defaultdict(float)
    by_rank_clicked: Counter[int] = Counter()
    linked = unlinked = orphan = 0
    for event in events:
        linked_to = event.get("request_id")
        if linked_to is None:
            unlinked += 1
            continue
        source = by_request.get(str(linked_to))
        item = None
        if source is not None:
            item = next(
                (
                    i
                    for i in source.get("items") or []
                    if i.get("recipe_id") == event.get("recipe_id")
                ),
                None,
            )
        if item is None:
            orphan += 1
            continue
        linked += 1
        kind = str(event.get("event_type"))
        slot = slot_of(item)
        if kind in REACTIONS:
            reactions[slot][kind] += 1
        if kind == "click":
            propensity = float(item.get("propensity") or 1.0)
            ips_clicks[slot] += 1.0 / propensity if propensity > 0 else 0.0
            by_rank_clicked[int(item.get("final_rank") or 0)] += 1
            log = (source.get("log") or {}) if source else {}
            created = _parse(log.get("created_at"))
            per_day[created.date().isoformat() if created else "unknown"][2] += 1

    by_slot = {}
    for slot in SLOTS:
        n = impressions_by_slot[slot]
        got = reactions[slot]
        by_slot[slot] = SlotStats(
            impressions=n,
            clicks=got["click"],
            saves=got["save"],
            cooks=got["cook"],
            ctr=_ratio(got["click"], n),
            save_rate=_ratio(got["save"], n),
            cook_rate=_ratio(got["cook"], n),
            ips_ctr=_ratio(ips_clicks[slot], ips_weight[slot]),
            mean_propensity=_ratio(propensity_sum[slot], n),
        )
    by_rank = {
        rank: (
            by_rank_shown[rank],
            by_rank_clicked[rank],
            _ratio(by_rank_clicked[rank], by_rank_shown[rank]),
        )
        for rank in sorted(by_rank_shown)
    }
    none_ratio = {key: (none_count[key] / shown if shown else 1.0) for key in FEATURE_KEYS}
    dead = tuple(key for key in FEATURE_KEYS if shown and none_ratio[key] >= DEAD_FEATURE_RATIO)
    ordered = sorted(latencies)
    return OfflineReport(
        first_at=min(stamps) if stamps else None,
        last_at=max(stamps) if stamps else None,
        recommendations=len(recommendations),
        impressions=shown,
        users=len(users),
        events=linked + unlinked + orphan,
        events_linked=linked,
        events_unlinked=unlinked,
        events_orphan=orphan,
        by_slot=by_slot,
        by_rank=by_rank,
        feature_none_ratio=none_ratio,
        dead_features=dead,
        dead_weight=round(sum(DEFAULT_WEIGHTS.get(key, 0.0) for key in dead), 4),
        degraded_ratio=_ratio(degraded, len(recommendations)),
        fallback_stages=dict(fallbacks),
        same_dish_per_request=_ratio(sum(same_dish), len(same_dish)),
        latency_p50_ms=_quantile(ordered, 0.5),
        latency_p95_ms=_quantile(ordered, 0.95),
        unknown_allergy_requests=unknown_allergy,
        model_versions=dict(versions),
        by_day={
            day: (requests, impressions, _ratio(clicks, impressions))
            for day, (requests, impressions, clicks) in sorted(per_day.items())
        },
    )


def render(report: OfflineReport) -> str:
    """사람이 읽는 표. 숫자가 없는 칸은 `-` 입니다."""
    lines = [
        f"기간         {_stamp(report.first_at)} ~ {_stamp(report.last_at)}",
        f"추천 {report.recommendations} · 노출 {report.impressions} · 사용자 {report.users} · "
        f"모델 {report.model_versions}",
        f"이벤트 {report.events} — 이어짐 {report.events_linked} · request_id 없음 "
        f"{report.events_unlinked} · 고아 {report.events_orphan}",
        "",
        "칸별 반응      노출     클릭   저장   조리    클릭률    IPS 클릭률   평균 노출확률",
    ]
    for slot, s in report.by_slot.items():
        lines.append(
            f"  {slot:<12} {s.impressions:>6} {s.clicks:>6} {s.saves:>6} {s.cooks:>6}"
            f"   {_pct(s.ctr):>7}   {_pct(s.ips_ctr):>10}   {_num(s.mean_propensity):>8}"
        )
    lines += [
        "",
        "순위별 클릭률   "
        + " ".join(f"{rank:>2}:{_pct(ctr, 1):>5}" for rank, (_, _, ctr) in report.by_rank.items()),
    ]
    lines += ["", f"꺼진 신호 (가중치 합 {report.dead_weight:.2f})"]
    for key in report.dead_features:
        weight = DEFAULT_WEIGHTS.get(key, 0.0)
        missing = _pct(report.feature_none_ratio[key])
        lines.append(f"  {key:<14} 가중치 {weight:.2f}  값 없음 {missing}")
    if not report.dead_features:
        lines.append("  없음")
    lines += [
        "",
        f"엔진        후보 부족 {_pct(report.degraded_ratio)} · 사다리 {report.fallback_stages}",
        f"            판본 탈락 {_num(report.same_dish_per_request)}/요청 · "
        f"지연 p50 {_num(report.latency_p50_ms)}ms p95 {_num(report.latency_p95_ms)}ms · "
        f"모르는 알레르기 라벨 요청 {report.unknown_allergy_requests}",
        "",
        "날짜별         요청    노출   클릭률",
    ]
    for day, (requests, impressions, ctr) in report.by_day.items():
        lines.append(f"  {day}  {requests:>6}  {impressions:>6}   {_pct(ctr)}")
    return "\n".join(lines)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _quantile(ordered: Sequence[float], q: float) -> float | None:
    if not ordered:
        return None
    return (
        float(statistics.quantiles(ordered, n=100)[int(q * 100) - 1])
        if len(ordered) > 1
        else ordered[0]
    )


def _stamp(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "-"


def _pct(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}" if value != int(value) else f"{int(value)}"
