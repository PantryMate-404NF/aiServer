"""P3 — 매칭 캐스케이드 (설계 4-4).

    P2 산출 `ParsedIngredient.name`  →  `ingredient.id`

## 캐스케이드 — 앞에서 잡히면 뒤는 안 돈다. 순서가 곧 신뢰도다

    L0  ingredient.name 완전일치          score 1.00  exact   ✅ 자동확정
    L1  ingredient_alias.alias 완전일치   score 0.95  alias   ✅ 자동확정
    L2  수식어 whitelist 제거 후 재시도    score 0.85  rule    ✅ 자동확정 (구조 검증 통과 시)
    ──  미매칭 → normalization_queue
        suggested = 자모 trgm 후보 (사람이 확정. **자동확정 금지**)

## 🔴 퍼지 매칭은 자동 확정 경로에 없다

4-4-1 실측: trgm 임계값 0.6 에서 **재현율 0%**. 정탐 중앙값 0.143 vs 오탐 0.118 로
분포가 겹쳐 어떤 임계값도 정밀도 99% 를 만들지 못한다(`reco/eval/threshold.py` 로 재확인).
그래서 L3/L4 는 **검수 큐의 후보 제안**으로만 쓴다.

사람이 확정한 결과는 `ingredient_alias(source='manual')` 로 쌓여
**다음 회차부터 L1 이 잡는다.** 검수가 곧 커버리지 상승이다.

## L2 는 두 겹이다

수식어를 떼고 재시도하는 것만으로는 위험하다 — `건포도` 에서 `건` 을 떼면 `포도` 가 되는데
둘 다 사전 표제어인 **다른 재료**다. 그래서 떼기 전에 구조 매칭이 감시한다 (`p3_head`).

    ① 수식어 whitelist 제거 → L0/L1 재시도
    ② 🔴 구조 검증: relation(원문, 후보) 이 same|rule 이어야 확정.
       sibling(참기름↔들기름) · hyponym(애호박↔호박) 은 **검수 큐로 보낸다**

## DB 없이도 돈다

사전은 재료 525 + alias 245 로 작아 **메모리에 통째로 올린다.** 300만 행을 매칭하면서
행마다 DB 를 왕복하면 배치가 끝나지 않는다. 시드에서 로드하면 `make normalize-test` 처럼
DB 없이 검증할 수 있고, 운영에서는 같은 인터페이스로 DB 에서 로드한다.
"""
from __future__ import annotations

import csv
import io
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from features.recommend.enums import MatchMethod
from features.recommend.ingest.head import HeadIndex

ROOT = Path(__file__).resolve().parents[4]
SEEDS = ROOT / "seeds"

#: 단계별 점수 (설계 4-4 표). `recipe_ingredient.match_score` 에 그대로 들어간다.
SCORE = {MatchMethod.EXACT: 1.00, MatchMethod.ALIAS: 0.95, MatchMethod.RULE: 0.85}

#: 검수 큐에 붙일 후보 개수. 4-7 이 "1건 15초"를 전제하므로 5개를 넘기면 고르는 데 더 걸린다.
N_SUGGEST = 5


@dataclass
class MatchResult:
    """P3 산출. `recipe_ingredient` 한 행이 된다."""
    query: str                                   # P2 가 낸 name
    ingredient_id: int | None = None
    ingredient_name: str | None = None
    method: MatchMethod | None = None
    score: float = 0.0
    #: L2 에서 제거한 수식어. 되돌릴 수 있게 남긴다
    stripped: list[str] = field(default_factory=list)
    #: 미매칭일 때만. [(재료명, 유사도, 사유)] — normalization_queue.suggested 로 간다
    suggested: list[tuple[str, float, str]] = field(default_factory=list)
    #: 구조 매칭이 거부한 이유 (sibling/hyponym). 검수자에게 경고로 보여준다
    blocked_by: str | None = None

    @property
    def matched(self) -> bool:
        return self.ingredient_id is not None


