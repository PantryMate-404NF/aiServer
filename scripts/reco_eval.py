"""추천 평가 파이프라인 명령 (명세 9.1).

실행:
    uv run python scripts/reco_eval.py synth --users 50 --requests 500 --hit-rate 0.3 \
        --out data/eval/synth.jsonl
    uv run python scripts/reco_eval.py run --records data/eval/synth.jsonl \
        --out data/eval/report_synth.json

계산은 전부 `features.recommend.evaluation` 에 있고 이 파일은 인자를 받아 넘기기만 합니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from config import get_settings
from features.recommend import repository_eval
from features.recommend.evaluation import export, quality, report, synth
from features.recommend.evaluation.record import read_jsonl, write_jsonl


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


def _run(args: argparse.Namespace) -> None:
    header, records = read_jsonl(args.records)
    target = (
        json.loads(args.target_weights.read_text(encoding="utf-8")) if args.target_weights else None
    )
    options = report.RunOptions(
        ks=tuple(args.k),
        catalog_size=args.catalog_size,
        model_version=args.model_version,
        include_simulated=args.include_simulated,
        position_correct=args.position_correct,
        target_weights=target,
        seed=args.seed,
        resamples=args.resamples,
    )
    built = report.build_report(
        header, records, options, input_sha256=hashlib.sha256(args.records.read_bytes()).hexdigest()
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report.summary(built))
    print(f"report → {args.out}")


def _quality(args: argparse.Namespace) -> None:
    _, records = read_jsonl(args.records)
    health, error = quality.fetch_health(args.health_url) if args.health_url else (None, "skipped")
    row = quality.snapshot(
        records,
        health=health,
        health_error=error,
        db=repository_eval.quality_items() if args.db else None,
    )
    quality.append(args.out, row)
    print(f"quality: alerts={len(row['alerts'])} → {args.out}")
    for alert in row["alerts"]:
        print(f"  - {alert}")


def _aware(text: str) -> datetime:
    """ISO 8601. 시간대가 없으면 UTC 입니다.

    naive 값을 TIMESTAMPTZ 와 비교하면 서버 시간대만큼 창이 밀립니다.
    """
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _export(args: argparse.Namespace) -> None:
    salt = get_settings().eval_salt
    if not salt:
        sys.exit("EVAL_SALT 가 없습니다. 가명화 없이는 내보내지 않습니다")
    until = args.until or datetime.now(UTC)
    rows = repository_eval.fetch_eval_rows(since=args.since, until=until)
    if not rows.logs:
        sys.exit(f"{args.since:%Y-%m-%d} ~ {until:%Y-%m-%d} 에 recommendation_log 가 없습니다")
    header, records = export.build_records(
        rows,
        export.ExportOptions(salt=salt, with_candidates=args.with_candidates),
        exported_at=until,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out, header, records)
    print(f"export: {len(records)}건 → {args.out}")


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

    r = commands.add_parser("run", help="검증부터 리포트까지")
    r.add_argument("--records", type=Path, required=True)
    r.add_argument("--k", type=int, nargs="+", default=[5, 10])
    r.add_argument("--catalog-size", type=int, default=None, help="없으면 헤더 값")
    r.add_argument("--model-version", default=None)
    r.add_argument("--include-simulated", action="store_true")
    r.add_argument("--position-correct", action="store_true")
    r.add_argument("--target-weights", type=Path, default=None, help="가중치 JSON 파일")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--resamples", type=int, default=report.stats.RESAMPLES)
    r.add_argument("--out", type=Path, required=True)
    r.set_defaults(run=_run)

    q = commands.add_parser("quality", help="품질 스냅샷 1행 추가")
    q.add_argument("--records", type=Path, required=True)
    q.add_argument("--health-url", default=None, help="예: http://localhost:8000/v1/health")
    q.add_argument("--db", action="store_true", help="DB 항목도 계산 (config 의 DB 설정 사용)")
    q.add_argument("--out", type=Path, required=True)
    q.set_defaults(run=_quality)

    e = commands.add_parser("export", help="DB → 가명화한 JSONL")
    e.add_argument(
        "--since",
        type=_aware,
        required=True,
        help="ISO 8601(시간대 없으면 UTC). 라우터·엔진 연결일보다 앞서면 안 됩니다",
    )
    e.add_argument("--until", type=_aware, default=None, help="없으면 지금")
    e.add_argument(
        "--with-candidates", action="store_true", help="미노출 후보도 실음(오프폴리시용)"
    )
    e.add_argument("--out", type=Path, required=True)
    e.set_defaults(run=_export)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
