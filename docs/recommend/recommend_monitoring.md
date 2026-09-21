# 추천 엔진의 평가와 모니터링

**정하는 것**: 추천 엔진이 내보내는 목록을 숫자로 쌓고(Prometheus), 그려 보고(Grafana), 지금 고칠 것을 뽑아 주는(관리자 페이지) 관측 체계 — 무엇을 재는지, 어디에 쌓이는지, 어떻게 띄우는지, 규칙이 무엇을 경고하는지, 그리고 아직 못 하는 것

**적용 대상**: 추천 엔진을 운영하거나 개선하는 사람(트랙 C). 백엔드와 클라우드 팀은 5절의 띄우는 법과 6절의 보안만 보면 됩니다

**버전**: 1.1.0 · **최종 수정**: 2026-09-22 · **작성자**: 유재현

---

## 1. 왜 필요한가

추천 서버는 설계상 실패하지 않습니다. 후보가 모자라면 인기순으로 채우고, 신호가 없으면 그 신호를 빼고 계산하고, 로그 적재가 실패하면 예외를 삼키고 200 을 냅니다. 그래서 상태코드와 에러 로그만 봐서는 품질이 무너져도 모릅니다. 파트 B 를 만들며 실제로 만난 결함이 전부 그 모양이었습니다 — 후보 500건이 같은 점수였고, 같은 냉장고의 40명이 같은 목록을 받았고, 새우 알레르기 사용자의 1위가 건새우볶음이었습니다. 셋 다 에러가 없었습니다.

이 체계는 그 자리를 봅니다. 묻는 것은 여섯 가지입니다.

| 물음 | 보는 지표 |
|---|---|
| 엔진이 답은 하고 있는가 | 요청 수 · 지연 · 후보 부족(`degraded`) · 목록 길이 |
| 어느 신호가 꺼져 있는가 | 신호별 값 없음 비율. 가중치가 있는데 값이 없는 신호 |
| 탐색은 설계대로 도는가 | 칸 종류별 노출 · 노출확률 · 탐색 부족분 |
| 후보는 어디서 사라지는가 | 단계별 걸러짐 · 재정렬 탈락(같은 요리의 판본 등) |
| 들어오는 입력은 온전한가 | 냉장고 크기 · 모르는 알레르기 라벨 · 400 의 사유 |
| 사용자는 반응하는가 | 이벤트 종류 · 순위 · 추천과 이어졌는가 · 칸별 클릭률 |

---

## 2. 구조

```text
추천 AI 서버 (FastAPI)
  ├─ 요청마다 관측을 남김 ──▶ 등록부(프로세스 메모리)
  ├─ GET /metrics ◀──────────── Prometheus 가 15초마다 긁어 90일 보관 ──▶ Grafana 대시보드 · 경보 규칙
  ├─ GET /v1/admin/monitoring/summary   등록부를 읽어 요약과 할 일을 계산 (JSON)
  └─ GET /admin/monitoring              위 요약을 그리는 관리자 페이지 (HTML 한 장)
```

| 자리 | 파일 | 하는 일 |
|---|---|---|
| 공통 | `src/utils/metrics.py` | 등록부, HTTP 요청 수 · 지연 · 400 의 사유. 도메인 지식 없음 |
| 쓰는 쪽 | `src/features/recommend/evaluation/monitor.py` | 추천 · 이벤트 · 온보딩 한 건을 지표로 남김 |
| 읽는 쪽 | `src/features/recommend/evaluation/diagnosis.py` | 등록부 → 요약 지표 → 할 일 |
| HTTP | `src/features/recommend/evaluation/router.py` · `admin.html` | 요약 API 와 관리자 페이지 |
| 수집 | `deploy/prometheus/` | 수집 설정 · 대상 · 경보 규칙 |
| 그림 | `deploy/grafana/provisioning/` | Prometheus 데이터소스와 대시보드 `recommend_engine.json` |

**엔진을 계약으로만 봅니다.** 관측이 읽는 것은 요청 · 응답 · 로그의 계약 모델뿐입니다. 엔진 안에는 관측 코드가 한 줄도 없습니다. 그래서 목업이 답하든 실엔진이 답하든 같은 코드가 돌고, 엔진이 평가 축을 부르지 않는다는 축의 방향(02 의 2.2)도 지켜집니다. 관측을 부르는 곳은 라우터입니다.

