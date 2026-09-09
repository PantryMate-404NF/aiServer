#!/usr/bin/env python3
"""크롤 JSONL → PostgreSQL 적재 (S6 · 04 3-1).

    .venv/bin/python ingest/loader.py raw_data/recipe_raw_data.jsonl
    .venv/bin/python ingest/loader.py raw_data/*.jsonl --limit 1000   # 시험 적재

## 이 코드가 없어서 크롤 데이터를 넣을 수 없었다

`ingest/` 에 어댑터와 프로브는 있었지만 **DB 에 INSERT 하는 코드가 0개**였다.

## 원칙

1. **원문 불변** — `recipe.raw_json` 과 `recipe_ingredient_raw` 는 크롤 그대로 넣는다.
   정규화 규칙이 바뀌면 정규화 결과만 다시 만든다 (설계 4-1).
2. **멱등** — 같은 파일을 두 번 넣어도 결과가 같다. `ON CONFLICT` 로 처리한다.
3. **COPY 대신 executemany** — 46,552건이면 배치 INSERT 로 충분하고,
   `ON CONFLICT` 멱등성을 COPY 로는 못 얻는다.
4. 🔴 **닉네임 원문을 저장하지 않는다** (02 C-5). HMAC 해시만 남긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path

BATCH = 2000
SRC = "mangae"
_NOW = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#: 후기 문자열은 두 형식이 섞여 있다 (실측 99.14% 파싱).
#:   A) "닉네임2026-08-23 06:58:11본문"
#:   B) "닉네임2026-05-21 09:12|답글|신고본문"
#: 닉네임이 아예 없는 것도 0.86% 있다 — author_hash NULL 로 둔다.
REVIEW_RE = re.compile(
    r"^(?P<a>.*?)(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)"
    r"(?:\|답글\|신고)?(?P<b>.*)$", re.S)

_SERVING_RE = re.compile(r"(\d+)")
_TIME_RE = re.compile(r"(\d+)")

#: 크롤 난이도 문자열 → recipe.difficulty (SMALLINT 1~5).
#: 실측 분포: 아무나 53% · 초급 37% · 중급 6.7% · 고급 0.4% · 신의경지 0.03%
DIFFICULTY = {"아무나": 1, "초급": 2, "중급": 3, "고급": 4, "신의경지": 5}


def _salt() -> bytes:
    s = os.environ.get("REVIEW_SALT")
    if not s:
        sys.exit(
            "REVIEW_SALT 가 환경변수에 없습니다 — 닉네임 해시의 키입니다.\n"
            "\n"
            "  🔴 **새로 만들지 마세요.** 이미 적재된 후기의 작성자 해시가\n"
            "     기존 값으로 만들어졌습니다. 바꾸면 같은 사람이 쓴 후기를\n"
            "     더 이상 같은 사람으로 못 봅니다. 되돌릴 수 없습니다.\n"
            "\n"
            "  팀 채널에서 기존 값을 받아 .env 에 **한 줄 추가**하고("
            "'>' 가 아니라 '>>'),\n"
            "  이 셸에 올리세요:\n"
            "     printf 'REVIEW_SALT=팀에서받은값\\n' >> .env\n"
            "     set -a; . ./.env; set +a\n"
            "\n"
            "  (데이터가 하나도 없는 새 프로젝트를 시작하는 경우에만"
            " 새로 만듭니다.)")
    return s.encode()


def author_hash(nick: str, salt: bytes) -> str | None:
    """HMAC-SHA256 앞 16hex. 복원 불가, 재계산 가능 → 삭제 요청 대응이 된다."""
    nick = nick.strip()
    return hmac.new(salt, nick.encode(), hashlib.sha256).hexdigest()[:16] if nick else None


def _safe_ts(ts: str) -> str | None:
    """크롤 원문에 `'0000-00-00 00:00:00'` 같은 값이 섞여 있다 (실측).
    PostgreSQL 이 거부하므로 여기서 거른다 — 날짜가 없어도 후기 본문은 살린다."""
    try:
        from datetime import datetime
        fmt = "%Y-%m-%d %H:%M:%S" if len(ts) > 16 else "%Y-%m-%d %H:%M"
        datetime.strptime(ts, fmt)
        return ts
    except ValueError:
        return None


def parse_review(raw: str, salt: bytes) -> tuple[str | None, str | None, str] | None:
    m = REVIEW_RE.match(raw)
    if not m:
        return None
    body = m.group("b").strip()
    if not body:
        return None
    return author_hash(m.group("a"), salt), _safe_ts(m.group("ts")), body


def _int_or_none(pat: re.Pattern, s: str | None) -> int | None:
    if not s:
        return None
    m = pat.search(s)
    return int(m.group(1)) if m else None


def load(paths: list[Path], limit: int | None, dsn: str) -> None:
    try:
        import psycopg
        from psycopg.types.json import Json
    except ImportError:
        sys.exit("psycopg 필요:  uv sync")

    salt = _salt()
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    cur.execute("SET search_path TO reco, public")

    n_rec = n_raw = n_rev = n_step = n_skip = 0
    rec_buf: list[tuple] = []
    pending: list[dict] = []

    def flush() -> None:
        nonlocal rec_buf, pending, n_rec, n_raw, n_rev, n_step
        if not rec_buf:
            return
        # 주의: RETURNING 을 한 줄도 흘리면 안 된다. psycopg2 시절 execute_values 는
        #    page_size 를 배치 크기로 맞추지 않으면 여러 문장으로 쪼개 실행하고
        #    RETURNING 이 마지막 조각만 돌아왔다. idmap 이 비어 재료·후기가 조용히
        #    버려졌다 — 실제로 후기 627,610 → 29,035 였다. 에러가 안 나서 더 위험했다.
        #    psycopg3 는 executemany(returning=True) 로 모든 실행의 결과 집합을
        #    돌려주므로, nextset() 으로 끝까지 훑어 전부 모은다.
        cur.executemany("""
            INSERT INTO recipe (source, source_id, url, title, description,
                                servings, cook_minutes, difficulty,
                                review_count, raw_json, crawled_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, source_id) DO UPDATE SET
                title = EXCLUDED.title, raw_json = EXCLUDED.raw_json,
                review_count = EXCLUDED.review_count
            RETURNING id, source_id""", rec_buf, returning=True)
        idmap = {}
        while True:
            if cur.pgresult is not None and cur.rowcount > 0:
                idmap.update({sid: rid for rid, sid in cur.fetchall()})
            if not cur.nextset():
                break
        if len(idmap) != len(rec_buf):
            sys.exit(f"🔴 RETURNING 유실: 보낸 {len(rec_buf)}건 · 받은 {len(idmap)}건")
        n_rec += len(rec_buf)

        raw_rows, rev_rows, step_rows = [], [], []
        for d in pending:
            rid = idmap.get(d["source_id"])
            if rid is None:
                continue
            for pos, it in enumerate(d["ings"]):
                # 주의: 원문 보존 — 수량을 분리하지 않고 한 문자열로 넣는다.
                #    분리는 P2 의 일이고, 규칙이 바뀌면 여기서 다시 만든다 (설계 4-1).
                txt = f'{it["name"]} {it["amount"]}'.strip() if it.get("amount") else it["name"]
                raw_rows.append((rid, it.get("group"), pos, txt[:255]))
            for a, ts, body in d["revs"]:
                rev_rows.append((rid, a, ts, body))
            for st in d["steps"]:
                step_rows.append((rid, st["step_no"], st["text"], st["image_url"]))

        if raw_rows:
            cur.executemany("""
                INSERT INTO recipe_ingredient_raw (recipe_id, group_name,
                                                   position, raw_text)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""", raw_rows)
            n_raw += len(raw_rows)
        if rev_rows:
            cur.executemany("""
                INSERT INTO recipe_review (recipe_id, author_hash, written_at, body)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""", rev_rows)
            n_rev += len(rev_rows)
        if step_rows:
            cur.executemany("""
                INSERT INTO recipe_step (recipe_id, step_no, text, image_url)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""", step_rows)
            n_step += len(step_rows)
        conn.commit()
        rec_buf, pending = [], []

    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if limit and n_rec + len(rec_buf) >= limit:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    n_skip += 1
                    continue
                sid = str(d.get("recipe_id") or "")
                if not sid or not d.get("title"):
                    n_skip += 1
                    continue

                revs = []
                for r in d.get("reviews") or []:
                    if isinstance(r, str):
                        got = parse_review(r, salt)
                        if got:
                            revs.append(got)

                ings = []
                for g in d.get("ingredient_groups") or []:
                    gname = g.get("group_name")
                    for it in g.get("items") or []:
                        if it.get("name"):
                            ings.append({"name": it["name"], "amount": it.get("amount"),
                                         "group": gname})
                if not ings:      # groups 가 비면 평면 목록으로 폴백
                    for nm in d.get("ingredient_names") or []:
                        ings.append({"name": nm, "amount": None, "group": None})

                rec_buf.append((
                    SRC, sid, d.get("url"), d["title"], d.get("description"),
                    _int_or_none(_SERVING_RE, d.get("serving")),
                    _int_or_none(_TIME_RE, d.get("cooking_time")),
                    DIFFICULTY.get((d.get("difficulty") or "").strip()),
                    len(d.get("reviews") or []),
                    # crawled_at 은 NOT NULL — 원문이 깨졌으면 적재 시각으로 대체한다
                    Json(d), _safe_ts(d.get("crawled_at") or "") or _NOW,
                ))
                # 조리 순서. 어댑터(adapter.map_steps)와 같은 모양으로 만든다.
                steps = []
                for i, stp in enumerate(d.get("steps") or [], start=1):
                    txt = stp if isinstance(stp, str) else (
                        (stp.get("instruction") or stp.get("text") or "")
                        if isinstance(stp, dict) else "")
                    if str(txt).strip():
                        steps.append({
                            "step_no": stp.get("step_no", i) if isinstance(stp, dict) else i,
                            "text": str(txt).strip(),
                            "image_url": (stp.get("image") or stp.get("img"))
                                         if isinstance(stp, dict) else None,
                        })
                pending.append({"source_id": sid, "ings": ings,
                                "revs": revs, "steps": steps})
                if len(rec_buf) >= BATCH:
                    flush()
                    print(f"  … 레시피 {n_rec:,} · 재료 {n_raw:,} · 후기 {n_rev:,}", flush=True)
    flush()
    cur.close()
    conn.close()

    print("\n✅ 적재 완료")
    print(f"   레시피 {n_rec:,} · 재료원문 {n_raw:,} · 조리순서 {n_step:,} · 후기 {n_rev:,} · 건너뜀 {n_skip:,}")
    print("   다음:  make coverage   (정규화 커버리지 실측)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--limit", type=int, default=None, help="시험 적재용")
    ap.add_argument("--dsn", default=os.environ.get(
        "DATABASE_URL", "postgresql://reco:reco@localhost:5432/recodb"))
    a = ap.parse_args()
    load(a.paths, a.limit, a.dsn)


if __name__ == "__main__":
    main()
