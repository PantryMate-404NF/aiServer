"""추천 도메인의 입출력 경계. 요청 파싱과 응답 직렬화만 담당합니다."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/recommendations", tags=["recommend"])
