"""스테이지 공통 타입.

⚠️ 이 패키지가 **계약 그 자체**다. 설계 문서가 아니라 여기가 SoT.
   문서(01 ⑦절)는 엔드포인트 목록과 배경만 설명한다.

값을 바꾸면 DB CHECK 제약(db/init/02_schema.sql)도 함께 바꿔야 한다.
"""
from __future__ import annotations

from enum import StrEnum

CONTRACT_VERSION = "v1"


# ── 로그 (설계 3-2) — 🔴 1주차 동결 대상 ───────────────────────────
class EventType(StrEnum):
    """추가는 가능, **변경·삭제는 불가**. 소급이 안 된다."""
    IMPRESSION = "impression"
    CLICK = "click"
    SAVE = "save"
    UNSAVE = "unsave"
    COOK = "cook"
    RATING = "rating"
    DISMISS = "dismiss"
    SEARCH = "search"


#: 랭킹 학습 라벨 가중치 (설계 3-2). 값은 조정 가능, 키는 고정.
LABEL_WEIGHT: dict[EventType, float] = {
    EventType.IMPRESSION: 0.0,    # negative 후보. 없으면 학습 자체가 불가
    EventType.CLICK: 0.3,
    EventType.SAVE: 0.6,
    EventType.UNSAVE: -0.3,
    EventType.COOK: 1.0,          # 최강 신호
    EventType.DISMISS: -0.5,
    EventType.SEARCH: 0.0,
}


def rating_to_label(value: float) -> float:
    """별점 1~5 → -1.0~+1.0"""
    return (value - 3.0) / 2.0


# ── 파이프라인 ────────────────────────────────────────────────────
class Stage(StrEnum):
    RETRIEVAL = "retrieval"
    RANKING = "ranking"
    RERANK = "rerank"
    MEALPLAN = "mealplan"


class UserMode(StrEnum):
    """user_vector.computed_from 과 동일한 값 (설계 2-5)."""
    COLD = "onboarding"
    BLENDED = "blended"
    WARM = "behavior"


class MatchMethod(StrEnum):
    """recipe_ingredient.match_method (설계 4-4)."""
    EXACT = "exact"
    ALIAS = "alias"
    RULE = "rule"
    FUZZY = "fuzzy"
    EMBED = "embed"
    MANUAL = "manual"


class IngredientRole(StrEnum):
    ESSENTIAL = "essential"
    OPTIONAL = "optional"
    SEASONING = "seasoning"
    GARNISH = "garnish"


# ── 랭킹 피처 (설계 5-2-1) ────────────────────────────────────────
#: contrib 딕셔너리의 키. 여기 없는 키를 쓰면 이유 생성이 깨진다.
FEATURE_KEYS: tuple[str, ...] = (
    # A군 — 재료 매칭
    "f_coverage", "f_missing", "f_expiring", "f_pantry_use",
    # B군 — 유저 선호
    "f_taste", "f_ing_pref", "f_cuisine", "f_dish_type",
    "f_cooccur",      # ★ 가중 자카드로 재정의 (설계 5-2-1). item2vec 불필요
    "f_group_pref", "f_ing_cf", "f_content",
    # C군 — 품질
    "f_popularity", "f_quality",
    # D군 — 컨텍스트
    "f_time_fit", "f_season", "f_skill_fit",
)

#: v0 선형 가중치 (설계 5-2-2).
#:
#: 🔴 **검증되지 않은 추정치다.** 근거는 도메인 직관뿐이며 ablation(R9) 이 필수다.
#:    W3 에 쌍대비교 라벨로 학습한 값으로 교체한다 (설계 5-2-5).
#:
#: ⚠️ **여기 있는 피처는 전부 실제로 계산 가능해야 한다.**
#:    04_실행계획에서 잘린 수단(item2vec·KMeans)에 의존하는 피처에 가중치를 주면
#:    Σw 가 1.00 으로 검증을 통과하면서도 서빙은 0.84 짜리 랭커가 된다.
DEFAULT_WEIGHTS: dict[str, float] = {
    "f_coverage": 0.24, "f_taste": 0.16, "f_expiring": 0.15,
    "f_ing_pref": 0.11, "f_cooccur": 0.10, "f_popularity": 0.10,
    "f_missing": 0.05, "f_cuisine": 0.04,
    "f_time_fit": 0.03, "f_season": 0.02,
    # ── w=0 — 계산 수단은 있으나 아직 켜지 않은 것 ──────────────
    "f_quality": 0.0,       # f_popularity 와 상관. ablation 대상
    "f_pantry_use": 0.0, "f_dish_type": 0.0, "f_skill_fit": 0.0,
    # ── w=0 — 웜 전환 시 활성화 (설계 5-2-3) ───────────────────
    "f_content": 0.0,
    # ── w=0 — 계산 수단 자체가 없다 (04 에서 컷) ────────────────
    "f_ing_cf": 0.0,        # user×ingredient ALS 미구현
    "f_group_pref": 0.0,    # KMeans 클러스터링 미구현
}

#: 🔴 **수단 자체가 없다.** 04 에서 잘린 방법(item2vec·ALS)에 의존한다.
#: 되살리려면 그 방법을 구현해야 하므로 **가중치가 0 이어야 한다** — 계약 테스트가 강제한다.
#: features 로깅 시 0.0 이 아니라 None 이어야 한다.
#: 0.0 은 "계산했더니 0", None 은 "계산할 수 없음" — 학습에서 완전히 다른 의미다.
#:
#:   f_ing_cf     user×ingredient ALS 미구현
#:   f_group_pref KMeans 클러스터링 미구현
#:   f_content    `recipe_feature.content_emb` 를 **만드는 코드가 저장소에 없다.**
#:                A-12 가 ko-SBERT 대신 TF-IDF→SVD 판으로 확정하면서 임베딩 배치가
#:                3주 계획에서 빠졌다 (09-03 추가). 응답에서 항상 None 이다.
UNAVAILABLE_FEATURES: frozenset[str] = frozenset(
    {"f_ing_cf", "f_group_pref", "f_content"})

