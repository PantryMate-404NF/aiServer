"""시뮬 시드를 DB 없이 엔진에 직접 넣어 이용 시나리오를 돌립니다.

실행: uv run python scripts/sim/scenario_engine.py [--seed-dir deploy/seed/sim] [--limit 0]

`scenario_run.py` 는 HTTP 로 서버를 부르는데, 라우터가 목업이라 시드가 응답에 닿지 않습니다
(DB 전환 점검표 M-01·M-03). 이 스크립트는 그 두 항목이 할 일을 파이썬 안에서 대신합니다.
시드 SQL 을 읽어 온보딩 원본·행동 이벤트·냉장고·알러지·선호를 엔진의 입력 모델로 만들고,
레시피는 Mock 카탈로그 120건을 published 레시피처럼 씁니다(읽기는 `sim_seed.py`). 냉장고와
임박 판정은 A 트랙 SQL 함수 `user_pantry_ids` · `effective_expiry` 의 규칙을 따릅니다 -
staple 합집합, 유저 입력이 없으면 구매일 + 기본 소비기한, D-3 이내가 임박(지난 것 포함).

순서
  [1] 시드 전원 - 집단별 페르소나 출처·모드, 후보 수, 완화 단계, 탐색 칸, 불변식
  [2] 이벤트가 가장 무거운 A 집단 유저 - 이력을 시간순으로 따라가며 콜드 → 웜 전환을 봅니다
  [3] 같은 유저 - 기준 목록 → cook 5건 추가 → 페르소나·상위 10 변동
  [4] 같은 유저 - 아직 없는 재료를 임박으로 추가 → 그 재료를 쓰는 레시피 수 변동
  [5] 같은 시드 두 번 실행의 동일성, onboarding 만 있는 유저와의 상위 10 겹침
  [6] 온보딩에서 고른 음식 유형이 목록에 닿는가 - 유형 슬롯을 끈 정책과 견줍니다

'지금' 은 유저마다 마지막 활동(이벤트·냉장고 등록) 한 시간 뒤입니다. 그래야 감쇠와 임박
판정이 시드의 달력 위에서 돕니다. 종료 코드로 판정합니다. 0 = 불변식(알러지 0건 · 중복 없음 ·
노출 확률 (0,1] · 사유 채움 · 조리시간 상한 · 순위 연속) 전부 통과, 재현 동일, 콜드 → 웜 전환,
행동과 냉장고가 목록을 움직임. 이 시드는 임의 데이터라 수치를 '실측' 으로 쓰지 않습니다.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from sim_seed import (
    KST,
    ROOT,
    PantryRow,
    SimEvent,
    SimUser,
    allergy_ids,
    load_catalog,
    load_eval,
    load_shelf_life,
    load_users,
)
from sim_world import (
    OVERLAP_K,
    PRESENTED_PATH,
    TOP_K,
    Summary,
    World,
    context_of,
    summarize,
)

from features.recommend import service
from features.recommend.enums import CUISINE_LABELS, EventType, UserMode
from features.recommend.policy import RankingPolicy
from features.recommend.profile_store import load_presented_flavors

#: [3] 의 자극. `scenario_run.py` 와 같은 조리 수입니다. [4] 의 재료는 유저마다 고릅니다.
EXTRA_COOKS = 5
EXTRA_INGREDIENTS = 2
#: [2] 에서 페르소나를 다시 재는 이벤트 수. 마지막 이벤트 시점은 항상 더합니다.
CHECKPOINTS = (0, 1, 5, 10, 20, 50)
TOP_SHOW = 5


# ─────────────────────────────────────────────────────────────────
# 출력과 시나리오
# ─────────────────────────────────────────────────────────────────
def print_group(group: str, part: Sequence[Summary]) -> None:
    with_candidates = [s for s in part if s.candidates > 0]
    print(f"  {group}: {len(part)}명 · 냉장고 있음 {sum(1 for s in part if s.pantry)}명", end="")
    print(f" · 후보 있음 {len(with_candidates)}명", end="")
    print(f" · 임박 재료 있음 {sum(1 for s in part if s.expiring)}명")
    print(f"     시드 computed_from  {dict(Counter(s.sim_mode for s in part))}")
    print(f"     엔진 출처/모드      {dict(Counter(f'{s.source}/{s.mode}' for s in part))}")
    for sim_mode in sorted({s.sim_mode for s in part}):
        same = [s for s in part if s.sim_mode == sim_mode]
        weights = sorted(s.behavior_weight for s in same)
        print(f"     시드 {sim_mode:10s} → 엔진 {dict(Counter(s.mode for s in same))}", end="")
        print(f" · 행동 무게 중위 {statistics.median(weights):.1f} 최대 {weights[-1]:.1f}")
    if with_candidates:
        served = statistics.fmean(s.served for s in with_candidates)
        print(f"     노출 수 평균 {served:.1f}", end="")
        print(f" · 완화 단계 {dict(Counter(s.stage for s in with_candidates))}")
        explores = dict(sorted(Counter(s.explore for s in with_candidates).items()))
        print(f"     탐색 칸 {explores}", end="")
        print(f" · 부족분 합 {sum(s.shortfall for s in with_candidates)}")
        chosen = [s for s in with_candidates if s.cuisines]
        got = sum(1 for s in chosen if s.cuisine_slots)
        unmet = Counter(f for s in chosen for f in s.cuisine_unmet)
        print(f"     음식 유형 고른 사람 {len(chosen)}명 · 유형 칸 받은 사람 {got}명", end="")
        print(f" · 목록에 못 닿은 유형 {dict(unmet)}")


def print_everyone(summaries: Sequence[Summary]) -> bool:
    print(f"\n=== [1] 전원 {len(summaries)}명 · 각자 마지막 활동 한 시간 뒤 기준")
    for group in sorted({s.group for s in summaries}):
        print_group(group, [s for s in summaries if s.group == group])
    unmapped = sum(s.unmapped for s in summaries)
    total = unmapped + sum(s.pantry for s in summaries)
    print(f"  냉장고 재료 {total}건 중 Mock 카탈로그에 이름이 없는 것 {unmapped}건", end="")
    print(" (실 DB 에서는 전부 이름으로 조인됩니다)")
    broken = [s for s in summaries if s.violations]
    kinds = dict(Counter(v for s in broken for v in s.violations))
    print(f"  불변식 위반 {len(broken)}명 {kinds}")
    for s in broken[:TOP_SHOW]:
        print(f"     user {s.user_id}: {s.violations}")
    return not broken


def timeline(world: World, user: SimUser) -> tuple[str, str]:
    """이벤트를 시간순으로 따라가며 페르소나가 어떻게 바뀌는지. 처음과 끝의 모드를 돌려줍니다."""
    events = [e for e in user.events if e.code in world.cat.by_code]
    print(f"\n=== [2] user {user.user_id} ({user.group}, 시드 {user.sim_mode} {user.sim_events}건)")
    print(f"    온보딩 picks={user.picks} scales={user.scales}", end="")
    print(f" · 냉장고 {len(user.pantry)}건 · 알러지 {user.allergy_groups or '-'}", end="")
    print(f" · 상한 {user.max_cook_minutes}분")
    modes: list[str] = []
    for n in sorted({*(c for c in CHECKPOINTS if c <= len(events)), len(events)}):
        hour = timedelta(hours=1)
        moment = events[0].at - hour if n == 0 else events[n - 1].at + hour
        trimmed = replace(user, events=tuple(events[:n]))
        result, stage, n_candidates, made = world.run(trimmed, moment)
        personal = [i for i in result.items if not i.is_exploration][:3]
        titles = ", ".join(world.title(i.recipe_id) for i in personal)
        print(f"    이벤트 {n:3d}건 · {moment:%Y-%m-%d}: {made.mode.value:10s}", end="")
        print(f" 무게 {made.behavior_weight:6.2f} · 후보 {n_candidates:3d} {stage:14s} | {titles}")
        modes.append(made.mode.value)
    return modes[0], modes[-1]


def scenario_behavior(world: World, user: SimUser, now: datetime) -> bool:
    """개인화 상위 5건을 지금 조리한 것으로 넣으면 페르소나와 목록이 움직여야 합니다."""
    before, _, _, made = world.run(user, now)
    top_before = [i.recipe_id for i in before.items[:OVERLAP_K]]
    print(f"\n=== [3] user {user.user_id} · cook {EXTRA_COOKS}건 추가")
    print(f"    기준 페르소나 {made.prior_source.value}/{made.mode.value}", end="")
    print(f" 무게 {made.behavior_weight:.2f} 취향 {world.ev.fmt(made.vec)}")
    for item in before.items[:TOP_SHOW]:
        flag = "X" if item.is_exploration else " "
        print(f"     {item.final_rank:2d} {flag} {item.score:.3f}", end="")
        print(f" {world.title(item.recipe_id)} | {item.reason}")
    cooked = [i.recipe_id for i in before.items if not i.is_exploration][:EXTRA_COOKS]
    extra = tuple(
        SimEvent(code=world.cat.code_of[rid], kind=EventType.COOK, at=now) for rid in cooked
    )
    later = now + timedelta(hours=1)
    after, _, _, after_made = world.run(replace(user, events=(*user.events, *extra)), later)
    top_after = [i.recipe_id for i in after.items[:OVERLAP_K]]
    moved = sum(1 for a, b in zip(top_before, top_after, strict=False) if a != b)
    delta = max(
        abs((after_made.vec[i] or 0.0) - (made.vec[i] or 0.0)) for i in range(len(made.vec))
    )
    still = [world.title(rid) for rid in cooked if rid in top_after]
    print(f"    → 무게 {made.behavior_weight:.2f} → {after_made.behavior_weight:.2f}", end="")
    print(f" · 취향 최대 변화 {delta:.3f} · 상위 {OVERLAP_K} 중 {moved} 자리 변동")
    print(f"    방금 조리한 {len(cooked)}건 중 상위 {OVERLAP_K} 에 남은 것: {still or '없음'}")
    return moved > 0 or delta > 0.0


def stimulus_names(world: World, user: SimUser, now: datetime) -> list[str]:
    """[4] 의 자극. 유저가 아직 없는 재료 중 카탈로그(상한·알러지 안)에서 가장 많이 쓰이는 것.

    `scenario_run.py` 처럼 이름을 고정하면 이미 그 재료가 임박한 유저에게는 자극이 아닙니다.
    """
    held = {row.name for row in user.pantry if row.added <= now}
    banned = allergy_ids(user, world.cat)
    cap = user.max_cook_minutes
    usage: Counter[int] = Counter()
    for recipe in world.cat.recipes.values():
        if recipe.all_ids & banned or (cap is not None and (recipe.cook_minutes or 0) > cap):
            continue
        usage.update(recipe.all_ids - world.cat.staple_ids)
    name_of = {i: n for n, i in world.cat.ingredient_ids.items()}
    fresh = [name_of[i] for i, _ in usage.most_common() if name_of[i] not in held]
    return fresh[:EXTRA_INGREDIENTS]


def scenario_expiring(world: World, user: SimUser, now: datetime) -> bool:
    """임박 재료를 냉장고에 더하면 그 재료를 쓰는 레시피가 목록에 더 올라와야 합니다.

    "임박 재료를 하나라도 쓰는 비율" 은 기준이 못 됩니다 - 이미 임박 재료가 넷인 유저는
    다섯째를 더해도 그 비율이 흔들릴 뿐입니다. 더한 재료 자체를 쓰는 레시피 수를 봅니다.
    """
    names = stimulus_names(world, user, now)
    added_ids = frozenset(world.cat.ingredient_ids[n] for n in names)
    soon = now.date() + timedelta(days=2)
    added = tuple(PantryRow(name=n, purchased=now.date(), expires=soon, added=now) for n in names)
    later = now + timedelta(hours=1)
    richer = replace(user, pantry=(*user.pantry, *added))
    before, _, _, made = world.run(user, now)
    after, _, _, _ = world.run(richer, later)
    ctx_before = context_of(user, world.cat, world.shelf, made, now)
    ctx_after = context_of(richer, world.cat, world.shelf, made, later)

    def uses(result: service.RankingResult, ids: frozenset[int]) -> int:
        personal = [i for i in result.items if not i.is_exploration]
        return sum(1 for i in personal if world.cat.recipes[i.recipe_id].all_ids & ids)

    cap = user.max_cook_minutes
    usable = sum(
        1
        for r in world.cat.recipes.values()
        if r.all_ids & added_ids and (cap is None or (r.cook_minutes or 0) <= cap)
    )
    hits_before, hits_after = uses(before, added_ids), uses(after, added_ids)
    any_before = uses(before, ctx_before.expiring_ids)
    any_after = uses(after, ctx_after.expiring_ids)
    measured = any(i.features.get("f_expiring") is not None for i in after.items)
    print(f"\n=== [4] user {user.user_id} · 임박 재료 {names} 추가 (아직 없는 재료 중 최다 사용)")
    print(f"    임박 재료 {len(ctx_before.expiring_ids)} → {len(ctx_after.expiring_ids)}종", end="")
    print(f" · 더한 재료를 쓰는 개인화 레시피 {hits_before} → {hits_after}건", end="")
    print(f" (카탈로그에서 조리시간 상한 안인 것 {usable}건)")
    print(f"    임박 재료를 하나라도 쓰는 개인화 레시피 {any_before} → {any_after}건", end="")
    print(f" · f_expiring 측정 {measured}")
    return measured and hits_after > hits_before


def scenario_repeat(world: World, user: SimUser, now: datetime, peers: Sequence[Summary]) -> bool:
    first, _, _, _ = world.run(user, now)
    second, _, _, _ = world.run(user, now)
    same = [i.model_dump() for i in first.items] == [i.model_dump() for i in second.items]
    print(f"\n=== [5] 같은 시드 두 번 실행 동일: {same}")
    top = {i.recipe_id for i in first.items[:OVERLAP_K]}
    cold = next((s for s in peers if s.mode == UserMode.COLD.value and s.candidates >= TOP_K), None)
    if cold is not None:
        overlap = len(top & set(cold.top))
        print(f"    onboarding 만 있는 user {cold.user_id} 의 상위 {OVERLAP_K} 과 겹침", end="")
        print(f" {overlap}/{OVERLAP_K}")
    return same


def scenario_cuisine(world: World, users: dict[int, SimUser], summaries: Sequence[Summary]) -> bool:
    """[6] 고른 음식 유형이 목록에 닿는가. 유형 슬롯을 끈 정책과 같은 시드로 견줍니다.

    슬롯의 값어치는 "고른 유형이 Top-K 에 한 번도 안 나오는 사람"이 몇 명 줄었는가입니다.
    늘어나면 슬롯이 무언가를 밀어내기만 한 것이므로 실패로 봅니다.
    """
    targets = [s for s in summaries if s.cuisines and s.candidates > 0]
    print(f"\n=== [6] 음식 유형 · 고른 사람 {len(targets)}명 (후보가 있는 사람만)")
    if not targets:
        print("    고른 유형이 있는 유저가 없어 견줄 것이 없습니다")
        return False
    off = replace(world, policy=replace(world.policy, cuisine_slot_ratio=0.0))
    missed_off = 0
    for summary in targets:
        user = users[summary.user_id]
        result, _stage, _n, _made = off.run(user, off.now_of(user))
        served = {world.cat.recipes[item.recipe_id].cuisine for item in result.items}
        if any(family not in served for family in summary.cuisines):
            missed_off += 1
    missed_on = sum(1 for s in targets if s.cuisine_unmet)
    slots = sum(s.cuisine_slots for s in targets)
    labels = {CUISINE_LABELS.get(f, f) for s in targets for f in s.cuisines}
    print(f"    고른 유형 {sorted(labels)} · 유형 칸 합 {slots}")
    print(f"    고른 유형이 목록에 없는 사람: 슬롯 끄면 {missed_off}명 → 켜면 {missed_on}명")
    print("    (남는 것은 후보 자체에 그 유형이 없는 경우입니다 — Mock 카탈로그 120건의 한계)")
    # 늘지 않은 것만으로는 부족합니다 — 슬롯이 아예 안 돌아도 같은 값이 나옵니다.
    # 채울 것이 있었는데(끄면 못 닿은 사람이 있었는데) 한 칸도 안 떼었으면 실패로 봅니다.
    return missed_on <= missed_off and (slots > 0 or missed_off == 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-dir", type=Path, default=ROOT / "deploy" / "seed" / "sim")
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N 명만 (0 = 전원)")
    args = parser.parse_args()

    ev = load_eval()
    cat = load_catalog(ev)
    users = load_users(args.seed_dir)
    if args.limit:
        users = dict(list(users.items())[: args.limit])
    last = max(
        (m for u in users.values() if (m := u.last_activity()) is not None),
        default=datetime.now(tz=KST),
    )
    world = World(
        ev=ev,
        cat=cat,
        shelf=load_shelf_life(),
        presented=load_presented_flavors(PRESENTED_PATH),
        policy=RankingPolicy(),
        fallback_now=last + timedelta(days=1),
    )
    print(f"seed={args.seed_dir} users={len(users)} recipes={len(cat.recipes)}", end="")
    print(f" staples_in_catalog={len(cat.staple_ids)} fallback_now={world.fallback_now:%Y-%m-%d}")

    summaries = [summarize(world, user) for user in users.values()]
    invariants_ok = print_everyone(summaries)

    warm = max(
        (s for s in summaries if s.group == "sim_funnel_A" and s.candidates >= TOP_K),
        key=lambda s: s.behavior_weight,
        default=None,
    )
    if warm is None:
        print("\nA 집단에 후보가 충분한 유저가 없어 시나리오를 돌리지 못했습니다")
        print("\nRESULT: FAIL")
        return 1
    user = users[warm.user_id]
    now = world.now_of(user)
    first_mode, last_mode = timeline(world, user)
    converted = first_mode == UserMode.COLD.value and last_mode == UserMode.WARM.value
    print(f"    콜드 → 웜 전환: {converted} ({first_mode} → {last_mode})")
    behavior_ok = scenario_behavior(world, user, now)
    expiring_ok = scenario_expiring(world, user, now)
    peers = [s for s in summaries if s.group == user.group]
    repeat_ok = scenario_repeat(world, user, now, peers)

    cuisine_ok = scenario_cuisine(world, users, summaries)

    verdict = {
        "invariants": invariants_ok,
        "cuisine_reaches_list": cuisine_ok,
        "cold_to_warm": converted,
        "behavior_moves_list": behavior_ok,
        "expiring_reaches_list": expiring_ok,
        "repeatable": repeat_ok,
    }
    print(f"\n판정 {verdict}")
    ok = all(verdict.values())
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
