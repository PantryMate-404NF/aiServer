# 데이터 파트의 09-17 요청 5건을 엔진에 반영한 결정

**정하는 것**: 데이터 파트(A)의 결정 기록 `2026-09-17_data_track_dictionary_and_versioning.md` 8절이 파트 B 에 넘긴 다섯 가지를 어떻게 처리했는지, 그 과정에서 정한 규칙 넷(D-48~D-51)과 되돌릴 조건, 그리고 A 브랜치 검수에서 찾아 A 에 돌려주는 것 셋.

**적용 대상**: 추천 엔진(파트 B)과 데이터 파트(A). 백엔드는 4절의 `occurred_at` 만 보면 됩니다.

**버전**: 1.0.0 · **최종 수정**: 2026-09-18 · **작성자**: 유재현

---

## 1. 결정 요약

A 의 요청 다섯 가지는 전부 타당했고 전부 반영했습니다. 보류한 것은 A 가 함께 물은 "온보딩이 `category_id` 를 채우게 할 것인가" 하나이며, 그것은 백엔드 스키마 회신 뒤에 정합니다. 브랜치는 `feat/recommend-engine-core` 이고 `origin/develop-data-part` 17 커밋을 먼저 합친 뒤(`1aa0507`) 작업했습니다.

| 요청 | 전 | 후 | 식별자 |
|---|---|---|---|
| 빈 팬트리 콜드스타트 | 완화 사다리 k=2→3→4 를 밟았고, 조인 게이트가 조여진 뒤에는 양념 제조법 94건이 통과해 인기순 폴백이 안 걸렸습니다 | 사용자가 넣은 재료가 없으면 첫 조회부터 인기순 | D-48, F-97 |
| Mock 알러지 어휘 | 생성기가 대문자 18종을 따로 들고 있어 DDL 의 소문자 10종과 갈렸습니다 | 생성기가 `seeds/ingredient.csv` 에서 그룹을 읽고, Mock 재료 이름도 시드 이름으로 | D-49, F-99 |
| 동결 키 2종 | `REQUIRED_TRACE_PARAMS` 10종. 어느 피처판·군집판의 추천인지 로그에 없었습니다 | `feature_version` · `cluster_version` 을 더해 12종. 호출자가 `load_batch_versions()` 로 읽어 넘기고 목업은 None | D-51, F-100 |
| `EventIn.occurred_at` | 이벤트 시각 = 서버 수신 시각뿐 | 선택 필드. 시간대 없는 값은 거부, 없으면 수신 시각 | G-26, F-101 |
| `RecipeFeature` 로더 | `retrieve()` 가 5칸만 돌려줘 맛·인기·조리시간이 서빙에 닿지 않았습니다 | `repository.load_recipe_features()` · `load_corpus_stats()`. 변환은 `context.recipe_feature_from_row` 하나 | D-50, F-102 |

기록 정정 4건(A 의 5절)은 검증 기록·안건·작업 기록에 반영했고(F-103), `enums.py` 의 낡은 주석("`cuisine_family` 전수 0건")도 고쳤습니다.

---

## 2. 빈 팬트리는 첫 조회부터 인기순입니다 (D-48)

A 가 조인 게이트를 `n_unmatched = 0` 으로 조인 뒤 빈 팬트리(상비 재료 39종만) 후보가 1,498건에서 94건으로 줄었습니다. 이 94건은 필수 재료가 아예 없는 레시피, 즉 쌈장·초고추장 같은 양념 제조법입니다. 문제는 그 수가 완화 종료 기준(취향 없으면 52건)보다 커서 사다리가 첫 칸에서 멈춘다는 점입니다. 신규 사용자가 양념 제조법 스무 개를 받는데 에러는 없습니다.

