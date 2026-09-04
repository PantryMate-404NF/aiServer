# requirements/ — 자동 생성 사본

> 작성자: 박재우 · 작성일: 2026-09-03

**정본은 `pyproject.toml` + `uv.lock` 입니다.** 이 폴더의 `.txt` 는 거기서 뽑아낸
사본이고, uv 를 쓸 수 없는 환경(순정 pip · 코랩 · 채점 서버)만을 위한 것입니다.

## 🔴 손으로 고치지 마세요

고치면 락과 조용히 갈라지고, **갈라진 걸 아무도 눈치채지 못합니다.**
버전 하나가 달라진 채로 "내 컴퓨터에선 되는데" 가 시작되는 자리입니다.

도구를 추가·변경하려면:

```bash
# 1. pyproject.toml 의 dependencies / optional-dependencies 를 고친다
# 2. 사본을 다시 뽑는다
make requirements
# 3. pyproject.toml · uv.lock · requirements/ 를 한 커밋으로 올린다
```

어긋났는지 확인:

```bash
make requirements-check      # 다르면 실패하고 어느 파일인지 알려준다
```

## 어느 파일을 쓰나

| 파일 | 트랙 | 묶음 | 패키지 수 |
|---|---|---|---|
| `base.txt` | (공통) | 없음 | 7 |
| `A.txt` | A 데이터 | `ml` | 14 |
| `B.txt` | B 엔진 | `api` + `ml` | 22 |
| `C.txt` | C 관측 | `dash` + `ml` + `obs` | 105 |
| `all.txt` | (전부) | `api`+`dash`+`obs`+`ml`+`rank-v1` | 106 |

```bash
pip install -r requirements/B.txt
```

## 여기에 없는 것

- **`embed` (sentence-transformers)** — 어느 파일에도 안 넣었습니다.
  torch 를 포함해 2GB 를 끌고 오는데, A-12 가 임베딩 없는 TF-IDF→SVD 판으로
  확정돼서 **이번 3주에 부를 코드가 없습니다.**
  필요해지면 `make install TRACK=? EXTRA=embed` 로 uv 쪽에서 얹으세요.
- **dev 그룹 (httpx2)** — 테스트용이라 사본에서 뺐습니다 (`--no-dev`).
  `make contract` 를 돌리려면 uv 쪽 설치가 필요합니다.
