"""사용자 취향 원본의 JSON 저장소. 사용자당 파일 하나입니다.

실 DB 가 붙기 전까지 페르소나 원본(`engine/persona.TasteProfile`)의 정본입니다. 붙은
뒤에는 같은 내용을 `user_vector` 와 `event_log` 에서 읽는 것이 DB 전환 점검표의
항목이고, 그때 이 파일은 이전 도구가 됩니다. SQL 이 아니라서 `repository.py` 에
두지 않습니다(03 의 5절).

## 사용자가 늘어도 관리되는 형태

- **사용자당 파일 하나.** 파일 하나에 전부 넣으면 쓰기 한 번이 전체를 다시 씁니다.
- **폴더 256개로 나눕니다.** 한 폴더에 파일 수십만 개가 쌓이면 목록 조회가 느려집니다.
- **이벤트를 잘라냅니다.** 저장할 때 `RankingPolicy.persona_max_events` 와
  `persona_max_event_age_days` 를 넘는 것을 버립니다. 감쇠 때문에 결과에는 영향이 없습니다.
- **원자적 쓰기.** 임시 파일에 쓰고 이름을 바꿉니다. 쓰다가 죽어도 이전 파일이 남습니다.

파일 안에는 개인의 행동 이력이 들어 있습니다. 기본 위치 `data/` 는 `.gitignore` 가
막고 있어 커밋되지 않습니다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from features.recommend.engine import taste
from features.recommend.engine.persona import TasteEvent, TasteProfile
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import EventType

#: 파일 형식 버전. 필드를 바꾸면 올리고 읽는 쪽에서 옛 판을 변환합니다.
SCHEMA_VERSION = 1
SHARD_COUNT = 256


class ProfileStore(Protocol):
    """페르소나 원본을 읽고 쓰는 곳. JSON 과 DB 구현이 같은 모양이어야 서비스가 바뀌지 않습니다."""

    def load(self, user_id: int) -> TasteProfile | None: ...

    def save(self, profile: TasteProfile) -> None: ...


class JsonProfileStore:
    """`root/<샤드>/<user_id>.json`. 샤드는 `user_id % 256` 입니다."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, user_id: int) -> Path:
        return self.root / f"{user_id % SHARD_COUNT:02x}" / f"{user_id}.json"

    def load(self, user_id: int) -> TasteProfile | None:
        """없으면 None. 있는데 못 읽으면 예외를 올립니다 — 조용히 빈 취향이 되면 안 됩니다."""
        target = self.path(user_id)
        if not target.exists():
            return None
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            return _from_json(raw)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"취향 파일을 읽을 수 없습니다: {target}") from error

    def save(self, profile: TasteProfile) -> None:
        target = self.path(profile.user_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_to_json(profile), ensure_ascii=False, indent=1)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)


def load_presented_flavors(path: Path) -> tuple[FlavorVector, ...]:
    """온보딩 제시 목록의 6축. `seeds/onboarding_recipes.yaml` 의 `presented` 순서입니다.

    `OnboardingIn.picks` 는 이 배열의 인덱스입니다(레시피 ID 가 아닙니다). 축 순서가
    엔진과 같은지 확인합니다 — 어긋나면 값이 전부 0~1 이라 어떤 검사에도 안 걸립니다.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    axes = tuple(document.get("axes", ()))
    if axes != taste.FLAVOR_AXES:
        raise ValueError(f"제시 목록의 축 순서가 엔진과 다릅니다: {axes} != {taste.FLAVOR_AXES}")
    presented = document["presented"]
    return tuple(taste.as_vector([float(v) for v in entry["flavor"]]) for entry in presented)


def _to_json(profile: TasteProfile) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "user_id": profile.user_id,
        "picks": list(profile.picks),
        "pick_flavors": [list(v) for v in profile.pick_flavors],
        "scales": None if profile.scales is None else list(profile.scales),
        "updated_at": None if profile.updated_at is None else profile.updated_at.isoformat(),
        "events": [
            {
                "recipe_id": e.recipe_id,
                "kind": e.kind.value,
                "at": e.at.isoformat(),
                "flavor": list(e.flavor),
                "value": e.value,
            }
            for e in profile.events
        ],
    }


def _from_json(raw: dict[str, Any]) -> TasteProfile:
    if raw.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"모르는 파일 형식 버전입니다: {raw.get('schema')}")
    return TasteProfile(
        user_id=int(raw["user_id"]),
        picks=tuple(int(i) for i in raw.get("picks", [])),
        pick_flavors=tuple(taste.as_vector(v) for v in raw.get("pick_flavors", [])),
        scales=None if raw.get("scales") is None else tuple(float(s) for s in raw["scales"]),
        events=tuple(
            TasteEvent(
                recipe_id=int(e["recipe_id"]),
                kind=EventType(e["kind"]),
                at=datetime.fromisoformat(e["at"]),
                flavor=taste.as_vector(e["flavor"]),
                value=e.get("value"),
            )
            for e in raw.get("events", [])
        ),
        updated_at=(
            None if raw.get("updated_at") is None else datetime.fromisoformat(raw["updated_at"])
        ),
    )