그래서 문맥에 "사용자가 직접 넣은 재료"(`UserContext.own_pantry_ids`)를 따로 두고, 그것이 비면 `candidate.first_plan(policy, pantry_is_bare=True)` 가 인기순 계획을 바로 냅니다. 조회 집합(`pantry_ids`)은 그대로 상비 재료를 포함하므로 `f_pantry_use` 는 변하지 않습니다. `own_pantry_ids` 를 안 넘기는 호출자(검사·평가 도구)는 `pantry_ids` 전부를 사용자 것으로 봅니다.

**되돌릴 조건**: 온보딩이 `pantry_item` 을 채우게 되면 `own_pantry_ids` 가 비지 않으므로 코드 변경 없이 사다리로 돌아갑니다. A 가 게이트를 다시 풀어 빈 팬트리 후보가 충분히 다양해지면 이 규칙을 지워도 됩니다. 시뮬 1,600명에서 냉장고 없는 B 집단 800명 전원이 첫 조회부터 인기순이었고, 냉장고 있는 A 집단은 폴백 없음 41 · 완화 139 · 인기순 620 이었습니다.

---

## 3. Mock 알러지 어휘는 시드에서 읽습니다 (D-49)

정본은 DDL 의 소문자 10종이고 `seeds/ingredient.csv` 가 재료마다 그 그룹을 답니다. 생성기가 자기 표를 들고 있으면 언젠가 다시 갈립니다. 그래서 Mock 재료 60종의 이름을 시드 이름과 같게 두고(달걀·체다치즈·플레인요거트·맛살·배추김치·청양고추 여섯을 바꿨습니다) 그룹은 시드에서 읽습니다. 시드에 없는 이름이면 생성기가 멈춥니다.

시드의 판단을 그대로 따릅니다. 배추김치가 `shellfish`(젓갈), 마요네즈가 `egg`, 참치캔·어묵·맛살이 `fish` 인 것은 시드의 것이고, 다르다고 생각하면 시드를 고칩니다. 페르소나 4명의 알러지도 코드로 바꿨습니다(EGG→egg, MILK→dairy, 갑각류 다섯→shellfish·fish, WHEAT·PEANUT→gluten·nut). 그 결과 1012 의 컷은 이전 대문자 표의 72건에서 51건(밀가루·면류·견과 셋)으로 달라졌습니다. 시뮬의 `sim_seed.ALLERGEN_MAP`(소문자→대문자 표)은 지웠고, 그때까지 검사 대상이 아니던 `sesame` 도 참기름으로 잡힙니다. 냉장고 재료 이름이 Mock 에 없던 건수도 1,836 에서 1,577 로 줄었습니다.

같은 자리에서 시드 재생성 누락도 처리했습니다(F-98). A 가 변환기에 `buckwheat` 을 넣었는데 `deploy/seed/sim/04_user_allergy.sql` 은 재생성되지 않아 11명의 메밀 알러지가 저장소 시드에 없었습니다. 재생성했고(다른 여섯 파일과 `stats.json` 은 동일), `tests/unit/sim/test_sim_seed.py::test_committed_seed_matches_the_generator` 가 앞으로 생성기와 저장소 시드가 어긋나면 빨개집니다.

---

## 4. 배치 판 번호 두 키와 `occurred_at` (D-51 · G-26)

`feature_version` 은 A 쪽에서 다섯 번 갈아탔는데 로그에 칸이 없었습니다. 재정규화하면 같은 레시피의 피처 값이 바뀌므로 이 키 없이는 "이 추천이 어느 피처판이었나" 를 나중에 물을 수 없습니다. `cluster_version` 도 같습니다. 둘을 `REQUIRED_TRACE_PARAMS` 에 더했고(`test_contract.py` 의 10종 → 12종), `policy.trace_params()` 와 `service.rank_candidates(batch_versions=)` 로 실립니다. 정책은 DB 를 보지 않으므로 값은 호출자가 `repository.load_batch_versions()` 로 읽어 넘기고, 목업은 None 을 싣습니다. 키 자체가 소급 불가라 None 이라도 키는 항상 있습니다.

