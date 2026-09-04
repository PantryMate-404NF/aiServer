"""설정 로딩과 DSN 조립."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import config


def test_settings_read_environment() -> None:
    settings = config.get_settings()
    assert settings.db_name == "aiserver_test"
    assert settings.db_port == 5432
    assert settings.log_level == "DEBUG"


def test_database_dsn_is_assembled_from_parts() -> None:
    dsn = config.get_settings().database_dsn
    assert dsn.startswith("postgresql+psycopg://")
    assert "tester" in dsn
    assert "localhost:5432" in dsn
    assert dsn.endswith("/aiserver_test")


def test_settings_are_cached() -> None:
    assert config.get_settings() is config.get_settings()


def test_missing_required_key_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """필수 키가 없으면 처리 중간이 아니라 시작 시점에 터져야 합니다."""
    monkeypatch.delenv("DB_HOST")
    config.get_settings.cache_clear()
    with pytest.raises(ValidationError):
        config.get_settings()
