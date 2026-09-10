# 파트 B 구현 명세서 (개발착수본)

**[기술 설계서] Pentry-Mate AI 추천 코어 엔진 (Track B)**

| 항목 | 내용 |
|---|---|
| 문서 식별자 | PM-ENG-RECO-B-001 |
| 문서 상태 | OnReview (개발 착수본) |
| 최종 수정일 | 2026-09-09 |
| 작성/담당 | AI 추천 파트 B (엔진) / 유재현 | 

---

## 1. 시스템 아키텍처 및 설계 원칙

본 문서는 Pentry-Mate 추천 시스템의 코어 서빙 파이프라인(Track B) 구현을 위한 상세 설계서이다. 초기 서비스의 극단적 데이터 희소성(Sparsity > 99.99%) 및 표본 라벨 한계(600쌍) 환경을 고려하여, 외부 상용 API에 의존하지 않고 로컬 CPU 환경에서 완결되는 3단계 계층형 파이프라인을 구축한다.

### 1.1 3계층 엔지니어링 계층 구조 (Layered Architecture)

시스템 개발 및 검증 파이프라인은 3단계 엔지니어링 레이어로 분리하여 구축한다.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ [Layer 1: 단위 트랙 격리 구현 (Unit Track Layer)]                             │
│ - Mock Fixture 기반 Retrieval, 5-Block Scorer, Penalty, Re-ranking 코어 구현  │
│ - 외부 DB 및 A 파트 데이터 비의존적 순수 함수 단위 검증                       │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼────────────────────────────────────────┐
│ [Layer 2: 통합 레이어 (Integration Layer)]                                    │
│ - Schema Adapter: 백엔드(BE) 가변 DTO와 AI 17개 피처 간 결합 격리             │
│ - DB Binding: PostgreSQL 16 intarray GIN 연동 및 A 파트 recipe_feature 바인딩 │
│ - Logging Pipeline: 서빙 결과를 recommendation_log에 비동기 예외 격리 적재    │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼────────────────────────────────────────┐
│ [Layer 3: 엔드투엔드 종단 검증 레이어 (E2E Test Layer)]                       │
│ - 계약 검증: make contract 42개 단언 테스트 통과                              │
│ - 부하 성능 검증: Locust 부하 테스트 기반 단일 요청 p95 < 58ms 달성           │
│ - 피드백 루프 검증: POST /v1/events 수신 -> user_vector 지수 감쇠 갱신 루프   │
└───────────────────────────────────────────────────────────────────────────────┘
```

- **Layer 1**: 실제 데이터가 없는 개발 초기 단계에서 Mock 데이터셋을 기반으로 Retrieval, Scorer, Penalty, Re-ranking 코어 알고리즘을 독립 구현한다.
- **Layer 2**: 백엔드(BE)의 가변적인 요청을 흡수하는 스키마 어댑터를 배치하고, Track A의 `recipe_feature` 실데이터 및 PostgreSQL `intarray` 인덱스를 바인딩하며, 서빙 로그 적재 파이프라인을 개통한다.
- **Layer 3**: 42개 계약 검증 테스트 통과, 동시 부하 상황에서의 SLA(p95 < 58ms) 충족 여부, 유저 피드백 반영 루프를 최종 종단 검증한다.

### 1.2 엔진 코어 4대 불변 원칙

1. **Zero-Drop 정규화**: 측정할 수 없는 피처(데이터 미입력, 임박 재료 부재 등)는 0점으로 처리하지 않고 점수 계산의 분자와 분모 가중치 합에서 동시에 제외한다.
2. **곱연산 감점 (Penalty Multiplication)**: 최근 노출 및 조리 이력에 대한 감점은 뺄셈이 아닌 곱셈으로 적용하여 고득점 요리의 독점을 원천 차단한다.
3. **알레르기 100% 하드컷**: 알레르기 유발 식재료는 감점 대상이 아니며, Stage 1 Retrieval 단계에서 SQL 조건으로 완전 제외한다.
4. **맛 벡터 코퍼스 중심화 (Mean Centering)**: 맛 코사인 유사도 계산 전, `feature_stats` 테이블에 기록된 코퍼스 평균 벡터를 레시피와 사용자 벡터 양쪽에서 동일하게 차감한다.

---

## 2. 데이터 계약 및 스키마 어댑터 상세 명세

백엔드 및 클라이언트의 필드 변경 요구가 추천 랭킹 코어를 파괴하지 않도록 Pydantic v2 기반의 어댑터 패턴(Tolerant Reader)을 적용한다.

```
[외부 BE 요청 JSON] ──> [Pydantic Request] ──> [Input Adapter] ──> [내부 표준 Context] ──> [랭킹 코어]
  (비정형 텍스트/수량)     (extra="ignore")      (정규식/결측 보정)     (17개 피처 벡터)        (순수 함수)
