"""기획 가상운영데이터(xlsx, v0.4) → reco 스키마 SQL 시드.

실행: uv run python scripts/sim/convert_planning_data.py --src <xlsx 폴더> --out deploy/seed/sim
적재: bash deploy/seed/sim/load_sim.sh   (psql, ON_ERROR_STOP)

기획 데이터에는 엔진이 쓰는 재료·취향·알러지·소비기한이 없습니다. 이 스크립트는
  변환  users → app_user / recipe_events → event_log(click) / cart(source=recipe) → event_log(cook)
  합성  온보딩(picks·scales) · user_vector · user_preference · user_allergy · pantry_item
을 결정론적 해시(SEED)로 만듭니다. 같은 입력이면 같은 SQL 이 나옵니다.

원본 값(U00001, RCP0102, event_id)은 event_log.context 와 app_user.display_name 에 남겨
기획 데이터와 역추적할 수 있게 합니다.

🔴 recipe_id 는 적재 시점에 `recipe(status='published')` 를 id 순으로 번호 매겨 RCP 번호에
   바인딩합니다. event_log.recipe_id 에 FK 가 없어 아무 값이나 들어가므로, published 가
   RCP 최댓값보다 적으면 DO 블록이 적재를 중단합니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from features.recommend.enums import normalize_cuisine

SEED = "sim-planning-v0.4"
ROOT = Path(__file__).resolve().parents[2]
ONBOARDING_YAML = ROOT / "seeds" / "onboarding_recipes.yaml"
INGREDIENT_CSV = ROOT / "seeds" / "ingredient.csv"
N_WARM = 20  # scoring_config.n_warm 기본값. user_vector.computed_from 판정 기준

#: 냉장고에 넣을 재료 풀. 전부 seeds/ingredient.csv 에 있는 이름이어야 한다 (적재 시 이름으로 조인).
PANTRY_POOL = [
    "양파",
    "대파",
    "마늘",
    "감자",
    "당근",
    "애호박",
    "양배추",
    "시금치",
    "두부",
    "달걀",
    "우유",
    "돼지고기",
    "소고기",
    "닭고기",
    "새우",
    "오징어",
    "배추김치",
    "쌀",
    "밀가루",
    "스파게티면",
    "어묵",
    "베이컨",
    "토마토",
    "오이",
    "콩나물",
    "무",
    "청양고추",
    "표고버섯",
    "느타리버섯",
    "햄",
    "참치캔",
    "고등어",
    "파프리카",
    "상추",
    "깻잎",
    "멸치",
    "가지",
    "브로콜리",
    "단호박",
    "고구마",
    "숙주",
    "부추",
    "소시지",
    "연어",
    "새송이버섯",
    "팽이버섯",
    "닭가슴살",
    "떡국떡",
    "라면사리",
    "우동면",
    "당면",
]
ALLERGEN_GROUPS = ["nut", "sesame", "soy", "gluten", "egg", "dairy", "fish", "shellfish", "peach"]
COOK_CAPS = [20, 30, 45, 60]


# ── 결정론적 난수 ────────────────────────────────────────────────
def h(*keys: object) -> float:
    """[0,1) 실수. 같은 키면 항상 같은 값."""
    digest = hashlib.sha256(("|".join(map(str, keys)) + SEED).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def hint(lo: int, hi: int, *keys: object) -> int:
    return lo + int(h(*keys) * (hi - lo + 1))


def hpick(pool: list, n: int, *keys: object) -> list:
    order = sorted(pool, key=lambda x: h(*keys, x))
    return order[:n]


#: 시뮬 app_user.id = 이 값 + 기획 번호. 실유저·스모크 합성 유저가 쓰는 낮은 id 와 겹치지 않고,
#: 시퀀스는 건드리지 않는다 (setval 없음). 09-14 실측: 스모크 유저 8명이 id 1~8 을 차지해 충돌했다.
SIM_ID_BASE = 1_000_000


def uid(code: str) -> int:
    return SIM_ID_BASE + int(code[1:])


def hkey(u: int) -> int:
    """합성 값의 해시 키 = 기획 번호. id 를 옮겨도 합성 내용(취향·알러지·냉장고)이 같다."""
    return u - SIM_ID_BASE


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def sql_arr(xs: list, cast: str) -> str:
    return "ARRAY[" + ",".join(str(x) for x in xs) + "]::" + cast


def d(v: object) -> date | None:
    if pd.isna(v):
        return None
    return pd.Timestamp(v).date()


# ── 온보딩 시드 ──────────────────────────────────────────────────
def load_presented() -> list[dict]:
    doc = yaml.safe_load(ONBOARDING_YAML.read_text(encoding="utf-8"))
    return doc["presented"]


def synth_onboarding(k: int, group: str, presented: list[dict]) -> dict:
    """picks 3개 + scales[매움,짠맛,단맛] → taste_vec 6축. k 는 해시 키(기획 번호).

    taste_vec 은 고른 레시피 flavor 의 6축 평균이다. 척도는 원본으로만 저장한다 — 엔진의
    사전 취향은 picks 가 있으면 척도를 쓰지 않는다 (결정 D-29, engine/persona.py `_prior`).
    행동 반영 전 값이며, 서버의 온보딩 계산과 다르면 서버 쪽이 정본이다.
    """
    picks = sorted(hpick(list(range(len(presented))), 3, "pick", k))
    scales = [hint(0, 4, "scale", k, i) for i in range(3)]
    if group == "A":  # A 집단은 팬트리/레시피 사용자 — 매운맛 약간 높게 (실측 아님)
        scales[0] = min(4, scales[0] + 1)
    mean = [round(sum(presented[p]["flavor"][i] for p in picks) / len(picks), 4) for i in range(6)]
    # 기획 데이터에는 음식 유형 문항이 없습니다. 고른 음식의 계열을 그 답으로 둡니다 -
    # 유형을 고르는 사람과 그 유형의 음식을 고르는 사람이 다르지 않다고 보는 것이고, 실제
    # 응답이 오면 원본으로 바꿉니다. 저장은 라벨이 아니라 코드입니다.
    codes = {normalize_cuisine(presented[p]["family"]) for p in picks}
    if None in codes:
        raise SystemExit(f"제시 목록에 모르는 계열이 있습니다: {picks}")
    families = sorted(code for code in codes if code is not None)
    return {"picks": picks, "scales": scales, "taste_vec": mean, "families": families}


# ── 본체 ─────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="기획 xlsx 폴더")
    ap.add_argument("--out", type=Path, default=ROOT / "deploy" / "seed" / "sim")
    ap.add_argument(
        "--pantry-for-b",
        action="store_true",
        help="B 집단(팬트리 미사용)에도 소형 냉장고를 준다. 기본은 기획대로 비워 둔다",
    )
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    users = pd.read_excel(args.src / "users.xlsx")
    cohort = pd.read_excel(args.src / "cohort_analysis_base.xlsx")
    pantry_ev = pd.read_excel(args.src / "pantry_events.xlsx")
    recipe_ev = pd.read_excel(args.src / "recipe_events.xlsx")
    cart_ev = pd.read_excel(args.src / "cart_events.xlsx")
    presented = load_presented()

    known = {r["name"] for r in pd.read_csv(INGREDIENT_CSV).to_dict("records")}
    bad = [p for p in PANTRY_POOL if p not in known]
    if bad:
        raise SystemExit(f"PANTRY_POOL 에 ingredient.csv 에 없는 이름: {bad}")

    cohort_by_user = {r["user_id"]: r for r in cohort.to_dict("records")}
    stats: dict[str, int] = defaultdict(int)

    # ── 00 persona ───────────────────────────────────────────────
    pa = json.dumps(
        {"funnel": "A", "uses_pantry": True, "uses_recipe": True, "source": "planning_v0.4"},
        ensure_ascii=False,
    )
    pb = json.dumps(
        {"funnel": "B", "uses_pantry": False, "uses_recipe": False, "source": "planning_v0.4"},
        ensure_ascii=False,
    )
    persona_sql = f"""-- 생성물. scripts/sim/convert_planning_data.py 가 만든다.
