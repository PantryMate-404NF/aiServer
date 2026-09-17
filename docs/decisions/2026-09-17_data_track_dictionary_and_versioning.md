# 사전 검수를 반영하고 판 번호를 입력에서 끌어낸다

**정하는 것**: 검수 800행을 시드에 반영하며 바뀐 데이터·계약, 판 번호를 사람이 올리지 않고 입력 해시에서 끌어내는 방식, 조회 게이트를 조인 근거, 그리고 파트 B 가 이어서 해야 하는 일

**적용 대상**: `src/features/recommend/` 의 엔진·계약을 고치는 인원과 AI 코딩 에이전트. 파트 B(추천 코어)는 1·5·6절, 파트 C(평가)는 3·5절을 봅니다

**버전**: 1.0.0 · **최종 수정**: 2026-09-17 · **작성자**: 파트 A (데이터)

---

## 1. 결정 요약

| 항목 | 전 | 후 | 파트 B 가 알아야 할 것 |
|---|---|---|---|
| 언급 커버리지 | 0.8113 | **0.9069** | W5 목표 0.85 를 넘었습니다 |
| `feature_version` | `"v1"` 고정 | `v1-<시드해시 6자>` | 값이 바뀌면 피처가 바뀐 것입니다. 캐시 키로 쓰십시오 |
| `cluster_version` | `v1-tfidf-svd48` 고정 | `v1-svd48-<입력해시 6자>` | 재군집하면 값이 바뀝니다. Thompson 관측을 이어 붙이기 전에 대조하십시오 |
| 조회 ⓪'' 게이트 | 필수 0건이면 미매칭 비율 ≤ 0.3 | 필수 0건이면 **미매칭 = 0** | 빈 팬트리 후보가 1,498 → 94 로 줍니다 |
| 알러지 어휘 | 세 갈래(소문자 10 · 대문자 18 · 문서 19) | **소문자 10종이 정본** | `scripts/generate_mock_fixtures.py` 를 고쳐 주십시오 |
| 골든 픽스처 | `make data-gate` 에만 (DB 필요) | `pytest tests/unit` 에도 | DB 없이 도는 계약 검사가 생겼습니다 |
| `recipe.cuisine_family` | 46,353건 전수 NULL | **28,604건 배정** (61.7%) | 유형 슬롯과 `f_cuisine` 이 이제 돕니다 |

## 2. 검수 반영 — 커버리지 0.8113 → 0.9069

미매칭 상위 800종을 사람이 검수해 시드에 넣었습니다.

```text
seeds/ingredient_alias.csv    246 → 672행   (별칭 426 추가)
seeds/ingredient.csv          536 → 696종   (신규 160 추가)

언급 커버리지   0.8113 → 0.9069  (+0.0956)
미매칭 합      82,213 → 40,368  (절반 이하)
평균 필수재료   3.00 → 3.41
재료 0개       1,006 → 945
필수 0개       2,739 → 1,512
recipe_ingredient  345,892 → 385,568행
```

**신규 재료는 `ingredient.csv` 맨 끝에 붙였습니다.** 중간에 넣으면 `Dictionary.from_seeds` 의 행순서 id 가 DB serial 과 밀리는데, 밀린 id 도 유효한 외래키라 INSERT 는 성공하고 **돼지고기 자리에 닭고기가 들어간 채 에러 없이 계속 돕니다**. 앞으로 재료를 더할 때도 끝에 붙이십시오.

## 3. 판 번호를 사람이 올리지 않는다

### 무엇이 문제였나

`feature_version` 이 `"v1"` 로 굳어 있었습니다. 검수를 반영해 재정규화한 2회차 피처가 1회차와 **같은 판 번호**를 달고 나가면, 캐시한 옛 값과 새 값을 구분할 방법이 없습니다. 에러가 아니라 점수만 조금씩 어긋나므로 아무도 알아채지 못합니다.

사람이 기억해서 올리는 규칙으로 두면 언젠가 빠집니다. 그래서 **입력에서 끌어냅니다.**

### 지금 방식

```text
feature_version = f"v1-{sha256(정규화 경로가 읽는 시드 전부)[:6]}"
cluster_version = f"v1-svd48-{sha256(K|SVD_DIM|SEED|MAX_ITER|TOL|feature_version)[:6]}"
```

기본은 **포함**입니다. 새 시드 파일이 생기면 저절로 지문에 들어갑니다 — 모르는 파일을 빼면 조용히 낡고, 넣으면 헛된 판 번호가 하나 올라갈 뿐이라 실패 비용이 비대칭입니다.

영향이 없다고 **확인된** 셋만 뺐습니다: `onboarding_recipes.yaml`(profile_store·schema 만 읽음) · `substitutable_pairs.yaml`(`ingredient_substitute` 0행, 읽는 코드 없음) · `ingredient_shelf_life.yaml`(`effective_expiry` 로 감).

