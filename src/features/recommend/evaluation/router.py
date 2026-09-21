"""관측의 HTTP 표면 — 요약(JSON)과 관리자 페이지(HTML).

라우터가 둘입니다. 요약은 내부 키가 있어야 읽히고, 페이지는 열려 있습니다. 브라우저는 주소창으로
들어올 때 헤더를 붙일 수 없어서입니다. 페이지는 빈 껍데기이고 숫자는 요약에서만 나옵니다 — 키를
넣기 전에는 아무것도 보이지 않습니다.

`/metrics` 는 여기 없습니다. 영수증 기능의 요청 지표도 함께 나가는 앱 전체의 것이라 `main.py` 에
있습니다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from features.recommend.evaluation import diagnosis
from utils.metrics import REGISTRY

_PAGE = Path(__file__).with_name("admin.html")

router = APIRouter(tags=["admin"])
page_router = APIRouter(include_in_schema=False)


@router.get("/v1/admin/monitoring/summary", response_model=diagnosis.MonitoringSummary)
def monitoring_summary() -> diagnosis.MonitoringSummary:
    """지금까지 쌓인 지표의 요약과 할 일. 이 프로세스가 뜬 뒤의 누적입니다."""
    return diagnosis.summarize(diagnosis.snapshot(REGISTRY))


@page_router.get("/admin/monitoring", response_class=HTMLResponse)
def monitoring_page() -> HTMLResponse:
    """관리자 페이지. 정적 파일 하나이고 바깥 자원을 부르지 않습니다."""
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))