-- 손으로 고치지 않는다.
SET search_path TO reco, public;
INSERT INTO sim_persona (name, description, params) VALUES
  ('sim_funnel_A', '기획 v0.4 A집단: 첫구매 → 팬트리·레시피·상품탐색 → 장바구니 → 재구매',
   {sql_str(pa)}::jsonb),
  ('sim_funnel_B',
   '기획 v0.4 B집단: 첫구매 → 상품탐색만 → 장바구니 → 재구매 (팬트리·레시피 미사용)',
   {sql_str(pb)}::jsonb)
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, params = EXCLUDED.params;
"""  # noqa: S608  # 시드 생성. 값은 sql_str 로 이스케이프, 입력은 저장소 파일
    (out / "00_sim_persona.sql").write_text(persona_sql, encoding="utf-8", newline="\n")

    # ── 01 app_user (+ 재실행 청소) ──────────────────────────────
    rows = []
    for r in users.to_dict("records"):
        u = uid(r["user_id"])
        signup = d(r["signup_date"])
        rows.append(
            f"({u}, 'sim_{r['user_id'].lower()}', "  # noqa: S608  # 시드 생성. 값은 sql_str 로 이스케이프, 입력은 저장소 파일
            f"{sql_str(r['user_id'])}, TRUE, "
            f"(SELECT id FROM sim_persona WHERE name = 'sim_funnel_{r['group']}'), "
            f"'{signup}'::timestamptz, 'v1-min', FALSE, '{signup}'::timestamptz)"
        )
        stats["app_user"] += 1
    user_sql = (
        "-- 생성물. id 는 1,000,000 + 기획 번호 (SIM_ID_BASE). 시퀀스는 건드리지 않는다.\n"  # noqa: S608  # 시드 생성. 값은 sql_str 로 이스케이프, 입력은 저장소 파일
        "-- 재실행 가능: 기존 sim_u* 계정을 지우면\n"
        "-- FK CASCADE 로 하위 행이 함께 사라진다.\n"
        "SET search_path TO reco, public;\n"
        "DELETE FROM app_user WHERE is_simulated AND username LIKE 'sim_u%';\n"
        "INSERT INTO app_user (id, username, display_name, is_simulated, persona_id, "
        "consent_at, consent_version, consent_research, created_at) VALUES\n"
        + ",\n".join(rows)
        + ";\n"
    )
    (out / "01_app_user.sql").write_text(user_sql, encoding="utf-8", newline="\n")

    # ── 이벤트 (user_vector 의 n_events 를 알아야 하므로 먼저 계산) ──
    # 유저별 레시피 열람 이력 (날짜순) — cart(source=recipe) 에 recipe_id 를 붙일 때 쓴다
    viewed: dict[int, list[tuple[date, str]]] = defaultdict(list)
    events: list[dict] = []
    max_rcp = 0
    for r in recipe_ev.to_dict("records"):
        u, day, rcp = uid(r["user_id"]), d(r["event_date"]), str(r["recipe_id"])
        viewed[u].append((day, rcp))
        max_rcp = max(max_rcp, int(rcp[3:]))
        events.append(
            {
                "u": u,
                "day": day,
                "type": "click",
                "rcp": rcp,
                "src": r["event_id"],
                "ctx": {"origin": "recipe_detail_view"},
            }
        )
        stats["event_click"] += 1
    for v in viewed.values():
        v.sort()
    for r in cart_ev.to_dict("records"):
        u, day = uid(r["user_id"]), d(r["event_date"])
        if r["source"] != "recipe":
            stats["cart_browse_skipped"] += 1
            continue
        prior = [rcp for vd, rcp in viewed.get(u, []) if vd <= day]
        if not prior:
            stats["cart_recipe_no_view_skipped"] += 1
            continue
        events.append(
            {
                "u": u,
                "day": day,
                "type": "cook",
                "rcp": prior[-1],
                "src": r["event_id"],
                "ctx": {
                    "origin": "add_to_cart",
                    "cart_source": "recipe",
                    "item_count": int(r["item_count"]),
                },
            }
        )
        stats["event_cook"] += 1
    events.sort(key=lambda e: (e["day"], e["u"]))
    n_events: dict[int, int] = defaultdict(int)
    for e in events:
        n_events[e["u"]] += 1

    # ── 02~04 preference / vector / allergy ──────────────────────
    pref_rows, vec_rows, allergy_rows = [], [], []
    for r in users.to_dict("records"):
        u, g = uid(r["user_id"]), r["group"]
        k = hkey(u)
        ob = synth_onboarding(k, g, presented)
        cap = COOK_CAPS[hint(0, len(COOK_CAPS) - 1, "cap", k)]
        pref_rows.append(
            f"({u}, {ob['scales'][0]}, {ob['scales'][2]}, {ob['scales'][1]}, {cap}, "
            f"{hint(1, 3, 'skill', k)}, {hint(1, 4, 'hh', k)}, "
            f"{sql_arr([sql_str(f) for f in ob['families']], 'varchar[]')}, 'v1')"
        )
        ne = n_events.get(u, 0)
        mode = "onboarding" if ne == 0 else ("behavior" if ne >= N_WARM else "blended")
        stats[f"mode_{g}_{mode}"] += 1
        vec_rows.append(
            f"({u}, {sql_arr(ob['taste_vec'], 'real[]')}, {ne}, {ne}, '{mode}', "
            f"{sql_arr(ob['picks'], 'smallint[]')}, {sql_arr(ob['scales'], 'smallint[]')})"
        )
        if h("allergy", k) < 0.06:
            grp = ALLERGEN_GROUPS[hint(0, len(ALLERGEN_GROUPS) - 1, "agrp", k)]
            allergy_rows.append(f"({u}, '{grp}', 'allergy')")
            stats["allergy_users"] += 1

    (out / "02_user_preference.sql").write_text(
        "-- 생성물. spicy/sweet/salty 는 온보딩 scales[매움,짠맛,단맛] 를\n"
        "-- 컬럼 순서에 맞춰 넣는다.\n"
        "-- pref_cuisines 는 cuisine_family 코드다 (enums.ONBOARDING_CUISINES).\n"
        "-- 라벨('한식')로 넣으면 recipe.cuisine_family 와 영영 안 만난다.\n"
        "SET search_path TO reco, public;\n"
        "INSERT INTO user_preference (user_id, spicy_level, sweet_level, salty_level, "
        "max_cook_minutes, skill_level, household_size, pref_cuisines, onboarding_version) VALUES\n"
        + ",\n".join(pref_rows)
        + ";\n",
        encoding="utf-8",
        newline="\n",
    )
    (out / "03_user_vector.sql").write_text(
        "-- 생성물. taste_vec 은 온보딩 picks 의 6축 평균이며 행동 반영 전 값이다.\n"
        "-- 척도는 원본(onboarding_scales)으로만 둔다 — picks 가 있으면 엔진이 척도를 안 쓴다.\n"
        f"-- computed_from 은 event_log 건수 기준: 0→onboarding, "
        f"<{N_WARM}→blended, >={N_WARM}→behavior.\n"
        "-- 🔴 behavior 벡터 자체는 여기서 만들지 않는다 —\n"
        "-- 배치(M-03 이후)가 event_log 로 다시 계산한다.\n"
        "SET search_path TO reco, public;\n"
        "INSERT INTO user_vector (user_id, taste_vec, n_events, n_positive, computed_from, "
        "onboarding_picks, onboarding_scales) VALUES\n" + ",\n".join(vec_rows) + ";\n",
        encoding="utf-8",
        newline="\n",
    )
    (out / "04_user_allergy.sql").write_text(
        "-- 생성물. 약 6% 유저에게 알러지 그룹 1개. severity='allergy' 라야 그룹 전개가 켜진다.\n"
        "SET search_path TO reco, public;\n"
        + (
            "INSERT INTO user_allergy (user_id, allergen_group, severity) VALUES\n"
            + ",\n".join(allergy_rows)
            + ";\n"
            if allergy_rows
            else "-- (없음)\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    # ── 05 pantry_item ───────────────────────────────────────────
    # pantry_entry → 초기 6~10종, pantry_revisit → 2~3종 추가. 활성 (user, ingredient) 유일.
    pantry_rows = []
    by_user_pev: dict[int, list[tuple[date, str]]] = defaultdict(list)
    for r in pantry_ev.to_dict("records"):
        by_user_pev[uid(r["user_id"])].append((d(r["event_date"]), r["event_type"]))
    if args.pantry_for_b:
        for r in users.to_dict("records"):
            if r["group"] == "B":
                c = cohort_by_user.get(r["user_id"])
                fp = d(c["first_purchase_date"]) if c is not None else d(r["signup_date"])
                by_user_pev[uid(r["user_id"])].append((fp, "pantry_entry"))
    for u, evs in by_user_pev.items():
        k = hkey(u)
        evs.sort()
        have: set[str] = set()
        for day, et in evs:
            n = hint(6, 10, "pn", k, day) if et == "pantry_entry" else hint(2, 3, "pn", k, day)
            picks = hpick([p for p in PANTRY_POOL if p not in have], n, "pantry", k, day)
            for name in picks:
                have.add(name)
                purchased = day - timedelta(days=hint(0, 2, "pd", k, name))
                if h("exp", k, name) < 0.3:  # 30% 는 유저가 소비기한을 직접 입력한 것으로
                    exp = f"'{purchased + timedelta(days=hint(3, 14, 'ed', k, name))}'"
                    src = "user"
                else:
                    exp, src = "NULL", "estimated"
                pantry_rows.append(
                    f"({u}, (SELECT id FROM ingredient WHERE name = {sql_str(name)}), "  # noqa: S608  # 시드 생성. 값은 sql_str 로 이스케이프, 입력은 저장소 파일
                    f"{hint(1, 3, 'q', k, name)}, '{purchased}', {exp}, '{src}', "
                    f"'{day}'::timestamptz)"
                )
                stats["pantry_item"] += 1
        stats["pantry_users"] += 1
    (out / "05_pantry_item.sql").write_text(
        "-- 생성물. 재료는 이름으로 ingredient 에 조인한다 (id 는 적재 환경마다 다르다).\n"
        "-- expires_at NULL 은 effective_expiry() 가 purchased_at + shelf_life_days 로 추정한다.\n"
        "SET search_path TO reco, public;\n"
        + (
            "INSERT INTO pantry_item (user_id, ingredient_id, quantity, purchased_at, expires_at, "
            "expires_at_source, added_at) VALUES\n" + ",\n".join(pantry_rows) + ";\n"
            if pantry_rows
            else "-- (없음)\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    # ── 06 event_log ─────────────────────────────────────────────
    ev_rows = []
    for e in events:
        ts = datetime.combine(e["day"], datetime.min.time()) + timedelta(
            hours=hint(7, 22, "hr", e["src"]), minutes=hint(0, 59, "mi", e["src"])
        )
        ctx = {"sim": "planning_v0.4", "src_event_id": e["src"], "rcp_code": e["rcp"], **e["ctx"]}
        ev_rows.append(
            f"({e['u']}, {sql_str(e['rcp'])}, '{e['type']}', "
            f"'d-{e['u']}-{e['day'].strftime('%Y%m%d')}', "
            f"{sql_str(json.dumps(ctx, ensure_ascii=False))}::jsonb, '{ts}'::timestamptz)"
        )
    event_sql = f"""-- 생성물. RCP 번호 → recipe.id 는 적재 시점에 published 레시피를
