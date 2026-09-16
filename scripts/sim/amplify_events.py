"""기획 이벤트를 웜 전환이 일어나는 규모로 증폭한다 (recipe_events · cart_events 만).

실행: uv run python scripts/sim/amplify_events.py --src <원본 xlsx 폴더> --out <증폭본 폴더>
이후 convert_planning_data.py --src <증폭본 폴더> 로 SQL 을 만든다.

원본 v0.4 는 유저당 이벤트 최댓값이 3건이라 아무도 n_warm=20 을 넘지 못한다.
증폭 규칙 (A 집단 · 결정론적):
  cook 이 1건 이상인 유저 (engaged)   → 총 24~40 건  (warm)
  열람만 있는 유저                   → 총 8~14 건   (blended)
  나머지                             → 그대로       (cold)
새 이벤트는 그 유저의 첫 열람일부터 30일 안에 놓고, 레시피는 본인 열람 목록 70% +
전체 RCP 풀 30% 로 뽑는다. click:cook 비율은 약 3:1. 다른 xlsx 는 그대로 복사한다.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from datetime import timedelta
from pathlib import Path

import pandas as pd

SEED = "sim-amplify-v1"
RCP_POOL = [f"RCP{i:04d}" for i in range(1, 120)]


def h(*keys: object) -> float:
    digest = hashlib.sha256(("|".join(map(str, keys)) + SEED).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def hint(lo: int, hi: int, *keys: object) -> int:
    return lo + int(h(*keys) * (hi - lo + 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    recipe = pd.read_excel(args.src / "recipe_events.xlsx")
    cart = pd.read_excel(args.src / "cart_events.xlsx")
    recipe["event_date"] = pd.to_datetime(recipe["event_date"])
    cart["event_date"] = pd.to_datetime(cart["event_date"])

    cooks = set(cart.loc[cart["source"] == "recipe", "user_id"])
    views = recipe.groupby("user_id")
    r_seq = int(recipe["event_id"].str[1:].astype(int).max())
    c_seq = int(cart["event_id"].str[1:].astype(int).max())
    new_r, new_c = [], []
    tiers = {"warm": 0, "blended": 0}

    for u, g in views:
        base_n = len(g) + int((cart["user_id"] == u).sum())
        if u in cooks:
            target, tier = hint(24, 40, "t", u), "warm"
        else:
            target, tier = hint(8, 14, "t", u), "blended"
        tiers[tier] += 1
        own = list(g["recipe_id"])
        start = g["event_date"].min()
        for k in range(max(0, target - base_n)):
            day = start + timedelta(days=hint(0, 30, "d", u, k))
            rcp = (
                own[hint(0, len(own) - 1, "o", u, k)]
                if h("p", u, k) < 0.7
                else RCP_POOL[hint(0, 118, "r", u, k)]
            )
            if h("kind", u, k) < 0.25:
                c_seq += 1
                new_c.append(
                    {
                        "event_id": f"C{c_seq:06d}",
                        "user_id": u,
                        "event_date": day,
                        "event_type": "add_to_cart",
                        "source": "recipe",
                        "item_count": hint(1, 5, "ic", u, k),
                    }
                )
                # cook 은 열람 이후여야 recipe_id 가 붙는다 → 같은 날 열람을 하나 앞에 둔다
                r_seq += 1
                new_r.append(
                    {
                        "event_id": f"R{r_seq:06d}",
                        "user_id": u,
                        "event_date": day,
                        "event_type": "recipe_detail_view",
                        "recipe_id": rcp,
                    }
                )
            else:
                r_seq += 1
                new_r.append(
                    {
                        "event_id": f"R{r_seq:06d}",
                        "user_id": u,
                        "event_date": day,
                        "event_type": "recipe_detail_view",
                        "recipe_id": rcp,
                    }
                )

    recipe_out = pd.concat([recipe, pd.DataFrame(new_r)]).sort_values(
        ["user_id", "event_date", "event_id"]
    )
    cart_out = pd.concat([cart, pd.DataFrame(new_c)]).sort_values(
        ["user_id", "event_date", "event_id"]
    )
    for df in (recipe_out, cart_out):
        df["event_date"] = df["event_date"].dt.strftime("%Y-%m-%d")
    recipe_out.to_excel(
        args.out / "recipe_events.xlsx", index=False, sheet_name="recipe_events.csv"
    )
    cart_out.to_excel(args.out / "cart_events.xlsx", index=False, sheet_name="cart_events.csv")
    for f in args.src.glob("*.xlsx"):
        if f.name not in {"recipe_events.xlsx", "cart_events.xlsx"}:
            shutil.copy(f, args.out / f.name)
    print(
        f"recipe_events {len(recipe)} -> {len(recipe_out)}, "
        f"cart_events {len(cart)} -> {len(cart_out)}, "
        f"users warm-target={tiers['warm']} blended-target={tiers['blended']}"
    )


if __name__ == "__main__":
    main()
