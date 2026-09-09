"""추천 도메인의 공개 API.

02 의 7.3 — **이 프로젝트의 유일한 배럴입니다.** 다른 도메인은 여기만 봅니다.
같은 도메인 안에서는 배럴을 경유하지 않고 모듈을 직접 import 합니다.

    from features.recommend import RecommendResponse, recommend_router
"""

from __future__ import annotations

from features.recommend.enums import CONTRACT_VERSION
from features.recommend.router import router
from features.recommend.schema import RecommendRequest, RecommendResponse
from features.recommend.service import counters

__all__ = [
    "CONTRACT_VERSION",
    "RecommendRequest",
    "RecommendResponse",
    "counters",
    "router",
]
