"""SQLAlchemy Engine 과 세션. 생성은 이 파일에서만 합니다.

02 의 1.3 — **무거운 자원의 생성 위치는 한 곳뿐입니다.** Engine 에 커넥션 풀이
붙어 있어서, 여러 곳에서 만들면 메모리와 지연시간으로 즉시 드러납니다.

    from infra.db import session_scope
    from sqlalchemy import text

    with session_scope() as s:
        rows = s.execute(text("SELECT * FROM retrieve_for_user(...)"), params).all()

## 🔴 풀 크기를 설정에서 받습니다

`pool_size` 를 작게 잡으면 반납할 때 초과분을 **닫아** 매 요청이 새 커넥션을
엽니다 — 동시 8요청에서 p50 32ms 였고, 크기를 맞추자 2.5ms 가 됐습니다
(09-02 실측). 그래서 `config.py` 가 값을 들고 있고 여기서는 읽기만 합니다.

## 세션은 컨텍스트 매니저로만 엽니다

함수가 세션을 반환하게 만들지 않습니다 (03 의 5절). 반환하면 닫는 책임이
호출부로 흩어지고, 예외 경로에서 새는 것을 아무도 못 봅니다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import cast

from psycopg import Cursor
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Engine 은 프로세스당 하나만 만듭니다. 커넥션 풀이 여기 붙습니다."""
    settings = get_settings()
    return create_engine(
        settings.database_dsn,
        pool_size=settings.pool_min,
        max_overflow=settings.pg_max_conn - settings.pool_min,
        pool_timeout=settings.pool_timeout_sec,
        pool_pre_ping=True,
        # 주의: search_path 를 명시한다. 지금은 접속 유저명(`reco`)과 스키마명이
        #    같아서 기본값 `"$user", public` 이 우연히 맞는다. 유저가 달라지는
        #    순간(예: 대시보드용 `reco_ro`) 조용히 public 만 보게 되고,
        #    `retrieve_for_user()` 를 못 찾아 런타임에야 드러난다.
        #    DATABASE 수준으로 걸지 않는 이유는 같은 DB 를 다른 용도로 쓰는
        #    쪽까지 끌고 가기 때문이다 — 커넥션 단위로만 건다.
        connect_args={"options": f"-csearch_path={settings.db_schema},public"},
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """세션은 이 컨텍스트 매니저로만 엽니다.

    예외를 삼키지 않고 롤백한 뒤 그대로 올립니다 (03 의 3절). 삼키면 무엇이 왜
    실패했는지 남지 않아 재처리할 수 없습니다.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def cursor(commit: bool = False) -> Iterator[Cursor]:
    """드라이버 커서를 그대로 엽니다. **원시 SQL 전용 통로입니다.**

    `retrieve_for_user()` 같은 저장 프로시저 호출과 `recommendation_log` INSERT 는
    ORM 을 거치지 않습니다 — 매핑할 엔티티가 없고, SQL 이 `deploy/init/` 의
    함수와 1:1로 대응해야 하기 때문입니다. 그래서 세션 대신 커서를 씁니다.

    🔴 **풀은 하나입니다.** 여기도 `get_engine()` 의 커넥션을 빌려 쓰므로
       `session_scope()` 와 같은 풀·같은 설정을 탑니다. 별도 풀을 만들면
       `pool_size` 계산이 두 배로 어긋납니다.

    파라미터는 `%s` 자리표시자를 씁니다 (psycopg3 도 pyformat 을 받습니다).
    """
    conn = get_engine().raw_connection()
    try:
        # 드라이버는 psycopg3 로 고정돼 있습니다. SQLAlchemy 의 DBAPI 프로토콜 타입은
        # 컨텍스트 매니저를 약속하지 않으므로 실제 타입으로 좁힙니다.
        with cast(Cursor, conn.cursor()) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()  # 풀로 반납한다. 실제로 닫지 않는다


def healthy() -> bool:
    """`/health` 가 읽습니다. 실패를 예외로 올리지 않고 False 로 돌려줍니다."""
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def dispose_engine() -> None:
    """테스트와 종료 훅 전용. 풀을 닫고 캐시를 비웁니다."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