### 이번에 실제로 작동했습니다

검수를 반영하니 연쇄가 돌았습니다.

```text
검수 반영 → feature_version  v1-cd752f → v1-b24451
         → cluster_version  v1-svd48-4c40f8 → v1-svd48-bc8616

요리 규칙 → feature_version  v1-b24451 → v1-01c6ae
(아래 6절) → cluster_version  v1-svd48-bc8616 → v1-svd48-fcf4ee
```

군집 입력이 `essential_ids` 멀티핫 ⊕ `flavor_vec` 이라, 피처가 바뀌면 같은 알고리즘·같은 시드라도 배정이 달라집니다. 그래서 `cluster_version` 이 `feature_version` 을 입력으로 받습니다.

### 파트 B 에게

이 두 값이 **로그에 안 남습니다.** `feature_version` 은 지금까지 `v1` → `v1-15a8c5` → `v1-da56de` → `v1-cd752f` → `v1-b24451` 로 다섯 번 갈아탔는데 `recommendation_log` 에 한 칸도 없습니다. 나중에 "이 추천이 어느 피처판으로 나왔지" 를 물을 방법이 없고, **켜고 나면 소급이 안 됩니다.**

값은 준비했습니다 — `repository.load_batch_versions()` 가 `{"feature_version": ..., "cluster_version": ...}` 을 돌려줍니다. 재정규화 도중이라 두 판이 섞이면 `+` 로 이어 남깁니다(조용히 하나만 고르면 그 로그로는 어느 쪽인지 알 수 없습니다).

```text
A 가 한 것   load_batch_versions() 제공
B 가 할 것   REQUIRED_TRACE_PARAMS 에 두 키 추가 + _trace_params 에서 호출
```

`Candidate` 에 칸을 더하지 않은 이유는 `ScoredCandidate` → `RankedItem` 으로 번져 17키 계약이 깨지기 때문입니다.

## 4. 조회 게이트를 조였다 (D-10·D-14 개정)

### 무엇이 문제였나

`n_essential = 0` 인 레시피를 미매칭 **비율** 0.3 으로 갈랐습니다. 게이트를 정할 때 실측한
2,739건이 이렇게 나뉘었습니다 (검수 반영 전 기준).

```text
미매칭 0건        59건   쌈장·초고추장·만능양념장 — 진짜로 양념만으로 만든다
미매칭 있음     1,439건   비율이 0.3 이하라 통과하던 것
비율 > 0.3     1,241건   옛 임계값이 걸러내던 것
```

검수를 반영한 지금은 1,512건 = 미매칭 0건 **94** + 미매칭 있음 960 + 비율 초과 458 입니다.
사전이 좋아져 미매칭이 줄면 이 숫자는 계속 움직입니다 — 갈래의 뜻이 요점이지 값이 아닙니다.

가운데 1,439건이 문제였습니다. 필수를 **하나도** 못 찾았는데 미매칭이 남아 있으면 "양념만으로 되는 요리" 가 아니라 **"아직 못 읽은 재료 안에 필수가 있다"** 는 뜻입니다. 그런데 `coverage` 가 1.0 만점을 주고, ① 의 `cardinality(essential_ids)=0` 가지가 팬트리 필터까지 건너뛰어 **모든 사용자 상위에 올라왔습니다.**

비율 임계값은 필수가 1건 이상일 때 "얼마나 놓쳤나" 를 재는 값이라, 필수가 0건인 자리에서는 뜻이 다릅니다 — 거기서는 미매칭 1건도 판정 불가입니다.

### 파트 B 에게 — 두 가지 영향

**하나.** 빈 팬트리 후보가 **1,498 → 94** 로 줍니다. `essential_ids` 는 `AND NOT is_staple` 로 만들어져 staple 이 애초에 못 들어가므로, staple 39종을 자동 부여해도 늘지 않습니다. **온보딩이 `pantry_item` 을 채우지 않으면 신규 사용자가 빈 화면에 가깝게 봅니다.** 채우는지 알려 주십시오.

**둘.** 완화 사다리(k=2→3→4)가 빈 팬트리에서 **구조적으로 무의미했습니다.** 통과하던 1,498건이 전건 `coverage 1.0` · `missing_count 0` 이라 k 를 올려도 결과가 같았습니다. 게이트를 조여 그 집합이 94건으로 줄었으니 사다리의 뜻이 살아납니다.

## 5. 파트 B·C 가 잘못 알고 있는 것

문서를 읽다 찾은 것들입니다. **코드를 고칠 필요는 없고, 전제만 바로잡으시면 됩니다.**

