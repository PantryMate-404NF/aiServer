"""P4 — 역할 판정 (설계 4-5).

    ParsedIngredient + MatchResult  →  essential / optional / seasoning / garnish

## 🔴 설계의 1순위 근거가 실측에서 사라졌다

설계 4-5 는 우선순위 1·2 를 `group_name` (`[양념]`·`[고명]`) 에 두고
**"1·2번이 압도적으로 정확하다"** 고 썼다. 그런데 실측 결과:

    tests/fixtures/responses/real/*.json  3건 전부  group_name = "기본재료"

`[양념]`·`[고명]` 구분이 원본에 존재하지 않는다. **1·2번은 영원히 발동하지 않고,
처음부터 3~5번 휴리스틱만으로 시작한다** — 설계가 "이게 없으면 정확도가 크게
떨어진다"고 경고한 바로 그 상태다.

## 그래서 무엇이 실제로 중요한가

```sql
essential_ids = role='essential' AND NOT is_staple   -- ★ Retrieval 이 쓰는 것
all_ids       = 전부 (양념·고명 포함)                  -- 알러지 검사용
```

**`optional`·`seasoning`·`garnish` 는 전부 `essential_ids` 에서 빠진다 — Retrieval 결과가 같다.**
즉 4분류를 시도하되 **정확도가 중요한 것은 `essential` 경계 하나**다.

| 오판 방향 | 증상 |
|---|---|
| 양념 → `essential` | **간장이 없으면 김치찌개가 안 나온다.** 후보가 말라죽는다 |
| 주재료 → `seasoning`/`optional` | **돼지고기 없이 제육볶음을 추천.** 만들 수 없는 것을 권한다 |

두 번째가 더 나쁘다 — 첫 번째는 후보가 줄어 눈에 띄지만, 두 번째는 **조용히 틀린다.**

## 🔴 `garnish` 는 규칙으로 산출되지 않는다

`group_name` 이 유일한 근거였다. 위치(고명은 뒤쪽에 몰린다)는 **측정된 적 없는 가정**이라
쓰지 않는다 — 4-4-1 에서 임계값 0.6 을 근거 없이 정했다가 재현율 0% 를 겪었다.
`GARNISH_BY_POSITION` 훅만 남기고 **기본 비활성**이다.

다행히 `garnish` 와 `seasoning` 은 `essential_ids` 에 동일하게 영향한다(둘 다 제외).
차이는 표시용뿐이므로 **지금 구분하지 못해도 추천 결과는 바뀌지 않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field

from features.recommend.enums import IngredientRole
from features.recommend.ingest.match import Dictionary, MatchResult
from features.recommend.stage import ParsedIngredient

#: 주의: "약간/적당량" 이 붙어도 필수로 남기는 대분류.
#:    설계 4-5 규칙 5 를 그대로 쓰면 `돼지고기 적당량` 이 optional 이 되어
#:    돼지고기 없는 사람에게 제육볶음을 권하게 된다. 그 실패는 조용하다.
#:    실측 표본(3건)에는 나타나지 않았으나 비용이 0 이고 실패가 치명적이라 먼저 막는다.
#:
#: 09-09 에 processed·dairy 를 더했다. meat·seafood 만으로는 `스팸 적당량` 이
#: optional 로 빠졌는데, 스팸은 processed.meatproc 이라 가드 밖이었다. 위 주석이
#: 걱정한 것과 같은 실패이고 재료만 다르다. 전량 실측으로 optional 6,854행 중
#: dairy 613 · processed 507 이 이 경로였다 — 모짜렐라치즈·배추김치·햄·떡볶이떡처럼
#: 그 음식을 그 음식이게 하는 것들이다.
#:
#: 버터·우유(dairy.milk)까지 함께 필수가 된다. 실패가 비대칭이라 그 편을 택했다 —
#: 치즈 없는 사람에게 피자를 권하는 쪽이, 치즈 있는 사람에게 피자를 덜 권하는
#: 쪽보다 나쁘다. 되돌리는 값은 이 줄과 재정규화 10분이 전부다
#: (recipe_ingredient 는 recipe_ingredient_raw 에서 언제든 다시 만든다).
MAIN_CATEGORIES = ("meat", "seafood", "processed", "dairy")

#: 위치 기반 garnish 추정. 측정 전까지 켜지 않는다 (근거 없는 임계값 금지).
GARNISH_BY_POSITION = False


@dataclass
class RoleResult:
    role: IngredientRole | None = None  # None = 판정 보류 (미매칭)
    rule: str = ""  # 어느 규칙이 발동했는가 — 측정용
    note: str = ""

    @property
    def is_essential(self) -> bool:
        return self.role == IngredientRole.ESSENTIAL


def judge(
    p: ParsedIngredient,
    m: MatchResult,
    d: Dictionary,
    pos: int | None = None,
    n_total: int | None = None,
) -> RoleResult:
    """역할 하나를 판정한다. **위에서 먼저 걸리면 종료** (설계 4-5).

    Args:
        p: P2 산출
        m: P3 산출 — 미매칭이면 판정을 보류한다
        pos·n_total: 레시피 내 위치 (GARNISH_BY_POSITION 이 켜졌을 때만 쓴다)
    """
    # ── 0. 미매칭은 판정하지 않는다 ─────────────────────────
    #    사전에 없으므로 is_staple 을 알 수 없고, ingredient_id 가 없어
    #    어차피 essential_ids 에 들어가지 못한다. 검수 큐에서 함께 해결된다.
    if not m.matched or m.ingredient_id is None:
        return RoleResult(None, "R0_미매칭", "P3 검수 큐 대기")

    meta = d.meta.get(m.ingredient_id, {})
    cat0 = (meta.get("category_path") or "").split(".")[0]

    # ── 1·2. group_name — 실측상 발동하지 않는다 ─────────
    #    원본이 [양념]/[고명] 을 구분하지 않는다. 자리만 남긴다.

    # ── 3. 사전 플래그 — 지금 가장 강한 근거 ────────────────
    if meta.get("is_seasoning"):
        return RoleResult(IngredientRole.SEASONING, "R3_is_seasoning")
    if meta.get("is_staple"):
        return RoleResult(IngredientRole.SEASONING, "R3_is_staple")

    # ── 4. 명시적 선택 힌트 ('생략가능' '취향껏') ────────────
    if p.is_optional_hint:
        return RoleResult(IngredientRole.OPTIONAL, "R4_note_hint", p.note or "")

    # ── 5. 모호 수량 ('약간' '적당량') + 주재료 가드 ──────
    if p.is_ambiguous_qty:
        if cat0 in MAIN_CATEGORIES:
            return RoleResult(
                IngredientRole.ESSENTIAL,
                "R5_가드_주재료",
                f"'{p.unit}' 이지만 {cat0} 는 필수로 남긴다",
            )
        return RoleResult(IngredientRole.OPTIONAL, "R5_모호수량")

    # ── 6. 위치 기반 garnish — 기본 비활성 ──────────────────
    if GARNISH_BY_POSITION and pos is not None and n_total and pos >= n_total - 2:
        return RoleResult(IngredientRole.GARNISH, "R6_위치추정", "⚠️ 미측정 가정")

    # ── 7. 그 외 전부 ───────────────────────────────────────
    return RoleResult(IngredientRole.ESSENTIAL, "R7_기본")


@dataclass
class RoleStats:
    """규칙별 발동 횟수. **어느 규칙이 실제로 일하는지** 보여준다.

    설계 4-5 는 1·2번이 주력이라고 썼는데 실측은 3번이 주력이다.
    이 표가 그 차이를 계속 감시한다.
    """

    by_rule: dict[str, int] = field(default_factory=dict)
    by_role: dict[str, int] = field(default_factory=dict)
    n_essential: int = 0
    n_total: int = 0
    n_unjudged: int = 0

    def add(self, r: RoleResult) -> None:
        self.n_total += 1
        self.by_rule[r.rule] = self.by_rule.get(r.rule, 0) + 1
        if r.role is None:
            self.n_unjudged += 1
        else:
            self.by_role[r.role.value] = self.by_role.get(r.role.value, 0) + 1
            if r.is_essential:
                self.n_essential += 1

    @property
    def essential_ratio(self) -> float:
        judged = self.n_total - self.n_unjudged
        return self.n_essential / judged if judged else 0.0

    def report(self) -> str:
        roles = " · ".join(f"{k} {v}" for k, v in sorted(self.by_role.items()))
        rules = "\n".join(
            f"    {k:<18}{v:>4}" for k, v in sorted(self.by_rule.items(), key=lambda kv: -kv[1])
        )
        return (
            f"판정 {self.n_total - self.n_unjudged}/{self.n_total} "
            f"(보류 {self.n_unjudged})\n  {roles}\n"
            f"  essential 비율 {self.essential_ratio:.1%}\n  규칙별 발동:\n{rules}"
        )


def judge_all(
    items: list[tuple[ParsedIngredient, MatchResult]], d: Dictionary
) -> tuple[list[RoleResult], RoleStats]:
    """레시피 하나의 재료 전체. 위치를 알 수 있으므로 여기서 넘긴다."""
    n = len(items)
    out, st = [], RoleStats()
    for i, (p, m) in enumerate(items):
        r = judge(p, m, d, pos=i, n_total=n)
        out.append(r)
        st.add(r)
    return out, st