```

### 2.1 API 입출력 스키마 명세 (`src/features/recommend/schema.py`)

#### 1) 요청 스키마 (`RecommendRequest`)

```python
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TastePreference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    spicy_level: int = Field(2, ge=0, le=4, description="매운맛 선호도 (0~4)")
    sweet_level: int = Field(2, ge=0, le=4, description="단맛 선호도 (0~4)")
    salty_level: int = Field(2, ge=0, le=4, description="짠맛 선호도 (0~4)")


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: int = Field(..., description="사용자 식별자")
    pantry_ingredient_ids: list[int] = Field(..., description="보유 식재료 표준 ID 목록")
    expiring_ingredient_ids: list[int] = Field(
        default_factory=list, description="D-3 소비기한 임박 식재료 ID 목록"
    )
    taste_preference: TastePreference = Field(default_factory=TastePreference)
    household_size: int = Field(default=1, ge=1, description="가족 구성원 수")
    preferred_cuisines: list[str] = Field(default_factory=list, description="선호 요리 종류")
    max_cook_minutes: int | None = Field(default=None, description="가용 조리시간 상한(분)")
    allergy_group_codes: list[str] = Field(
        default_factory=list, description="식약처 19종 표준 알레르기 코드군"
    )
    top_k: int = Field(default=20, ge=1, le=50, description="반환 레시피 수")

    @field_validator("household_size", mode="before")
    @classmethod
    def parse_household_size(cls, v: Any) -> int:
        if isinstance(v, int):
            return max(1, v)
        if isinstance(v, str):
            match = re.search(r"\d+", v)
            return int(match.group()) if match else 1
        return 1

    @field_validator("preferred_cuisines", mode="before")
    @classmethod
    def parse_cuisines(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(item).strip() for item in v if item]
        if isinstance(v, str):
            return [c.strip() for c in re.split(r"[,/ ]+", v) if c.strip()]
        return []
```

#### 2) 응답 스키마 (`RecommendResponse`)

```python
class RecommendedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rank: int = Field(..., description="추천 노출 순위 (1~K)")
    recipe_id: int = Field(..., description="레시피 고유 식별자")
    recipe_title: str = Field(..., description="레시피 명칭")
    match_score: float = Field(..., description="최종 랭킹 스코어 (0.0~1.0)")
    cook_minutes: int | None = Field(default=None, description="조리 소요 시간(분)")
    missing_ingredient_ids: list[int] = Field(
        default_factory=list, description="부족한 필수 재료 ID 목록"
    )
    missing_count: int = Field(..., description="부족한 필수 재료 개수 (k<=2)")
    reason: str = Field(..., description="z-salience 기반 추천 사유 문구")
    matched_product_ids: list[int] = Field(
        default_factory=list, description="연계 커머스 상품 ID 목록"
    )
    is_exploration: bool = Field(default=False, description="우연성 탐색 슬롯 추천 여부")


class RecommendResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: UUID = Field(..., description="요청 추적 고유 ID (Log 조인 키)")
    recommendations: list[RecommendedItem] = Field(..., description="Top-K 추천 리스트")
    meta: dict[str, Any] = Field(..., description="지연시간 및 폴백 적용 메타데이터")
```

#### 3) 피드백 이벤트 스키마 (`POST /v1/events`)

```python
class FeedbackEventRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: int = Field(..., description="사용자 식별자")
    request_id: UUID = Field(..., description="추천 요청 추적 ID")
    recipe_id: int = Field(..., description="이벤트 대상 레시피 ID")
    position: int = Field(..., ge=1, le=50, description="노출 순위 (Position Bias 보정용)")
    event_type: str = Field(..., pattern=r"^(click|cook|dismiss)$")
    timestamp: str = Field(..., description="ISO-8601 이벤트 발생 시각")


class FeedbackEventResponse(BaseModel):
    success: bool
    updated_taste_vector: list[float]