| | 문서에 적힌 것 | 실측 |
|---|---|---|
| 코퍼스 평균 μ | "부트스트랩 영벡터" | **실값입니다** (`feature_stats`, `stats_version` 7). 실 DB 에서 `f_taste` 가 켜집니다 |
| `cluster_id` | "군집 없는 후보 폴백" | **전량 배정** (46,353건 · 군집 50). NULL 이 0건이라 그 경로는 실 DB 에서 안 옵니다 |
| `ingredient_substitute` 0행 | 미완성으로 읽힘 | **결정입니다.** `substitutable_pairs.yaml` 이 "측정 전에 구현하지 않는다" 고 스스로 못박았습니다 |
| 검증 8.1 "값 나온 9종" | 통과로 기록 | **Mock 한정입니다.** 실 DB 는 `f_cuisine` None · `f_quality` 전건 0 이라 **7종**입니다 |

마지막 줄이 가장 중요합니다. 검증 문서가 `f_quality`(값 78종)·`f_pantry_use`(37종)를 "값이 가장 많이 변하는데 가중치가 0" 이라며 **가중치 재배분 후보로 지목**해 뒀는데, 그 78·37종이 Mock 에서만 나오는 숫자입니다. 실 DB 에서는 `quality_score` 가 전건 0(평점 0건)이고 `pantry_item` 이 0행이라 **측정 자체가 불가능**합니다. Mock 근거로 가중치를 옮기면 그대로 0 으로 돌아옵니다.

### 알러지 어휘 — 고쳐 주셔야 합니다

정본은 **DDL 의 소문자 10종**입니다.

```text
nut · sesame · soy · gluten · egg · dairy · fish · shellfish · peach · buckwheat
```

`scripts/generate_mock_fixtures.py` 가 대문자 18종(`EGG`·`MILK`·`WHEAT`…)을 자기 파일에 하드코딩해 두고 있습니다. DB 를 안 거쳐 `CHECK` 에 안 걸리므로 조용히 갈렸습니다. 겹치는 `PEACH`·`SHELLFISH`·`BUCKWHEAT` 조차 대소문자가 다릅니다.

A 쪽은 `enums.ALLERGEN_GROUPS` 로 정본을 세우고 `OnboardingIn.allergy_groups` 에 거부 validator 를 달았습니다(그 자리만 검증이 없었습니다). `tests/unit/recommend/test_allergen_vocab.py` 가 DDL 과의 일치를 대조합니다.

### 알러젠 전개가 실제로는 한 갈래만 돕니다

`expand_user_allergens` 에 차단 경로가 넷인데, 실제 등록 경로가 `allergen_group` 만 넣습니다.

```text
① 재료 직접 지정   ua.ingredient_id 필요   →  안 채움
② 카테고리 전개    ua.category_id 필요     →  안 채움
③ 컬럼 일치       allergen_group         →  이것만 돕니다
③' 재료로 확산     ua.ingredient_id 필요   →  안 채움
```

즉 `ingredient.allergen_group` 컬럼이 **유일한 방어선**이고 허용치가 없는 하드컷입니다. 온보딩이 `category_id` 를 채우도록 고치면 ② 가 살아납니다.

## 6. 요리 계열 (G-30) — 반영 완료

`recipe.cuisine_family` 가 46,353건 전수 NULL 이라 B 가 만든 유형 슬롯과 `f_cuisine` 이 대상 후보를 못 찾았습니다.

**규칙 기반(안 b)으로 정했습니다.** 태그 매핑(안 a)은 약 12시간이 들고 태그 없는 레시피가 26.1% 라 상한이 74% 인데, 규칙 기반은 반나절에 61.7% 입니다.

```text
레시피 46,353건 · 배정 28,604건 (61.7%)
korean 21,475 · western 5,338 · japanese 847 ·
chinese 777 · asian_other 151 · fusion 16
```

`recipe` 와 `recipe_feature` 를 한 트랜잭션에서 맞춥니다. 조회는 `recipe_feature` 를 보고 온보딩 대조는 `recipe` 를 보므로, 갈리면 조용히 어긋납니다.

### 순서가 규칙의 절반이다

```text
① 태그(퓨전 제외)  ② 비한식 제목  ③ 한식 제목  ④ 퓨전 태그  ⑤ 시그니처 재료
```

비한식을 한식보다 먼저 보는 이유는 `김치파스타` 가 양식이어야 하기 때문이고, 한식 제목을 재료보다 먼저 보는 이유는 다진마늘·참기름·고춧가루가 거의 모든 레시피에 있어(실측 27,359건) 재료를 먼저 보면 **전부 한식이 되기** 때문입니다.

### 사람이 표본 120건을 검수했고 규칙 셋을 고쳤습니다

