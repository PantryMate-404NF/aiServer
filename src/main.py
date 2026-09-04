"""애플리케이션 진입점. uv run python -m main 으로 실행합니다."""

from __future__ import annotations

import logging

from config import get_settings
from utils.logging import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("aiServer started (db=%s)", settings.db_name)


if __name__ == "__main__":
    main()
