"""테이블 메타데이터 수집 지점."""

from __future__ import annotations

from infra.metadata import Base, metadata


def test_metadata_is_the_declarative_base_metadata() -> None:
    assert metadata is Base.metadata
