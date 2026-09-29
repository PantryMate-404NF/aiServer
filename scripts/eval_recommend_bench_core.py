"""추천 벤치마크의 재료 — 덤프 읽기 · 가상 사용자 · 후보 준비 · 지표 · 이유 충실도.

`scripts/eval_recommend_benchmark.py` 가 이것으로 돕니다. 둘을 나눈 선은 "무엇을 재는가"(여기)와
"어떤 순서로 돌려 보고서를 쓰는가"(벤치마크)입니다. 여기 함수는 인자로 받은 것만 씁니다.
"""

from __future__ import annotations

import csv
import math
import random
import re
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from features.recommend import service, serving  # noqa: E402
from features.recommend.backend_client import (  # noqa: E402
    BackendIngredient,
    BackendRecipe,
    BackendRecipeIngredient,
)
from features.recommend.engine import allergy, dish, history, rerank, retrieval, taste  # noqa: E402
from features.recommend.engine.catalog import Catalog  # noqa: E402
from features.recommend.engine.context import UserContext, build_context  # noqa: E402
from features.recommend.enums import ALLERGEN_LABELS, cuisine_label  # noqa: E402
from features.recommend.policy import RankingPolicy  # noqa: E402
from features.recommend.profile_store import (  # noqa: E402
    load_presented_names,
)
from features.recommend.schema import (  # noqa: E402
    RecommendPantryItem,
    RecommendRequest,
)
from features.recommend.stage import Candidate, RankedItem  # noqa: E402

CSV_DIR = ROOT / "data/Backend_Data_Dump/pantry_recipe_domain_dump/csv"
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=KST)
TOP_K = 20
AT_10 = 10
CUISINE_CHOICES = ("한식", "중식", "일식", "양식")
SYSTEMS = ("engine", "score_only", "coverage_only", "random", "naive_noallergy")
#: 재정렬을 전부 끈 정책. 점수 순 그대로가 나옵니다.
SCORE_ONLY = {
    "exploration_ratio": 0.0,
    "cold_exploration_ratio": 0.0,
    "mmr_lambda": 1.0,
    "cuisine_slot_ratio": 0.0,
    "max_per_dish": 0,
}