**관측은 서빙을 깨뜨리지 않습니다.** 관측이 예외를 내도 응답은 그대로 나가고 실패는 로그에 남습니다. 지표가 틀리는 것과 추천이 안 나가는 것은 무게가 다릅니다.

---

## 3. 지표

전부 `/metrics` 로 나갑니다. 서버의 등록부는 프로세스 하나의 것이라 **재시작하면 0 부터 다시 셉니다.** 그래서 대시보드와 경보는 전부 `rate()` · `increase()` 로 봅니다. 누적값을 그대로 그리면 재시작 때마다 절벽이 생기고 그 절벽이 장애처럼 보입니다.

| 지표 | 라벨 | 뜻 |
|---|---|---|
| `http_requests_total` | `method` `route` `status` | 요청 수. `route` 는 값이 아니라 틀입니다 |
| `http_request_duration_seconds` | `method` `route` | HTTP 왕복 시간 |
| `http_validation_failures_total` | `route` `code` | 400 의 사유(`missing` · `extra_forbidden` 등) |
| `reco_requests_total` | `engine` `user_mode` `degraded` | 추천 응답 수. `engine` 은 `mock` 또는 `real` |
| `reco_latency_seconds` | `engine` | 엔진이 잰 처리 시간 |
| `reco_stage_latency_seconds` · `reco_stage_out_count` | `stage` | 단계별 시간과 넘긴 후보 수 |
| `reco_filtered_total` · `reco_dropped_total` | `stage` `reason` | 조회에서 걸러진 것 · 재정렬에서 빠진 것 |
| `reco_list_length` | | 서빙한 목록의 길이 |
| `reco_items_total` · `reco_item_score` | `slot` | 칸 종류별 노출과 점수. 서버가 세는 노출입니다 |
| `reco_item_missing_count` | | 서빙한 아이템의 부족 재료 수 |
| `reco_exploration_propensity` | | 탐색 칸의 노출확률 |
| `reco_feature_items_total` | `feature` `state` | 신호가 값을 가졌는가(`value`) 없었는가(`none`) |
| `reco_feature_value_total` | `feature` | 값 합계. 평균의 분자입니다 |
| `reco_request_pantry_size` | | 요청에 실려 온 냉장고의 재료 수 |
| `reco_allergy_labels_total` | `source` `known` | 받은 알레르기 라벨. `known="no"` 는 막지 못한 라벨 |
| `reco_events_total` | `event_type` `linked` `slot` | 행동 이벤트. `linked` 는 `request_id` 가 있었는가 |
| `reco_events_rejected_total` · `reco_event_position` | `event_type` | 거부된 이벤트 · 반응이 나온 순위 |
| `reco_internal_events_total` | `key` | 서비스 내부 카운터. 삼킨 예외가 여기 모입니다 |

**라벨에 사용자 id · 레시피 id · 요청 id 를 넣지 않습니다.** 값의 가짓수만큼 시계열이 생겨 Prometheus 가 먼저 죽고, 그 값들은 개인정보이기도 합니다. 추적의 사유 키처럼 엔진이 정하는 문자열은 모양을 검사해 어긋나면 `other` 로 뭉칩니다. 라벨 이름의 허용 목록을 검사로 박아 두었습니다(`test_no_metric_carries_an_identifier_as_a_label`).

**운영 호출에는 추적이 없습니다.** 백엔드는 `include_trace=false` 로 부르므로 응답에 단계 정보가 없습니다. 추적은 로그에 그대로 남으므로 관측은 로그에서 읽습니다. 응답만 봤다면 개발 호출만 보이고 운영은 안 보였을 것입니다.

---

## 4. 규칙 — 지금 할 일

관리자 페이지와 경보 규칙이 같은 기준을 봅니다. 임계값은 전부 착수 추정치입니다(예시값, 실제 데이터로 대체 필요). 정본은 `diagnosis.py` 이고 `deploy/prometheus/rules/reco.yml` 이 같은 값을 씁니다. 둘이 어긋나지 않게 검사가 대조합니다.

