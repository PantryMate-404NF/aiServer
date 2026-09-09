"""스테이지 간 계약 (설계 5-0).

    RetrievalInput → [①] → Candidate
                   → [②] → ScoredCandidate
                   → [③] → RankedItem

이 모델들은 **경계를 넘습니다.** `RankedItem` 과 `StageTrace` 는
`RecommendResponse` 에 그대로 실려 나가므로 `engine/` 이 아니라 도메인 루트에
둡니다. 점수 계산 같은 순수 함수는 `engine/rank.py` 가 가져갔습니다.

규율 (설계 1-2)
  1. 스테이지는 이 모델로만 대화합니다. dict 전달을 금지합니다.
  2. 스테이지 간 직접 import 를 금지합니다. `service.py` 가 순서대로 호출합니다.
  3. 스테이지는 DB 접근을 `repository.py` 에 위임합니다.

책임 경계 — **제외는 오직 ①에서만 합니다.** ②가 후보를 빼기 시작하면
"왜 빠졌는지" 를 두 곳에서 찾아야 합니다.

P1·P2 산출 타입(`Preprocessed`, `ParsedIngredient`)도 스테이지 계약이라
이 파일 끝에 함께 둡니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator

from features.recommend.enums import FEATURE_KEYS, Stage, UserMode

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# ─────────────────────────────────────────────────────────────────
# ① Retrieval
# ─────────────────────────────────────────────────────────────────
class RetrievalInput(_Base):
    """DB 함수 **retrieve_candidates()** 의 인자와 1:1 대응.

    집합을 직접 넘긴다 — 시뮬레이터·벤치가 유저 없이 후보를 뽑을 때 쓴다.
    서빙 경로는 아래 `RetrievalRequest` 다.
    """
    user_id: int
    pantry_ids: list[int] = Field(description="냉장고 + staple 전체 (결정 2)")
    allergy_ids: list[int] = Field(default_factory=list, description="4경로 합집합 전개 결과")
    max_missing: int = Field(default=2, ge=0, le=10)
    max_minutes: int | None = None
    limit: int = Field(default=500, ge=1, le=2000)


class RetrievalRequest(_Base):
    """DB 함수 **retrieve_for_user()** 의 인자와 1:1 대응 — 서빙 경로.

    pantry·allergy 를 넘기지 않는다. SQL 안에서 `user_pantry_ids()` 와
    `expand_user_allergens()` 가 유도한다 — 🔴 **왕복 1회를 지키려면
    파이썬이 먼저 조회해서 넘기면 안 된다** (01 1-7).

    경계값은 위 `RetrievalInput` 과 같아야 한다. 두 경로가 다른 상한을 쓰면
    시뮬 결과와 서빙 결과가 조용히 갈라진다.
    """
    user_id: int
    max_missing: int = Field(default=2, ge=0, le=10)
    max_minutes: int | None = Field(default=None, ge=1)
    limit: int = Field(default=500, ge=1, le=2000)


class Candidate(_Base):
    """① 산출. 아직 점수가 없다."""
    recipe_id: int
    missing_count: int = Field(ge=0)
    missing_ids: list[int] = Field(default_factory=list)
    coverage: float = Field(ge=0.0, le=1.0)
    #: 우연성·다양성 축 (설계 5-3-5). `recipe_feature.cluster_id` 를 그대로 싣는다.
    #: None 이면 클러스터링 배치가 아직 안 돌았다는 뜻 — 균등 탐색으로 폴백한다.
    cluster_id: int | None = None


# ─────────────────────────────────────────────────────────────────
# ② Ranking
# ─────────────────────────────────────────────────────────────────
class ScoredCandidate(Candidate):
    """② 산출.

    🔴 **`contrib`(=w·f) 가 아니라 피처 원값 `features` 를 저장한다.** *(v1.9)*

    이전 판은 `contrib = {k: w·f for k,w in weights if w > 0}` 를 저장했다.
    두 가지가 동시에 깨진다.

    1. **w=0 인 피처 7개가 로그에서 사라진다.** `f_content`·`f_ing_cf`·`f_quality`·
       `f_pantry_use`·`f_dish_type`·`f_skill_fit`·`f_group_pref`. 나중에 그 피처를 켜서 LightGBM 을
       학습하려 해도 **과거 로그에 값이 없어 소급이 불가능**하다. R9 ablation 도 못 한다.
    2. **training-serving skew.** 온라인은 SQL, 오프라인은 pandas 로 피처를 두 번
       계산하면 반드시 어긋난다. 서빙 시점 피처를 그대로 학습에 쓰면 원천 제거된다.

    `contrib` 는 저장하지 않고 `contrib(weights)` 로 언제든 되계산한다.
    가중치가 바뀌어도 과거 로그를 다시 해석할 수 있다.
    """
    #: 🔴 FEATURE_KEYS 전부가 있어야 한다. **`None` 과 `0.0` 은 다른 의미다.**
    #:    `0.0` = 계산했더니 0 · `None` = 계산할 수 없음(수단 미구현·데이터 없음).
    #:    LightGBM 은 결측을 native 로 처리하므로 0 으로 메우면 정보가 왜곡된다.
    features: dict[str, float | None] = Field(
        description="피처 원값 17종. w 와 무관하게 항상 전부 기록한다")
    score: float
    penalty: float = Field(default=1.0, ge=0.0, le=1.0,
                           description="p_recent · p_cooked · (1-p_avoid) 의 곱")

    @field_validator("features")
    @classmethod
    def _all_keys(cls, v: dict[str, float | None]) -> dict[str, float | None]:
        missing = set(FEATURE_KEYS) - set(v)
        unknown = set(v) - set(FEATURE_KEYS)
        if missing:
            raise ValueError(f"features 에 빠진 피처: {sorted(missing)} — 전부 기록해야 한다")
        if unknown:
            raise ValueError(f"FEATURE_KEYS 에 없는 피처: {sorted(unknown)}")
        return v

    def contrib(self, weights: dict[str, float]) -> dict[str, float]:
        """w·f. 저장하지 않고 필요할 때 계산한다 (디버거 막대그래프용)."""
        return {k: weights.get(k, 0.0) * (self.features.get(k) or 0.0)
                for k in FEATURE_KEYS if weights.get(k, 0.0) > 0}



# ─────────────────────────────────────────────────────────────────
# ③ Re-ranking
# ─────────────────────────────────────────────────────────────────
class RankedItem(ScoredCandidate):
    """③ 산출. 유저에게 나가는 최종 형태."""
    final_rank: int = Field(ge=1)
    reason: str = Field(default="", description="템플릿 생성 문구 (설계 5-5)")
    reason_features: list[str] = Field(
        default_factory=list,
        description="이유를 만든 피처 (z-salience 상위). 디버거가 근거를 보여준다")
    mmr_penalty: float = 0.0
    is_exploration: bool = Field(
        default=False,
        description="무작위 삽입 슬롯. position bias 보정의 기준점 (설계 5-3-3)",
    )
    #: 🔴 **소급 불가.** 이 아이템이 이 위치에 노출될 확률. IPS/SNIPS 의 분모다.
    #:    나중에 off-policy 평가를 하려면 그때의 로그 정책을 알아야 하는데,
    #:    저장해두지 않으면 영원히 복원할 수 없다 (설계 3-2).
    propensity: float | None = Field(
        default=None, gt=0.0, le=1.0,
        description="노출 확률. exploration 슬롯은 1/|pool|, 결정적 슬롯은 1.0")
    #: 🔑 탐색 슬롯을 **어느 경로가** 채웠는가 (설계 5-3-5).
    #:    'uniform'  — 균등 무작위. support 보장용. propensity 가 모든 후보에 > 0
    #:    'thompson' — 클러스터 Thompson. 우연성용. propensity 가 아이템마다 다르다
    #:    🔴 구분하지 않으면 두 경로의 로그가 섞여 off-policy 분석에서 나눌 수 없다.
    explore_source: str | None = Field(
        default=None, description="uniform | thompson | None(탐색 슬롯이 아님)")
    #: Team-Draft Interleaving 시 어느 랭커가 이 자리를 가져갔는가 (설계 5-7-2)
    team: str | None = None


# ─────────────────────────────────────────────────────────────────
# stage_trace — 🔴 1주차 동결 대상 (설계 3-1)
# ─────────────────────────────────────────────────────────────────
class StageInfo(_Base):
    """단계 하나의 기록. filters 가 디버깅에서 가장 유용하다."""
    name: Stage
    in_count: int
    out_count: int
    latency_ms: int

    strategy: str | None = None
    model: str | None = None
    fallback: str | None = Field(
        default=None, description="폴백이 발동했으면 대체 모델명 (설계 5-6)")

    filters: dict[str, int] = Field(
        default_factory=dict,
        description="탈락 사유별 건수. 예 {'no_overlap':227999,'allergy_cut':12}")
    dropped: dict[str, int] = Field(
        default_factory=dict, description="③ 전용. 다양성·캡으로 제외한 건수")
    params: dict[str, float | int | str | None] = Field(default_factory=dict)
    score_stats: dict[str, float] = Field(
        default_factory=dict, description="min·p25·p50·p75·max")
    exploration_items: list[int] = Field(default_factory=list)


class TraceTotals(_Base):
    latency_ms: int
    cache_hit: bool = False
    degraded: bool = Field(
        default=False,
        description="폴백 경로를 탔다. 이 비율이 조용히 오르는 것이 가장 위험한 실패 양상")
    user_mode: UserMode = UserMode.COLD


class StageTrace(_Base):
    """recommendation_log.stage_trace 에 그대로 직렬화된다."""
    trace_version: str = "v1"
    stages: list[StageInfo] = Field(default_factory=list)
    totals: TraceTotals


# ─────────────────────────────────────────────────────────────────
# P1·P2 산출 타입 — P3 매칭은 ParsedIngredient.name 만 봅니다.
# ─────────────────────────────────────────────────────────────────
@dataclass
class Preprocessed:
    """P1 산출. 아직 분해되지 않았다."""
    original: str                                  # 원문. 절대 수정하지 않는다
    text: str                                      # 괄호를 걷어낸 본문
    notes: list[str] = field(default_factory=list)         # 부위·상태 설명
    conversions: list[str] = field(default_factory=list)   # '400ml' 같은 환산값
    optional_hints: list[str] = field(default_factory=list)  # '생략가능'
    substitutes: list[str] = field(default_factory=list)     # '또는 미림'


@dataclass
class ParsedIngredient:
    """P2 산출. P3 매칭의 입력."""
    raw_text: str                     # 원문 (recipe_ingredient_raw.raw_text)
    name: str                         # ← P3 매칭 대상
    quantity: float | None = None     # 모호하면 None. 0 으로 채우지 않는다
    unit: str | None = None
    note: str | None = None
    modifiers: list[str] = field(default_factory=list)    # 제거한 수식어 (L2 재활용)
    substitutes: list[str] = field(default_factory=list)
    is_optional_hint: bool = False    # → P4 optional 신호
    is_ambiguous_qty: bool = False    # '약간' 류 → P4 optional 신호
    split_candidate: bool = False     # 복합 의심 → P3 실패 시 검수 큐
    position: int = 0                 # 한 raw_text 에서 몇 번째로 나왔나
    #: 🔴 재료가 아니다 — 조리도구·용기·소모품 (seeds/non_ingredient.yaml).
    #:    만개의레시피는 재료 목록에 도구를 섞어 넣는다 (도마 ×2,259 · 냄비 ×1,124).
    #:    **지우지 않고 표시만 한다.** 유저에게 묻는 값이 아니라
    #:    후속 코드가 읽어가는 내부 플래그다 — position 이 어긋나면 안 되고,
    #:    "무엇을 걸렀는지" 자체가 데이터 품질 지표이기 때문이다.
    is_non_ingredient: bool = False
    non_ingredient_kind: str | None = None   # tool | vessel | consumable | action
