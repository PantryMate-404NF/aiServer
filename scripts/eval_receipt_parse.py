"""평가 셋을 새 서버 코드로 돌려 predictions.json 을 만들고 채점합니다.

기준선과 같은 잣대로 재기 위해 채점기는 PoC 의 `eval/evaluate.py` 를 그대로 씁니다.
채점기를 옮겨 쓰면 기준선 96.7% 와 비교할 수 없게 됩니다.

    uv run python scripts/eval_receipt_parse.py --poc /path/to/ocr_poc

평가 대상은 기본적으로 PoC 의 OCR 캐시에 들어 있는 24장입니다. 그 24장이 기준선을
잰 셋이고, 정답 셋은 그 뒤로 48장까지 늘어나 있어 전체를 돌리면 비교가 깨집니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from features.receipt.pipeline import s1_preprocess, s2_ocr, s3_parse, s5_normalize
from features.receipt.schema import ParsedReceipt
from utils.errors import AppError

# 무료 티어는 분당 15회가 상한입니다. 여유 1회분을 빼고 간격을 잡습니다.
DEFAULT_RPM = 14
BASELINE_CACHE = "eval/cache/ocr_clahe_v4_maxpixels.json"
# 한 장이 실패해도 실행 전체를 버리지 않습니다. 배치라 기다릴 사람이 없으므로 요청
# 경로보다 넉넉하게 다시 시도합니다. 그래도 안 되면 그 장을 빼고 계속하되 끝에 보고합니다.
LLM_ATTEMPTS = 3
LLM_RETRY_WAIT_SEC = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poc", type=Path, required=True, help="PoC 저장소 경로")
    parser.add_argument("--out", type=Path, default=Path("data/eval/predictions.json"))
    parser.add_argument("--rpm", type=int, default=DEFAULT_RPM, help="분당 LLM 호출 상한")
    parser.add_argument("--all", action="store_true", help="정답 셋 전체를 돌립니다")
    return parser.parse_args()


def pick_images(poc: Path, use_all: bool) -> list[str]:
    ground = json.loads((poc / "eval/ground_truth.json").read_text(encoding="utf-8"))
    names = [receipt["image"] for receipt in ground["receipts"]]
    if use_all:
        return names
    baseline = set(json.loads((poc / BASELINE_CACHE).read_text(encoding="utf-8")))
    return [name for name in names if name in baseline]


async def run_ocr(poc: Path, names: list[str]) -> dict[str, str]:
    """OCR 은 워커 풀에 그대로 맡깁니다. 운영과 같은 경로로 재야 측정에 의미가 있습니다."""
    await s2_ocr.start_pool()

    async def one(name: str) -> tuple[str, str]:
        started = time.perf_counter()
        image = s1_preprocess.to_ocr_input((poc / "resources" / name).read_bytes())
        text = s3_parse.group_lines(await s2_ocr.read(image))
        print(f"  {name:<14} {time.perf_counter() - started:>5.1f}s  {len(text.splitlines()):>3}줄")
        return name, text

    try:
        results = await asyncio.gather(*(one(name) for name in names))
    finally:
        s2_ocr.shutdown_pool()
    return dict(results)


async def run_llm(texts: dict[str, str], rpm: int) -> list[dict[str, object]]:
    """호출 간격을 벌려 무료 티어 상한 아래로 유지합니다. 병렬로 던지면 429 가 납니다."""
    interval = 60.0 / rpm
    receipts: list[dict[str, object]] = []
    failed: list[str] = []
    for name, text in texts.items():
        started = time.perf_counter()
        parsed = await _parse_with_retry(name, text)
        if parsed is None:
            failed.append(name)
            continue
        receipts.append(
            {
                "image": name,
                "purchased_at": parsed.purchased_at.isoformat() if parsed.purchased_at else None,
                # is_food 를 남깁니다. 채점기가 식재료 판정 정확도를 이 값으로 잽니다.
                "items": [{"name": i.name, "is_food": i.is_food} for i in parsed.items],
            }
        )
        elapsed = time.perf_counter() - started
        print(
            f"  {name:<14} {elapsed:>5.1f}s  품목 {len(parsed.items):>2}개  {parsed.purchased_at}"
        )
        await asyncio.sleep(max(0.0, interval - elapsed))

    if failed:
        print(f"\n  실패해서 뺀 영수증 {len(failed)}장: {', '.join(failed)}")
        print("  분모가 줄었으므로 이 실행은 기준선과 그대로 비교하지 않습니다.")
    return receipts


async def _parse_with_retry(name: str, text: str) -> ParsedReceipt | None:
    """배치라 기다릴 사람이 없으므로 요청 경로보다 넉넉하게 다시 시도합니다."""
    for attempt in range(1, LLM_ATTEMPTS + 1):
        try:
            return await s5_normalize.parse_receipt(text)
        except AppError as error:
            if attempt == LLM_ATTEMPTS:
                print(f"  {name:<14} 포기: {error}")
                return None
            print(f"  {name:<14} 실패 {attempt}/{LLM_ATTEMPTS} ({error}) — 잠시 후 재시도")
            await asyncio.sleep(LLM_RETRY_WAIT_SEC)
    return None


async def main() -> int:
    args = parse_args()
    names = pick_images(args.poc, args.all)
    print(f"평가 셋 {len(names)}장\n\nOCR")
    texts = await run_ocr(args.poc, names)

    print("\nLLM 파싱")
    receipts = await run_llm(texts, args.rpm)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"receipts": receipts}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n-> {args.out}")

    # 개발자가 자기 터미널에서 넘긴 경로입니다. 서버가 받는 입력이 아닙니다.
    scorer = args.poc / "eval/evaluate.py"
    command = [sys.executable, str(scorer), str(args.out)]
    return subprocess.run(command, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
