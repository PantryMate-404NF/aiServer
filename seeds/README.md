# seeds/ — 재료 시드 데이터 세트

> 작성자: 박재우 · 작성일: 2026-09-03

크롤링 완료를 기다리지 않고 만든 **매칭 대상 사전**이다.
문서 [`docs/reco/design/02_RECOMMENDER_DESIGN.md`](../docs/reco/design/02_RECOMMENDER_DESIGN.md) 섹션 ②·④ 의 산출물 1~9번에 해당한다.

> **이것이 없으면 크롤링이 끝나도 아무것도 못 한다.** 정규화 파이프라인(P3 매칭 캐스케이드)의
> L0/L1 단계가 전부 이 사전을 조회한다.

---

## 파일

| 파일 | 대상 테이블 | 규모 | 역할 |
|---|---|---|---|
| `ingredient_category.yaml` | `ingredient_category` | 65 노드 | 계층 트리 + 알러지 전개 기준 |
| `ingredient.csv` | `ingredient` | **525종** | 정규 재료 + staple/seasoning/알러지 |
| `ingredient_alias.csv` | `ingredient_alias` | **245개** | 표기 변형 (아래 갭 항목 참조) |
| `ingredient_unit_weight.csv` | `ingredient_unit_weight` | **193행** | 개수 단위 → g 환산 |
| `measure_units.yaml` | (코드 상수) | 40+ | 큰술·컵 등 계량 단위 + 한글 수사 + 분수 |
| `modifier_whitelist.yaml` | (코드 상수) | 52 | L2 규칙 매칭 제거 대상 수식어 |
| `confusable_pairs.yaml` | (코드 상수) | 42쌍 | L3/L4 매칭 금지 쌍 |
| `cuisine_taxonomy.yaml` | `cuisine_taxonomy` | 15 | 요리 계열 2계층 + 분류 규칙 초안 |
| `substitutable_pairs.yaml` | (측정 전용) | pos **69** · neg **31** | **재료 대체 라벨** — 구현 전 측정용 (설계 6-4-6). ⚠️ `confusable_pairs` 와 목적이 다르고 **8쌍 겹침** |
| `ingredient_shelf_life.yaml` | `ingredient.shelf_life_days` | 기본 **58** + 예외 **42** | **기본 소비기한** — `f_expiring` 을 유저 입력 없이 동작시킨다 (설계 5-2-1). ⚠️ 통념 기반, 실측 아님 (02 I-14) |
| `validate.py` | — | — | 정합성 검증 (소비기한 전량 해소·구조매칭 회귀 포함) |

## 검증

```bash
make install TRACK=A    # pyyaml 은 공통 의존이라 어느 트랙이든 깔린다
python3 seeds/validate.py
```

FK 위반·고아 참조·중복·alias 충돌·비정상 무게를 잡는다.
**적재 전에 반드시 통과**시킨다. 현재 상태: `✅ 통과 (경고 1건)`

경고 1건은 의도된 것이다 — `modifier_whitelist` 의 `생` 은 `생강` 을 깨뜨릴 수 있으므로
**제거 후 2음절 이상 남을 때만 적용**하도록 P2 코드에서 가드해야 한다.

## 적재 순서

FK 의존 때문에 순서를 지켜야 한다 (설계 문서 2-11).

```
1. ingredient_category.yaml   (path 로 부모 해석)
2. ingredient.csv             (category_path → category_id 로 변환)
3. ingredient_alias.csv       (ingredient_name → ingredient_id)
4. ingredient_unit_weight.csv (ingredient_name → ingredient_id)
5. cuisine_taxonomy.yaml
   measure_units / modifier_whitelist / confusable_pairs → 코드가 직접 로드 (DB 미적재)
```

CSV 가 이름 기반인 이유: **사람이 손으로 고칠 수 있어야 하기 때문**이다.
ID 기반이면 검수자가 편집할 수 없고, 순서를 바꾸면 전부 깨진다.

---

## 🔴 알려진 갭 — 반드시 읽을 것

### 1. alias 245개는 설계 문서의 목표(2,000~3,000)에 크게 못 미친다

**의도적이다.** 설계 문서의 추정치가 잘못되었고, 문서를 수정했다.

| 변형 종류 | 예시 | 누가 처리하나 | alias 필요? |
|---|---|---|---|
| 띄어쓰기 | `대 파` → `대파` | ~~P1 전처리~~ → **P3 조회 키** *(v2.2 정정 — P1 은 공백을 떼지 않는다)* | ❌ |
| 원산지·상태 수식어 | `국내산 대파` → `대파` | **L2 화이트리스트** | ❌ |
| 단순 오타 | `얘호박` → `애호박` | **L3 trgm** | ❌ |
| **환원 불가능한 변형** | `계란`→`달걀`, `오뎅`→`어묵`, `정구지`→`부추` | **alias 만 가능** | ✅ |

