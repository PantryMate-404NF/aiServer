"""수집한 재료 이미지 주소를 **백엔드 덤프 원본에 그대로 써 넣습니다.**

    PYTHONUTF8=1 uv run python scripts/apply_ingredient_images_to_dump.py --check
    PYTHONUTF8=1 uv run python scripts/apply_ingredient_images_to_dump.py --write

`scripts/crawl_ingredient_images.py` 가 만든 `data/ingredient_images/ingredients.csv` 를 읽어,
받은 덤프의 두 자리에 같은 값을 넣습니다. 고친 덤프를 그대로 백엔드에 돌려주기 위한 것입니다.

    csv/ingredients.csv                 `image_url` 칸 (이미 있는 칸입니다. 새로 만들지 않습니다)
    pantry_recipe_domain_dump.sql       `COPY public.ingredients ...` 블록의 네 번째 값

**둘 다 고칩니다.** 백엔드 안내서(README 3절)는 `.sql` 로 복원하라고 적고 있어, CSV 만 고치면
복원한 DB 의 `image_url` 이 그대로 비어 있습니다 — 에러 없이 빈 화면이 됩니다.

지키는 것 셋입니다.

1. **서식을 건드리지 않습니다.** LF 줄바꿈 · BOM 없는 UTF-8 · 따옴표 규칙을 그대로 둡니다.
   쓰기 전에 **아무것도 바꾸지 않고 다시 쓴 결과가 원본과 바이트까지 같은지** 확인합니다.
   다르면 쓰지 않고 멈춥니다.
2. **`image_url` 말고는 아무 칸도 건드리지 않습니다.** 쓰고 난 뒤 전 행을 대조합니다.
3. `--write` 없이는 쓰지 않습니다. 기본은 검사(`--check`)입니다.

원본 백업은 `data/backup/` 에 있습니다. 되돌리려면 그 폴더를 덮어쓰면 됩니다.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "data/Backend_Data_Dump/pantry_recipe_domain_dump"
CSV_PATH = DUMP / "csv/ingredients.csv"
SQL_PATH = DUMP / "pantry_recipe_domain_dump.sql"
SOURCE = ROOT / "data/ingredient_images/ingredients.csv"

COPY_PREFIX = "COPY public.ingredients ("
#: COPY 블록에서 `image_url` 이 몇 번째 값인가. 헤더에서 읽어 확인하므로 고정값이 아닙니다.
NULL = chr(92) + "N"
END = chr(92) + "."
LF = chr(10)


def read_urls(path: Path) -> dict[str, str]:
    """재료 번호 → 이미지 주소. 빈 값은 담지 않습니다."""
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            row["ingredient_id"]: row["image_url"]
            for row in csv.DictReader(handle)
            if (row.get("image_url") or "").strip()
        }


def render_csv(rows: list[dict[str, str]], fields: list[str]) -> bytes:
    """원본과 같은 서식으로 CSV 를 만듭니다. LF 줄바꿈, 필요한 곳만 따옴표."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator=LF)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def patch_csv(urls: dict[str, str], *, write: bool) -> tuple[int, str]:
    """CSV 의 `image_url` 을 채웁니다. 먼저 무변경 왕복이 바이트까지 같은지 봅니다."""
    original = CSV_PATH.read_bytes()
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "image_url" not in fields:
        return 0, "image_url 칸이 없습니다"
    if render_csv(rows, fields) != original:
        return 0, "무변경 왕복이 원본과 다릅니다 — 서식을 보존하지 못하므로 쓰지 않습니다"

    filled = 0
    for row in rows:
        url = urls.get(row["ingredient_id"], "")
        if url and row["image_url"] != url:
            row["image_url"] = url
            filled += 1
    if write:
        CSV_PATH.write_bytes(render_csv(rows, fields))
    return filled, ""


