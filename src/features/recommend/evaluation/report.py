"""리포트 조립 (명세 9.2). 파일만 읽고 JSON 한 벌을 냅니다.

`model_version` 이 필수 그룹 키이고 그 아래를 `user_mode` 3종과 전체로 나눕니다. 세그먼트와
지표를 전부 보고하되 판정은 전체 세그먼트의 nDCG@10 "서빙 − coverage baseline" 하나로만 합니다.
라벨은 기록마다 한 번만 계산해 모든 단계에 넘깁니다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from features.recommend.enums import UserMode
from features.recommend.evaluation import estimator, stats
from features.recommend.evaluation.labels import cooked_recipes, gains
from features.recommend.evaluation.metrics import (
    catalog_coverage,
    gain_sequence,
    ild,
    latency_percentiles,
    ndcg_at_k,
    position_ctr,
    recall_at_k,
    recall_user_level,
)
from features.recommend.evaluation.record import (
    LABEL_VERSION,
    METRIC_VERSION,
    EvalHeader,
    EvalRecord,
    apply_exclusions,
    count_excluded,
)

SEGMENTS = ("all", *(mode.value for mode in UserMode))
BASELINES = stats.BASELINES
VERDICT_METRIC = "ndcg@10 vs coverage"
VERDICT_K = 10
USER_RECALL_K = 10
NOT_MEASURABLE = "not_measurable"
LIMITS = [
    "정답은 노출분에서만 옵니다. 노출되지 않은 후보의 품질은 오프라인으로 알 수 없습니다",
    "impression 은 served 기준입니다. 하위 순위는 실제 열람이 아닐 수 있어 위치 보정은 근사입니다",
    "유저 수가 두 자릿수인 동안 오프라인 판정은 대부분 보류입니다",
]
#: Track B 해설서 9.2 의 목표치. 판정에 쓰지 않는 참고값입니다
REFERENCE_TARGETS = {"ndcg@10": 0.85, "ild": 0.95, "coverage": 0.15, "latency_p95_ms": 58}

Labeled = list[tuple[EvalRecord, Mapping[int, float]]]


@dataclass(frozen=True)
class RunOptions:
    ks: tuple[int, ...] = (5, 10)
    catalog_size: int | None = None
    model_version: str | None = None
    include_simulated: bool = False
    position_correct: bool = False
    target_weights: Mapping[str, float] | None = None
    seed: int = 0
    resamples: int = stats.RESAMPLES


def _by_user(
    records: Sequence[EvalRecord], values: Sequence[float | None]
) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for record, value in zip(records, values, strict=True):
        if value is not None:
            result.setdefault(record.user_hash, []).append(value)
    return result


def _ci(values: Mapping[str, list[float]], options: RunOptions) -> dict[str, Any] | None:
    estimate = stats.bootstrap(values, resamples=options.resamples, seed=options.seed)
    return asdict(estimate) if estimate else None


def _corrected_sequences(sequences: Sequence[list[float]]) -> list[list[float]] | None:
    """examination 보정. 위치별 CTR 을 1위로 정규화한 θ 로 gain 을 나눕니다.

    1위 CTR 이 0 이면 θ 를 정할 수 없어 None 입니다.
    """
    ctr = position_ctr(sequences)
    if not ctr or ctr[0] == 0.0:
        return None
    theta = [c / ctr[0] for c in ctr]
    return [[g / theta[i] if theta[i] > 0 else 0.0 for i, g in enumerate(s)] for s in sequences]


def _segment(
    labeled: Labeled, header: EvalHeader, options: RunOptions
) -> tuple[dict[str, Any], stats.Estimate | None]:
    """세그먼트 지표 한 벌과, 판정·검출력이 쓰는 서빙 − coverage 대응 차이."""
    records = [r for r, _ in labeled]
    labels = [g for _, g in labeled]
    full = [gain_sequence(r, g) for r, g in labeled]
    variants: dict[str, list[list[float]] | None] = {
        "full": full,
        "no_exploration": [gain_sequence(r, g, include_exploration=False) for r, g in labeled],
    }
    if options.position_correct:
        variants["position_corrected"] = _corrected_sequences(full)
    out: dict[str, Any] = {}
    for k in options.ks:
        out[f"ndcg@{k}"] = {
            name: _ci(_by_user(records, [ndcg_at_k(s, k) for s in seqs]), options)
            if seqs is not None
            else NOT_MEASURABLE
            for name, seqs in variants.items()
        }
        measurable = [(r, s) for r, s in zip(records, full, strict=True) if k < len(r.items)]
        out[f"recall@{k}"] = (
            _ci(
                _by_user([r for r, _ in measurable], [recall_at_k(s, k) for _, s in measurable]),
                options,
            )
            if measurable
            else NOT_MEASURABLE
        )
    out[f"recall_user@{USER_RECALL_K}"] = _ci(
        _by_user(
            records,
            [
                recall_user_level([i.recipe_id for i in r.items], cooked_recipes(r), USER_RECALL_K)
                for r in records
            ],
        ),
        options,
    )
    out["ild"] = (
        statistics.fmean(
            ild(
                [frozenset(r.ingredients.get(i.recipe_id, [])) for i in r.items],
                header.ingredient_idf,
            )
            for r in records
        )
        if records
        else None
    )
    out["coverage"] = catalog_coverage(
        [i.recipe_id for r in records for i in r.items], options.catalog_size or header.catalog_size
    )
    out["latency_ms"] = latency_percentiles([r.total_latency_ms for r in records])
    out["degraded_ratio"] = (
        sum(1 for r in records if r.degraded) / len(records) if records else None
    )
    out["position_ctr"] = position_ctr(full)
    served = stats.ndcg_by_user(records, VERDICT_K, labels=labels)
    baselines = {
        kind: stats.ndcg_by_user(
            records, VERDICT_K, labels=labels, baseline=kind, seed=options.seed
        )
        for kind in BASELINES
    }
    out["baseline"] = {kind: _ci(values, options) for kind, values in baselines.items()}
    diff = stats.paired_bootstrap(
        served, baselines["coverage"], resamples=options.resamples, seed=options.seed
    )
    out["power"] = stats.power(diff)
    return out, diff


def _group(labeled: Labeled, header: EvalHeader, options: RunOptions) -> dict[str, Any]:
    hashes = Counter(r.config_hash for r, _ in labeled)
    dominant, _ = hashes.most_common(1)[0]
    kept = [(r, g) for r, g in labeled if r.config_hash == dominant]
    segments: dict[str, Any] = {}
    diffs: dict[str, stats.Estimate | None] = {}
    for name in SEGMENTS:
        segments[name], diffs[name] = _segment(
            [(r, g) for r, g in kept if name == "all" or r.user_mode.value == name], header, options
        )
    verdict = stats.verdict(diffs["all"])
    interleaving = stats.interleaving([r for r, _ in kept], labels=[g for _, g in kept])
    others = sorted(str(h) for h in hashes if h != dominant)
    return {
        "config_hash": {"dominant": dominant, "others": others},
        "warnings": [f"config_hash 가 {len(hashes)}개. {dominant} 만 집계, 나머지 {others} 는 제외"]
        if others
        else [],
        "segments": segments,
        "interleaving": asdict(interleaving) if interleaving else None,
        "verdict": {
            "metric": VERDICT_METRIC,
            "diff": asdict(diffs["all"]) if diffs["all"] else None,
            "status": verdict.status,
            "reason": verdict.reason,
        },
    }


def build_report(
    header: EvalHeader, records: Sequence[EvalRecord], options: RunOptions, *, input_sha256: str
) -> dict[str, Any]:
    rows = apply_exclusions(records, include_simulated=options.include_simulated)
    evaluated = [r for r in rows if r.excluded_reason is None]
    if options.model_version:
        evaluated = [r for r in evaluated if r.model_version == options.model_version]
    labeled: Labeled = [(r, gains(r)) for r in evaluated]
    groups = {
        version: _group([(r, g) for r, g in labeled if r.model_version == version], header, options)
        for version in sorted({r.model_version for r in evaluated})
    }
    off_policy = None
    if options.target_weights is not None:
        target = estimator.weight_swap_policy(options.target_weights, idf=header.ingredient_idf)
        off_policy = asdict(
            estimator.estimate(
                evaluated, target, name="weight_swap", labels=[g for _, g in labeled]
            )
        )
    return {
        "stamp": {
            "generated_at": datetime.now(UTC).isoformat(),
            "label_version": LABEL_VERSION,
            "metric_version": METRIC_VERSION,
            "seed": options.seed,
            "input_sha256": input_sha256,
            "catalog_size": options.catalog_size or header.catalog_size,
            # 분모의 출처. G-06 이 정해지기 전에는 DB 건수와 인자값을 구분해 둡니다
            "catalog_size_source": "arg" if options.catalog_size else "header",
            "include_simulated": options.include_simulated,
            "source": header.source,
        },
        "records": {
            "total": len(rows),
            "excluded": count_excluded(rows),
            "evaluated": len(evaluated),
            "no_positive": sum(1 for _, g in labeled if not any(g.values())),
        },
        "limits": LIMITS,
        "groups": groups,
        "off_policy": off_policy,
        "reference_targets": REFERENCE_TARGETS,
    }


def summary(built: Mapping[str, Any]) -> str:
    """표준출력용 요약. 그룹마다 판정과 nDCG@10, 경고를 냅니다."""
    lines = [f"records: {built['records']}"]
    for version, group in built["groups"].items():
        full = group["segments"]["all"].get("ndcg@10", {})
        full = full.get("full") if isinstance(full, dict) else None
        mean = full.get("mean") if isinstance(full, dict) else None
        lines.append(
            f"{version}: verdict={group['verdict']['status']} ({group['verdict']['reason']}) "
            f"ndcg@10={mean}"
        )
        lines.extend(f"  warning: {w}" for w in group["warnings"])
    if built["off_policy"]:
        block = built["off_policy"]
        lines.append(f"off_policy: usable={block['usable']} snips={block['snips']}")
    return "\n".join(lines)