| 지적 | 근거 | 조치 |
|---|---|---|
| `카레` → `asian_other` | 표본 16건이 **전부** 한국식 카레라이스 | 한식 규칙으로 옮김 (463건) |
| `퓨전` 태그 → `fusion` | 표본 20건 중 **10건이 다른 계열** | 제목 규칙 **뒤로** 내림 (43 → 16건) |
| `짜장` → `chinese` | 짜파게티·짜장라면이 걸림 | 제외어 추가 (157건 중 29건) |

작성자가 쓴 `퓨전` 은 "섞어 만들었다" 는 뜻이고 우리 모델의 `fusion` 은 **"어느 선호에도 안 걸림"** 이라 뜻이 다릅니다. `fusion` 은 `ONBOARDING_CUISINES` 에 없어 사용자가 고를 수 없으므로, 그 라벨이 붙으면 유형 기능에서 통째로 보이지 않습니다. 그래서 제목 신호가 있으면 그쪽을 먼저 쓰고, 신호가 없을 때만 `fusion` 으로 둡니다.

`exclude` 절을 새로 뒀습니다. 부분 문자열 함정을 막습니다 — `치커리`(채소)가 `커리` 에 걸려 54건 중 10건이 아시안으로 갔고, `짜파게티` 는 한국 제품인데 `짜장` 에 걸렸습니다.

### 못 정하면 비운다

전부 실패하면 NULL(17,749건)입니다. 한식으로 폴백하면 100% 가 되지만 그중에 분류 못 한 양식·퓨전이 섞여 있고, 한식으로 박으면 그 사용자가 영영 못 만납니다. 비한식 계열은 폴백을 켜도 숫자가 같습니다.

`seeds/validate.py` 가 규칙이 taxonomy 의 family 축 밖 계열을 내면 ERROR 로 막습니다. 시드가 한때 `southeast_asian`(축에 없음)과 `italian`(세분 코드)을 뱉었는데, 그대로 배정하면 온보딩이 고른 값과 영영 안 만나면서 UPDATE 는 성공합니다.

## 7. F-90 — A 소유 파일 6개 승인

B 가 유형 슬롯을 만들며 A 파일 6개(`enums.py`·`schema.py`·`stage.py`·`test_contract.py`·`docker-compose.yml`·`Makefile`)를 고쳐 main 에 넣었습니다.

**승인합니다.** `make contract` 종료 코드 0, 98건 그대로입니다. 추가한 `CuisineFamily`·`ONBOARDING_CUISINES`·`is_cuisine_slot` 에 사유 주석이 달려 있고 `test_cuisine.py` 가 시드 family 축과의 일치를 강제합니다.

**단서 하나**를 주석에 박았습니다 — 17번째 키가 보는 `cuisine_family` 는 6절이 반영되기 전까지 실 DB 에서 **항상 False** 입니다. 목업에서만 True 가 나옵니다.

## 8. 남은 것

| | 누가 | 무엇 |
|---|---|---|
| 로그 두 키 배선 | **B** | `REQUIRED_TRACE_PARAMS` 추가 + `load_batch_versions()` 호출 |
| `occurred_at` | **B** | `EventIn` 에 선택 필드 (tz 필수, 없으면 수신 시각) |
| 알러젠 어휘 | **B** | `generate_mock_fixtures.py` 를 소문자 10종으로 |
| 온보딩이 팬트리를 채우는가 | **B** | 4절의 질문. 답에 따라 게이트를 되돌릴 수도 있습니다 |
| 요리 계열 반영 | A | 표본 정확도 확인 후 |
| 골든 픽스처 표본 조건 | A | "필수 0건" 8건이 새 게이트에서 빠지는 행입니다. `n_unmatched = 0` 으로 좁혀 다시 찍습니다 |
| `RecipeFeature` 로더 | **B** | `retrieve()` 가 5칸만 돌려줘 `f_taste`·`f_expiring`·`f_popularity` 가 못 도는 상태입니다 |

## 9. 알아 두면 좋은 함정 셋

세션 중에 실제로 물린 것들입니다.

**`infra.db.cursor()` 는 `commit` 기본값이 `False`** 입니다. DDL 을 적용했는데 롤백되고 **에러가 안 났습니다.** 숫자가 안 바뀌어서 알아챘습니다. `cursor(commit=True)` 를 명시하십시오.

**`seeds/ingredient.csv` 는 `#` 를 주석으로 읽지 않습니다.** CSV 라서요. 주석 한 줄을 넣었더니 `validate.py` 가 에러 목록도 없이 `TypeError` 로 죽었습니다.

**`scripts/reco/bench/` 는 ruff 검사에서 제외돼 있습니다**(`pyproject.toml` 의 `extend-exclude`, Tech Lead 승인 문서 있음). 파일을 직접 지정하면 그 설정이 우회되므로, 저장소 게이트와 다른 결과가 나옵니다.