#: 🔴 세션 접두어 — DDL CHECK · API pattern · 라이터가 **같은 것을 봐야 한다.**
#:    세 곳에 따로 적어 뒀더니 라이터만 'd-' 를 몰라, 디버거가 규약대로 보낸
#:    session_id 를 'g-…' 로 **바꿔서** 저장했다 (09-03 실측). CHECK 는 통과하고
#:    로그도 정상으로 보이는데, 실유저 지표에서 개발 트래픽을 걷어낼 수 없게 된다.
#:      c- 실사용자 · g- 게스트 · d- 개발·디버거·시딩
SESSION_PREFIXES: tuple[str, ...] = ("c-", "g-", "d-")

#: 🔴 **수단은 있는데 데이터가 없다.** 위와 구분한다 — 데이터가 오면 코드 변경 없이 켜진다.
#:
#:   f_cuisine  레시피 쪽 `cuisine_family` 가 **46,353건 전수 0건**이다.
#:              만개의레시피 4축이 실제 크롤에 오지 않았고 categories 는
#:              고유 45,529종 자유 태그다. 태그→유형 매핑(약 12h)이 선행이다.
#:   f_season   제철 시드가 없다. `recipe_feature.season_vec` 은 컬럼만 있다.
#:   f_dish_type 같은 이유다 — 원본에 분류축이 없어 `dish_type` 이 전수 비어 있다.
#:              `w=0` 인 것은 v0 스코어에서 애초에 안 쓰기 때문이고, 데이터 부재와는
#:              별개다 (09-03 추가 — 01 2-3-1 이 이미 PENDING 이라 적고 있었다).
#:
#: **가중치를 0 으로 내리지 않는다.** 5-2-2-1 의 나눗셈 정규화가
#: `raw = Σwᵢfᵢ / Σwᵢ` (fᵢ≠None 인 것만) 이라 항상 None 인 피처는 분자·분모에서
#: 함께 빠진다 — 비례 재분배와 수학적으로 같다. 그래서 지금 점수는 옳고,
#: 데이터가 오면 **가중치를 다시 유도할 필요 없이** 그대로 켜진다.
PENDING_DATA_FEATURES: frozenset[str] = frozenset(
    {"f_cuisine", "f_season", "f_dish_type"})

#: 오늘 실제로 랭킹에 기여하는 가중치 합. **문서에 쓰는 숫자는 이것이다.**
#: Σw=1.00 은 설계 의도이고, 오늘 서빙되는 랭커는 이만큼으로 돈다.
ACTIVE_WEIGHT_TODAY: float = round(
    sum(w for k, w in DEFAULT_WEIGHTS.items()
        if w > 0 and k not in UNAVAILABLE_FEATURES and k not in PENDING_DATA_FEATURES), 4)

# ─────────────────────────────────────────────────────────────────
# 🔴 로그 규약 동결 (S0 ① · 2026-09-01 확정) — 소급 불가
# ─────────────────────────────────────────────────────────────────
#: propensity 가 **무엇의 확률인가**. `"item"` 으로 동결했다.
#:
#: 아이템이 Top-K 어딘가에 노출될 **주변확률**이다. (아이템, 위치) 결합확률이 아니다.
#: 이유: 클릭은 `P(examine|position) × P(relevant|item)` 로 분해되는데,
#: 곱해서 한 컬럼에 넣으면 **위치 효과를 다시 뺄 수 없다.** 나눠 두면 언제든 곱한다.
#: 위치 효과는 exploration 슬롯 위치를 매 요청 무작위화해 따로 추정한다 (01 5-3-3).
PROPENSITY_SEMANTICS: str = "item"

#: `StageInfo.params` 에 **반드시** 실어야 하는 키. 값이 아니라 **정의**가 소급 불가다.
#: 없으면 로그가 있어도 propensity 를 재구성할 수 없다 (07 E-3 ①).
REQUIRED_TRACE_PARAMS: tuple[str, ...] = (
    "policy_id",             # 어느 정책이었나
    "propensity_semantics",  # 무엇의 확률인가 — 위 상수와 일치해야 한다
    "explore_pool_size",     # 탐색 풀 크기 (설계 5-3-3 = 상위 200)
    "uniform_share",         # 혼합 정책의 균등 비율 (5-3-5)
    "propensity_mc",         # MC 반복 수
    "rng_seed",              # 재현용
    "max_missing_final",     # 폴백 완화 후 **실제** 값
    # ── v2.9 추가 3종. 셋 다 스칼라 한 칸이고, 없으면 사후 재구성이 막힌다 ──
    "top_k",                 # 몇 개를 노출했나. propensity 재계산의 분모
    "n_explore",             # 탐색 슬롯이 몇 칸이었나
    "serving_mode",          # real | sim | load_test — candidates 저장 정책이 다르다.
                             # 없으면 "잘려서 없는 것"과 "원래 없던 것"이 구분되지 않는다
)

#: `serving_mode` 별 `candidates` 저장 개수 (설계 3-6).
#: 🔴 **절단 후 `served` 를 반드시 합집합한다.** exploration 은 상위 200 풀에서 뽑히므로
#:    상위 50 밖으로 떨어질 수 있는데, 하필 그것이 propensity ≠ 1.0 인 유일한 행이다.
CANDIDATE_KEEP: dict[str, int | None] = {"real": 50, "sim": 10, "load_test": 0}
