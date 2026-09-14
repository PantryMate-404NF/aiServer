"""k-means cluster_id — 재정렬에 다양성 축을 만든다 (A-12).

    python -m features.recommend.ingest.cluster_build
    make cluster-build

`recipe_feature.cluster_id` 를 채웁니다. 이 값이 없으면 ③ 재정렬에 다양성 축이
**하나도** 없습니다 — 지금 46,353건 전부 NULL 이라 탐색이 균등 무작위로
폴백하고 있습니다.

## 설계대로의 k-means 는 지금 못 합니다

설계는 `content_emb`(768차 문장 임베딩) 위에서 k-means 를 돌리라고 합니다.
그런데 그것을 만드는 코드가 저장소에 없고 `sentence-transformers` 도
안 깔려 있습니다.

대체 축인 분류축도 죽어 있습니다 — `dish_type`·`cuisine`·`main_ing_cat` 이
실측 NOT NULL **0건**입니다.

그래서 **있는 것으로 만듭니다.**

    essential_ids 멀티핫(536차) → TF-IDF → SVD 48차
    ⊕ 중심화한 flavor_vec(6차)  → Lloyd K=50

`sklearn` 없이 numpy 만 씁니다. `cluster_version` 을 `v1-tfidf-svd48` 로 남겨
나중에 `content_emb` 판이 오면 구분되게 합니다.

## L2 정규화를 반드시 합니다

빼면 **클러스터가 '재료 개수'로 갈립니다.** 재료 12개짜리 레시피끼리 한
클러스터가 되는데, 균형 지표(최대 비중·빈 클러스터)는 오히려 좋아 보이고
다양성 지표도 올라갑니다. **숫자가 전부 초록인데 뜻이 없습니다.**

유일한 방어는 클러스터별 대표 제목을 눈으로 보는 것이라, 마지막에 함께
출력합니다.

## 판 번호를 반드시 남깁니다

`cluster_version` 을 안 남기고 다시 클러스터링하면 과거 로그의 `cluster_id` 가
다른 것을 가리키게 되어 Thompson belief 가 조용히 오염됩니다 (D-12).

## 같은 시드면 같은 결과입니다

초기 중심을 난수로 고르면 실행마다 배정이 바뀌어, 어제 로그의 클러스터와
오늘 것이 다른 뜻이 됩니다. 고정 시드 + `recipe_id` 순 입력으로 못박습니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from features.recommend.ingest.run_log import batch_run
from features.recommend.repository_ingest import (
    load_cluster_samples,
    load_cluster_source,
    load_cluster_stats,
    set_cluster_ids,
)

logger = logging.getLogger(__name__)

#: 클러스터 개수. 설계 5-3-5 가 K≈50 으로 정했습니다.
K = 50

#: SVD 로 줄일 차원. 536차 희소 벡터를 그대로 쓰면 거리가 전부 비슷해집니다
#: (차원의 저주). 48 은 설계가 적어 둔 값입니다.
SVD_DIM = 48

#: 난수 씨앗. 바뀌면 배정이 통째로 달라지므로 판 번호와 함께 고정합니다.
SEED = 20260914

#: 판 번호. content_emb 판이 오면 v2- 로 시작하게 합니다.
CLUSTER_VERSION = "v1-tfidf-svd48"

#: Lloyd 반복 상한과 수렴 기준.
#:
#: 주의: 실측으로 80회에서 수렴합니다(46,353건 · K=50). 상한을 60 으로 두면
#:    수렴 전에 잘리는데, 결과는 그럴듯하고 완료 기준도 전부 통과합니다 —
#:    잘렸다는 사실이 어디에도 안 남습니다. 넉넉히 두고 수렴했는지를 기록합니다.
MAX_ITER = 200
TOL = 1e-6

#: 완료 기준 — 한 클러스터가 이보다 크면 편중으로 봅니다.
MAX_SHARE = 0.15


@dataclass
class ClusterStats:
    recipes: int = 0
    assigned: int = 0
    n_clusters: int = 0
    max_share: float = 0.0
    iterations: int = 0
    converged: bool = False
    empty_filled: int = 0
    samples: dict[int, list[str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.assigned == self.recipes and self.n_clusters == K and self.max_share < MAX_SHARE

    def report(self) -> str:
        s = "\n".join(
            f"    {cid:>2}번  " + " · ".join(t[:18] for t in titles[:5])
            for cid, titles in sorted(self.samples.items())
        )
        return (
            f"레시피 {self.recipes:,}건 · 배정 {self.assigned:,}건 · "
            f"클러스터 {self.n_clusters}개 (목표 {K})\n"
            f"  최대 비중 {self.max_share:.3f} (기준 {MAX_SHARE} 미만) · "
            f"Lloyd {self.iterations}회 "
            f"{'수렴' if self.converged else '🔴 상한에서 잘림'} · "
            f"빈 클러스터 되살림 {self.empty_filled}회\n"
            f"  클러스터 10개의 대표 제목 — '재료 개수'로 갈리지 않았는지 봅니다\n{s}"
        )


def _tfidf(rows: Sequence[tuple[int, list[int], list[float]]], n_ing: int) -> np.ndarray:
    """essential_ids 멀티핫 → TF-IDF → L2 정규화.

    주의: L2 정규화가 핵심입니다. 빼면 재료가 많은 레시피의 벡터가 길어져
       '재료 개수' 로 군집이 갈립니다 — 지표는 좋아 보이고 뜻은 없습니다.
    """
    n = len(rows)
    m = np.zeros((n, n_ing), dtype=np.float32)
    for i, (_rid, ess, _fv) in enumerate(rows):
        for j in ess or ():
            if 0 <= j - 1 < n_ing:
                m[i, j - 1] = 1.0

    df = m.sum(0)
    idf = np.log((1.0 + n) / (1.0 + df)) + 1.0
    m *= idf
    norm = np.linalg.norm(m, axis=1, keepdims=True)
    out: np.ndarray = m / np.maximum(norm, 1e-9)
    return out


def _svd(m: np.ndarray, dim: int) -> np.ndarray:
    """상위 특이벡터로 투영. 희소 536차를 dim 차로 줄입니다."""
    # 주의: full_matrices=False 라야 46,353×536 에서 메모리가 터지지 않습니다.
    _u, s, vt = np.linalg.svd(m, full_matrices=False)
    d = min(dim, len(s))
    out: np.ndarray = m @ vt[:d].T
    return out


def _lloyd(x: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, int, bool, int]:
    """Lloyd k-means. (배정, 반복 횟수, 수렴 여부, 빈 클러스터 되살림 횟수)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    # k-means++ 초기화. 무작위 k개를 고르면 가까운 것끼리 뽑혀 빈 클러스터가
    # 많아지고, 그만큼 되살리기가 잦아져 결과가 시드에 더 민감해집니다.
    centers = np.empty((k, x.shape[1]), dtype=x.dtype)
    centers[0] = x[rng.integers(n)]
    d2 = ((x - centers[0]) ** 2).sum(1)
    for i in range(1, k):
        p = d2 / max(d2.sum(), 1e-12)
        centers[i] = x[rng.choice(n, p=p)]
        d2 = np.minimum(d2, ((x - centers[i]) ** 2).sum(1))

    xx = (x**2).sum(1)
    labels = np.zeros(n, dtype=np.int32)
    empty_filled = 0
    converged = False
    it = 0
    for it in range(1, MAX_ITER + 1):
        # (n,k) 거리를 행렬곱으로 냅니다. 브로드캐스트로 (n,k,d) 를 만들면
        # 46,353×50×54×4바이트 = 0.5GB 가 반복마다 잡힙니다.
        # ‖a−b‖² = ‖a‖² − 2a·b + ‖b‖² 는 (n,k) 9MB 로 끝납니다.
        dist = xx[:, None] - 2.0 * (x @ centers.T) + (centers**2).sum(1)[None, :]
        new = dist.argmin(1).astype(np.int32)
        if it > 1 and np.array_equal(new, labels):
            labels = new
            converged = True
            break
        labels = new
        shift = 0.0
        for c in range(k):
            mask = labels == c
            if not mask.any():
                # 빈 클러스터는 가장 먼 점으로 되살립니다. 그냥 두면
                # count(DISTINCT cluster_id) 가 50 미만이 되어 완료 기준을
                # 못 맞추고, 다양성 슬롯도 그만큼 줄어듭니다.
                far = dist.min(1).argmax()
                centers[c] = x[far]
                empty_filled += 1
                continue
            nc = x[mask].mean(0)
            shift = max(shift, float(np.linalg.norm(nc - centers[c])))
            centers[c] = nc
        if shift < TOL:
            converged = True
            break
    return labels, it, converged, empty_filled