def patch_sql(urls: dict[str, str], *, write: bool) -> tuple[int, str]:
    """SQL 덤프의 `COPY public.ingredients` 블록에서 네 번째 값을 채웁니다.

    다른 표의 COPY 블록은 건드리지 않습니다 — 블록의 시작과 `\\.` 사이만 봅니다.
    """
    with SQL_PATH.open(encoding="utf-8", newline="") as handle:
        lines = handle.read().split(LF)
    start = next((i for i, line in enumerate(lines) if line.startswith(COPY_PREFIX)), None)
    if start is None:
        return 0, "ingredients 의 COPY 블록을 찾지 못했습니다"
    header = lines[start]
    cols = header[header.index("(") + 1 : header.index(")")].replace(" ", "").split(",")
    if "image_url" not in cols:
        return 0, "COPY 헤더에 image_url 이 없습니다"
    at = cols.index("image_url")

    filled = 0
    for i in range(start + 1, len(lines)):
        if lines[i].startswith(END):
            break
        parts = lines[i].split(chr(9))
        if len(parts) != len(cols):
            return 0, f"{i + 1}번째 줄의 값 개수가 헤더와 다릅니다"
        url = urls.get(parts[0], "")
        if url and parts[at] != url:
            parts[at] = url
            lines[i] = chr(9).join(parts)
            filled += 1
    if write:
        with SQL_PATH.open("w", encoding="utf-8", newline="") as handle:
            handle.write(LF.join(lines))
    return filled, ""


def verify(urls: dict[str, str]) -> list[str]:
    """쓰고 난 뒤 — 두 자리의 값이 서로 같고, 다른 칸은 그대로인지."""
    problems: list[str] = []
    backup = ROOT / "data/backup/pantry_recipe_domain_dump_원본_20260929/csv/ingredients.csv"
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        now = list(csv.DictReader(handle))
    if backup.exists():
        with backup.open(encoding="utf-8", newline="") as handle:
            before = list(csv.DictReader(handle))
        if len(before) != len(now):
            problems.append(f"행 수가 달라졌습니다: {len(before)} → {len(now)}")
        else:
            changed = {
                key for a, b in zip(before, now, strict=True) for key in a if a[key] != b[key]
            }
            if changed - {"image_url"}:
                problems.append(
                    f"image_url 밖의 칸이 바뀌었습니다: {sorted(changed - {'image_url'})}"
                )
    else:
        problems.append("백업이 없어 원본과 대조하지 못했습니다")

    missing = [r["ingredient_id"] for r in now if not r["image_url"]]
    if missing:
        problems.append(f"CSV 에 아직 빈 image_url: {len(missing)}건")

    with SQL_PATH.open(encoding="utf-8", newline="") as handle:
        lines = handle.read().split(LF)
    start = next((i for i, line in enumerate(lines) if line.startswith(COPY_PREFIX)), -1)
    header = lines[start]
    cols = header[header.index("(") + 1 : header.index(")")].replace(" ", "").split(",")
    at = cols.index("image_url")
    seen = 0
    for i in range(start + 1, len(lines)):
        if lines[i].startswith(END):
            break
        parts = lines[i].split(chr(9))
        seen += 1
        want = urls.get(parts[0], "")
        if want and parts[at] != want:
            problems.append(f"SQL 의 재료 {parts[0]} 값이 CSV 와 다릅니다")
            break
        if not want and parts[at] == NULL:
            problems.append(f"SQL 의 재료 {parts[0]} 가 아직 비어 있습니다")
            break
    if seen != len(now):
        problems.append(f"SQL 행 수({seen})와 CSV 행 수({len(now)})가 다릅니다")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true", help="실제로 원본을 고칩니다")
    args = parser.parse_args()

    if not SOURCE.exists():
        print(
            f"{SOURCE} 가 없습니다. 먼저 crawl_ingredient_images.py 를 돌리십시오", file=sys.stderr
        )
        return 2
    urls = read_urls(SOURCE)
    print(f"채울 주소 {len(urls)}건 — {SOURCE}")

    csv_n, csv_err = patch_csv(urls, write=args.write)
    if csv_err:
        print(f"CSV: {csv_err}", file=sys.stderr)
        return 1
    sql_n, sql_err = patch_sql(urls, write=args.write)
    if sql_err:
        print(f"SQL: {sql_err}", file=sys.stderr)
        return 1

    verb = "고쳤습니다" if args.write else "고칠 수 있습니다(아직 쓰지 않음)"
    print(f"  csv/ingredients.csv            {csv_n}건 {verb}")
    print(f"  pantry_recipe_domain_dump.sql  {sql_n}건 {verb}")
    if not args.write:
        print("\n실제로 쓰려면 --write 를 붙이십시오.")
        return 0

    problems = verify(urls)
    if problems:
        print("\n확인에서 걸린 것:", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("\n확인 통과 — 두 자리의 값이 같고 다른 칸은 그대로입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
