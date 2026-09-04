"""크롤러 출력 → 우리 스키마 어댑터.

    from features.recommend.ingest.adapter import SourceAdapter
    a = SourceAdapter.load("mangae")
    rec = a.map_recipe(crawled_json)

**목적: 크롤러 출력 형태가 우리 스키마에 직접 닿지 않게 하는 것.**

크롤링이 끝나고 실제 JSON 을 보면 `sources/*.yaml` 의 `paths` 만 고치면 되고,
이 파일 · DB 스키마 · 정규화 파이프라인은 건드리지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: 🔴 매핑 yaml 은 코드가 아니라 **시드 데이터**다. 09-04 에 seeds/sources/ 로
#:    옮겼다 — 크롤 대상이 늘 때 고치는 사람이 데이터 담당이지 코드 담당이
#:    아니고, 다른 시드(ingredient.csv 등)와 같은 자리에 있어야 찾는다.
SOURCES = Path(__file__).resolve().parents[4] / "seeds" / "sources"

_DIFFICULTY = {"아무나": 1, "초급": 2, "중급": 3, "고급": 4, "신의경지": 5}


# ─────────────────────────────────────────────────────────────────
# 값 변환
# ─────────────────────────────────────────────────────────────────
def t_strip(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def t_int(v: Any) -> int | None:
    if v is None:
        return None
    m = re.search(r"-?\d+", str(v).replace(",", ""))
    return int(m.group()) if m else None


def t_float(v: Any) -> float | None:
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def t_minutes(v: Any) -> int | None:
    """'30분' '1시간' '1시간 30분' '30' → 분.

    🔴 파싱 실패는 반드시 None 이다. 0 으로 채우면 모든 실패 레시피가
    "가장 빠른 요리"로 최상위에 올라온다 (설계 2-3).
    """
    if v is None:
        return None
    s = str(v)
    h = re.search(r"(\d+)\s*시간", s)
    m = re.search(r"(\d+)\s*분", s)
    if h or m:
        return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    bare = re.fullmatch(r"\s*(\d+)\s*", s)
    return int(bare.group(1)) if bare else None


def t_difficulty(v: Any) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in _DIFFICULTY:
        return _DIFFICULTY[s]
    n = t_int(s)
    return n if n and 1 <= n <= 5 else None


def t_count(v: Any) -> int | None:
    """배열 길이를 센다. reviews 개수를 인기도 프록시로 쓴다."""
    return len(v) if isinstance(v, list) else None


def t_list(v: Any) -> list[str] | None:
    if v is None:
        return None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
    else:
        out = [x.strip() for x in re.split(r"[,·|/]", str(v)) if x.strip()]
    return out or None


TRANSFORMS = {
    "strip": t_strip, "int": t_int, "float": t_float,
    "minutes": t_minutes, "difficulty": t_difficulty, "list": t_list,
    "count": t_count,
}


# ─────────────────────────────────────────────────────────────────
# 경로 추출
# ─────────────────────────────────────────────────────────────────
def dig(obj: Any, path: str) -> Any:
    """'a.b.c' 점 표기 · 'a[].b' 배열 순회 · '[]' 자기 자신이 배열.

    찾지 못하면 None. 예외를 내지 않는다 — 크롤러 출력이 어떤 모양이든
    probe 가 리포트할 수 있어야 한다.
    """
    if path == "[]":
        return obj if isinstance(obj, list) else None
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if part.endswith("[]"):
            key = part[:-2]
            cur = cur.get(key) if (key and isinstance(cur, dict)) else cur
            if not isinstance(cur, list):
                return None
            # 배열 원소에서 남은 경로를 각각 뽑는 것은 호출부가 처리한다
            return cur
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            cur = [d.get(part) if isinstance(d, dict) else None for d in cur]
        else:
            return None
    return cur


def first_hit(obj: Any, paths: list[str]) -> tuple[Any, str | None]:
    """후보 경로를 순서대로 시도. (값, 적중한 경로) 를 돌려준다."""
    for p in paths:
        v = dig(obj, p)
        if v not in (None, "", [], {}):
            return v, p
    return None, None


# ─────────────────────────────────────────────────────────────────
@dataclass
class MapResult:
    values: dict[str, Any] = field(default_factory=dict)
    hit_paths: dict[str, str] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_required


class SourceAdapter:
    def __init__(self, spec: dict):
        self.spec = spec
        self.source: str = spec["source"]

    @classmethod
    def load(cls, name: str) -> SourceAdapter:
        p = SOURCES / f"{name}.yaml"
        if not p.exists():
            raise FileNotFoundError(f"매핑 없음: {p}")
        return cls(yaml.safe_load(p.read_text(encoding="utf-8")))

    # ── 필드 하나 ────────────────────────────────────────────────
    def _one(self, obj: Any, name: str, rule: dict, res: MapResult) -> None:
        v, hit = first_hit(obj, rule.get("paths", []))
        if hit:
            res.hit_paths[name] = hit
        if v is None:
            if rule.get("required"):
                res.missing_required.append(name)
                return
            fb = rule.get("fallback", "null")
            res.fallbacks.append(f"{name}: {fb}")
            if isinstance(fb, str) and fb.startswith("const:"):
                res.values[name] = fb.split(":", 1)[1]
            else:
                res.values[name] = None      # derive: 는 호출부가 처리
            return
        fn = TRANSFORMS.get(rule.get("transform", ""))
        res.values[name] = fn(v) if fn else v

    # ── 레시피 ──────────────────────────────────────────────────
    def map_recipe(self, obj: dict) -> MapResult:
        res = MapResult()
        for name, rule in self.spec["recipe"].items():
            self._one(obj, name, rule, res)
        res.values["source"] = self.source
        res.values["raw_json"] = obj          # 원본 전체 보존 (설계 2-3)
        return res

    # ── 재료 ────────────────────────────────────────────────────
    def map_ingredients(self, obj: dict) -> tuple[list[dict], MapResult]:
        """[{group_name, position, raw_text}] 를 돌려준다.

        group_name 이 없으면 None 으로 두고 fallback 을 기록한다.
        P4 역할 판정(설계 4-5)이 is_staple/is_seasoning 플래그와 수량 표현으로
        대체 판별한다 — 정확도는 떨어지지만 동작은 한다.
        """
        res = MapResult()
        ing = self.spec["ingredients"]
        container, hit = first_hit(obj, ing["container"]["paths"])
        if container is None:
            res.missing_required.append("ingredients.container")
            return [], res
        res.hit_paths["ingredients.container"] = hit or ""

        rows: list[dict] = []
        pos = 0
        groups = container if isinstance(container, list) else [container]
        has_group = False

        for g in groups:
            gname = None
            if isinstance(g, dict):
                for gp in ing["group_name"]["paths"]:
                    key = gp.removeprefix("[].")
                    if key in g and g[key]:
                        gname, has_group = str(g[key]).strip(), True
                        break
                items = None
                for ip in ing["raw_text"]["paths"]:
                    key = ip.removeprefix("[].").removesuffix("[]")
                    if key and key in g:
                        items = g[key]
                        break
                items = items if isinstance(items, list) else [g]
            else:
                items = [g]

            for it in items:
                # 🔴 raw_text 를 최우선으로 본다. name 만 쓰면 수량이 통째로 날아간다
                #    (실측: {"name":"소고기","amount":"100g","raw_text":"소고기 100g"})
                txt = it if isinstance(it, str) else (
                    it.get("raw_text") or it.get("text")
                    or (f"{it.get('name','')} {it.get('amount','')}".strip()
                        if it.get("name") else None)
                    or it.get("ingredient")
                    if isinstance(it, dict) else None)
                if not txt:
                    continue
                pos += 1
                rows.append({"group_name": gname, "position": pos,
                             "raw_text": str(txt).strip()[:255]})

        if not has_group:
            res.fallbacks.append("ingredients.group_name: derive:group_from_flat")
        if not rows:
            res.missing_required.append("ingredients.raw_text")
        return rows, res

    # ── 조리 단계 ───────────────────────────────────────────────
    def map_steps(self, obj: dict) -> list[dict]:
        st = self.spec.get("steps", {})
        container, _ = first_hit(obj, st.get("container", {}).get("paths", []))
        if not isinstance(container, list):
            return []
        out = []
        for i, s in enumerate(container, start=1):
            txt = s if isinstance(s, str) else (
                s.get("instruction") or s.get("text") or s.get("description")
                or s.get("content") if isinstance(s, dict) else None)
            if txt:
                img = s.get("image") or s.get("img") if isinstance(s, dict) else None
                out.append({"step_no": i, "text": str(txt).strip(), "image_url": img})
        return out