`EventIn.occurred_at` 은 선택 필드이고 시간대가 없는 값은 계약(pydantic validator)에서 거부합니다. 없으면 서버 수신 시각입니다. **백엔드가 오프라인 동기화 배치를 보낼 때 이 값을 실어야** 시간 감쇠와 요일·시간대 주기가 뭉개지지 않습니다. 이 사실은 백엔드 요청 문서 회신 때 함께 전합니다.

---

## 5. `RecipeFeature` 와 코퍼스 통계 로더 (D-50)

`repository.load_recipe_features(recipe_ids, include_test=False, month=None)` 가 `recipe_feature` 와 `recipe.title` 을 후보 id 로 읽고, `load_corpus_stats()` 가 `feature_stats` 의 최신 μ 와 `ingredient.freq_count` 로 IDF(`log(N/freq)`)를 만듭니다. 행을 엔진 모델로 바꾸는 것은 `context.recipe_feature_from_row` 하나이며 골든 픽스처 검사도 같은 함수를 지납니다. 두 경로가 다른 변환을 하면 검사가 통과한 값과 서빙 값이 조용히 갈라지기 때문입니다.

| 칸 | 규칙 | 이유 |
|---|---|---|
| 없는 값 | None 으로 둡니다. 0 으로 메우지 않습니다 | 0 은 "계산했더니 0" 이고 None 은 "못 쟀다" 입니다. Zero-Drop 이 분모에서 함께 빼는 쪽이 None 입니다 |
| `difficulty` | DB 의 1~5 를 `(d-1)/4` 로 0~1 | 엔진의 `f_skill_fit` 이 0~1 을 전제합니다 |
| `season_vec` | 달(`month`)이 있어야 `season_score` 하나가 됩니다. 12칸이 아니면 None | 달별 12칸이라 지금 달을 모르면 점수가 없습니다 |
| `cuisine_family` | `normalize_cuisine` 을 지나 모르는 값은 None | 열거형 밖의 값이 슬롯에 들어오면 안 됩니다 |
| `quality_score` | DB 값 그대로(지금은 전건 0) | 0 을 None 으로 바꾸는 것은 A 의 데이터 결정입니다 |
| μ 없음 | `flavor_mean=None`, `stats_version=None` | 0 벡터를 넣으면 모든 레시피가 평균에서 멀어 보여 맛 점수가 엉뚱하게 살아납니다 |

`CorpusStats.stats_version` 을 새로 두어 `recommendation_log.stats_version` 에 실을 수 있게 했습니다. DB 없는 검사 4건(`test_repository_loaders.py`)이 SQL 의 모양과 변환을 봅니다. **실 DB 와의 대조는 하지 못했습니다** — 이 PC 의 Docker Desktop 데몬이 꺼져 있었습니다(E-17). 라우터 연결(M-01·M-02·M-04)은 백엔드 스키마 회신 뒤로 미룹니다.

---

## 6. 기록 정정 4건 (F-103)

A 가 5절에서 지적한 넷을 그대로 받았습니다.

| 문서에 있던 것 | 실측 | 고친 자리 |
|---|---|---|
| 코퍼스 평균 μ 가 부트스트랩 영벡터 | 실값(`feature_stats`, `stats_version` 7) | 검증 기록 S-04 주석 |
| `cluster_id` 없는 후보의 균등 폴백 경로 | 46,353건 전량 배정, NULL 0건이라 실 DB 에서는 안 오는 경로 | 안건 G-27 은 그대로 두되 "실 DB 에서는 안 걸림" 을 적음 |
| `ingredient_substitute` 0행이 미완성 | "측정 전에 구현하지 않는다" 는 결정 | 작업 기록 A 항목 |
| 검증 8.1 "값 나온 9종" | Mock 한정. 실 DB 는 `f_quality` 전건 0 · `f_cuisine` 은 배정 61.7% → 7종 | 검증 기록 V-18 주석, 안건 N-09 |

마지막 줄의 뜻: `f_quality` · `f_pantry_use` 를 가중치 재배분 후보로 지목했던 것은 Mock 숫자였습니다. 실 데이터에서 다시 잽니다.