alias 는 **규칙으로 환원할 수 없는 것만** 담는다. 나머지를 alias 에 넣으면
사전이 비대해지고 유지보수가 불가능해진다.

**남은 갭은 상상으로 채우지 않는다.** 실제 크롤링 데이터의 `raw_text` 빈도를 집계해
`normalization_queue` 로 올라온 것을 검수하며 채운다. 없는 데이터를 추측해서 alias 를
만들면 오탐만 늘어난다.

### 2. 단위 환산 193행도 목표(750)보다 적다

**개수 단위를 실제로 쓰는 재료만** 대상이기 때문이다. 대부분의 재료는 g/ml 로만 표기되어
환산표가 필요 없다. 176종 × 평균 1.1단위 = 193행이 현실적인 규모다.

다만 `confidence` 평균이 **0.78** 이다. 상식 기반 추정치이므로 실제 레시피에서
`quantity_g` 이상치가 나오면 이 표를 의심해야 한다.

### 3. staple 28종은 **튜닝 파라미터**다

| staple 이 너무 많으면 | staple 이 너무 적으면 |
|---|---|
| 거의 모든 레시피가 "만들 수 있음"으로 나와 Retrieval 이 무의미해짐 | 한식 레시피 대부분이 "재료 부족"으로 걸러짐 |

**1주차에 실제로 돌려보고 조정한다.** 조정 지표:
- 평균 후보 수가 500을 크게 넘으면 → staple 을 줄인다
- 냉장고 8개 재료로 후보가 10개 미만이면 → staple 을 늘린다

의도적으로 제외한 것들 (판단 근거는 `ingredient.csv` 의 note 열):
`달걀` `버터` `베이킹파우더` `베이킹소다` — 주재료로 쓰이거나 보유율이 낮다.

### 4. 저신뢰 alias 9개는 검수 1순위다

`호박` `솔` `밀` `다짐육` `란` `치즈` `시럽` `오일` `조미료`

전부 **다의어**다. `호박`이 애호박인지 단호박인지, `다짐육`이 소고기인지 돼지고기인지는
문맥 없이 결정할 수 없다. 크롤링 데이터에서 실제 등장 문맥을 보고 판정하거나,
`normalization_queue` 로 내려 사람이 결정하게 한다.

### 5. 재료 525종은 커버리지를 보장하지 않는다

지프의 법칙상 상위 재료가 등장 빈도의 대부분을 덮지만, **실제 몇 %인지는 크롤링 데이터로만 알 수 있다.**
1주차 커버리지 측정 결과가 목표(W2 = 0.55)에 못 미치면 알고리즘이 아니라 **이 사전을 키운다.**

---

## 보강 절차

```
크롤링 완료
   ↓
raw_text 빈도 집계 → normalization_queue INSERT (freq_count DESC)
   ↓
L0~L4 캐스케이드 실행 → 실패분이 큐에 남음
   ↓
[검수] 20명 × 100건, 1건당 15초       ← suggested 후보가 채워져 있어야 성립
   ↓
ingredient.csv / ingredient_alias.csv 에 반영 (사람이 직접 편집 가능)
   ↓
validate.py 통과 확인 → 전체 재정규화 ↺
   ↓
coverage_rate 갱신 → data_quality_snapshot
```

**사전 파일이 SoT 다.** DB 를 직접 수정하지 않는다. 검수 UI 가 DB 를 쓰더라도
반드시 이 CSV 로 export 해서 커밋해야 재현성이 유지된다.

---

## 편집 규칙

- `ingredient.csv` 의 `name` 은 **고유**해야 한다 (validate.py 가 검사)
- alias 는 다른 재료의 **정식명과 같으면 안 된다** (예: `안심` 을 `소안심` 의 alias 로 두면 `돼지안심` 과 충돌)
- 새 알러지 그룹을 추가하면 `ingredient_category.yaml` 의 `allergen_expansion`
  또는 `allergen_column_only` 에도 등록해야 한다
- 카테고리 `path` 를 바꾸면 `ingredient.csv` 의 `category_path` 도 함께 바꾼다
- `confusable_pairs` 에 넣는 이름은 alias 가 아니라 **정식 재료명**이어야 한다
- 수정 후 반드시 `python3 seeds/validate.py`

## 알러지 전개 방식 2종

```yaml
allergen_expansion:      # ltree 하위 전개 — 카테고리 = 알러젠 인 경우
  nut: [농산물.견과종실.견과류]

allergen_column_only:    # 컬럼으로만 — 카테고리에 비알러젠이 섞여 있는 경우
  - buckwheat            # 메밀은 잡곡류지만 같은 잡곡류의 보리·귀리는 메밀 알러젠이 아니다
```

이 이중 구조가 설계 문서 2-2의 "안전 관련은 이중화한다" 원칙의 구현이다.
계층이 잘못 잡혀도 `ingredient.allergen_group` 컬럼이 잡고, 컬럼이 비어도 계층이 잡는다.
