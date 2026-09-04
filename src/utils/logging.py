"""로깅 설정. 애플리케이션 시작 시 한 번만 호출합니다."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """루트 로거를 설정합니다. 모듈에서는 getLogger(__name__) 만 씁니다."""
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
