"""재정규화 원커맨드 — 검수 결과가 반영되는 경로를 한 줄로 (A-11).

    python -m features.recommend.ingest.renormalize
    python -m features.recommend.ingest.renormalize --sheet ~/Downloads/받은파일.csv
    make renormalize [SHEET=...]

검수로 사전이 바뀌면 그 아래가 전부 다시 만들어져야 합니다. 순서는 이렇습니다.

    검수 반영 → 시드 검증 → DB 사전 갱신 → 정규화 배치 → 피처 →
    맛 벡터와 μ → 재료 빈도 → 클러스터 → 회귀 게이트 → 전후 커버리지

## 왜 하나로 묶나

`recipe_ingredient` 를 비우고 다시 만들었는데 `recipe_feature` 를 안 만들면,
조회가 읽는 유일한 테이블이 옛 배열 그대로라 **화면은 멀쩡히 돕니다.** 두
테이블이 어긋난 사실을 어떤 쿼리도 알려주지 않습니다.

그래서 단계를 나누지 않습니다 — 사람이 반만 실행할 수 없게 합니다.

## 커버리지가 떨어지면 실패로 봅니다

검수는 커버리지를 올리려고 하는 일입니다. 떨어졌다면 시트에 잘못된 매핑이
들어갔다는 뜻이므로 `exit 1` 로 멈춥니다. 아무것도 안 채운 시트로 돌리면
커버리지가 그대로이고 `exit 0` 입니다.

"전" 값은 `batch_run` 에 남은 직전 **전량** 실행의 커버리지입니다. 부분
실행(`--limit`)은 뺍니다 — 2,000건 값과 46,353건 값을 나란히 놓으면 오르내림이
검수 때문인지 표본 때문인지 알 수 없습니다.

## 검수 반영은 선택입니다

`--sheet` 를 주면 그 시트를 먼저 시드에 반영하고, 안 주면 이미 반영된 시드로
다시 만들기만 합니다. 시트를 채우는 중에 돌려도 안전합니다.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from features.recommend.ingest import (
    batch,
    cluster_build,
    feature_build,
    feature_test,
    flavor_build,
    freq_build,
)
from features.recommend.repository_ingest import load_last_full_coverage

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[4]

#: 커버리지가 이보다 더 떨어지면 실패로 봅니다. 0 으로 두면 부동소수점
#: 끝자리 흔들림에도 빨개지므로 아주 작은 여유를 둡니다.
DROP_TOLERANCE = 0.0005


@dataclass
class RenormStats:
    before: float | None = None
    after: float = 0.0
    applied: bool = False
    seeded: bool = False
    recipes: int = 0
    rows: int = 0
    gate_failed: list[str] | None = None

    @property
    def delta(self) -> float:
        return self.after - (self.before or 0.0)

    @property
    def dropped(self) -> bool:
        return self.before is not None and self.delta < -DROP_TOLERANCE

    def report(self) -> str:
        b = f"{self.before:.4f}" if self.before is not None else "(기록 없음)"
        # 변화가 DROP_TOLERANCE 안이면 → 로 적는다. 부동소수점 끝자리 때문에
        # -0.0000 인데 ↓ 로 보이면 "떨어졌나" 하고 한 번 더 확인하게 된다.
        if self.before is None or abs(self.delta) <= DROP_TOLERANCE:
            arrow = "→"
        else:
            arrow = "↑" if self.delta > 0 else "↓"
        return (
            f"mention {b} {arrow} {self.after:.4f}"
            + (f"  ({self.delta:+.4f})" if self.before is not None else "")
            + f"\n  레시피 {self.recipes:,}건 · recipe_ingredient {self.rows:,}행"
        )


def _run(cmd: list[str], label: str) -> None:
    """하위 명령을 돌린다. 실패하면 그대로 올린다 — 반만 실행되면 안 된다."""
    logger.info("  ── %s", label)
    r = subprocess.run(cmd, cwd=ROOT, check=False)  # noqa: S603
    if r.returncode != 0:
        raise RuntimeError(f"{label} 이 실패했습니다 (exit {r.returncode})")


def run(sheet: Path | None = None) -> RenormStats:
    """검수 반영부터 게이트까지 한 번에. 중간에 실패하면 멈춘다."""
    st = RenormStats(before=load_last_full_coverage())
    py = str(ROOT / ".venv" / "bin" / "python")

    # ① 검수 결과를 시드에 반영한다 (시트를 준 경우에만)
    if sheet is not None:
        _run(
            [py, "scripts/reco/bench/review_apply.py", "--sheet", str(sheet), "--write"],
            f"검수 반영 — {sheet.name}",
        )
        st.applied = True

    # ② 시드가 성한지 본다. 여기서 막히면 아래를 돌리지 않는다 —
    #    깨진 사전으로 11분을 돌리는 것이 가장 아깝다.
    _run([py, "seeds/validate.py"], "시드 정합성")

    # ③ DB 사전을 갱신한다. 이걸 빼먹으면 재정규화가 옛 사전으로 돌아
    #    "검수했는데 별로 안 올랐네" 가 된다 — 에러가 아니라서 더 나쁘다.
    _run([py, "scripts/reco/migrate.py"], "DB 사전 갱신")
    st.seeded = True

    # ④ 정규화. recipe_feature 까지 이어서 만든다 (batch.main 이 묶어 놨다)
    logger.info("  ── 정규화 배치 (약 10분)")
    bs = batch.run(truncate=True)
    st.after, st.recipes, st.rows = bs.coverage, bs.recipes, bs.written
    fs = feature_build.build(unmatched=bs.unmatched_by_recipe, scope=bs.processed_ids)
    logger.info("     피처 %s건", f"{fs.recipes:,}")

    # ⑤ 맛 벡터와 μ. 사전이 바뀌면 재료 구성이 바뀌므로 다시 계산한다
    logger.info("  ── 맛 벡터와 코퍼스 평균")
    flavor_build.build(note="A-11 재정규화")

    # ⑥ 재료 빈도. f_cooccur 의 IDF 분모다
    logger.info("  ── 재료 빈도")
    freq_build.build()

    # ⑦ 클러스터. 재료 구성이 바뀌면 군집도 바뀐다. 안 다시 매기면 B 의
    #    재정렬이 옛 판 번호의 cluster_id 를 읽어 Thompson belief 가 어긋난다.
    logger.info("  ── 클러스터")
    cluster_build.build()

    # ⑧ 회귀 게이트
    logger.info("  ── 회귀 게이트")
    gate = feature_test.run_gate()
    st.gate_failed = [c.n for c in gate.failed] if not gate.passed else None
    return st


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="재정규화 원커맨드 (A-11)")
    ap.add_argument("--sheet", type=Path, help="검수 시트. 주면 먼저 시드에 반영한다")
    a = ap.parse_args(argv)

    if a.sheet is not None and not a.sheet.exists():
        logger.error("시트가 없습니다: %s", a.sheet)
        return 1

    st = run(sheet=a.sheet)
    logger.info("%s", "─" * 52)
    logger.info("%s", st.report())

    if st.gate_failed:
        logger.error("회귀 게이트 실패 — %s", " · ".join(st.gate_failed))
        return 1
    if st.dropped:
        # 주의: 떨어졌는데 통과시키면 아무도 안 본다. 검수가 잘못됐다는 뜻이다 —
        #    시트에서 엉뚱한 재료로 이은 항목을 찾아야 한다.
        logger.error(
            "커버리지가 떨어졌습니다 (%.4f) — 시트에 잘못된 매핑이 있는지 보십시오",
            st.delta,
        )
        return 1
    if st.before is not None and abs(st.delta) <= DROP_TOLERANCE:
        logger.info("커버리지 변화 없음 — 시트에 채운 것이 없거나 반영할 것이 없었습니다")
    logger.info("통과")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
