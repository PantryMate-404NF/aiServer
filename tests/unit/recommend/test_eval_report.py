"""리포트 조립. 명세 9.2 의 모양과 `run` 서브커맨드의 종단 실행."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from features.recommend.evaluation import report, synth
from features.recommend.evaluation.record import EvalHeader, EvalRecord, write_jsonl

ROOT = Path(__file__).resolve().parents[3]
SPEC = synth.SynthSpec(users=30, requests=240, hit_rate=0.3, position_decay=0.3, seed=11)
FAST = report.RunOptions(resamples=30)


@pytest.fixture(scope="module")
def generated() -> tuple[EvalHeader, list[EvalRecord]]:
    return synth.generate(SPEC)


@pytest.fixture(scope="module")
def built(generated: tuple[EvalHeader, list[EvalRecord]]) -> dict[str, Any]:
    header, records = generated
    return report.build_report(header, records, FAST, input_sha256="abc")


def test_top_level_shape(built: dict[str, Any]) -> None:
    assert set(built) == {"stamp", "records", "limits", "groups", "off_policy", "reference_targets"}
    stamp = built["stamp"]
    assert stamp["input_sha256"] == "abc"
    assert stamp["source"] == "synth"
    assert stamp["include_simulated"] is False
    assert stamp["catalog_size_source"] == "header"
    assert built["off_policy"] is None
    assert built["reference_targets"]["ndcg@10"] == 0.85
    assert len(built["limits"]) == 3


def test_record_counts_add_up(built: dict[str, Any]) -> None:
    counts = built["records"]
    assert counts["total"] == SPEC.requests
    assert counts["total"] == counts["evaluated"] + sum(counts["excluded"].values())
    assert counts["no_positive"] >= 0


def test_group_and_segment_shape(built: dict[str, Any]) -> None:
    group = built["groups"][synth.MODEL_VERSION]
    assert group["config_hash"] == {"dominant": synth.CONFIG_HASH, "others": []}
    assert group["warnings"] == []
    assert set(group["segments"]) == set(report.SEGMENTS)
    segment = group["segments"]["all"]
    assert set(segment["ndcg@10"]) == {"full", "no_exploration"}
    assert {"mean", "ci95"} <= set(segment["ndcg@10"]["full"])
    assert segment["recall@10"] == "not_measurable"  # K = top_k 는 정의상 1.0
    assert "mean" in segment["recall@5"]
    assert set(segment["baseline"]) == set(report.BASELINES)
    assert set(segment["latency_ms"]) == {"p50", "p95"}
    assert len(segment["position_ctr"]) == SPEC.top_k
    assert segment["power"]["users_needed_for"]["0.02"] > 0
    assert group["verdict"]["status"] in {"pass", "hold", "fail"}
    assert group["verdict"]["metric"] == "ndcg@10 vs coverage"
    assert group["interleaving"] is not None and group["interleaving"]["pairs"] > 0


def test_position_correction_adds_a_variant(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    header, records = generated
    built = report.build_report(
        header, records, report.RunOptions(resamples=20, position_correct=True), input_sha256="x"
    )
    segment = built["groups"][synth.MODEL_VERSION]["segments"]["all"]
    assert "position_corrected" in segment["ndcg@10"]


def test_include_simulated_moves_rows_into_evaluated(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    header, records = generated
    strict = report.build_report(header, records, FAST, input_sha256="x")["records"]
    loose = report.build_report(
        header, records, report.RunOptions(resamples=20, include_simulated=True), input_sha256="x"
    )["records"]
    assert "simulated_user" in strict["excluded"]
    assert "simulated_user" not in loose["excluded"]
    assert loose["evaluated"] > strict["evaluated"]


def test_target_weights_produce_an_off_policy_block(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    header, records = generated
    options = report.RunOptions(resamples=20, target_weights={"f_popularity": 1.0})
    built = report.build_report(header, records, options, input_sha256="x")
    block = built["off_policy"]
    assert isinstance(block, dict)
    assert {"target", "snips", "ess", "unsupported_ratio", "exposures_needed", "usable"} <= set(
        block
    )


def test_unknown_model_version_filter_yields_no_groups(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    header, records = generated
    built = report.build_report(
        header, records, report.RunOptions(resamples=20, model_version="nope"), input_sha256="x"
    )
    assert built["groups"] == {}


def test_group_without_positives_holds_instead_of_crashing() -> None:
    """초기 실데이터처럼 반응이 하나도 없는 기간은 예외가 아니라 보류입니다."""
    header, records = synth.generate(synth.SynthSpec(users=5, requests=20, hit_rate=0.0, seed=1))
    built = report.build_report(header, records, report.RunOptions(resamples=10), input_sha256="x")
    group = built["groups"][synth.MODEL_VERSION]
    assert group["verdict"]["status"] == "hold"
    assert group["verdict"]["diff"] is None
    assert group["segments"]["all"]["ndcg@10"]["full"] is None


def test_multiple_config_hashes_warn_and_keep_the_dominant(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    header, records = generated
    mixed = [
        r.model_copy(update={"config_hash": "other"}) if i % 7 == 0 else r
        for i, r in enumerate(records)
    ]
    built = report.build_report(header, mixed, report.RunOptions(resamples=10), input_sha256="x")
    group = built["groups"][synth.MODEL_VERSION]
    assert group["config_hash"]["others"] == ["other"]
    assert group["warnings"] and "warning" in report.summary(built)


def test_cli_run_writes_a_report_with_matching_sha(tmp_path: Path) -> None:
    records_path = tmp_path / "r.jsonl"
    out = tmp_path / "report.json"
    header, records = synth.generate(synth.SynthSpec(users=10, requests=40, hit_rate=0.3, seed=1))
    write_jsonl(records_path, header, records)

    done = subprocess.run(  # noqa: S603  # 인자는 전부 이 파일이 정한 상수·경로
        [
            sys.executable,
            str(ROOT / "scripts" / "reco_eval.py"),
            "run",
            "--records",
            str(records_path),
            "--resamples",
            "20",
            "--out",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
    built = json.loads(out.read_text(encoding="utf-8"))
    assert built["stamp"]["input_sha256"] == hashlib.sha256(records_path.read_bytes()).hexdigest()
    assert "verdict" in done.stdout