def _key(s: str) -> str:
    """조회 키 정규화 — **띄어쓰기를 뗀다.**

    🔴 `seeds/README` 는 띄어쓰기를 "P1 전처리" 담당으로 적었지만 **P1 은 하지 않는다**
       (`normalize("대 파 1대")` → name='대 파'). 실측으로 확인됨.

    P1 을 고치지 않고 여기서 푸는 이유: `ParsedIngredient.name` 은 **검수 큐에 그대로
    표시되는 값**이라 원문에 충실해야 한다. 공백 제거는 "이름의 정정"이 아니라
    "매칭을 위한 변형"이므로 조회 키에만 적용하는 것이 옳다.
    """
    return "".join(s.split())


def _jamo(s: str) -> str:
    """NFD 자모 분해. 한글 trgm 은 음절 단위로는 해상도가 너무 낮다 (설계 2-2).
    애호박↔얘호박 음절 0.143 → 자모 0.455 (실측)."""
    return unicodedata.normalize("NFD", s)


def _trgm(a: str, b: str) -> str | float:
    """자카드 기반 trigram 유사도. PostgreSQL pg_trgm 의 근사."""
    A = {a[i:i + 3] for i in range(max(1, len(a) - 2))}
    B = {b[i:i + 3] for i in range(max(1, len(b) - 2))}
    u = A | B
    return len(A & B) / len(u) if u else 0.0


class Dictionary:
    """재료 사전. 시드(파일) 또는 DB 에서 로드한다.

    운영에서는 배치 시작 시 한 번 올리고 재사용한다 — 300만 행을 매칭하는데
    행마다 DB 를 왕복하면 배치가 끝나지 않는다.
    """

    def __init__(self, names: dict[str, int], aliases: dict[str, int],
                 whitelist: set[str], confusable: set[tuple[str, str]] | None = None,
                 meta: dict[int, dict] | None = None):
        self.names = names                       # 정식명 → id
        self.aliases = aliases                   # alias → id
        # 조회 키(공백 제거) → id. 사전에 `대 파` 같은 표기가 있어도 흡수된다
        self.names_key = {_key(k): v for k, v in names.items()}
        self.aliases_key = {_key(k): v for k, v in aliases.items()}
        self.whitelist = whitelist               # 제거 가능한 수식어
        self.confusable = confusable or set()
        #: id → {is_staple, is_seasoning, category_path}. **P4 역할 판정의 유일한 근거**이다
        #: (`group_name` 이 실측에서 전부 '기본재료' 라 쓸 수 없기 때문 — 설계 4-5)
        self.meta = meta or {}
        self.id_to_name = {v: k for k, v in names.items()}
        self.head = HeadIndex(list(names))
        # 후보 제안용 자모 인덱스
        self._jamo = [(n, _jamo(n)) for n in names]

    # ── 로더 ────────────────────────────────────────────────
    @classmethod
    def from_seeds(cls, seeds: Path = SEEDS) -> "Dictionary":
        """시드 파일에서. **DB 불필요** — 테스트·CI 경로."""
        import yaml
        rows = list(csv.DictReader(io.open(seeds / "ingredient.csv", encoding="utf-8")))
        names = {r["name"]: i + 1 for i, r in enumerate(rows)}
        meta = {i + 1: {"is_staple": r["is_staple"] == "true",
                        "is_seasoning": r["is_seasoning"] == "true",
                        "category_path": r["category_path"]}
                for i, r in enumerate(rows)}
        aliases = {}
        for r in csv.DictReader(io.open(seeds / "ingredient_alias.csv", encoding="utf-8")):
            tgt = names.get(r["ingredient_name"])
            if tgt:
                aliases[r["alias"]] = tgt
        wl_raw = yaml.safe_load(io.open(seeds / "modifier_whitelist.yaml", encoding="utf-8"))
        # 🔴 do_not_remove_examples 는 "제거하면 안 되는 것" 목록이다. 섞으면 정반대로 동작한다
        wl = {x for k, v in wl_raw.items() if k != "do_not_remove_examples" for x in (v or [])}
        conf = {tuple(p[:2]) for p in
                yaml.safe_load(io.open(seeds / "confusable_pairs.yaml", encoding="utf-8"))["pairs"]
                if isinstance(p, (list, tuple))}
        return cls(names, aliases, wl, conf, meta)

    @classmethod
    def from_db(cls, conn, seeds: Path = SEEDS) -> "Dictionary":
        """운영 경로. 검수로 쌓인 `source='manual'` alias 까지 포함된다."""
        import yaml
        cur = conn.cursor()
        # 🔴 컬럼을 i. 로 한정한다. ingredient 와 ingredient_category 양쪽에
        #    id·name 이 있어 한정하지 않으면 AmbiguousColumn 으로 죽는다.
        cur.execute("SELECT i.id, i.name, i.is_staple, i.is_seasoning, c.path::text "
                    "FROM ingredient i LEFT JOIN ingredient_category c ON c.id = i.category_id")
        rows = cur.fetchall()
        names = {r[1]: r[0] for r in rows}
        meta = {r[0]: {"is_staple": r[2], "is_seasoning": r[3],
                       "category_path": r[4] or ""} for r in rows}
        cur.execute("SELECT alias, ingredient_id FROM ingredient_alias")
        aliases = dict(cur.fetchall())
        wl_raw = yaml.safe_load(io.open(seeds / "modifier_whitelist.yaml", encoding="utf-8"))
        wl = {x for k, v in wl_raw.items() if k != "do_not_remove_examples" for x in (v or [])}
        conf = {tuple(p[:2]) for p in
                yaml.safe_load(io.open(seeds / "confusable_pairs.yaml", encoding="utf-8"))["pairs"]
                if isinstance(p, (list, tuple))}
        return cls(names, aliases, wl, conf, meta)


