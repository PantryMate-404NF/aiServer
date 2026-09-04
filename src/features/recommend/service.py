"""흐름 조립과 로그 쓰기 계측 (04 3-1).

    from features.recommend.service import bump, counters, reset_counters

## 로그 실패가 추천을 실패시키지 않습니다

그렇다고 조용히 삼키면 데이터가 비어가는 것을 아무도 모릅니다. 그래서
**삼키되 반드시 셉니다** — `counters()` 가 대시보드와 `/health` 로 나갑니다.

## `stage_trace.totals.degraded` 에 얹지 않습니다

두 가지 이유입니다.

  1. `degraded` 는 `stage_trace` 안에 있고 `stage_trace` 는 그 INSERT 로만
     저장됩니다. 쓰기가 실패하면 `degraded=true` 를 담은 행도 같이 사라집니다 —
     **실패를 실패한 것 안에 기록할 수는 없습니다.**
  2. `degraded` 의 정의는 "폴백 경로를 탔다" 이고 폴백율 대시보드의 분자입니다.
     로깅 결함을 섞으면 두 신호가 한 칸에서 합쳐져 다시 못 나눕니다.

그래서 프로세스 메모리에 세고 밖으로 노출합니다.

⬜ 스테이지를 잇는 실제 흐름(① Retrieval → ② Ranking → ③ Re-ranking)은 아직
   `engine/mock.py` 가 들고 있습니다. 각 스테이지가 구현되는 시점에 이 파일이
   호출 순서를 가져갑니다 — 02 의 3.2 가 흐름 조립을 여기로 정해 두었습니다.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_C: dict[str, int] = {}


def bump(key: str, n: int = 1) -> None:
    with _LOCK:
        _C[key] = _C.get(key, 0) + n


def counters() -> dict[str, int]:
    """현재 카운터 스냅샷. `/health` 와 대시보드가 읽습니다.

    프로세스 메모리입니다 — 재시작하면 0 이 됩니다. 유실을 **누적 총계**로
    보려면 대시보드가 주기적으로 긁어 시계열로 쌓아야 합니다.
    """
    with _LOCK:
        return dict(_C)


def reset_counters() -> None:
    """테스트 전용."""
    with _LOCK:
        _C.clear()
