"""SQLAlchemy Engine 과 세션. 생성은 이 파일에서만 합니다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings

POOL_SIZE = 5  # (예시값, 실제 데이터로 대체 필요)
POOL_TIMEOUT_SEC = 30  # (예시값, 실제 데이터로 대체 필요)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Engine 은 프로세스당 하나만 만듭니다. 커넥션 풀이 여기 붙습니다."""
    settings = get_settings()
    return create_engine(
        settings.database_dsn,
        pool_size=POOL_SIZE,
        pool_timeout=POOL_TIMEOUT_SEC,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """세션은 이 컨텍스트 매니저로만 엽니다. 함수가 세션을 반환하지 않습니다."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