def _strip_modifiers(name: str, d: Dictionary) -> tuple[str, list[str]]:
    """앞에서부터 whitelist 수식어를 뗀다. 뗀 것을 함께 돌려준다.

    🔴 2음절 미만이 남으면 떼지 않는다 — `생강` → `강` 파괴를 막는다
       (seeds/modifier_whitelist.yaml 의 `생` 항목 경고).
    """
    cur = name
    removed: list[str] = []
    changed = True
    while changed:
        changed = False
        for m in sorted(d.whitelist, key=len, reverse=True):
            if cur.startswith(m) and len(cur) - len(m) >= HeadIndex.MIN_REMAINDER:
                cur = cur[len(m):].strip()
                removed.append(m)
                changed = True
                break
    return cur, removed


def _suggest(name: str, d: Dictionary, n: int = N_SUGGEST) -> list[tuple[str, float, str]]:
    """검수 큐에 붙일 후보. **자동 확정에 쓰지 않는다** (4-4-1).

    자모 분해본으로 trgm 을 재고, **위험한 후보는 순위를 강등한다** —
    검수자가 15초 만에 클릭하는 구조에서 위험쌍이 1순위로 올라오면 잘못 눌린다.

    🔴 위험 판정은 두 겹이다.
      ① `confusable_pairs.yaml` 에 등재된 쌍 (사람이 손으로 적은 것)
      ② **구조 매칭이 sibling/hyponym 으로 본 쌍** — 목록에 없어도 잡힌다.
         실측: `깨소금` 의 1순위 후보가 `소금`(0.6) 이었다. 둘은 다른 재료이고
         confusable 목록에도 없었다. 구조가 자동으로 막는다.
    """
    jq = _jamo(name)
    out = []
    for cand, jc in d._jamo:
        sim = _trgm(jq, jc)
        if sim <= 0.0:
            continue
        listed = (name, cand) in d.confusable or (cand, name) in d.confusable
        rel = d.head.relation(name, cand, d.whitelist)
        if listed:
            tag = "⚠️위험쌍(등재)"
        elif rel in ("sibling", "hyponym"):
            tag = f"⚠️위험({rel})"
        else:
            tag = "jamo_trgm"
        out.append((cand, round(sim, 3), tag))
    # 위험 후보는 뒤로 민다
    out.sort(key=lambda t: (t[2].startswith("⚠️"), -t[1]))
    return out[:n]