-- id 순으로 번호 매겨 묶는다.
-- session_id 접두어 'd-' = 개발·시딩 트래픽. impression 은 넣지 않는다 —
-- 서버가 /v1/recommend 에서 자동 기록한다.
SET search_path TO reco, public;
DO $$
DECLARE n_pub INT;
BEGIN
  SELECT count(*) INTO n_pub FROM recipe WHERE status = 'published';
  IF n_pub < {max_rcp} THEN
    RAISE EXCEPTION 'published 레시피 % 건 < RCP 최댓값 {max_rcp}. '
      '레시피를 먼저 적재하라 (scripts/reco/load_recipes.py)', n_pub;
  END IF;
END $$;
CREATE TEMP TABLE sim_recipe_map AS
  SELECT id AS recipe_id, 'RCP' || lpad(row_number() OVER (ORDER BY id)::text, 4, '0') AS rcp_code
  FROM recipe WHERE status = 'published';
CREATE TEMP TABLE sim_events (user_id BIGINT, rcp_code TEXT, event_type TEXT,
                              session_id TEXT, context JSONB, created_at TIMESTAMPTZ);
INSERT INTO sim_events VALUES
{",".join(chr(10) + r for r in ev_rows)};
INSERT INTO event_log (user_id, recipe_id, event_type, session_id, context, source, created_at)
SELECT e.user_id, m.recipe_id, e.event_type, e.session_id, e.context, 'client', e.created_at
FROM sim_events e JOIN sim_recipe_map m USING (rcp_code)
ORDER BY e.created_at;
DROP TABLE sim_events; DROP TABLE sim_recipe_map;
"""  # noqa: S608  # 시드 생성. 값은 sql_str 로 이스케이프, 입력은 저장소 파일
    (out / "06_event_log.sql").write_text(event_sql, encoding="utf-8", newline="\n")

    # ── 99 verify · loader · README ──────────────────────────────
    (out / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
