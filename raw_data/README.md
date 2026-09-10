# raw_data/ — 저장소에 없는 것

> 작성자: 박재우 · 작성일: 2026-09-03

여기 있어야 하는 파일은 **git 에 올리지 않는다** (`.gitignore`).
이 README 하나만 커밋된다.

## 무엇이 필요한가

| 파일 | 크기 | 받는 곳 |
|---|---|---|
| `recipe_raw_data.jsonl` | 약 209MB · 46,552행 | **팀 채널 / 공유 드라이브** — 각자 내려받아 여기 둔다 |

## 왜 안 올리나

1. **GitHub 파일 한도는 100MB 다.** 209MB 를 커밋하면 push 가 통째로 거부된다.
   한 번 커밋되면 파일을 지워도 히스토리에 남아 `git filter-repo` 가 필요하다.
2. 🔴 **후기 작성자 닉네임이 평문으로 들어 있다** — 고유 177,318종.
   `reviews` 원소가 `"<닉네임><타임스탬프><본문>"` 형태다.
   DB 에는 닉네임 대신 `author_hash`(HMAC-SHA256 앞 16hex)만 넣는데
   (`scripts/reco/load_recipes.py:72`), 원본이 공개되면 그 해시를 지키는 의미가 사라진다.

## 없으면 무엇이 막히나

**막히는 것** — 이것들은 원본 파일이 있어야 한다.

```
python scripts/reco/load_recipes.py raw_data/recipe_raw_data.jsonl   크롤 적재
python scripts/reco/bench/unmatched_dump.py                          미매칭 표현 덤프
make doc-check                                    문서 수치 대조 (recipe 행 필요)
```

**되는 것** — 원본 없이도 된다.

```
./scripts/reco/setup.sh --track ?    환경 준비 · 시드 적재까지
make contract           계약 검증        (DB 도 불필요)
make normalize-test     재료 정규화
make probe-all          크롤러 어댑터
make smoke · smoke-py   Retrieval (합성 레시피로)
```

## 적재 순서

```bash
# 1. 파일을 여기에 둔다
#    raw_data/recipe_raw_data.jsonl

# 2. REVIEW_SALT 를 셸에 올린다 (팀 채널에서 받은 기존 값 — 새로 만들지 않는다)
set -a; . ./.env; set +a

# 3. 적재
.venv/bin/python scripts/reco/load_recipes.py raw_data/recipe_raw_data.jsonl
```

> 🔴 `REVIEW_SALT` 를 새로 만들면 이미 적재된 후기 624,422건의 작성자 해시와
> 어긋난다. "같은 사람이 쓴 후기" 판정이 과거와 달라지고 되돌릴 수 없다.
