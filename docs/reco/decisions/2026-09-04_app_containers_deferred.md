# reco-api · dashboard 컨테이너 정의를 지움

**정하는 것**: `deploy/docker-compose.yml` 에서 `reco-api` · `dashboard` 서비스를 지우는 근거와 되살리는 조건.

**적용 대상**: 추천 파트의 배포 구성

**버전**: 1.0.0 · **최종 수정**: 2026-09-04 · **작성자**: 박재우

---

## 1. 무엇을 지웠나

`deploy/docker-compose.yml` 의 서비스 두 개입니다. 둘 다 `profiles: ["app"]` 뒤에 있었고, `make up-all` 만 이들을 불렀습니다.

```text
reco-api     build.dockerfile = db/app/Dockerfile.api
dashboard    build.dockerfile = db/app/Dockerfile.dashboard
```

`Makefile` 의 `up-all` 타깃도 함께 지웠습니다.

## 2. 지운 이유

**두 Dockerfile 이 저장소에 존재한 적이 없습니다.** 이전 저장소(`ai_server`)의 마지막 커밋 `a093422` 에서도 같은 경로였고, `db/app/` 이라는 디렉토리 자체가 어느 시점에도 없었습니다. `--profile app` 을 켜면 언제나 빌드에 실패합니다.

프로필 뒤에 숨어 있어 아무도 실행하지 않았기 때문에 드러나지 않았을 뿐입니다. 남겨두면 다음 사람이 `make up-all` 을 쳐 보고, 실패하고, 원인을 찾는 데 시간을 씁니다. **동작하지 않는 설정은 문서가 아니라 함정입니다.**

## 3. 되살리는 조건

추천 API 와 대시보드를 **컨테이너로 배포하기로 정하는 시점**입니다. 그때 다음을 함께 만듭니다.

- [ ] `deploy/api/Dockerfile` — `uvicorn main:create_app --factory` 진입점
- [ ] `deploy/dashboard/Dockerfile` — Streamlit 진입점
- [ ] `docker-compose.yml` 의 서비스 정의와 `make up-all`

지금은 로컬에서 `uvicorn` 과 `streamlit` 을 직접 띄웁니다. 남은 기간이 3주이고 컨테이너화가 그 안의 산출물이 아닙니다.

## 4. 함께 지운 것

같은 정리에서 아래도 지웠습니다. 근거는 각각 다릅니다.

| 대상 | 근거 |
|---|---|
| `requirements/` (6개) | `02` 의 2.1 — `requirements.txt` 를 만들지 않습니다. 설정은 `pyproject.toml` 하나입니다. 이 파일들은 `uv export` 산출물이라 고유 정보가 없고, `pyproject` 를 고친 뒤 낡아 `make requirements-check` 가 실패하고 있었습니다. |
| `docs/rules/00_aiserver_rules.md` | `02` 의 2.4 — 규칙 원문은 `docs/convention/` 에만 둡니다(MUST). `01`·`02`·`03` 이 같은 내용을 대체합니다. |
| `Makefile` 의 `requirements` · `requirements-check` | 위 삭제로 대상이 사라졌습니다. |

## 구성 근거

설정 파일에서 "언젠가 쓸 것"을 지우는 판단에는 기준이 필요합니다. 여기서 쓴 기준은 **지금 실행하면 성공하는가** 하나입니다. 실행되지 않는 정의는 의도를 남기지 못합니다 — 의도는 이 문서처럼 글로 남겨야 남습니다.

되살리는 조건을 체크리스트로 적어 둔 것은, 지웠다는 사실보다 **무엇을 갖춰야 되살릴 수 있는지**가 다음 사람에게 필요한 정보이기 때문입니다.
