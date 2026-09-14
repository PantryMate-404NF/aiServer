#!/usr/bin/env python3
"""검수 시트를 구글 스프레드시트용 CSV 로 내보낸다.

    .venv/bin/python scripts/reco/bench/review_export.py
    make review-csv

## 왜 CSV 로 따로 내보내나

원본은 탭으로 구분된 `.tsv` 입니다. 편집기에서 그대로 채우면 두 가지가 깨집니다.

    Tab 키를 누르면 공백이 들어간다     그 줄이 통째로 한 덩어리가 된다
    저장할 때 후행 탭이 잘린다          뒤쪽 열이 사라진다

첫 번째가 특히 위험합니다 — `review_apply` 가 **에러 없이 그 줄을 건너뛰어서**,
채웠는데 반영이 안 된 것을 알아챌 수 없습니다.

스프레드시트로 열면 구분자를 사람이 만질 일이 없습니다.

## 안내 주석을 본문에서 뺍니다

`.tsv` 는 맨 위 여섯 줄이 `#` 로 시작하는 안내입니다. 스프레드시트에서는 그게
데이터 행으로 보이므로 빼고, 같은 내용을 옆 파일로 냅니다.

## 되돌리는 길

구글 스프레드시트에서 `파일 → 다운로드 → 쉼표로 구분된 값(.csv)` 으로 받아
`review_apply.py --sheet <받은파일>` 에 넘기면 됩니다. 구분자는 자동으로
알아봅니다.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BENCH_OUT = ROOT / "scripts" / "reco" / "bench" / "out"
REVIEW_DIR = ROOT / "review"
SRC = REVIEW_DIR / "review_sheet.tsv"
OUT = REVIEW_DIR / "review_sheet.csv"
GUIDE = REVIEW_DIR / "review_guide.txt"


def main() -> None:
    if not SRC.exists():
        sys.exit(f"시트가 없습니다: {SRC}\n  먼저:  make review-sheet TOP=800")

    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    guide = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#")]

    rows = list(csv.reader(io.StringIO("".join(body)), delimiter="\t"))
    if not rows:
        sys.exit("시트에 내용이 없습니다")

    # csv.writer 가 쉼표·따옴표·줄바꿈을 알아서 감쌉니다. 손으로 join 하면
    # 재료명에 쉼표가 하나만 들어와도 열이 밀립니다.
    with open(OUT, "w", encoding="utf-8-sig", newline="") as w:
        csv.writer(w).writerows(rows)

    GUIDE.write_text(
        "검수 시트 안내\n\n"
        + "".join(ln.lstrip("# ").rstrip() + "\n" for ln in guide)
        + "\n쓸 수 있는 재료 이름 목록: review/dictionary.tsv (536종)\n"
        "\n다 채운 뒤:\n"
        "  1. 구글 스프레드시트에서 파일 → 다운로드 → 쉼표로 구분된 값(.csv)\n"
        "  2. make review-apply SHEET=<받은파일>        (미리보기)\n"
        "  3. make review-apply SHEET=<받은파일> WRITE=1 (실제 반영)\n",
        encoding="utf-8",
    )

    filled = sum(1 for r in rows[1:] if len(r) > 4 and r[4].strip())
    print(f"  → {OUT}  ({len(rows) - 1}행 · 기입 {filled}행)")
    print(f"  → {GUIDE}")
    print()
    print("  구글 스프레드시트에서:")
    print("    파일 → 가져오기 → 업로드 → 위 .csv 선택")
    print("    구분자는 '쉼표' 를 고르고, E열(결정)만 채우면 됩니다")
    print()
    print("  🔴 원본 .tsv 는 그대로 둡니다 — make review-sheet 를 다시 돌리면")
    print("     채운 내용이 날아갑니다.")


if __name__ == "__main__":
    main()