def build(n_ingredients: int = 536) -> ClusterStats:
    """클러스터를 다시 매긴다. 같은 시드면 같은 결과가 나온다."""
    rows = load_cluster_source()
    st = ClusterStats(recipes=len(rows))
    if not rows:
        return st

    tf = _tfidf(rows, n_ingredients)
    red = _svd(tf, SVD_DIM)

    # 맛 6축을 덧붙입니다. 중심화해서 코퍼스 평균 방향이 군집을 만들지 않게
    # 합니다 — 안 빼면 모든 레시피가 '한식' 방향으로 몰립니다.
    fv = np.asarray([r[2] for r in rows], dtype=np.float32)
    fv = fv - fv.mean(0)
    # 두 블록의 크기를 맞춥니다. TF-IDF 쪽은 L2 로 1 근처인데 맛은 0.5 안팎이라
    # 그대로 붙이면 재료가 맛을 압도합니다.
    fn = np.linalg.norm(fv, axis=1, keepdims=True)
    fv = fv / np.maximum(fn.mean(), 1e-9)
    x = np.hstack([red, fv]).astype(np.float32)

    labels, st.iterations, st.converged, st.empty_filled = _lloyd(x, K, SEED)
    pairs = [(int(rows[i][0]), int(labels[i])) for i in range(len(rows))]
    set_cluster_ids(pairs, CLUSTER_VERSION)

    st.recipes, st.assigned, st.n_clusters, st.max_share = load_cluster_stats()
    # 고르게 퍼진 10개를 봅니다. 앞에서 10개만 보면 한쪽에 쏠린 것을 놓칩니다.
    picked = sorted({round(i * (K - 1) / 9) for i in range(10)})
    for cid, title in load_cluster_samples(picked):
        st.samples.setdefault(cid, []).append(title)
    return st


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="k-means cluster_id (A-12)").parse_args(argv)

    with batch_run("cluster", {"k": K, "seed": SEED, "version": CLUSTER_VERSION}) as rl:
        st = build()
        rl.input_count = st.recipes
        rl.output_count = st.assigned
        rl.params["max_share"] = round(st.max_share, 4)
        rl.params["iterations"] = st.iterations
        rl.params["converged"] = st.converged

    logger.info("%s", "─" * 60)
    logger.info("%s", st.report())
    if st.passed:
        logger.info("통과 — 판 번호 %s", CLUSTER_VERSION)
        return 0
    logger.error(
        "미달 — 배정 %s/%s · 클러스터 %d개(목표 %d) · 최대 비중 %.3f(기준 %s 미만) · 수렴 %s",
        f"{st.assigned:,}",
        f"{st.recipes:,}",
        st.n_clusters,
        K,
        st.max_share,
        MAX_SHARE,
        st.converged,
    )
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