| 심각도 | 규칙 | 기준 | 다음에 할 일 |
|---|---|---|---|
| critical | 막지 못한 알레르기 라벨 | 1건 이상 | 서버 로그에서 라벨을 찾아 `enums.ALLERGEN_LABELS` 나 `engine/allergy.py` 의 동의어에 더합니다 |
| critical | 삼킨 예외 | 내부 카운터의 `fail` · `error` 1건 이상 | 로그 적재나 취향 저장이 실패했는데 200 이 나간 것입니다 |
| critical | 시간 예산 초과 | `/v1/recommend` HTTP p95 3초 초과 | 백엔드는 그 전에 끊습니다. 단계별 지연을 봅니다 |
| warning | 목업이 답하는 중 | 목업 응답 1건 이상 | 품질 수치는 엔진의 것이 아닙니다(DB 전환 M-01) |
| warning | 꺼진 신호 | 가중치가 있는데 값 없음이 95% 이상 | 그 가중치 합과, 신호마다 무엇이 와야 켜지는지를 알려 줍니다 |
| warning | 후보 부족 | `degraded` 5% 초과 | 후보 완화 단계와 재료 사전을 봅니다 |
| warning | 탐색 부족 | 부족분 1칸 이상 | 탐색 풀이 작습니다 |
| warning | 계약 위반 | `/v1/recommend` 의 400 이 1% 초과 | `missing` 이면 `pantry` · `allergies` 누락입니다 |
| warning | 이어지지 않는 이벤트 | `request_id` 없는 이벤트 10% 초과 | 백엔드가 `request_id` 를 싣는지 확인합니다 |
| warning | 탐색이 개인화를 이김 | 탐색 칸 클릭률이 개인화 칸보다 높음 | 가중치를 다시 유도합니다(T-14) |
| info | 엔진 지연 | 엔진 p95 58ms 초과 | 단계별 지연을 봅니다 |
| info | 판본이 많음 | 요청당 100건 초과 | 원천 데이터의 중복입니다 |

비율 규칙은 요청 30건(클릭률은 노출 200건)이 쌓이기 전에는 판정하지 않습니다. 세 건 중 한 건은 33% 가 아니라 "아직 모른다" 입니다.

---

## 5. 띄우는 법

### 5.1 서버만 (Docker 없이)

```bash
uv run uvicorn main:create_app --factory --reload
```

`http://localhost:8000/admin/monitoring` 을 열고 내부 API 키를 넣습니다. 키는 그 탭에만 남고 닫으면 사라집니다. 지표 원문은 아래로 봅니다.

```bash
curl -H "X-Internal-Api-Key: $INTERNAL_API_KEY" http://localhost:8000/metrics
```

### 5.2 Prometheus 와 Grafana 까지

```bash
make up-obs        # grafana · mlflow · prometheus
make up-app        # 앱을 컨테이너로 띄울 때만
```

`make` 가 없는 PC(Windows)에서는 같은 일을 하는 명령을 직접 부릅니다. 모니터링에 필요한 둘만 띄우면 MLflow 이미지를 빌드하지 않아 빠릅니다.

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile obs up -d prometheus grafana
```

| 주소 | 무엇 |
|---|---|
| `http://localhost:9090/targets` | 수집 대상이 UP 인지 |
| `http://localhost:9090/alerts` | 경보 규칙 7개의 상태 |
| `http://localhost:3000` | Grafana. `추천시스템` 폴더의 "추천 엔진 — 평가와 모니터링" |

수집 대상의 기본값은 `host.docker.internal:8000`, 곧 호스트의 8000 번입니다. 호스트에서 `uvicorn` 으로 띄운 개발 서버와 `make up-app` 의 앱 컨테이너(8000 번을 호스트에 내놓습니다)를 둘 다 덮습니다. 바꾸려면 `deploy/prometheus/targets/reco-api.yml` 을 고칩니다. 30초 안에 반영되고 재시작은 필요 없습니다. 두 대상을 함께 적지 않습니다 — 닿지 않는 쪽이 계속 down 으로 떠 경보가 울립니다.

