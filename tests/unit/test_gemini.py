"""Gemini 호출부. 네트워크를 타지 않고 재시도 규칙과 응답 해석만 봅니다."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from google.genai.errors import APIError

import config
from infra import gemini
from utils.errors import ExternalServiceError

SCHEMA: dict[str, Any] = {"type": "object"}


class _Response:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeModels:
    """정해진 순서대로 예외를 던지거나 응답을 돌려줍니다."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def generate_content(self, **kwargs: object) -> _Response:
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = type("Aio", (), {"models": models})()


def _install(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> _FakeModels:
    models = _FakeModels(outcomes)
    monkeypatch.setattr(gemini, "get_client", lambda: _FakeClient(models))
    monkeypatch.setenv("LLM_BACKOFF_BASE_SEC", "0")
    config.get_settings.cache_clear()
    return models


def _error(code: int) -> APIError:
    return APIError(code, {"error": {"message": "boom", "status": "x"}})


def test_successful_call_returns_the_parsed_object(monkeypatch: pytest.MonkeyPatch) -> None:
    models = _install(monkeypatch, ['{"items": []}'])

    result = asyncio.run(gemini.complete_json("prompt", SCHEMA))

    assert result == {"items": []}
    assert models.calls == 1


def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 는 잠깐 기다리면 풀립니다. 무료 티어는 분당 호출 수 제한이 있습니다."""
    models = _install(monkeypatch, [_error(429), '{"items": []}'])

    assert asyncio.run(gemini.complete_json("prompt", SCHEMA)) == {"items": []}
    assert models.calls == 2


def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 이나 인증 실패는 다시 보내도 같습니다. 재시도는 응답만 늦춥니다."""
    models = _install(monkeypatch, [_error(400)])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))
    assert models.calls == 1


def test_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """재시도가 끝없이 늘면 백엔드 타임아웃 30초를 넘깁니다."""
    models = _install(monkeypatch, [_error(503), _error(503)])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))
    assert models.calls == 2


def test_empty_response_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [""])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))


def test_non_json_response_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, ["말이 안 되는 응답"])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))


def test_json_array_response_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """스키마는 객체를 요구합니다. 배열이 오면 뒷단이 조용히 잘못된 값을 읽습니다."""
    _install(monkeypatch, ["[1, 2, 3]"])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))


def test_gateway_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """504 는 데드라인을 다 쓰고 옵니다. 다시 보내면 응답 예산을 두 배로 씁니다."""
    models = _install(monkeypatch, [_error(504)])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))
    assert models.calls == 1


def test_transport_failure_becomes_an_external_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """타임아웃이 httpx 예외 그대로 새어 나가면 500 계약으로 바뀌지 않습니다."""
    models = _install(monkeypatch, [httpx.ReadTimeout("timed out")])

    with pytest.raises(ExternalServiceError):
        asyncio.run(gemini.complete_json("prompt", SCHEMA))
    assert models.calls == 1
