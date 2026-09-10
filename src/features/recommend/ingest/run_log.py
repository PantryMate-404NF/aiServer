"""배치 실행 기록 — batch_run 행을 running 에서 success/failed 로 닫는다 (A-7, D-1).

    from features.recommend.ingest.run_log import batch_run

    with batch_run("normalize", {"limit": None}) as run:
        st = ...
        run.input_count = st.raw_rows
        run.output_count = st.written

## 실패를 반드시 남깁니다

예외를 삼키고 `success` 로 닫는 것이 이 프로젝트가 가장 경계하는 형태입니다.
실제로 `coverage.py` 가 `--min` 없이는 항상 0 을 반환해 커버리지가 떨어져도 CI 가
계속 초록이었던 전례가 있습니다.

그래서 여기서는 **예외를 잡아 기록하고 그대로 다시 올립니다.** 삼키지 않습니다.
`BaseException` 까지 잡는 이유는 Ctrl+C 로 중단한 것도 실패한 실행이기 때문입니다 —
`running` 인 채로 영영 남는 것보다 낫습니다.

## DB 가 죽으면 아무것도 못 남깁니다

정직하게 적어 둡니다. 기록도 같은 DB 에 하므로, DB 자체가 안 뜨면 `running` 행조차
못 만듭니다. 그건 결함이 아니라 구조상 당연한 한계입니다.

**대신 닫히지 않은 `running` 행이 그 사고의 흔적입니다.** 프로세스가 통째로 죽으면
(OOM·강제 종료·DB 단절) 행이 `running` 으로 남습니다. `make batch-log` 가 그것을
보여 주므로, `running` 인데 오래된 행이 보이면 그 실행은 끝나지 못한 것입니다.

## params 에 재현 정보를 남깁니다

같은 코드라도 코퍼스가 바뀌면 결과가 달라지는 배치가 있습니다 — popularity 의
백분위 순위가 그렇습니다. 몇 건 기준이었는지를 `params` 에 남기지 않으면 나중에
아무도 재현하지 못합니다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from features.recommend.repository import finish_batch_run, start_batch_run

logger = logging.getLogger(__name__)

#: error_msg 에 넣는 상한. 전체 트레이스백을 넣으면 행이 수십 KB 가 되고,
#: 정작 읽을 때는 첫 줄만 봅니다.
MAX_ERROR = 2000


@dataclass
class RunHandle:
    """실행 중에 채워 넣는 값들. 끝날 때 batch_run 행에 실립니다."""

    run_id: int
    input_count: int | None = None
    output_count: int | None = None
    #: 재현에 필요한 것을 담습니다. 코퍼스 크기·임계값·모드 같은 것입니다.
    params: dict[str, Any] = field(default_factory=dict)


def _dump(params: dict[str, Any]) -> str | None:
    return json.dumps(params, ensure_ascii=False) if params else None


@contextmanager
def batch_run(job_name: str, params: dict[str, Any] | None = None) -> Iterator[RunHandle]:
    """배치 하나를 기록한다. 성공이든 실패든 반드시 닫는다.

    Args:
        job_name: `normalize` · `feature` · `flavor` · `popularity` 중 하나.
        params: 시작 시점에 아는 것. 실행 중에 `run.params` 로 더 넣을 수 있습니다.
    """
    run = RunHandle(
        run_id=start_batch_run(job_name, _dump(params or {})), params=dict(params or {})
    )
    logger.info("batch_run %d 시작 — %s", run.run_id, job_name)
    try:
        yield run
    except BaseException as exc:
        # 주의: 삼키지 않는다. 기록만 하고 그대로 올린다. 여기서 예외를 먹으면
        #    호출자는 성공한 줄 알고, batch_run 만 failed 로 남아 둘이 어긋난다.
        finish_batch_run(
            run.run_id,
            "failed",
            run.input_count,
            run.output_count,
            f"{type(exc).__name__}: {exc}"[:MAX_ERROR],
            _dump(run.params),
        )
        logger.error("batch_run %d 실패 — %s: %s", run.run_id, type(exc).__name__, exc)
        raise
    finish_batch_run(
        run.run_id, "success", run.input_count, run.output_count, None, _dump(run.params)
    )
    logger.info(
        "batch_run %d 성공 — 입력 %s · 출력 %s",
        run.run_id,
        f"{run.input_count:,}" if run.input_count is not None else "-",
        f"{run.output_count:,}" if run.output_count is not None else "-",
    )
