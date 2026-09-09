"""P3 매칭 캐스케이드 회귀 검증 (설계 4-4).

    .venv/bin/python -m tests.unit.recommend.test_match

**DB 불필요** — 사전을 시드에서 로드한다.
여기서 깨지면 커버리지가 조용히 떨어지거나, 더 나쁘게는 **틀린 재료로 자동 확정**된다.
"""
from __future__ import annotations

import sys

from features.recommend.enums import MatchMethod
from features.recommend.ingest.match import Dictionary, match, match_all

ok, fail = 0, []


def check(label: str, cond: bool):
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        fail.append(label)
        print(f"  ✗ {label}")


print("[P3 매칭 캐스케이드]")
d = Dictionary.from_seeds()
check(f"사전 로드 — 재료 {len(d.names)} · alias {len(d.aliases)}",
      len(d.names) > 500 and len(d.aliases) > 200)

# ── L0/L1/L2 가 각각 제 역할을 하는가 ───────────────────────
for q, want_name, want_method in [
    ("대파", "대파", MatchMethod.EXACT),
    ("왕파", "대파", MatchMethod.ALIAS),
    ("계란", "달걀", MatchMethod.ALIAS),
    ("국내산대파", "대파", MatchMethod.RULE),
    ("냉동새우", "새우", MatchMethod.RULE),
]:
    r = match(q, d)
    check(f"{want_method.value:<5} {q} → {want_name}",
          r.ingredient_name == want_name and r.method == want_method)

# ── 조회 키 정규화 — P1 이 띄어쓰기를 떼지 않는다 ────────
check("띄어쓰기가 있어도 매칭된다 ('대 파' → 대파)",
      match("대 파", d).ingredient_name == "대파")
check("수식어 + 띄어쓰기 조합 ('국내산 대파' → 대파)",
      match("국내산 대파", d).method == MatchMethod.RULE)

# ── 자동 확정이 새면 안 되는 것들 ────────────────────────
#    L2 는 수식어를 떼고 재시도하는데, 그것만으로는 다른 재료를 합쳐버린다.
check("🔴 건포도 를 포도 로 합치지 않는다 (둘 다 사전 표제어)",
      match("건포도", d).ingredient_name == "건포도")
check("🔴 생강 을 강 으로 부수지 않는다 (2음절 미만 잔여 금지)",
      match("생강", d).ingredient_name == "생강")
for a, b in [("참기름", "들기름"), ("진간장", "양조간장"), ("찹쌀가루", "쌀가루")]:
    ra, rb = match(a, d), match(b, d)
    check(f"🔴 {a} 와 {b} 가 서로 다른 재료로 남는다",
          ra.ingredient_id != rb.ingredient_id)

# ── 미매칭은 자동 확정되지 않는다 ────────────────────────
r = match("우주선전투기", d)
check("사전에 없는 표현은 자동 확정되지 않는다", not r.matched)
check("미매칭은 검수 후보를 받는다 (자동확정 아님)", isinstance(r.suggested, list))

# ── 위험 후보는 1순위로 올라오지 않는다 ──────────────────
#    실측: 깨소금 의 trgm 1순위가 소금(0.6) 이었다. 둘은 다른 재료이고
#    confusable 목록에도 없다 — 구조 매칭이 자동으로 강등한다.
r = match("깨소금", d)
top = r.suggested[0][0] if r.suggested else None
check("🔴 깨소금 의 1순위 후보가 소금 이 아니다 (구조 기반 강등)", top != "소금")
check("위험 후보에 사유 태그가 붙는다",
      all(t.startswith("⚠️") or t == "jamo_trgm" for _, _, t in r.suggested))

# ── 오탈자는 alias 가 잡는다 ────────────────────────────────
check("오탈자 '얘호박' → 애호박 (alias)", match("얘호박", d).ingredient_name == "애호박")

# ── 커버리지 집계가 두 숫자를 모두 낸다 (설계 4-8) ──────────
res, cov = match_all(["대파", "대파", "왕파", "우주선전투기"], d)
check("mention 과 distinct 를 따로 센다 (4-8)",
      cov.mention_total == 4 and cov.distinct_total == 3)
check("mention 75% · distinct 67% (같은 표현 반복을 반영)",
      abs(cov.mention - 0.75) < 1e-9 and abs(cov.distinct - 2 / 3) < 1e-9)
check("method 별 집계가 남는다", cov.by_method.get("exact") == 2)

# ── 배치 캐시가 동작하는가 (300만 행 전제) ──────────────────
big = ["대파"] * 1000 + ["양파"] * 1000
res2, cov2 = match_all(big, d)
check("같은 표현은 캐시된다 (distinct 2)", cov2.distinct_total == 2)
check("캐시해도 mention 은 전량 센다", cov2.mention_total == 2000)

print(f"\n{'✅ 전부 통과' if not fail else f'❌ {len(fail)}건 실패'} ({ok}건 확인)")
sys.exit(1 if fail else 0)
