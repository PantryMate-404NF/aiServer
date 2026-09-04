"""모든 테이블이 등록되는 유일한 수집 지점.

각 기능의 tables.py 를 여기서 import 해야 하나의 MetaData 에 모입니다.
기능을 추가하면 아래 형태로 import 한 줄을 더하고 `__all__` 에 이름을 넣습니다.

    from features.receipt import tables as receipt_tables
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 테이블이 상속하는 선언적 베이스."""


# 기능별 테이블 모듈 import 는 여기에 둡니다. 위 docstring 의 형태를 따릅니다.

metadata = Base.metadata

__all__ = ["Base", "metadata"]
