"""영수증 도메인의 공개 API. 도메인 밖에서는 이 이름들만 씁니다."""

from features.receipt.pipeline.s2_ocr import is_ready, shutdown_pool, start_pool

__all__ = ["is_ready", "shutdown_pool", "start_pool"]