```

---

## 3. 알고리즘 및 수학적 수식 명세

### 3.1 Stage 1: Retrieval (PostgreSQL intarray 연산)

46,552건의 레시피 풀에서 사용자 인벤토리와 호환되는 500개 후보군을 15ms 이내에 추출한다.

$$
\text{Candidates} = \{\, r \in \mathcal{R} \mid (E_r \cap P_u \neq \emptyset \lor |E_r| = 0) \land (A_r \cap \text{Allergy}_u = \emptyset) \land |E_r \setminus P_u| \leq k \land T_r \leq T_{\max} \,\}
$$

- $E_r$: 레시피 필수 식재료 ID 정수 배열 (`essential_ids`)
- $A_r$: 레시피 전체 식재료 ID 정수 배열 (`all_ids`)
- $P_u$: 유저 보유 식재료 ID 정수 배열 (`pantry_ids`)
- $k$: 허용 부족 식재료 수 ($k \leq 2$)

```sql
SELECT recipe_id,
       icount(essential_ids - :pantry_ids) AS missing_count,
       cardinality(essential_ids) AS essential_count,
       flavor_vec, popularity_score, quality_score, cook_minutes
FROM recipe_feature
WHERE (essential_ids && :pantry_ids OR cardinality(essential_ids) = 0)
  AND NOT (all_ids && :allergy_ids)
  AND icount(essential_ids - :pantry_ids) <= :max_missing
  AND (:max_minutes IS NULL OR cook_minutes <= :max_minutes)
ORDER BY missing_count ASC, popularity_score DESC
LIMIT 500;
```

**3단계 완화 폴백**: 후보군이 20건 미만일 경우 부족수 완화($k = 2 \rightarrow 3 \rightarrow 4$) → 대체재 확장 → 전체 인기순 폴백을 단계적으로 실행하며, `meta.degraded = True` 상태로 200 OK를 보장한다.

### 3.2 Stage 2: 5블록 Zero-Drop 랭킹 스코어러 및 감점기

17개 피처를 5개 독립 블록으로 그룹화하여 가중합을 계산하며, 결측 블록은 분자/분모에서 동시 제외한다.

$$
\text{Score}(u, r) = \left( \frac{\sum_{b \in \text{Measurable}} w_b \cdot S_b(u, r)}{\sum_{b \in \text{Measurable}} w_b} \right) \times P_{\text{recent}} \times P_{\text{cooked}} \times (1 - P_{\text{avoid}})
$$

#### 1) 5대 블록 스코어 수식 ($S_b \in [0.0, 1.0]$)

**$S_{\text{match}}$ (재료 충족도, $w_1 = 0.29$)**

$$S_{\text{match}} = 1.0 - \frac{|E_r \setminus P_u|}{|E_r| + \epsilon}$$

**$S_{\text{exp}}$ (소비기한 임박 소진율, $w_2 = 0.15$)**

$$S_{\text{exp}} = \frac{|E_r \cap P_{u,\text{D-3}}|}{|P_{u,\text{D-3}}| + \epsilon}$$

단, $|P_{u,\text{D-3}}| = 0$ 이면 블록 계산에서 제외한다.

**$S_{\text{taste}}$ (3축 맛 코사인 유사도, $w_3 = 0.31$)**

$$S_{\text{taste}} = \frac{(\mathbf{u}_{\text{taste}} - \boldsymbol{\mu}) \cdot (\mathbf{r}_{\text{flavor}} - \boldsymbol{\mu})}{\|\mathbf{u}_{\text{taste}} - \boldsymbol{\mu}\| \, \|\mathbf{r}_{\text{flavor}} - \boldsymbol{\mu}\|}$$

단, $\boldsymbol{\mu}$는 `feature_stats` 테이블에서 조회한 코퍼스 3축 평균 벡터.

**$S_{\text{quality}}$ (품질 및 대중성, $w_4 = 0.15$)**

$$S_{\text{quality}} = 0.6 \cdot \text{popularity\_score} + 0.4 \cdot \text{quality\_score}$$

**$S_{\text{ctx}}$ (조리시간 및 환경 적합도, $w_5 = 0.10$)**

$$S_{\text{ctx}} = 1.0 - \max\left(0, \frac{T_r - T_{\max}}{T_{\max}}\right)$$

#### 2) 감점 계수 (곱연산 적용)

| 계수 | 조건 | 적용 |
|---|---|---|
| $P_{\text{recent}}$ | 최근 7일 내 추천 노출 이력 존재 | $\times 0.7$ |
| $P_{\text{cooked}}$ | 최근 14일 내 조리 완료 이력 존재 | $\times 0.5$ |
| $P_{\text{avoid}}$ | 기피 재료 포함 비율 | $\times 2.0$ (최대 0.8 감점 한도) |

### 3.3 Stage 3: Re-ranking 및 20% 비(非)페르소나 탐색 정책

최종 Top-20 서빙 결과 중 16개(80%)는 개인화 맞춤형으로, 4개(20%)는 취향 외 영역 탐색형으로 배분한다.

```
[후보 500개] ──> [5블록 스코어링] ──┬──> 개인화 16개 (80%): MMR 다양성 필터 (lambda=0.7)
                                    └──> 탐색 4개 (20%): 비선호 요리군 (Thompson 2 + Uniform 2)
                                                    │
                                                    ▼
                                    [위치 무작위화 믹싱] ──> [최종 Top-20 서빙 반환]
