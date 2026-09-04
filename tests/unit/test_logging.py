"""로깅 설정."""

from __future__ import annotations

import logging

from utils.logging import configure_logging


def test_configure_logging_sets_root_level() -> None:
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
    configure_logging("INFO")
    assert logging.getLogger().level == logging.INFO
