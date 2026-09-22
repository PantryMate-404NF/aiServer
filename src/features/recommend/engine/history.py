"""사용자 이력 — 취향 이벤트와 메모리 사전에서 `UserHistory` 를 만듭니다.

원래 계획(DB 전환 M-03)은 데이터 파트의 표(`user_ingredient_pref` · `event_log` ·
`user_cluster_stat`)에서 읽는 것이었습니다. 레시피의 정본이 백엔드 API 로 가고(D-59) 이벤트가
우리 취향 저장소에 쌓이면서 원천이 바뀌었습니다 — 여기 있는 것은 그 두 가지(취향 이벤트와
메모리 사전)만으로 같은 `UserHistory` 를 만드는 순수 함수입니다.

- **조리한 레시피**: 최근 `COOKED_WINDOW_DAYS` 일의 조리 이벤트. 재료 집합과 제목이
  `f_cooccur` 와 "지난번 만드신 X 와 비슷해요" 사유로 갑니다.
- **선호 재료**: 조리 · 저장 · 클릭 · 별점 이벤트의 레시피에 든 재료를 종류 무게 x 시간 감쇠로
  셉니다(취향 벡터와 같은 무게, `engine/persona.py`). 상비 재료는 세지 않습니다 — 소금 · 간장은
  어느 레시피에나 들어 있어 선호가 아닙니다. 무게 합이 `LIKED_MIN_WEIGHT` 를 넘는 재료만
  선호로 봅니다(조리 한 건, 또는 저장 두 건 정도).
- **최근 노출**: 호출자가 넘깁니다(서빙이 메모리의 추천 로그에서 찾습니다).

없는 것은 없는 채로 둡니다. 이력이 없으면 `f_ing_pref` · `f_cooccur` 는 측정 불가(None)이고
그것이 맞습니다 — 0 을 주면 콜드 사용자 전원을 똑같이 깎습니다(`feature._preference`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from features.recommend.engine import persona as persona_engine
from features.recommend.engine.context import RecipeFeature, UserHistory
from features.recommend.engine.persona import TasteEvent
from features.recommend.enums import EventType
from features.recommend.policy import RankingPolicy

#: 최근 조리로 보는 기간. `UserHistory.cooked_recipe_ids` 의 주석("최근 14일 조리")과 같습니다.
COOKED_WINDOW_DAYS = 14
#: 최근 노출로 보는 기간("최근 7일 노출"). 서빙이 이 기간의 로그에서 찾아 넘깁니다.
RECENT_WINDOW_DAYS = 7
#: `f_cooccur` 가 보는 조리 레시피 수의 상한. 후보 500건 x 이 수만큼 자카드를 계산합니다.
MAX_COOKED = 20
#: 선호 재료 수의 상한과, 선호로 보는 무게의 하한.
MAX_LIKED = 20
LIKED_MIN_WEIGHT = 1.0


def build_history(
    events: Sequence[TasteEvent],
    recipes: Mapping[int, RecipeFeature],
    staple_ids: frozenset[int],
    now: datetime,
    policy: RankingPolicy,
    recent_served: Iterable[int] = (),
) -> UserHistory:
    """이벤트와 사전에서 이력을 만듭니다. 순수 함수이며 같은 입력이면 같은 결과입니다."""
    persona_engine.require_aware(now, "now")
    cooked = _cooked(events, recipes, now)
    return UserHistory(
        liked_ingredient_ids=_liked(events, recipes, staple_ids, now, policy),
        recent_recipe_ids=frozenset(recent_served),
        cooked_recipe_ids=frozenset(recipe.recipe_id for recipe in cooked),
        cooked_ingredient_sets=tuple(recipe.all_ids for recipe in cooked),
        cooked_titles=tuple(recipe.title for recipe in cooked),
    )


def _cooked(
    events: Sequence[TasteEvent], recipes: Mapping[int, RecipeFeature], now: datetime
) -> list[RecipeFeature]:
    """최근 조리한 레시피, 최신 것부터. 사전에 없는 레시피(지워졌거나 비공개)는 뺍니다."""
    limit = COOKED_WINDOW_DAYS * persona_engine.SECONDS_PER_DAY
    fresh = [
        event
        for event in events
        if event.kind is EventType.COOK and 0.0 <= (now - event.at).total_seconds() <= limit
    ]
    fresh.sort(key=lambda event: event.at, reverse=True)
    picked: list[RecipeFeature] = []
    seen: set[int] = set()
    for event in fresh:
        recipe = recipes.get(event.recipe_id)
        if recipe is None or event.recipe_id in seen:
            continue
        seen.add(event.recipe_id)
        picked.append(recipe)
        if len(picked) >= MAX_COOKED:
            break
    return picked


def _liked(
    events: Sequence[TasteEvent],
    recipes: Mapping[int, RecipeFeature],
    staple_ids: frozenset[int],
    now: datetime,
    policy: RankingPolicy,
) -> frozenset[int]:
    """재료마다 (종류 무게 x 시간 감쇠) 를 더해 하한을 넘는 것을 무게 순으로 상한까지."""
    weight: dict[int, float] = defaultdict(float)
    for event in events:
        recipe = recipes.get(event.recipe_id)
        if recipe is None:
            continue
        base = persona_engine.kind_weight(event.kind, event.value)
        if base <= 0.0:
            continue
        age_days = max(0.0, (now - event.at).total_seconds()) / persona_engine.SECONDS_PER_DAY
        contribution = base * persona_engine.time_decay(age_days, policy.persona_half_life_days)
        for ingredient_id in recipe.all_ids - staple_ids:
            weight[ingredient_id] += contribution
    # 무게가 같으면 번호가 낮은 재료가 앞입니다 — 실행마다 같은 답이 나오게 하는 것뿐입니다.
    ranked = sorted(
        (ingredient_id for ingredient_id, total in weight.items() if total >= LIKED_MIN_WEIGHT),
        key=lambda ingredient_id: (-weight[ingredient_id], ingredient_id),
    )
    return frozenset(ranked[:MAX_LIKED])
