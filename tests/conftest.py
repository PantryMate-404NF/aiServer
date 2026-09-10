"""모든 테스트가 공유하는 픽스처."""

from __future__ import annotations

import pytest

import config
from infra import db

ENV = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "aiserver_test",
    "DB_USER": "tester",
    "DB_PASSWORD": "secret",
    "LOG_LEVEL": "DEBUG",
    "INTERNAL_API_KEY": "test-internal-key",
    "GEMINI_API_KEY": "test-gemini-key",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """환경변수를 세우고 설정 캐시를 비웁니다. 앞 테스트의 값이 새지 않게 합니다.

    정리는 하지 않습니다. 테스트가 캐시 함수 자체를 monkeypatch 하면
    teardown 에서 cache_clear 를 부를 수 없기 때문입니다.
    """
    # 개발자 머신의 .env 를 읽지 않게 막습니다. README 대로 .env 를 만들어 둔 사람은
    # 이 줄이 없으면 필수 키 누락 테스트가 그 파일의 값을 읽어 통과해 버립니다.
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_session_factory.cache_clear()