```

#### 개인화 슬롯 (16개, MMR 다양성 제어, $\lambda = 0.7$)

$$r^* = \arg\max_{r \in C \setminus S} \left[ \lambda \cdot \text{Score}(u, r) - (1 - \lambda) \max_{s \in S} \text{Sim}_{\text{Jaccard\_IDF}}(r, s) \right]$$

$$\text{Sim}_{\text{Jaccard\_IDF}}(r, s) = \frac{\sum_{i \in (A_r \cap A_s)} \text{IDF}(i)}{\sum_{j \in (A_r \cup A_s)} \text{IDF}(j)}$$

#### 탐색(Novelty) 슬롯 (4개, 비선호 영역)

- 사용자가 온보딩에서 선택하지 않은 요리군(`preferred_cuisines` 외) 및 미경험 맛 영역 중 `quality_score` 상위 후보를 추출한다.
- 2건은 카테고리 사후 분포 $\text{Beta}(\alpha_c, \beta_c)$ 기반 Thompson Sampling, 2건은 균등 무작위로 추출한다.
- 각 탐색 아이템의 노출 확률 역수($1 / P(r)$)를 `propensity_scores`로 산출하여 로깅한다 (오프라인 학습 Position Bias 보정용).
- 4개 아이템을 1~20위 구간에 무작위 분산 배치(`random.sample(range(top_k), 4)`)하며 `is_exploration = True`로 마킹한다.

### 3.4 동적 페르소나 피드백 루프 수식

조리 및 클릭 이벤트 발생 시 유저의 행동 풍미 벡터를 지수 이동 평균(EMA)으로 갱신하고 콜드/웜 전이를 처리한다.

$$\alpha = \min\left(1.0, \frac{n_{\text{events}}}{20}\right)$$

$$\mathbf{u}_{\text{behavior}}^{(t)} = (1 - \gamma)\,\mathbf{u}_{\text{behavior}}^{(t-1)} + \gamma\,\mathbf{r}_{\text{flavor}} \quad (\gamma = 0.2)$$

$$\mathbf{u}_{\text{taste\_effective}} = (1 - \alpha)\,\mathbf{u}_{\text{onboarding}} + \alpha\,\mathbf{u}_{\text{behavior}}^{(t)}$$

---

## 4. 데이터베이스 DDL 및 관측 인터페이스 명세

### 4.1 테이블 DDL 명세 (`src/features/recommend/tables.py`)

```sql
-- 1. 추천 서빙 로그 테이블 (Track C 평가 원천)
CREATE TABLE IF NOT EXISTS recommendation_log (
    request_id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL,
    config_fingerprint VARCHAR(64) NOT NULL,
    pantry_snapshot JSONB NOT NULL,
    served_recipe_ids JSONB NOT NULL,
    candidates_features JSONB NOT NULL,
    propensity_scores JSONB NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reco_log_user ON recommendation_log(user_id, created_at DESC);

-- 2. 사용자 피드백 이벤트 로그 테이블
CREATE TABLE IF NOT EXISTS event_log (
    event_id BIGSERIAL PRIMARY KEY,
    request_id UUID REFERENCES recommendation_log(request_id),
    user_id BIGINT NOT NULL,
    recipe_id BIGINT NOT NULL,
    position INTEGER NOT NULL,
    event_type VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_log_request ON event_log(request_id);

-- 3. 유저 동적 취향 벡터 테이블
CREATE TABLE IF NOT EXISTS user_vector (
    user_id BIGINT PRIMARY KEY,
    taste_vec REAL[] NOT NULL DEFAULT '{0.5, 0.5, 0.5}',
    events_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.2 관측 및 신뢰성 인터페이스

1. **설정 지문 (`config_fingerprint`)**: 가중치 및 감점 계수 조합의 해시값을 생성하여 서빙 로그에 바인딩한다. Track C의 추천 디버거 화면이 당시 점수 가중치를 복원하는 유일한 단서가 된다.
2. **`/health` 관측 카운터**: `reco_served_total`, `reco_failed_total`, `reco_degraded_total` 카운터를 관리하고 엔드포인트에 노출한다.
3. **특성 리포트 생성 (`evaluation/feature_report.py`)**: 17개 특성별 min, median, max, null 비율을 계산하는 리포트 생성기를 배치하여 Track C에 제공한다.

---

## 5. 소스 코드 배치 및 모듈 책임 규약

저장소 규칙에 따라 모든 코드는 `src/features/recommend/` 하위에 위치한다.

```
src/features/recommend/
├── __init__.py           # 공개 API 배럴 (RecommendRequest, RecommendResponse)
├── router.py             # HTTP 엔드포인트 (POST /v1/recommend, POST /v1/events)
├── schema.py             # Pydantic v2 계약 모델 및 유연한 파서
├── service.py            # 파이프라인 흐름 제어 및 비동기 로깅 호출
├── repository.py         # DB 쿼리, 로그 적재, user_vector 갱신 전담
├── tables.py             # recommendation_log 등 테이블 DDL 선언
├── engine/               # 핵심 순수 함수 모듈 (외부 I/O 절대 금지)
│   ├── candidate.py      # Stage 1: intarray 결과 처리 및 완화 폴백
│   ├── rank.py           # Stage 2: 5블록 Zero-Drop 가중합 순수 함수
│   ├── penalty.py        # Stage 2: 노출/조리/기피재료 곱연산 감점기
│   ├── rerank.py         # Stage 3: MMR 다양성 필터 및 20% 탐색 슬롯 믹싱
│   └── explain.py        # z-salience 기반 추천 사유 문구 생성기
└── evaluation/
    └── feature_report.py # 17개 특성 관측용 요약 통계량 산출 모듈
```

---

## 6. 단계별 구현 및 테스트 계획 (체크리스트)

### Step 0: 데이터 계약 및 가상 환경 구축 (Day 1)

**구현 작업**

- [ ] `src/features/recommend/schema.py` Pydantic v2 스키마 작성
- [ ] `scripts/generate_mock_fixtures.py` 작성 및 실행하여 12인 가상 인벤토리 생성
- [ ] 더미 응답을 서빙하는 기본 라우터 골격 연결

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-0-1 | 가변 페르소나 입력 파싱 | 문자열 "4인 가구" 유입 시 `household_size=4` 추출 | `uv run pytest tests/unit/recommend/test_schema.py -k test_persona` | [ ] Pass / [ ] Fail |
| TC-0-2 | 규약 외 임의 필드 유입 | 에러 없이 임의 필드 무시 (`extra="ignore"`) | `uv run pytest tests/unit/recommend/test_schema.py -k test_extra` | [ ] Pass / [ ] Fail |
| TC-0-3 | 12인 픽스처 유효성 | 12개 Mock JSON 파싱 성공 | `uv run pytest tests/unit/recommend/test_schema.py -k test_fixtures` | [ ] Pass / [ ] Fail |

### Step 1: 관측 및 피드백 로깅 배관 개통 (Day 2)

**구현 작업**

- [ ] `tables.py` 선언 및 DB 테이블 마이그레이션 (`recommendation_log`, `event_log`, `user_vector`)
- [ ] `repository.py` 내 비동기 예외 격리 로깅(`log_serving_result`) 작성
- [ ] `/health` 카운터 메트릭 등록

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-1-1 | 서빙 로그 적재 확인 | 1회 서빙 요청 시 `recommendation_log`에 1건 저장 | `make log-test` | [ ] Pass / [ ] Fail |
| TC-1-2 | 로깅 예외 격리 검증 | DB 연결 강제 차단 상태에서도 서빙 응답 정상 200 OK | `uv run pytest tests/unit/recommend/test_logging.py` | [ ] Pass / [ ] Fail |
| TC-1-3 | 관측 카운터 노출 | 서빙 시 `/health`의 `reco_served_total` 1 증가 | `curl -s http://localhost:8000/health \| grep reco_served` | [ ] Pass / [ ] Fail |

### Step 2: Stage 1 Retrieval 및 3단계 완화 폴백 구현 (Day 3)

**구현 작업**

- [ ] `engine/candidate.py` 내 PostgreSQL `intarray` 쿼리 바인딩 작성
- [ ] 알레르기 유발 식재료 100% 하드컷 검증
- [ ] 후보군 20건 미만 시 $k$ 완화 및 인기순 폴백 구현

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-2-1 | 알레르기 차단 검증 | 결과 500개 중 알레르기 식재료 포함 레시피 0건 | `uv run pytest tests/unit/recommend/test_candidate.py -k test_allergy` | [ ] Pass / [ ] Fail |
| TC-2-2 | 3단계 완화 폴백 검증 | 후보 부족 인벤토리 유입 시 `meta.degraded=True` 및 20건 반환 | `uv run pytest tests/unit/recommend/test_candidate.py -k test_fallback` | [ ] Pass / [ ] Fail |
| TC-2-3 | 집합 쿼리 지연시간 | 후보 500개 추출 레이턴시 p95 < 15ms | `make smoke` | 실측치: \_\_\_\_ ms |

### Step 3: Stage 2 랭킹 스코어러 및 감점기 구현 (Day 4~5)

**구현 작업**

- [ ] `engine/rank.py` 내 Zero-Drop 가중합 순수 함수 구현
- [ ] `feature_stats` 코퍼스 평균 차감(중심화) 로직 작성
- [ ] `engine/penalty.py` 내 최근 노출(0.7), 조리(0.5) 감점 곱연산 구현

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-3-1 | Zero-Drop 정규화 검증 | 임박 재료 없을 시 $w_2$가 분모에서 제외되어 점수 왜곡 없음 | `uv run pytest tests/unit/recommend/test_rank.py -k test_zero_drop` | [ ] Pass / [ ] Fail |
| TC-3-2 | 중심화 미적용 감지 | 코퍼스 평균 미차감 시 테스트 강제 실패 (0.767 쏠림 방지) | `uv run pytest tests/unit/recommend/test_rank.py -k test_centering` | [ ] Pass / [ ] Fail |
| TC-3-3 | 감점 곱연산 검증 | 최근 14일 내 조리 레시피는 최종 스코어가 정확히 50% 감쇄 | `uv run pytest tests/unit/recommend/test_penalty.py` | [ ] Pass / [ ] Fail |

### Step 4: Stage 3 Re-ranking 및 20% 탐색 정책 구현 (Day 6~7)

**구현 작업**

- [ ] `engine/rerank.py` 내 MMR 자카드 다양성 필터 구현 ($\lambda = 0.7$)
- [ ] 4개 비선호 탐색 슬롯 생성 (Thompson Sampling 2건 + 균등 무작위 2건)
- [ ] 탐색 아이템 위치 무작위화 및 IPS 역확률 계산 로깅
- [ ] `engine/explain.py` 내 $z$-salience 사유 문구 생성기 연결

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-4-1 | 탐색 슬롯 비율 검증 | 20개 결과 중 `is_exploration=True` 아이템 정확히 4개 | `uv run pytest tests/unit/recommend/test_rerank.py -k test_exploration_count` | [ ] Pass / [ ] Fail |
| TC-4-2 | 위치 무작위화 검증 | 100회 실행 시 탐색 슬롯의 평균 인덱스 분산 확인 (고정 순위 금지) | `uv run pytest tests/unit/recommend/test_rerank.py -k test_random_positions` | [ ] Pass / [ ] Fail |
| TC-4-3 | 사유 문구 유효성 | 결측값 없이 자연스러운 한국어 조사 바인딩 완료 | `uv run pytest tests/unit/recommend/test_explain.py` | [ ] Pass / [ ] Fail |

### Step 5: 실서빙 조립 및 피드백 루프 연동 (Day 8~9)

**구현 작업**

- [ ] `service.py`와 `router.py` 엔드투엔드 파이프라인 연결 (난수 제거)
- [ ] `POST /v1/events` 엔드포인트 구현 및 유저 취향 벡터 EMA 감쇠 갱신
- [ ] `evaluation/feature_report.py` 특성 요약 통계 산출기 작성

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-5-1 | 난수 점수 잔존 여부 | 동일 입력에 대해 결정론적 스코어 출력 확인 | `uv run pytest tests/unit/recommend/test_serve.py -k test_deterministic` | [ ] Pass / [ ] Fail |
| TC-5-2 | 피드백 감쇠 갱신 | 조리 이벤트 20회 누적 시 행동 풍미 반영 비율 100% 도달 | `uv run pytest tests/unit/recommend/test_events.py` | [ ] Pass / [ ] Fail |
| TC-5-3 | 특성 리포트 무결성 | 17개 특성의 결측률 및 통계치가 정상 산출됨 | `uv run python src/features/recommend/evaluation/feature_report.py` | [ ] Pass / [ ] Fail |

### Step 6: 통합 종단 테스트 및 레이턴시 검증 (Day 10~11)

**구현 작업**

- [ ] Track A `recipe_feature` 실데이터 연동 확인
- [ ] 42개 계약 검증 단언 테스트 통과
- [ ] Locust 기반 부하 테스트 및 레이턴시 SLA 검증

**검증 항목**

| 검증 ID | 검증 시나리오 | 통과 기준 | 검증 명령 | 결과 |
|---|---|---|---|---|
| TC-6-1 | 계약 검증 단언 테스트 | 42개 케이스 100% 통과 | `make contract` | [ ] Pass / [ ] Fail |
| TC-6-2 | 실데이터 무결성 검증 | 비정상 결측치 및 테이블 스키마 오류 0건 | `make feature-test` | [ ] Pass / [ ] Fail |
| TC-6-3 | 서빙 지연시간 SLA | 100회 동시 요청 상황에서 단일 요청 p95 < 58ms (SLA 300ms) | `locust -f tests/load/locustfile.py --headless -u 10 -r 2 --run-time 1m` | 실측 p95: \_\_\_\_ ms |

---

## 7. 정량적 목표 지표 및 최종 실측 기록표

배포 전 오프라인 평가 하네스 및 실측 테스트를 통해 아래 지표를 달성하고 기록한다.

| 평가지표 | 최저 기준치 | 제안 모델 목표치 | 단위/통합 테스트 실측치 | E2E 부하 실측치 | 최종 판정 |
|---|---|---|---|---|---|
| 서빙 지연시간 (p95) | < 300ms | **< 58ms** | [ 실측치 기입 ] | [ 실측치 기입 ] | [ ] Pass / [ ] Fail |
| NDCG@10 (정밀도) | >= 0.85 | **0.926** | [ 실측치 기입 ] | - | [ ] Pass / [ ] Fail |
| Recall@20 (재현율) | >= 0.60 | **>= 0.60** | [ 실측치 기입 ] | - | [ ] Pass / [ ] Fail |
| 카탈로그 커버리지 | >= 15.0% | **>= 15.0%** | [ 실측치 기입 ] | - | [ ] Pass / [ ] Fail |
| 목록 다양성 (ILD) | >= 0.95 | **>= 0.95** | [ 실측치 기입 ] | - | [ ] Pass / [ ] Fail |

### 최적화 가중치 실측 파라미터 (Bradley-Terry 학습 후 확정)

| 파라미터 | 항목 | 착수 값 | 학습 후 실측치 |
|---|---|---|---|
| `w_match` | 재료 충족도 | 0.29 | \_\_\_\_ |
| `w_expiring` | 소비기한 소진 | 0.15 | \_\_\_\_ |
| `w_taste` | 3축 미각 적합도 | 0.31 | \_\_\_\_ |
| `w_quality` | 품질 및 대중성 | 0.15 | \_\_\_\_ |
| `w_ctx` | 상황 및 시간 적합도 | 0.10 | \_\_\_\_ |
| `config_fingerprint` | 생성된 MD5 해시값 | - | \_\_\_\_ |

---

## 8. 최종 품질 검증 명령

본 명세서의 모든 단계를 완료한 후, 커밋 및 PR 생성 전 아래 통합 명령어를 실행하여 전수 통과 결과를 확보해야 한다.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit/recommend
```
