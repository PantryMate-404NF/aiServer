"""요청 단위 자원 조립과 주입. 자원의 최초 생성은 여기서 하지 않습니다."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from config import get_settings

INTERNAL_API_KEY_HEADER = "X-Internal-Api-Key"


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
