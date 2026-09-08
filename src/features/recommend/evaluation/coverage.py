"""실제 크롤 데이터로 P1→P2→P3→P4 를 관통시켜 커버리지를 잰다 (설계 4-8).

    make coverage
    .venv/bin/python -m features.recommend.evaluation.coverage tests/fixtures/responses/real/*.json

## 🔴 두 숫자를 모두 보고한다

    mention   전체 언급 중 매칭된 비율 — 빈도 가중. 서비스 체감에 가깝다
    distinct  고유 표현 중 매칭된 비율 — 검수 부담에 가깝다

하나만 보고하면 오해를 부른다. `양파` 처럼 흔한 재료가 잡히면 mention 은 쉽게 오르지만
롱테일이 안 잡히면 distinct 는 낮다 — 그 격차가 곧 남은 검수량이다.
"""
from __future__ import annotations

import glob
import io
import json
import sys
from collections import Counter

from features.recommend.ingest.parse import normalize
from features.recommend.ingest.match import Dictionary, match, match_all
from features.recommend.ingest.role import judge_all


def _records(f: str):
    """JSON 한 건 또는 JSONL 여러 건. 크롤 산출물이 JSONL 이라 둘 다 받는다."""
    with io.open(f, encoding="utf-8") as fh:
        head = fh.read(2048)
        fh.seek(0)
        if head.lstrip()[:1] == "[":                 # JSON 배열
            yield from json.load(fh)
        elif head.count("\n") and head.lstrip()[:1] == "{" and "}\n{" in head:
            for line in fh:                          # JSONL
                line = line.strip()
                if line:
                    yield json.loads(line)
        else:                                        # 단건 JSON
            yield json.load(fh)


def _iter_mentions(files: list[str], limit: int | None = None):
    n = 0
    for f in files:
        for d in _records(f):
            for g in d.get("ingredient_groups", []):
                for it in g.get("items", []):
                    txt = it.get("raw_text") or it.get("name", "")
                    for p in normalize(txt):
                        if p.is_non_ingredient:
                            continue      # 도구·용기는 분모에서 뺀다
                        yield p
            n += 1
            if limit and n >= limit:
                return


def main(patterns: list[str]) -> int:
    # --limit N : 대용량 JSONL 을 부분만 재본다.  --min X : 미달이면 exit 1
    limit = None
    min_cov = None
    args = []
    it = iter(patterns)
    for a in it:
        if a == "--limit":
            limit = int(next(it))
        elif a == "--min":
            min_cov = float(next(it))
        else:
            args.append(a)
    patterns = args or ["tests/fixtures/responses/real/*.json"]
    files = [f for p in patterns for f in sorted(glob.glob(p))]
    if not files:
        print(f"파일 없음: {patterns}")
        return 1

    d = Dictionary.from_seeds()
    parsed = list(_iter_mentions(files, limit))
    res, cov = match_all([p.name for p in parsed], d)

    # 🔴 len(files) 는 *파일* 수다. JSONL 한 개에 4.6만 건이 들어 있어서
    #    "레시피 1건 · 언급 72,790건" 으로 찍혔다.
    print(f"파일 {len(files)}개 · 재료 언급 {cov.mention_total:,}건\n")
    print("═══ P3 매칭 커버리지 (설계 4-8) ═══")
    print(" ", cov.report())
    print(f"\n  W3 목표 mention ≥ 0.55 → {'✅ 달성' if cov.mention >= .55 else '⬜ 미달'}")
    print(f"  W5 목표 mention ≥ 0.85 → {'✅ 달성' if cov.mention >= .85 else '⬜ 미달'}")

    # ── P4 역할 판정 ────────────────────────────────────────
    _, st = judge_all([(p, match(p.name, d)) for p in parsed], d)
    print("\n═══ P4 역할 판정 (설계 4-5) ═══")
    print(" ", st.report().replace("\n", "\n "))
    print("\n  🔴 R1·R2(group_name) 가 0 인 것이 정상이다 —")
    print("     원본이 [양념]/[고명] 을 구분하지 않는다 (실측 3/3 '기본재료').")

    miss = Counter(r.query for r in res if not r.matched)
    if miss:
        print(f"\n═══ 미매칭 {len(miss)}종 — 검수 큐 후보 ═══")
        for q, c in miss.most_common(20):
            r = next(x for x in res if x.query == q)
            s = " | ".join(f"{n}({v}){'' if t == 'jamo_trgm' else ' ' + t}"
                           for n, v, t in r.suggested[:3]) or "후보 없음"
            blocked = f" 🔴차단={r.blocked_by}" if r.blocked_by else ""
            print(f"  {q:<14} ×{c}{blocked}  → {s}")
        print("\n  🔴 자동 확정하지 않는다 (4-4-1). 후보는 검수자에게 제안될 뿐이다.")

    # 🔴 --min 이 없으면 항상 0 을 반환한다 — 커버리지가 떨어져도 CI 가 초록이었다.
    # 🔴 속성명은 `mention` 이다. `mention_rate` 로 적혀 있어서 --min 이
    #    조건과 무관하게 AttributeError 로 죽었다 — 100% 여도 실패했다.
    if min_cov is not None and cov.mention < min_cov:
        print(f"\n❌ mention {cov.mention:.3f} < 목표 {min_cov}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tests/fixtures/responses/real/*.json"]))
