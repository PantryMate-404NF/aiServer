"""시뮬 시드(`deploy/seed/sim/*.sql`)와 Mock 카탈로그를 엔진 입력에 가까운 모양으로 읽습니다.

`scenario_engine.py` 가 씁니다. 여기는 읽기와 매핑만 있고 엔진 호출은 없습니다.

- 시드 SQL 은 생성물이라 행 형식이 고정입니다. 정규식으로 읽고, 읽은 행 수를 `stats.json`
  과 대조해 정규식이 행을 놓치면 멈춥니다.
- 레시피는 Mock 카탈로그 120건을 published 레시피처럼 봅니다. RCP 번호 → id 순 매핑은
  `06_event_log.sql` 의 적재 규칙과 같습니다.
- 기본 소비기한은 A 트랙 SQL `effective_expiry()` 의 규칙(이름 예외 → 가장 긴 카테고리 경로)을
  `seeds/ingredient_shelf_life.yaml` 과 `seeds/ingredient.csv` 에서 그대로 따릅니다.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import yaml

from features.recommend.engine.context import CorpusStats, RecipeFeature
from features.recommend.enums import EventType

ROOT = Path(__file__).resolve().parents[2]
EVAL_PATH = ROOT / "scripts" / "eval_recommend_mock.py"
INGREDIENT_CSV = ROOT / "seeds" / "ingredient.csv"
SHELF_LIFE_YAML = ROOT / "seeds" / "ingredient_shelf_life.yaml"
KST = timezone(timedelta(hours=9))
USER_ROW = re.compile(
    r"^\((\d+), 'sim_u\d+', '[^']*', TRUE, \(SELECT id FROM sim_persona WHERE name = '(\w+)'\)"
)
PREF_ROW = re.compile(
    r"^\((\d+), (\d+), (\d+), (\d+), (\d+), (\d+), (\d+), ARRAY\[([^\]]*)\]::varchar\[\], 'v1'\)"
)
VECTOR_ROW = re.compile(
    r"^\((\d+), ARRAY\[[^\]]+\]::real\[\], (\d+), (\d+), '(\w+)', "
    r"ARRAY\[([^\]]+)\]::smallint\[\], ARRAY\[([^\]]+)\]::smallint\[\]\)"
)
ALLERGY_ROW = re.compile(r"^\((\d+), '(\w+)', 'allergy'\)")
PANTRY_ROW = re.compile(
    r"^\((\d+), \(SELECT id FROM ingredient WHERE name = '([^']+)'\), (\d+), '([^']+)', "
    r"(NULL|'[^']+'), '(\w+)', '([^']+)'::timestamptz\)"
)
EVENT_ROW = re.compile(
    r"^\((\d+), '(RCP\d{4})', '(\w+)', '(d-[^']+)', '\{[^']*\}'::jsonb, '([^']+)'::timestamptz\)"
)


@dataclass(frozen=True)
class PantryRow:
    name: str
    purchased: date
    expires: date | None
    added: datetime


@dataclass(frozen=True)
class SimEvent:
    code: str
    kind: EventType
    at: datetime


@dataclass(frozen=True)
class SimUser:
    """시드 한 사용자. SQL 여섯 파일을 합친 것이며 이벤트와 냉장고는 시간순입니다."""

    user_id: int
    group: str = ""
    picks: tuple[int, ...] = ()
    scales: tuple[int, ...] = ()
    sim_mode: str = ""
    sim_events: int = 0
    max_cook_minutes: int | None = None
    skill_level: int | None = None
    cuisines: tuple[str, ...] = ()
    allergy_groups: tuple[str, ...] = ()
    pantry: tuple[PantryRow, ...] = ()
    events: tuple[SimEvent, ...] = ()

    def last_activity(self) -> datetime | None:
        moments = [e.at for e in self.events] + [row.added for row in self.pantry]
        return max(moments) if moments else None


@dataclass(frozen=True)
class Catalog:
    """Mock 카탈로그를 published 레시피처럼 본 것."""

    recipes: dict[int, RecipeFeature]
    clusters: dict[int, int]
    corpus: CorpusStats
    by_code: dict[str, int]
    code_of: dict[int, str]
    ingredient_ids: dict[str, int]
    allergen_groups: dict[str, list[int]]
    staple_ids: frozenset[int]
    cuisines: frozenset[str]


@dataclass(frozen=True)
class ShelfLife:
    """`effective_expiry()` 가 쓰는 기본 소비기한. 이름 예외 → 가장 긴 카테고리 경로 순입니다."""

    by_name: dict[str, int]
    by_path: dict[str, int]
    category_of: dict[str, str]

    def days(self, name: str) -> int | None:
        if name in self.by_name:
            return self.by_name[name]
        path = self.category_of.get(name)
        if path is None:
            return None
        hits = [p for p in self.by_path if path == p or path.startswith(p + ".")]
        return self.by_path[max(hits, key=len)] if hits else None


def load_eval() -> ModuleType:
    """Mock 카탈로그 로더·후보 조회·완화를 평가 스크립트에서 그대로 빌립니다."""
    spec = importlib.util.spec_from_file_location("eval_recommend_mock", EVAL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"평가 스크립트를 읽을 수 없습니다: {EVAL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rows(path: Path, pattern: re.Pattern[str]) -> list[tuple[str, ...]]:
    found = [pattern.match(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [m.groups() for m in found if m is not None]


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def load_users(seed_dir: Path) -> dict[int, SimUser]:
    """시드 SQL 을 읽습니다. 행 수가 `stats.json` 과 다르면 정규식이 행을 놓친 것이라 멈춥니다."""
    users: dict[int, SimUser] = {}
    for uid, group in rows(seed_dir / "01_app_user.sql", USER_ROW):
        users[int(uid)] = SimUser(user_id=int(uid), group=group)
    for uid, _spicy, _sweet, _salty, minutes, skill, _household, cuisines in rows(
        seed_dir / "02_user_preference.sql", PREF_ROW
    ):
        users[int(uid)] = replace(
            users[int(uid)],
            max_cook_minutes=int(minutes),
            skill_level=int(skill),
            cuisines=tuple(c.strip("'") for c in cuisines.split(",") if c.strip()),
        )
    for uid, n_events, _n_pos, mode, picks, scales in rows(
        seed_dir / "03_user_vector.sql", VECTOR_ROW
    ):
        # onboarding_scales 는 엔진과 같은 [매움, 짠맛, 단맛] 순서입니다.
        users[int(uid)] = replace(
            users[int(uid)],
            sim_events=int(n_events),
            sim_mode=mode,
            picks=tuple(int(p) for p in picks.split(",")),
            scales=tuple(int(s) for s in scales.split(",")),
        )
    allergies = rows(seed_dir / "04_user_allergy.sql", ALLERGY_ROW)
    for uid, group in allergies:
        user = users[int(uid)]
        users[int(uid)] = replace(user, allergy_groups=(*user.allergy_groups, group))
    pantry = rows(seed_dir / "05_pantry_item.sql", PANTRY_ROW)
    for uid, name, _qty, bought, expires, _source, added in pantry:
        user = users[int(uid)]
        row = PantryRow(
            name=name,
            purchased=date.fromisoformat(bought),
            expires=None if expires == "NULL" else date.fromisoformat(expires.strip("'")),
            added=parse_ts(added),
        )
        users[int(uid)] = replace(user, pantry=(*user.pantry, row))
    events = rows(seed_dir / "06_event_log.sql", EVENT_ROW)
    for uid, code, kind, _session, at in events:
        user = users[int(uid)]
        made = SimEvent(code=code, kind=EventType(kind), at=parse_ts(at))
        users[int(uid)] = replace(user, events=(*user.events, made))
    for uid, user in users.items():
        users[uid] = replace(
            user,
            events=tuple(sorted(user.events, key=lambda e: e.at)),
            pantry=tuple(sorted(user.pantry, key=lambda r: r.added)),
        )
    stats = json.loads((seed_dir / "stats.json").read_text(encoding="utf-8"))
    parsed = {
        "app_user": len(users),
        "pantry_item": len(pantry),
        "allergy_users": len(allergies),
        "event_click": sum(1 for e in events if e[2] == "click"),
        "event_cook": sum(1 for e in events if e[2] == "cook"),
    }
    wrong = {k: (v, stats.get(k)) for k, v in parsed.items() if stats.get(k) != v}
    if wrong:
        raise SystemExit(f"시드 행 수가 stats.json 과 다릅니다 (읽은 것, 기록): {wrong}")
    return users


def load_catalog(ev: ModuleType) -> Catalog:
    raw = ev.load_catalog()
    recipes = {int(r["recipe_id"]): ev.to_recipe(r) for r in raw["recipes"]}
    # 시드와 같은 규칙: published 레시피를 id 순으로 세어 RCP 번호에 묶습니다.
    by_code = {f"RCP{i:04d}": rid for i, rid in enumerate(sorted(recipes), 1)}
    ingredient_ids = {str(name): int(i) for i, name in raw["ingredients"].items()}
    with INGREDIENT_CSV.open(encoding="utf-8") as handle:
        staples = [r["name"] for r in csv.DictReader(handle) if r["is_staple"] == "true"]
    return Catalog(
        recipes=recipes,
        clusters={int(r["recipe_id"]): int(r["cluster_id"]) for r in raw["recipes"]},
        corpus=ev.build_corpus(raw, recipes),
        by_code=by_code,
        code_of={rid: code for code, rid in by_code.items()},
        ingredient_ids=ingredient_ids,
        allergen_groups={k: [int(x) for x in v] for k, v in raw["allergen_groups"].items()},
        staple_ids=frozenset(ingredient_ids[n] for n in staples if n in ingredient_ids),
        cuisines=frozenset(str(c) for c in raw["cuisines"]),
    )


def load_shelf_life() -> ShelfLife:
    document = yaml.safe_load(SHELF_LIFE_YAML.read_text(encoding="utf-8"))
    with INGREDIENT_CSV.open(encoding="utf-8") as handle:
        category_of = {r["name"]: r["category_path"] for r in csv.DictReader(handle)}
    return ShelfLife(
        by_name={str(e["name"]): int(e["days"]) for e in document.get("overrides", [])},
        by_path={str(e["path"]): int(e["days"]) for e in document.get("defaults", [])},
        category_of=category_of,
    )


def effective_expiry(row: PantryRow, shelf: ShelfLife) -> date | None:
    """SQL `effective_expiry()` 의 규칙. 유저 입력이 이기고, 없으면 구매일 + 기본 소비기한입니다."""
    if row.expires is not None:
        return row.expires
    days = shelf.days(row.name)
    return None if days is None else row.purchased + timedelta(days=days)


def allergy_ids(user: SimUser, cat: Catalog) -> frozenset[int]:
    """시드와 Mock 카탈로그가 같은 어휘(DDL 의 소문자 10종)라 그대로 찾습니다.

    09-18 전에는 대문자 사본으로 옮기는 표가 여기 있었고, `sesame` 은 대응이 없어 검사에서
    빠졌습니다. 이제 카탈로그가 시드에서 그룹을 읽으므로(`generate_mock_fixtures`) 표가 없습니다.
    """
    return frozenset(i for group in user.allergy_groups for i in cat.allergen_groups.get(group, []))
