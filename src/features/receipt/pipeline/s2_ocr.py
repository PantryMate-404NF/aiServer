"""PaddleOCR 프로세스 풀. OCR 모델 인스턴스는 이 파일에서만 만듭니다.

스레드가 아니라 프로세스로 나눈 이유는 PaddlePaddle 이 프로세스 전역 상태를 쓰기
때문입니다. 한 프로세스에서 동시에 돌리면 Lock 으로 직렬화할 수밖에 없어 동시 요청이
그대로 줄을 섭니다. 워커를 나누면 Lock 이 필요 없습니다.

시작 방식은 spawn 으로 못박습니다. 리눅스 기본값인 fork 로 워커를 만들면 부모가 이미
불러 둔 PaddlePaddle 상태가 복제되어 깨집니다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from contextlib import asynccontextmanager
from multiprocessing import get_context
from typing import Any

from config import get_settings
from features.receipt.pipeline.s1_preprocess import Image
from features.receipt.schema import OcrCell
from utils.errors import OcrPoolNotReadyError

logger = logging.getLogger(__name__)

OCR_LANG = "korean"
# 모델 버전을 고정합니다. 라이브러리 기본값을 따라가면 업그레이드가 곧 정확도 변화입니다.
OCR_VERSION = "PP-OCRv5"
# 예열을 다시 돌리는 최대 횟수. 한 바퀴에 워커가 다 뜨지 않는 경우를 위한 여유입니다.
WARMUP_ROUNDS = 2

# 워커 프로세스의 전역입니다. 메인 프로세스에서는 끝까지 None 입니다.
_engine: Any = None

_pool: ProcessPoolExecutor | None = None
_is_ready = False
# 워커 수만큼만 동시에 들여보냅니다. 이 문이 없으면 요청이 몰릴 때 전부 받아들여
# 각자 이미지 배열을 들고 줄을 서므로 대기 요청 수에 메모리가 비례합니다.
_slots: asyncio.Semaphore | None = None


def is_ready() -> bool:
    """워커가 모델을 다 올렸는지. 헬스체크가 이 값으로 503 과 200 을 가릅니다."""
    return _is_ready


async def start_pool() -> None:
    """풀을 만들고 워커가 모델을 올릴 때까지 기다립니다. lifespan 에서 한 번만 부릅니다."""
    global _pool, _is_ready, _slots

    workers = get_settings().ocr_workers
    _slots = asyncio.Semaphore(workers)
    _pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=get_context("spawn"),
        initializer=_load_engine,
    )

    started = await _spread_warmup(workers)
    _is_ready = True
    if started < workers:
        logger.warning(
            "ocr pool ready with only %d/%d workers loaded; first requests may be slow",
            started,
            workers,
        )
    else:
        logger.info("ocr pool ready workers=%d", workers)


def shutdown_pool() -> None:
    """워커를 정리합니다. 이걸 빼면 프로세스가 남아 메모리를 붙들고 있습니다."""
    global _pool, _is_ready, _slots

    _is_ready = False
    if _pool is not None:
        _pool.shutdown(wait=True, cancel_futures=True)
        _pool = None
        _slots = None
        logger.info("ocr pool shut down")


@asynccontextmanager
async def slot() -> AsyncIterator[None]:
    """OCR 처리 자리 하나를 잡습니다. 자리가 없으면 날 때까지 기다립니다.

    호출부는 이 안에서 전처리까지 함께 합니다. 전처리를 밖에 두면 대기 중인 요청이
    이미 디코딩한 이미지를 들고 있게 되어 자리를 제한한 의미가 사라집니다.
    """
    if _slots is None:
        raise OcrPoolNotReadyError("OCR 워커 풀이 아직 준비되지 않았습니다.")
    async with _slots:
        yield


async def read(image: Image) -> list[OcrCell]:
    """전처리를 마친 이미지에서 텍스트 조각을 읽습니다. Lock 을 쓰지 않습니다."""
    global _is_ready

    if _pool is None or not _is_ready:
        raise OcrPoolNotReadyError("OCR 워커 풀이 아직 준비되지 않았습니다.")

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_pool, _extract_cells, image)
    except BrokenExecutor as error:
        # 워커가 죽으면 풀은 이후 모든 제출을 거절합니다. 준비 상태를 내려서 헬스체크가
        # 사실을 말하게 합니다. 그러지 않으면 오케스트레이터가 재시작할 근거를 못 받습니다.
        _is_ready = False
        logger.error("ocr pool is broken; readiness turned off")
        raise OcrPoolNotReadyError("OCR 워커 풀이 깨졌습니다.") from error


async def _spread_warmup(workers: int) -> int:
    """워커 수만큼 프로세스를 띄우고 각자 모델을 올릴 때까지 기다립니다.

    예열 작업이 곧바로 끝나면 먼저 뜬 워커가 전부 집어가고 나머지는 뜨지 않습니다.
    그래서 각 작업이 잠깐 워커를 붙잡아 풀이 다음 프로세스를 띄우게 만듭니다.
    한 바퀴로 모자라면 한 번 더 돌립니다.
    """
    loop = asyncio.get_running_loop()
    pids: set[int] = set()
    for _ in range(WARMUP_ROUNDS):
        done = await asyncio.gather(*(loop.run_in_executor(_pool, _touch) for _ in range(workers)))
        pids.update(done)
        if len(pids) >= workers:
            break
    return len(pids)


def _touch() -> int:
    """워커 안에서 돕니다. initializer 가 끝난 뒤에만 실행되므로 모델이 이미 올라와 있습니다."""
    time.sleep(get_settings().ocr_warmup_hold_sec)
    return os.getpid()


def _load_engine() -> None:
    """워커 프로세스마다 한 번 실행됩니다. 모델 로딩은 여기서만 일어납니다."""
    global _engine

    from paddleocr import PaddleOCR

    _engine = PaddleOCR(
        lang=OCR_LANG,
        ocr_version=OCR_VERSION,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _extract_cells(image: Image) -> list[OcrCell]:
    """워커 안에서 돕니다. 라이브러리 원시 반환값을 OcrCell 로 바꿔서만 내보냅니다."""
    if _engine is None:
        raise OcrPoolNotReadyError("워커에 OCR 엔진이 없습니다. initializer 가 실패했습니다.")

    cells: list[OcrCell] = []
    for result in _engine.predict(image):
        texts = result.get("rec_texts", [])
        polys = result.get("rec_polys", result.get("dt_polys", []))
        scores = result.get("rec_scores", [0.0] * len(texts))
        for text, poly, score in zip(texts, polys, scores, strict=True):
            xs = [point[0] for point in poly]
            ys = [point[1] for point in poly]
            cells.append(
                OcrCell(
                    text=text,
                    x_left=float(min(xs)),
                    y_center=float((min(ys) + max(ys)) / 2),
                    height=float(max(ys) - min(ys)),
                    score=float(score),
                )
            )
    return cells
