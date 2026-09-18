"""추천 평가 파이프라인 명령 (명세 9.1).

실행:
    uv run python scripts/reco_eval.py synth --users 50 --requests 500 --hit-rate 0.3 \
        --out data/eval/synth.jsonl

서브커맨드는 명세 순서대로 늘립니다. 지금은 `synth` 만 있습니다.
계산은 전부 `features.recommend.evaluation` 에 있고 이 파일은 인자를 받아 넘기기만 합니다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from features.recommend.evaluation import synth
from features.recommend.evaluation.record import write_jsonl


def _synth(args: argparse.Namespace) -> None:
    spec = synth.SynthSpec(
        users=args.users,
        requests=args.requests,
        hit_rate=args.hit_rate,
        position_decay=args.position_decay,
        seed=args.seed,
    )
    header, records = synth.generate(spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out, header, records)
    print(f"synth: {len(records)}건 → {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("synth", help="합성 기록 생성")
    s.add_argument("--users", type=int, required=True)
    s.add_argument("--requests", type=int, required=True)
    s.add_argument("--hit-rate", type=float, required=True)
    s.add_argument("--position-decay", type=float, default=0.0)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", type=Path, required=True)
    s.set_defaults(run=_synth)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