`deploy/.env` 에 `INTERNAL_API_KEY` 를 적고 앱과 같은 값으로 둡니다. 다르거나 비어 있으면 대상이 401 로 DOWN 이 됩니다. 값을 바꿨으면 `prometheus` 컨테이너를 다시 만듭니다(위 명령을 한 번 더). 포트와 보관 기간은 `PROMETHEUS_PORT`(기본 9090) · `PROMETHEUS_RETENTION`(기본 90d)으로 바꿉니다.

---

## 6. 보안

| 경로 | 인증 | 이유 |
|---|---|---|
| `GET /metrics` | 내부 키. `X-Internal-Api-Key` 또는 `Authorization: Bearer` | Prometheus 는 어느 판에서나 Bearer 를 붙일 수 있습니다. 키는 하나입니다 |
| `GET /v1/admin/monitoring/summary` | 내부 키 | 다른 API 와 같습니다 |
| `GET /admin/monitoring` | 없음 | 브라우저는 주소창으로 들어올 때 헤더를 붙일 수 없습니다. 페이지는 빈 껍데기이고 숫자는 요약에서만 나옵니다. 바깥 자원을 부르지 않습니다 |

수집기의 키는 설정 파일에 적지 않습니다. compose 가 `deploy/.env` 의 값을 시크릿 파일로 넘기고 Prometheus 는 그 파일을 읽습니다. 지표에는 개인정보가 없습니다(3절). 그래도 `/metrics` 와 관리자 페이지는 내부망에서만 닿게 두는 것이 맞습니다.

---

## 7. 지금 못 하는 것

**수치가 목업의 것입니다.** `/v1/recommend` 가 아직 목업으로 답합니다(DB 전환 M-01). 관측은 목업과 실엔진을 `engine` 라벨로 가르고 관리자 페이지 맨 위에 그 사실을 띄웁니다. 실엔진이 연결되면 코드 변경 없이 그 수치가 들어옵니다. 목업은 신호 값을 난수로 채우므로 "꺼진 신호" 규칙은 실엔진에서야 의미를 갖습니다.

**관리자 페이지는 지금 상태만 봅니다.** 서버가 뜬 뒤의 누적이라 재시작하면 0 입니다. 지난주와 비교하는 일은 Grafana 에서 합니다.

**패널의 모양은 사람이 눈으로 보지 않았습니다.** 2026-09-22 에 실제로 띄워 API 로 확인한 것은 이렇습니다 — 수집 대상 UP(시크릿 파일의 Bearer 키로 `/metrics` 통과) · 경보 규칙 7개 전부 health ok 이고 `RecoUnknownAllergyLabel` 이 의도대로 firing · 대시보드의 식 36개를 Prometheus 에 전부 물어 문법 오류 0 · Grafana 가 데이터소스 둘과 대시보드(패널 29)를 읽었고 Grafana 를 거친 질의가 값을 돌려줌. 그 과정에서 하나를 고쳤습니다. 비율 통계 넷이 분자의 시계열이 아직 없을 때 0 이 아니라 "값 없음" 으로 나와 `or vector(0)` 를 붙였습니다. 색 · 단위 · 범례가 보기 좋은지는 열어서 봐야 합니다.

**워커가 하나일 때만 맞습니다.** 등록부가 프로세스마다 따로라, uvicorn 워커를 늘리면 수집 때마다 다른 워커가 답해 수치가 널뜁니다. 늘릴 때는 `prometheus_client` 의 다중 프로세스 모드로 바꿔야 합니다.

**오프라인 평가는 없습니다.** nDCG 같은 순위 지표와 IPS 보정은 라벨이 쌓인 뒤의 일입니다. 여기서는 그 재료 — 노출확률, 순위별 반응, 칸별 반응 — 가 빠짐없이 쌓이는지만 봅니다.

---

## 8. 다음에 할 것

| 순서 | 일 | 조건 |
|---|---|---|
| 1 | Grafana 에서 대시보드를 열어 패널의 모양(색 · 단위 · 범례)을 눈으로 확인 | 없음 |
| 2 | 실엔진 연결 뒤 "꺼진 신호" 와 칸별 클릭률을 기준선으로 기록 | DB 전환 M-01 |
| 3 | 임계값을 실제 분포로 다시 정함 | 운영 트래픽 2주 |
| 4 | 가중치 재유도의 입력으로 칸별 · 순위별 반응을 내보냄 | 클릭 이벤트 누적 |
