"""쌓인 추천 · 이벤트 로그(JSONL)를 이어 오프라인 평가표를 냅니다.

    uv run python scripts/eval_recommend_logs.py            # RECO_LOG_DIR 또는 var/reco_logs
    uv run python scripts/eval_recommend_logs.py --log-dir /app/var/reco_logs --since 2026-09-22
    uv run python scripts/eval_recommend_logs.py --json out/offline.json

계산은 전부 `features.recommend.evaluation.offline` 에 있고 여기는 인자와 출력뿐입니다.
반응이 난수인 트래픽(부하 스크립트)의 수치는 뜻이 없습니다 — 계산이 되는지만 보십시오.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from features.recommend.evaluation import offline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.environ.get("RECO_LOG_DIR", "var/reco_logs")),
        help="recommendations-*.jsonl · events-*.jsonl 이 있는 폴더",
    )
    parser.add_argument(
        "--since", type=date.fromisoformat, default=None, help="이 날짜부터 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--until", type=date.fromisoformat, default=None, help="이 날짜까지 (YYYY-MM-DD)"
    )
    parser.add_argument("--json", type=Path, default=None, help="보고서를 JSON 으로도 저장할 경로")
    args = parser.parse_args(argv)

    if not args.log_dir.is_dir():
        print(f"로그 폴더가 없습니다: {args.log_dir}", file=sys.stderr)
        return 2
    recommendations, events = offline.load_logs(args.log_dir, args.since, args.until)
    if not recommendations:
        print(f"추천 로그가 없습니다: {args.log_dir}", file=sys.stderr)
        return 1
    report = offline.evaluate(recommendations, events)
    print(offline.render(report))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nJSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
