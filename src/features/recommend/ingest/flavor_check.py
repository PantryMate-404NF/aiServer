"""flavor_vec 검증기 — 중심화가 실제로 낫다는 것을 증명한다 (설계 2-5-1 ⑤, A-5).

    python -m features.recommend.ingest.flavor_check
    make flavor-check

세 숫자를 냅니다.

    ① 원본 코사인 평균 vs 무작위 대조군    비음수 벡터끼리는 뭉치는가
    ② 중심화 후 퍼짐                      빼면 흩어지는가
    ③ 판별력                              제목 라벨을 실제로 가르는가   ← 게이트

## 게이트는 ③ 하나입니다

①②는 기하학적 성질일 뿐 "맛을 제대로 담았나" 를 말해 주지 않습니다. 무작위
벡터도 중심화하면 퍼집니다. 뜻이 있는 것은 **같은 라벨끼리 더 닮았는가** 이고,
그것만 게이트로 씁니다.

`매움`·`단맛` 두 라벨에서 중심화 격차가 원본 격차보다 커야 통과입니다.

`신선` 은 참고로만 찍습니다. 라벨 자체가 노이즈라(샐러드·겉절이가 꼭 신맛은
아닙니다) 게이트에 넣으면 멀쩡한 배치가 빨간불이 됩니다.

## 중심화는 여기서만 합니다

`recipe_feature.flavor_vec` 에는 원값이 들어 있습니다. 검증기가 읽어와서 μ 를
뺄 뿐, DB 를 고치지 않습니다. 빼기는 스코어러가 `flavor_vec` 과 유저
`taste_vec` 양쪽에 같은 μ 로 합니다.

## 강도 보정을 켠 근거

설계 2-5-1 은 강도 보정을 기각했습니다 — 표본 3건에서 코사인이 0.747 에서
0.867 로 나빠졌기 때문입니다. 다만 같은 문단이 "표본 3건으로 집계 방식을 정하는
것 자체가 위험이라 크롤 30~50건이 온 뒤로 미룬다" 고 적었습니다.

46,353건으로 다시 재보니 판별력 기준에서는 강도 보정이 낫습니다
(12,000건 표본 · 중심화 격차): 매움 0.179 → 0.206 · 신선 0.140 → 0.177 ·
단맛 0.111 → 0.106. 셋 중 둘이 좋아지고 하나가 거의 같습니다. 코사인 차이도
0.728 대 0.753 으로, 3건일 때의 0.12 가 아니라 0.025 입니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from features.recommend.ingest.flavor import N_AXIS
from features.recommend.repository_ingest import load_flavor_with_titles

logger = logging.getLogger(__name__)

#: 제목에 이 말이 있으면 그 라벨로 봅니다. 정답이 아니라 약한 신호입니다 —
#: 사람이 붙인 이름이라 노이즈가 섞입니다.
LABELS: dict[str, tuple[str, ...]] = {
    "매움": ("매운", "매콤", "불닭", "청양", "얼큰", "칼칼"),
    "단맛": ("달콤", "달달", "꿀", "시럽", "캐러멜", "디저트"),
    "신선": ("샐러드", "생채", "겉절이", "물김치", "냉채"),
}

#: 게이트로 쓰는 라벨. 신선은 노이즈라 뺍니다 (위 독스트링).
GATE_LABELS = ("매움", "단맛")

#: 라벨 표본이 이보다 적으면 판정하지 않습니다. 적은 표본의 격차는 흔들립니다.
MIN_LABEL = 30

#: 난수 씨앗. 무작위 대조군이 실행마다 달라지면 게이트가 흔들립니다.
SEED = 20260910


@dataclass
class LabelResult:
    name: str
    n: int
    raw_gap: float = 0.0
    centered_gap: float = 0.0
    skipped: bool = False

    @property
    def improved(self) -> bool:
        return self.centered_gap > self.raw_gap

    @property
    def ratio(self) -> float:
        return self.centered_gap / self.raw_gap if self.raw_gap else 0.0


@dataclass
class CheckResult:
    n_recipes: int = 0
    cos_ours: float = 0.0
    cos_random: float = 0.0
    spread_ours: float = 0.0
    spread_random: float = 0.0
    labels: list[LabelResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        gated = [x for x in self.labels if x.name in GATE_LABELS and not x.skipped]
        return bool(gated) and all(x.improved for x in gated)


def _unit(v: np.ndarray) -> np.ndarray:
    """길이를 1 로 맞춘다. 0 벡터가 섞여도 나눗셈이 터지지 않게 1e-9 를 더한다."""
    out: np.ndarray = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
    return out


def _mean_cos(v: np.ndarray) -> float:
    s = _unit(v) @ _unit(v).T
    return float(s[np.triu_indices(len(v), 1)].mean())


def _spread(v: np.ndarray) -> float:
    """단위벡터 합의 크기. 1 에 가까우면 한 방향에 뭉친 것입니다."""
    return float(np.linalg.norm(_unit(v).mean(0)))


def _gap(v: np.ndarray, idx: Sequence[int]) -> float:
    """같은 라벨끼리의 유사도 − 다른 것과의 유사도. 클수록 잘 가릅니다."""
    s = _unit(v) @ _unit(v).T
    mask = np.zeros(len(v), dtype=bool)
    mask[list(idx)] = True
    within = s[np.ix_(mask, mask)][np.triu_indices(int(mask.sum()), 1)].mean()
    between = s[np.ix_(mask, ~mask)].mean()
    return float(within - between)


def check(sample: int = 12000) -> CheckResult:
    """DB 의 flavor_vec 을 읽어 세 숫자를 낸다.

    Args:
        sample: 쌍 계산이 표본 수의 제곱이라 상한을 둡니다. 12,000건이면
            7,200만 쌍이고 몇 초입니다. 전량 46,353건은 10억 쌍입니다.
    """
    rows = [(rid, title or "", vec) for rid, title, vec in load_flavor_with_titles()]
    rows = [r for r in rows if any(r[2])]
    rows = rows[:sample]
    res = CheckResult(n_recipes=len(rows))
    if not rows:
        return res

    v = np.asarray([r[2] for r in rows], dtype=float)
    titles = [r[1] for r in rows]

    # ── ① 뭉치는가. 무작위 대조군과 나란히 본다 ─────────────
    rng = np.random.default_rng(SEED)
    # 실제 분포를 흉내낸다 — 대부분 낮고 일부만 높다 (설계 2-5-1 의 관찰)
    rand = rng.beta(1.2, 7.0, (len(v), N_AXIS))
    res.cos_ours = _mean_cos(v)
    res.cos_random = _mean_cos(rand)

    # ── ② 중심화하면 퍼지는가 ────────────────────────────
    vc = v - v.mean(0)
    res.spread_ours = _spread(vc)
    res.spread_random = _spread(rand - rand.mean(0))

    # ── ③ 판별력. 게이트는 이것뿐이다 ──────────────────────
    for name, kws in LABELS.items():
        idx = [i for i, t in enumerate(titles) if any(k in t for k in kws)]
        if len(idx) < MIN_LABEL:
            res.labels.append(LabelResult(name, len(idx), skipped=True))
            continue
        res.labels.append(
            LabelResult(name, len(idx), raw_gap=_gap(v, idx), centered_gap=_gap(vc, idx))
        )
    return res


def _report(res: CheckResult) -> None:
    logger.info("레시피 %s건 (6축이 전부 0 인 것은 뺐습니다)\n", f"{res.n_recipes:,}")

    logger.info("① 원본 코사인 평균 — 낮을수록 잘 갈린다")
    logger.info("     우리 %.3f  ·  무작위 대조군 %.3f", res.cos_ours, res.cos_random)
    logger.info("     비음수 벡터끼리는 무작위여도 뭉칩니다. 이 숫자만으로는 판정하지 않습니다.\n")

    logger.info("② 중심화 후 퍼짐 — 1 에 가까우면 한 방향에 뭉친 것")
    logger.info("     우리 %.3f  ·  무작위 %.3f\n", res.spread_ours, res.spread_random)

    logger.info("③ 판별력 — 같은 라벨끼리 더 닮은가 (게이트)")
    logger.info("     %-6s %6s %10s %12s %8s", "라벨", "건수", "원본", "중심화", "배수")
    for x in res.labels:
        gate = " (게이트)" if x.name in GATE_LABELS else " (참고)"
        if x.skipped:
            logger.info("     %-6s %6d   표본 부족 (%d 미만)%s", x.name, x.n, MIN_LABEL, gate)
            continue
        mark = "통과" if x.improved else "미달"
        logger.info(
            "     %-6s %6d %10.3f %12.3f %7.1fx  %s%s",
            x.name,
            x.n,
            x.raw_gap,
            x.centered_gap,
            x.ratio,
            mark,
            gate,
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="flavor_vec 검증기 (설계 2-5-1 ⑤, A-5)")
    ap.add_argument("--sample", type=int, default=12000, help="쌍 계산 표본 상한")
    a = ap.parse_args(argv)

    res = check(sample=a.sample)
    if not res.n_recipes:
        logger.error("flavor_vec 이 비어 있습니다 — flavor_build 를 먼저 돌리십시오")
        return 1

    _report(res)
    logger.info("%s", "─" * 60)
    if res.passed:
        logger.info("통과 — 매움·단맛 모두 중심화가 원본보다 잘 가릅니다")
        return 0
    bad = [x.name for x in res.labels if x.name in GATE_LABELS and not x.skipped and not x.improved]
    logger.error("미달 — %s 에서 중심화가 원본보다 못합니다", " · ".join(bad) or "표본 부족")
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