def match(name: str, d: Dictionary) -> MatchResult:
    """캐스케이드 본체. **앞에서 잡히면 뒤는 안 돈다.**"""
    r = MatchResult(query=name)
    q = _key(name)                               # 조회는 공백 뗀 형태로
    if not q:
        return r

    # ── L0 완전일치 ─────────────────────────────────────────
    if q in d.names_key:
        r.ingredient_id = d.names_key[q]
        r.ingredient_name = d.id_to_name.get(r.ingredient_id)
        r.method, r.score = MatchMethod.EXACT, SCORE[MatchMethod.EXACT]
        return r

    # ── L1 alias ────────────────────────────────────────────
    if q in d.aliases_key:
        r.ingredient_id = d.aliases_key[q]
        r.ingredient_name = d.id_to_name.get(r.ingredient_id)
        r.method, r.score = MatchMethod.ALIAS, SCORE[MatchMethod.ALIAS]
        return r

    # ── L2 수식어 제거 후 재시도 + 구조 검증 ─────────────────
    stripped, removed = _strip_modifiers(q, d)
    if removed and stripped != q:
        hit = d.names_key.get(stripped) or d.aliases_key.get(stripped)
        if hit:
            target = d.id_to_name.get(hit, stripped)
            # 🔴 구조 검증 — 수식어를 뗐더니 '다른 재료'가 되는 경우를 막는다
            rel = d.head.relation(q, target, d.whitelist)
            if rel in ("same", "rule"):
                r.ingredient_id = hit
                r.ingredient_name = target
                r.method, r.score = MatchMethod.RULE, SCORE[MatchMethod.RULE]
                r.stripped = removed
                return r
            r.blocked_by = rel          # sibling/hyponym → 검수 큐로, 사유를 남긴다

    # ── 미매칭 → 검수 큐 ────────────────────────────────────
    r.suggested = _suggest(q, d)
    return r


@dataclass
class Coverage:
    """4-8 이 요구하는 **두 숫자**. 하나만 보고하면 오해를 부른다."""
    mention_total: int = 0
    mention_matched: int = 0
    distinct_total: int = 0
    distinct_matched: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    blocked: dict[str, int] = field(default_factory=dict)

    @property
    def mention(self) -> float:
        return self.mention_matched / self.mention_total if self.mention_total else 0.0

    @property
    def distinct(self) -> float:
        return self.distinct_matched / self.distinct_total if self.distinct_total else 0.0

    def report(self) -> str:
        m = " · ".join(f"{k} {v}" for k, v in sorted(self.by_method.items()))
        b = (" · 구조차단 " + ", ".join(f"{k} {v}" for k, v in sorted(self.blocked.items()))
             if self.blocked else "")
        return (f"mention {self.mention:.1%} ({self.mention_matched}/{self.mention_total}) · "
                f"distinct {self.distinct:.1%} ({self.distinct_matched}/{self.distinct_total})"
                f"\n  {m}{b}")


def match_all(names: list[str], d: Dictionary) -> tuple[list[MatchResult], Coverage]:
    """배치 매칭 + 커버리지.

    **같은 표현이 여러 번 나오므로 캐시한다** — 300만 행에 고유 표현은 수만 개다.
    """
    cache: dict[str, MatchResult] = {}
    out, cov = [], Coverage()
    for nm in names:
        if nm not in cache:
            cache[nm] = match(nm, d)
        r = cache[nm]
        out.append(r)
        cov.mention_total += 1
        if r.matched:
            cov.mention_matched += 1
            cov.by_method[r.method.value] = cov.by_method.get(r.method.value, 0) + 1
        elif r.blocked_by:
            cov.blocked[r.blocked_by] = cov.blocked.get(r.blocked_by, 0) + 1
    cov.distinct_total = len(cache)
    cov.distinct_matched = sum(1 for r in cache.values() if r.matched)
    return out, cov
