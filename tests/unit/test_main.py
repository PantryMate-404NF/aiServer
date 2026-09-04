"""진입점이 설정을 읽고 로깅을 켠 뒤 앱을 만드는지."""

from __future__ import annotations

import logging

import pytest

import main


def test_create_app_configures_logging_and_logs_startup(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """configure_logging 은 basicConfig(force=True) 로 caplog 핸들러를 지우므로 막습니다."""
    called: list[str] = []
    monkeypatch.setattr(main, "configure_logging", called.append)

    with caplog.at_level(logging.INFO):
        app = main.create_app()

    assert called == ["DEBUG"]
    assert app.title == "aiServer"
    assert "aiServer started" in caplog.text
    assert "aiserver_test" in caplog.text
