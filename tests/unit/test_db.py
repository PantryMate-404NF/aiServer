"""Engine 생성과 세션 스코프의 커밋·롤백 동작."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from infra import db


def test_engine_is_created_once_per_process() -> None:
    assert db.get_engine() is db.get_engine()
    assert isinstance(db.get_engine(), Engine)


def test_engine_uses_the_assembled_dsn() -> None:
    assert db.get_engine().url.database == "aiserver_test"


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_session_scope_commits_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    with db.session_scope():
        pass
    assert session.committed
    assert session.closed
    assert not session.rolled_back


def test_session_scope_rolls_back_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """예외를 삼키지 않고 그대로 올려야 재처리 대상을 알 수 있습니다."""
    session = _FakeSession()
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    with pytest.raises(ValueError, match="boom"), db.session_scope():
        raise ValueError("boom")
    assert session.rolled_back
    assert session.closed
    assert not session.committed