# ── 덤프 → 백엔드 모양 ────────────────────────────────────────────
def _rows(name: str) -> list[dict[str, str]]:
    with (CSV_DIR / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class DumpClient:
    """`BackendClient` 자리에 들어가는 덤프 읽기. 인기 · 후기는 덤프에 없어 None 으로 둡니다."""

    def close(self) -> None:
        return None

    def fetch_ingredients(self) -> list[BackendIngredient]:
        return [
            BackendIngredient(
                ingredient_id=int(row["ingredient_id"]),
                name=row["name"],
                category=row["category"] or None,
                default_shelf_life_days=(
                    int(row["default_shelf_life_days"]) if row["default_shelf_life_days"] else None
                ),
            )
            for row in _rows("ingredients.csv")
        ]

    def fetch_recipes(self, updated_after: datetime | None = None) -> list[BackendRecipe]:
        names = {int(r["ingredient_id"]): r["name"] for r in _rows("ingredients.csv")}
        parts: dict[int, list[BackendRecipeIngredient]] = {}
        for row in sorted(
            _rows("recipe_ingredients.csv"), key=lambda r: int(r["recipe_ingredient_id"])
        ):
            parts.setdefault(int(row["recipe_id"]), []).append(
                BackendRecipeIngredient(
                    ingredient_id=int(row["ingredient_id"]),
                    name=names.get(int(row["ingredient_id"])),
                    is_main=row["is_main"] == "t",
                    unit=row["unit"] or None,
                )
            )
        return [
            BackendRecipe(
                recipe_id=int(row["recipe_id"]),
                title=row["title"],
                cuisine_type=row["cuisine_type"],
                cooking_time=int(row["cooking_time"]),
                difficulty=row["difficulty"],
                ingredients=parts.get(int(row["recipe_id"]), []),
            )
            for row in _rows("recipes.csv")
        ]


# ── 가상 사용자 ───────────────────────────────────────────────────
@dataclass(frozen=True)
class SimUser:
    user_id: int
    pantry: tuple[RecommendPantryItem, ...]
    allergies: tuple[str, ...]
    picks: tuple[str, ...]
    scales: tuple[int, ...] | None
    cuisines: tuple[str, ...]
    max_minutes: int | None
    warm: bool

    @property
    def onboarded(self) -> bool:
        return bool(self.picks)


def make_users(cat: Catalog, count: int, rng: random.Random) -> list[SimUser]:
    """냉장고 · 알레르기 · 온보딩 · 이력 유무를 섞은 사용자. 비율은 아래 상수대로입니다."""
    usage = Counter({i: len(v) for i, v in cat.by_essential.items() if i not in cat.staple_ids})
    pool = [i for i, _ in usage.most_common(80)]
    weights = [usage[i] for i in pool]
    names = load_presented_names()
    labels = list(ALLERGEN_LABELS)
    users: list[SimUser] = []
    for n in range(count):
        size = rng.randint(4, 12)
        chosen = list(dict.fromkeys(rng.choices(pool, weights=weights, k=size * 2)))[:size]
        expiring = set(rng.sample(chosen, k=rng.randint(1, 2))) if rng.random() < 0.6 else set()
        pantry = tuple(
            RecommendPantryItem(
                ingredient_id=i,
                expires_at=(NOW.date() + timedelta(days=rng.randint(1, 3)))
                if i in expiring
                else None,
            )
            for i in chosen
        )
        roll = rng.random()
        allergies = () if roll < 0.4 else tuple(rng.sample(labels, k=1 if roll < 0.85 else 2))
        onboarded = rng.random() < 0.8
        users.append(
            SimUser(
                user_id=100_000 + n,
                pantry=pantry,
                allergies=allergies,
                picks=tuple(rng.sample(names, k=3)) if onboarded else (),
                scales=tuple(rng.randint(0, 4) for _ in range(3)) if onboarded else None,
                cuisines=tuple(rng.sample(CUISINE_CHOICES, k=rng.randint(0, 2)))
                if onboarded
                else (),
                max_minutes=None if rng.random() < 0.5 else rng.choice((30, 45, 60)),
                warm=onboarded and rng.random() < 0.375,
            )
        )
    return users


def request_of(user: SimUser) -> RecommendRequest:
    return RecommendRequest(
        user_id=user.user_id,
        pantry=list(user.pantry),
        allergies=list(user.allergies),
        top_k=TOP_K,
        max_minutes=user.max_minutes,
        include_trace=True,
    )


# ── 후보와 기준선 ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Prepared:
    ctx: UserContext
    resolution: allergy.AllergyResolution
    found: retrieval.Retrieved
    by_id: Mapping[int, Candidate]


def prepare(
    personas: service.PersonaService,
    cat: Catalog,
    policy: RankingPolicy,
    req: RecommendRequest,
    recent_served: Iterable[int] = (),
) -> Prepared:
    """운영 `LiveServing.recommend` 와 같은 순서로 문맥과 후보를 만듭니다.

    `recent_served` 는 앞 세션에서 보여 준 목록입니다. 비우면 첫 방문, 채우면 재방문의 문맥입니다.
    """
    resolution = allergy.resolve(req.allergies, cat.corpus.ingredient_names, cat.allergen_groups)
    own = [
        item.ingredient_id
        for item in req.pantry
        if item.ingredient_id in cat.corpus.ingredient_names
    ]
    profile = personas.profile_for(req.user_id)
    events = () if profile is None else profile.events
    ctx = build_context(
        user_id=req.user_id,
        persona=personas.persona_from(profile, NOW),
        pantry_ids=sorted(set(own) | cat.staple_ids),
        own_pantry_ids=sorted(set(own)),
        expiring_ids=serving.expiring_ingredients(req.pantry, cat.shelf_life_days, NOW.date()),
        max_cook_minutes=req.max_minutes,
        history=history.build_history(
            events, cat.recipes, cat.staple_ids, NOW, policy, recent_served=recent_served
        ),
    )
    ladder = replace(policy, max_missing=req.max_missing)
    found = retrieval.retrieve(
        cat, ctx, resolution, ladder, req.top_k, rerank.exploration_ratio(ctx, policy)
    )
    return Prepared(ctx, resolution, found, {c.recipe_id: c for c in found.candidates})


def coverage_first(candidates: Sequence[Candidate], k: int) -> list[int]:
    ranked = sorted(candidates, key=lambda c: (-c.coverage, c.missing_count, c.recipe_id))
    return [c.recipe_id for c in ranked[:k]]


# ── 지표 ──────────────────────────────────────────────────────────
def jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def replaced(before: Iterable[int], after: Iterable[int]) -> float:
    """앞 목록의 몇 할이 뒤 목록에서 사라졌는가. 자카드 거리보다 "몇 개가 바뀌었나" 에 가깝습니다.

    20건 중 4건이 바뀌면 자카드 거리는 1 - 16/24 = 0.33 이지만 이 값은 0.2 입니다.
    """
    first = list(before)
    if not first:
        return 0.0
    return len(set(first) - set(after)) / len(first)


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(statistics.fmean(items), 4) if items else None


def gini(counts: Sequence[int]) -> float | None:
    xs = sorted(counts)
    total = sum(xs)
    if not xs or total == 0:
        return None
    n = len(xs)
    return round((2 * sum(i * x for i, x in enumerate(xs, 1))) / (n * total) - (n + 1) / n, 4)


def entropy(counter: Counter[str]) -> float | None:
    total = sum(counter.values())
    if total == 0:
        return None
    return round(-sum((c / total) * math.log2(c / total) for c in counter.values() if c), 4)


@dataclass
class Tally:
    """시스템 하나의 사용자별 지표를 모읍니다. 평균은 마지막에 한 번 냅니다."""

    leak: list[float]
    time_violation: list[float]
    coverage10: list[float]
    missing10: list[float]
    pantry_use10: list[float]
    expiring_hit5: list[float]
    taste10: list[float]
    cuisine_match: list[float]
    ild10: list[float]
    dish_distinct10: list[float]
    cuisine_entropy: list[float]
    exposure: Counter[int]

    @classmethod
    def empty(cls) -> Tally:
        return cls(*([] for _ in range(11)), Counter())  # type: ignore[arg-type]

    def summary(self, catalog_size: int) -> dict[str, float | None]:
        exposed = list(self.exposure.values()) + [0] * (catalog_size - len(self.exposure))
        return {
            "allergen_leak_rate": mean(self.leak),
            "time_violation_rate": mean(self.time_violation),
            "coverage@10": mean(self.coverage10),
            "missing@10": mean(self.missing10),
            "pantry_use@10": mean(self.pantry_use10),
            "expiring_hit@5": mean(self.expiring_hit5),
            "taste_align@10": mean(self.taste10),
            "cuisine_match@20": mean(self.cuisine_match),
            "ild@10": mean(self.ild10),
            "dish_distinct@10": mean(self.dish_distinct10),
            "cuisine_entropy@20": mean(self.cuisine_entropy),
            "catalog_coverage": round(len(self.exposure) / catalog_size, 4),
            "exposure_gini": gini(exposed),
        }


def measure(
    tally: Tally, ids: Sequence[int], prep: Prepared, cat: Catalog, mains: frozenset[str]
) -> None:
    recipes = [cat.recipes[i] for i in ids]
    top10 = recipes[:AT_10]
    ctx = prep.ctx
    tally.leak.append(
        mean(1.0 if allergy.blocks(prep.resolution, r.all_ids, r.title) else 0.0 for r in recipes)
        or 0.0
    )
    if ctx.max_cook_minutes is not None:
        tally.time_violation.append(
            mean(
                1.0 if r.cook_minutes is not None and r.cook_minutes > ctx.max_cook_minutes else 0.0
                for r in recipes
            )
            or 0.0
        )
    cands = [prep.by_id.get(r.recipe_id) for r in top10]
    tally.coverage10.append(
        mean(
            c.coverage
            if c is not None
            else len(r.essential_ids & ctx.pantry_ids) / max(1, len(r.essential_ids))
            for c, r in zip(cands, top10, strict=True)
        )
        or 0.0
    )
    tally.missing10.append(
        mean(
            float(c.missing_count if c is not None else len(r.essential_ids - ctx.pantry_ids))
            for c, r in zip(cands, top10, strict=True)
        )
        or 0.0
    )
    own = ctx.own_pantry_ids or frozenset()
    tally.pantry_use10.append(mean(float(len(r.all_ids & own)) for r in top10) or 0.0)
    if ctx.expiring_ids:
        tally.expiring_hit5.append(
            1.0 if any(r.essential_ids & ctx.expiring_ids for r in top10[:5]) else 0.0
        )
    if any(v is not None for v in ctx.taste_vec):
        aligned = [
            taste.centered_cosine(ctx.taste_vec, r.flavor_vec, cat.corpus.flavor_mean, 0.0)
            for r in top10
        ]
        found = [v for v in aligned if v is not None]
        if found:
            tally.taste10.append(statistics.fmean(found))
    if ctx.preferred_cuisines:
        tally.cuisine_match.append(
            mean(1.0 if r.cuisine in ctx.preferred_cuisines else 0.0 for r in recipes) or 0.0
        )
    pairs = [(a, b) for i, a in enumerate(top10) for b in top10[i + 1 :]]
    if pairs:
        tally.ild10.append(1.0 - statistics.fmean(jaccard(a.all_ids, b.all_ids) for a, b in pairs))
    keys = {dish.dish_name(r.title, mains) or r.title for r in top10}
    tally.dish_distinct10.append(len(keys) / max(1, len(top10)))
    tally.cuisine_entropy.append(entropy(Counter(r.cuisine or "?" for r in recipes)) or 0.0)
    tally.exposure.update(ids)


# ── 이유 충실도 ───────────────────────────────────────────────────
_NUM = re.compile(r"(\d+)가지")
_MIN = re.compile(r"(\d+)분이면")


def reason_checks(item: RankedItem, prep: Prepared, cat: Catalog) -> list[tuple[str, bool]]:
    """사유 문구의 주장마다 데이터와 맞는지. (피처, 참) 목록을 돌려줍니다."""
    recipe = cat.recipes[item.recipe_id]
    ctx, names, text = prep.ctx, cat.corpus.ingredient_names, item.reason
    out: list[tuple[str, bool]] = []
    if item.is_exploration:
        # 탐색 칸의 사유 셋 — 축 문구는 레시피가 취향보다 그만큼 강한지, 유형 문구는 유형이 맞는지.
        if text == "새로운 시도는 어떠세요":
            return [("exploration_generic", True)]
        if "강한 맛이에요" in text:
            found = [
                (recipe.flavor_vec[i] or 0.0) - (ctx.taste_vec[i] or 0.0)
                for i, axis in enumerate(taste.FLAVOR_AXES)
                if axis in text
                and ctx.taste_vec[i] is not None
                and recipe.flavor_vec[i] is not None
            ]
            return [("exploration_axis", bool(found) and found[0] >= rerank.EXPLORE_AXIS_MIN_GAP)]
        label = cuisine_label(recipe.cuisine) or "\0"
        return [("exploration_cuisine", label in text)]
    if not item.reason_features:
        return [("fallback", text == "추천 목록에 포함됐어요")]
    for key in item.reason_features:
        ok = True
        if key == "f_expiring":
            hit = [names[i] for i in recipe.essential_ids & ctx.expiring_ids]
            ok = any(f"{name}(D-3)" in text for name in hit)
        elif key == "f_coverage":
            ok = item.missing_count == 0 and "가진 재료로 바로" in text
        elif key == "f_missing":
            ok = item.missing_count == 1 and names.get(item.missing_ids[0], "\0") in text
        elif key == "f_pantry_use":
            found = _NUM.search(text)
            ok = found is not None and int(found.group(1)) == len(recipe.all_ids & ctx.pantry_ids)
        elif key == "f_taste":
            ok = ctx.persona is not None and any(axis in text for axis in taste.FLAVOR_AXES)
        elif key == "f_ing_pref":
            ok = any(names[i] in text for i in recipe.all_ids & ctx.history.liked_ingredient_ids)
        elif key == "f_cuisine":
            label = cuisine_label(recipe.cuisine) or "\0"
            ok = recipe.cuisine in ctx.preferred_cuisines and label in text
        elif key == "f_cooccur":
            ok = any(title in text for title in ctx.history.cooked_titles if title)
        elif key == "f_time_fit":
            found = _MIN.search(text)
            ok = (
                found is not None
                and int(found.group(1)) == recipe.cook_minutes
                and ctx.max_cook_minutes is not None
                and recipe.cook_minutes is not None
                and recipe.cook_minutes <= ctx.max_cook_minutes
            )
        out.append((key, ok))
    return out
