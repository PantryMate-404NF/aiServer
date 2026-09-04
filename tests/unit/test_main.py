"""진입점이 설정을 읽고 로깅을 켠 뒤 기동 로그를 남기는지."""

from __future__ import annotations

import logging

import pytest

import main


def test_main_logs_startup(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """configure_logging 은 basicConfig(force=True) 로 caplog 핸들러를 지우므로 막습니다."""
    called: list[str] = []
    monkeypatch.setattr(main, "configure_logging", called.append)

    with caplog.at_level(logging.INFO):
        main.main()

    assert called == ["DEBUG"]
    assert "aiServer started" in caplog.text
    assert "aiserver_test" in caplog.text
