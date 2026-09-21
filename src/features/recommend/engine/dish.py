"""같은 요리의 판본을 한 목록에 하나만 남깁니다.

백엔드 데이터에는 같은 요리를 여러 사람이 올린 판본이 많습니다. 2026-09-21 실데이터에서 Top-20
한 목록에 감자조림이 여섯 건 들어갔고, 제목이 완전히 같은 레시피만 782건입니다. 백엔드에는
판본을 묶는 대표 번호가 없어(2026-09-21 회신) 재정렬 앞에서 묶습니다.

MMR 만으로는 막히지 않습니다. MMR 은 재료가 겹치는 만큼 점수를 깎을 뿐이고, 한 냉장고에서 나온
후보는 서로 다 비슷해서 판본끼리의 감점이 다른 후보와의 감점보다 크게 두드러지지 않습니다.

무엇을 같은 판본으로 보는가는 셋입니다. 하나라도 같으면 같은 판본입니다.

1. 제목이 같다 — 기호와 공백을 뺀 뒤.
2. 요리 이름이 같다 — 제목에서 조리법으로 끝나는 낱말을 찾고, 그 안의 주재료부터 끝까지.
   "매콤 두부조림" · "두부 조림 만들기" · "백종원두부조림" 이 모두 `두부조림` 입니다.
3. 재료 전체가 같다 — 네 가지 이상일 때만. 두세 가지는 다른 요리도 같을 수 있습니다.

주의: **필수 재료가 같다는 것은 기준이 아닙니다.** 실측에서 달걀장조림 · 계란말이 · 계란찜이 한
   묶음이 되어 목록당 5칸이 사라졌습니다. 필수 재료가 하나뿐인 레시피가 많기 때문입니다.
주의: 잘못 묶으면 다른 요리 하나가 가려지고, 못 묶으면 판본이 남습니다. 앞쪽이 더 나쁘므로 요리
   이름은 주재료를 **포함해** 끝까지 봅니다 — `닭볶음탕` 과 `닭곰탕` 은 다른 열쇠입니다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from functools import lru_cache

from features.recommend.engine.context import RecipeFeature
from features.recommend.stage import ScoredCandidate

#: 요리 이름의 끝에 오는 조리법·요리 갈래. 긴 것부터 맞춥니다 — `볶음밥` 이 `밥` 보다 먼저입니다.
_METHODS: tuple[str, ...] = tuple(
    sorted(
        {
            *("볶음밥", "비빔밥", "덮밥", "김밥", "주먹밥", "국밥", "솥밥", "밥"),
            *("비빔국수", "칼국수", "잔치국수", "국수", "라면", "수제비", "우동", "냉면"),
            *("파스타", "스파게티", "리조또", "피자", "스테이크", "샐러드", "샌드위치", "토스트"),
            *("장조림", "조림", "볶음", "찌개", "전골", "국", "탕", "찜", "구이", "튀김", "무침"),
            *("부침개", "부침", "전", "말이", "나물", "장아찌", "절임", "김치", "깍두기", "겉절이"),
            *("카레", "떡볶이", "만두", "잡채", "불고기", "갈비", "죽", "스프", "수프"),
            *("꼬치", "강정", "수육", "오믈렛", "그라탕", "스튜", "짜글이"),
            *("케이크", "쿠키", "머핀", "빵", "잼", "청", "소스", "양념장", "드레싱", "쌈장"),
        },
        key=len,
        reverse=True,
    )
)
#: 요리 이름 뒤에 붙여 쓰는 말. 떼고 봅니다 — "두부조림만들기" 는 `두부조림` 입니다.
_TAILS: tuple[str, ...] = (
    "황금레시피",
    "만드는법",
    "만드는방법",
    "끓이는법",
    "볶는법",
    "만들기",
    "끓이기",
    "담그기",
    "레시피",
)
#: 재료 사전에는 없거나 한 글자라 뺀 것 가운데 요리 이름에 흔한 주재료.
#: 주의: 사전의 한 글자 이름을 전부 넣지 않습니다. "배" 는 배추김치에, "물" 은 물김치에 걸립니다.
_SHORT_MAINS: tuple[str, ...] = ("닭", "무", "파", "떡")
_MIN_NAME_LENGTH = 2
#: 재료 전체가 같다는 것을 판본의 근거로 삼는 최소 가짓수.
_MIN_SAME_SET = 4
_WORD = re.compile(r"[가-힣]+")
_NOT_TEXT = re.compile(r"[^0-9a-z가-힣]")
# 긴 것이 앞에 있어 `김치볶음밥` 은 `밥` 이 아니라 `볶음밥` 으로 끝납니다.
_METHOD_END = re.compile("(?:" + "|".join(_METHODS) + ")$")
_TAIL_END = re.compile("(?:" + "|".join(_TAILS) + ")$")
#: 제목 → 요리 이름을 기억해 둘 건수. 코퍼스 전체(2만여 건)가 들어가는 크기입니다.
_NAME_CACHE = 65_536


@lru_cache(maxsize=8)
def main_words(ingredient_names: tuple[str, ...]) -> frozenset[str]:
    """요리 이름 안에서 주재료로 찾을 말. 재료 사전의 이름(두 글자 이상)에 흔한 한 글자를 더합니다.

    같은 사전에는 **같은 객체**를 돌려줍니다. `dish_name` 의 기억 장치가 이 집합을 열쇠로 쓰는데,
    요청마다 새 집합을 만들면 찾을 때마다 150개를 하나씩 견줍니다.
    """
    return frozenset(
        {name for name in ingredient_names if len(name) >= _MIN_NAME_LENGTH} | set(_SHORT_MAINS)
    )


@lru_cache(maxsize=_NAME_CACHE)
def dish_name(title: str, mains: frozenset[str]) -> str | None:
    """제목에서 요리 이름을 뽑습니다. 못 찾으면 None 이고, 그 레시피는 이 기준으로 묶이지 않습니다.

    뒤에서부터 봅니다. 한국 레시피 제목은 수식이 앞에 오고 요리 이름이 뒤에 옵니다. 주재료가 든
    낱말을 먼저 찾고, 없을 때만 주재료 없는 요리 이름(`계란말이` · `바싹불고기`)을 받습니다 —
    순서를 섞으면 "건새우볶음 : 밑반찬 하나로 공기밥 뚝딱" 이 `공기밥` 이 됩니다.
    """
    words = [_TAIL_END.sub("", word) for word in _WORD.findall(title)]
    dishes: list[tuple[int, str, str]] = []
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        # 재료 이름 자체는 요리가 아닙니다 — "콩나물" 은 `나물` 로, "배추김치" 는 `김치` 로 끝납니다
        if not word or word in mains:
            continue
        ending = _METHOD_END.search(word)
        if ending is not None:
            dishes.append((index, word, ending.group()))
    for index, word, method in dishes:
        span = _last_main(word[: len(word) - len(method)], mains)
        if span is not None:
            return word[span[0] :]
        if index > 0:
            before = words[index - 1]
            span = _last_main(before, mains)
            # "양파 장아찌" · "햇양파 무침" — 주재료가 앞 낱말의 끝에 붙어 있을 때만 잇습니다.
            if span is not None and span[1] == len(before):
                return before[span[0] :] + word
    for _index, word, method in dishes:
        # 한 글자 조리법은 흔한 말에 붙습니다 — 공기밥 · 집밥 · 술국. 주재료 없이는 받지 않습니다.
        if len(method) >= _MIN_NAME_LENGTH and len(word) - len(method) >= _MIN_NAME_LENGTH:
            return word
    return None


def version_keys(recipe: RecipeFeature, mains: frozenset[str]) -> tuple[Hashable, ...]:
    """이 레시피가 속한 판본 묶음들. 어느 하나라도 이미 나간 묶음이면 같은 판본입니다."""
    keys: list[Hashable] = []
    plain = _NOT_TEXT.sub("", recipe.title.lower())
    if len(plain) >= _MIN_NAME_LENGTH:
        keys.append(("title", plain))
    name = dish_name(recipe.title, mains)
    if name is not None:
        keys.append(("dish", name))
    if len(recipe.all_ids) >= _MIN_SAME_SET:
        keys.append(("ingredients", recipe.all_ids))
    return tuple(keys)


def collapse_versions(
    ranked: Sequence[ScoredCandidate],
    recipes: Mapping[int, RecipeFeature],
    ingredient_names: Mapping[int, str],
    per_dish: int,
) -> list[ScoredCandidate]:
    """순위 순서로 훑어 한 요리에 `per_dish` 건까지만 남깁니다. 0 이하면 묶지 않습니다.

    `ranked` 는 **순위 순서**여야 합니다. 앞에 온 것이 남으므로, 남는 판본은 그 사용자에게 점수가
    가장 높은 것입니다 — 냉장고에 참치가 있으면 참치김치찌개가, 돼지고기가 있으면 돼지고기김치찌개가
    남습니다. 피처가 없는 후보는 판본을 알 수 없어 그대로 둡니다.
    """
    if per_dish <= 0:
        return list(ranked)
    mains = main_words(tuple(sorted(ingredient_names.values())))
    seen: Counter[Hashable] = Counter()
    kept: list[ScoredCandidate] = []
    for item in ranked:
        recipe = recipes.get(item.recipe_id)
        keys = () if recipe is None else version_keys(recipe, mains)
        if any(seen[key] >= per_dish for key in keys):
            continue
        seen.update(keys)
        kept.append(item)
    return kept


def _last_main(text: str, mains: frozenset[str]) -> tuple[int, int] | None:
    """`text` 에서 가장 뒤에서 끝나는 주재료의 (시작, 끝). 같은 자리에서 끝나면 긴 이름이 이깁니다.

    "건새우" 에는 `새우` 도 들어 있습니다. 긴 쪽을 골라야 건새우볶음과 새우볶음이 갈립니다.
    """
    best: tuple[int, int] | None = None
    for main in mains:
        at = text.rfind(main)
        if at < 0:
            continue
        found = (at + len(main), len(main))
        if best is None or found > best:
            best = found
    return None if best is None else (best[0] - best[1], best[0])