---

## 7. 파트 A 에게 돌려주는 것

A 브랜치 17 커밋을 검수했습니다. 게이트는 전부 종료 코드 0 이었고(ruff · format · mypy 67 · pytest 337 · contract 98 · 시나리오 PASS) 우리 브랜치와 충돌 없이 합쳐졌습니다. 아래 셋은 A 의 파일이라 고치지 않고 돌려드립니다.

**`cuisine_build.judge()` 가 태그 순회 순서에 따라 다른 계열을 냅니다 (F-104, G-31).** `judge()` 의 첫 규칙이 `for tag in tags` 로 `set` 을 돌면서 `tag_map` 에 있는 첫 태그로 끝냅니다. 태그가 둘 이상 매핑되는 레시피는 파이썬 해시 시드에 따라 결과가 달라집니다. 재현:

```text
for s in 0 1 2; do PYTHONHASHSEED=$s uv run python -c "
from features.recommend.ingest.cuisine_build import Rules, judge
r = Rules.load()
first = {}
for tag, fam in r.tag_map.items(): first.setdefault(fam, tag)
tags = [first['chinese'], first['korean']]
print($s, tags, judge('', set(tags), set(), r))
"; done

0 ['중식', '한식'] ('chinese', 'by_source_tag')
1 ['중식', '한식'] ('korean', 'by_source_tag')
2 ['중식', '한식'] ('korean', 'by_source_tag')
```

`build()` 의 docstring "같은 시드·같은 데이터면 같은 결과가 나온다" 가 지금은 참이 아닙니다. 태그를 `sorted()` 로 돌거나 우선순위를 정하면 됩니다. 몇 건이 이런지는 원본 태그를 봐야 알 수 있어 세지 못했습니다.

**결정 기록과 시드 주석의 숫자가 다릅니다 (F-105).** 결정 기록 1절·6절은 배정 28,604건, `seeds/cuisine_taxonomy.yaml` 의 실측 주석은 28,621건입니다. 위 비결정 때문에 실행마다 다를 수 있으니, 고친 뒤 한 번 더 세어 하나로 맞춰 주십시오. 결정 기록 8절의 "남은 것" 표에서 B 몫 다섯 줄은 이 문서로 닫혔습니다.

**골든 픽스처에 `cuisine_family` 가 없습니다 (F-106).** `ingest/golden.py` 의 KEYS 14종에 계열 칸이 없어 B 의 로더 검사가 골든으로 유형 변환을 검증하지 못합니다. 다음에 찍을 때 `cuisine_family` 와 `dish_type` · `season_vec` 을 함께 넣어 주시면 `recipe_feature_from_row` 가 실제 행으로 검증됩니다.

A 가 물은 "온보딩이 `pantry_item` 을 채우는가" 는 B 도 모릅니다. 백엔드 요청 문서(`docs/backend_schema_request.md`)의 회신에 달려 있고, 답이 올 때까지 2절의 규칙이 신규 사용자를 지킵니다. `category_id` 로 알러젠 전개 ② 를 살리는 것도 같은 회신 뒤에 정합니다.

---

## 8. 되돌릴 조건

- D-48: 온보딩이 팬트리를 채우면 자동으로 사다리로 돌아갑니다. 규칙 자체를 지우는 것은 A 가 게이트를 다시 풀 때입니다.
- D-49: 시드가 그룹을 바꾸면 생성기를 다시 돌리는 것으로 끝납니다. 생성기에 표를 되살리지 않습니다.
- D-50: 골든에 `cuisine_family` 가 들어오면 로더 검사를 골든 기준으로 옮깁니다. 변환 규칙(`difficulty`, `season_vec`)이 A 의 DDL 주석과 어긋나면 DDL 이 이깁니다.
- D-51: 라우터를 연결할 때(M-01) `load_batch_versions()` 호출이 빠지면 키는 있고 값만 None 이 됩니다. `test_db_cutover.py` 의 M-01 못이 그 자리를 가리킵니다.
