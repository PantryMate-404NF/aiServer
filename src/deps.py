"""요청 단위 자원 조립과 주입. 자원의 최초 생성은 여기서 하지 않습니다."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from config import get_settings

INTERNAL_API_KEY_HEADER = "X-Internal-Api-Key"
BEARER_PREFIX = "Bearer "


def verify_internal_api_key(
    x_internal_api_key: str = Header(default="", alias=INTERNAL_API_KEY_HEADER),
) -> None:
    """내부 호출자를 확인합니다. 키가 없거나 다르면 둘 다 401 입니다.

    사용자에게 보일 문구를 담지 않습니다. 이 경계를 넘는 요청은 백엔드가 보낸 것이고,
    실패는 배포 설정 문제이지 사용자가 고칠 수 있는 것이 아닙니다.
    """
    expected = get_settings().internal_api_key
    if not secrets.compare_digest(x_internal_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def verify_scraper_key(
    x_internal_api_key: str = Header(default="", alias=INTERNAL_API_KEY_HEADER),
    authorization: str = Header(default=""),
) -> None:
    """지표 수집기(`/metrics`)를 확인합니다. 같은 내부 키를 두 가지 헤더로 받습니다.

    Prometheus 가 수집 요청에 임의의 헤더를 붙이는 설정은 판마다 다릅니다. 어느 판에나 있는
    `authorization: Bearer` 를 함께 받아 수집기의 판에 묶이지 않게 합니다. 키는 하나입니다.
    """
    expected = get_settings().internal_api_key
    # 접두어가 없으면 Bearer 가 아닙니다. 접두어만 떼고 보면 키를 아무 모양으로 보내도 통과합니다.
    is_bearer = authorization.startswith(BEARER_PREFIX)
    bearer = authorization.removeprefix(BEARER_PREFIX) if is_bearer else ""
    presented = x_internal_api_key or bearer
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
