"""합성 기록. 뒤의 모든 검사가 이 픽스처를 쓰므로 심어야 할 것이 전부 있는지 봅니다 (계획 4.3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from features.recommend.enums import UserMode
from features.recommend.evaluation import synth
from features.recommend.evaluation.labels import cooked_recipes, gains
from features.recommend.evaluation.metrics import gain_sequence, position_ctr
from features.recommend.evaluation.record import (
    EvalHeader,
    EvalRecord,
    count_excluded,
    read_jsonl,
    write_jsonl,
)

ROOT = Path(__file__).resolve().parents[3]
SPEC = synth.SynthSpec(users=30, requests=300, hit_rate=0.3, position_decay=0.0, seed=1)


@pytest.fixture(scope="module")
def generated() -> tuple[EvalHeader, list[EvalRecord]]:
    """결정적이므로 모듈에서 한 번만 만듭니다."""
    return synth.generate(SPEC)


def test_same_seed_gives_same_records(generated: tuple[EvalHeader, list[EvalRecord]]) -> None:
    assert synth.generate(SPEC)[1] == generated[1]


def test_output_survives_the_validator_round_trip(
    tmp_path: Path, generated: tuple[EvalHeader, list[EvalRecord]]
) -> None:
    header, records = generated
    path = tmp_path / "synth.jsonl"
    write_jsonl(path, header, records)

    loaded_header, loaded = read_jsonl(path)

    assert loaded_header.source == "synth"
    assert len(loaded) == SPEC.requests
    excluded = count_excluded(loaded)
    assert excluded["d-session"] > 0
    assert excluded["simulated_user"] > 0
    assert excluded["not_reproducible"] > 0


def test_plants_every_fixture_the_later_stages_need(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    _, records = generated
    items = [item for r in records for item in r.items]

    exploration = [i for i in items if i.is_exploration]
    assert exploration and all(0.0 < (i.propensity or 0.0) < 1.0 for i in exploration)
    assert {i.explore_source for i in exploration} == {"uniform", "thompson"}
    assert any(i.is_cuisine_slot for i in items)
    assert {r.user_mode for r in records} == set(UserMode)
    interleaved = [r for r in records if r.policies]
    assert interleaved and all({i.team for i in r.items} == {"A", "B"} for r in interleaved)
    assert all(r.candidates and len(r.candidates) > len(r.items) for r in records)
    assert any(cooked_recipes(r) for r in records)
    assert all(r.ingredients.keys() == {i.recipe_id for i in r.items} for r in records)


def test_exploration_positions_are_randomised(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    _, records = generated
    ranks = {i.final_rank for r in records for i in r.items if i.is_exploration}
    assert len(ranks) > 3


def test_hit_rate_is_recovered_without_position_decay(
    generated: tuple[EvalHeader, list[EvalRecord]],
) -> None:
    _, records = generated
    sequences = [gain_sequence(r, gains(r)) for r in records]
    positives = sum(1 for s in sequences for g in s if g > 0)
    shown = sum(len(s) for s in sequences)
    assert positives / shown == pytest.approx(SPEC.hit_rate, abs=0.03)


def test_position_decay_plants_monotone_ctr() -> None:
    spec = synth.SynthSpec(users=50, requests=1000, hit_rate=0.3, position_decay=0.7, seed=2)
    planted = synth.position_probabilities(spec)
    assert planted == sorted(planted, reverse=True)
    assert planted[0] == pytest.approx(spec.hit_rate)

    _, records = synth.generate(spec)
    observed = position_ctr([gain_sequence(r, gains(r)) for r in records])
    for p, o in zip(planted, observed, strict=True):
        assert o == pytest.approx(p, abs=0.04)


def test_cli_writes_a_readable_file(tmp_path: Path) -> None:
    out = tmp_path / "cli.jsonl"
    done = subprocess.run(  # noqa: S603  # 인자는 전부 이 파일이 정한 상수·경로
        [
            sys.executable,
            str(ROOT / "scripts" / "reco_eval.py"),
            "synth",
            "--users",
            "5",
            "--requests",
            "20",
            "--hit-rate",
            "0.3",
            "--seed",
            "3",
            "--out",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
    assert len(read_jsonl(out)[1]) == 20
