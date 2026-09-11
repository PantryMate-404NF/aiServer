#!/usr/bin/env python3
"""크롤링 샘플을 진단한다. **크롤링 담당자에게 무엇을 고쳐달라 할지 알려주는 도구.**

    python scripts/reco/probe.py 샘플.json
    python scripts/reco/probe.py 샘플들/ --source mangae

"미확인" 상태를 "확인 가능"으로 바꾼다. 실제 크롤링 JSON 이 오면 이것부터 돌린다.
매핑이 틀렸으면 sources/mangae.yaml 의 paths 만 고치면 되고,
adapter.py · DB 스키마 · 정규화 파이프라인은 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.recommend.ingest.adapter import SourceAdapter

#: 없을 때 무엇이 나빠지는지. 크롤링 담당자에게 그대로 전달할 문장.
IMPACT = {
    "source_id": ("🔴", "중복 판정 불가. 레시피를 적재할 수 없다"),
    "title": ("🔴", "레시피 식별 불가"),
    "ingredients.container": ("🔴", "재료가 없으면 이 서비스가 성립하지 않는다"),
    "ingredients.raw_text": ("🔴", "정규화 대상이 없다"),
    "ingredients.group_name": (
        "🟡",
        "role 판별을 is_staple/is_seasoning 플래그와 수량 표현으로만 하게 된다. "
        "essential 과다 판정 → 추천 후보가 좁아진다 (설계 4-5)",
    ),
    "dish_type": ("🟡", "다양성 re-ranking 보조 축 상실 (설계 5-3-2)"),
    "situation": ("⚪", "상황 기반 필터 불가. 현재 설계에서 쓰는 곳 없음"),
    "main_ing_cat": ("🟡", "다양성 캡 축 하나 상실"),
    "method": ("⚪", "cuisine 분류 규칙의 보조 신호"),
    "cook_minutes": ("🟡", "f_time_fit 피처가 전부 중립값 0.5 가 된다"),
    "difficulty": ("⚪", "f_skill_fit 피처 비활성 (가중치 0)"),
    "view_count": ("🟡", "popularity_score 가 무의미해진다. 품질 하한선 상실"),
    "rating_avg": ("⚪", "popularity_score 정확도 하락"),
    "image_url": ("⚪", "데모 화면 품질. quality_score 감점"),
    "servings": ("⚪", "1인분 환산 불가 → nutrition 정확도 하락"),
    "url": ("⚪", "원본 링크 없음"),
    "description": ("⚪", "content_emb 품질 소폭 하락"),
    "rating_count": ("⚪", ""),
}


def load_samples(target: Path) -> list[dict]:
    if target.is_dir():
        out = []
        for p in sorted(target.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.extend(d if isinstance(d, list) else [d])
        return out
    d = json.loads(target.read_text(encoding="utf-8"))
    return d if isinstance(d, list) else [d]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path, help="샘플 JSON 파일 또는 디렉토리")
    ap.add_argument("--source", default="mangae")
    ap.add_argument("--show", type=int, default=3, help="샘플 값 표시 개수")
    a = ap.parse_args()

    samples = load_samples(a.target)
    if not samples:
        print("샘플이 비어 있습니다")
        return 1
    ad = SourceAdapter.load(a.source)

    print(f"소스 {a.source} · 샘플 {len(samples)}건\n")

    hit_c: Counter[str] = Counter()
    fb_c: Counter[str] = Counter()
    miss_c: Counter[str] = Counter()
    paths: dict[str, Counter[str]] = {}
    vals: dict[str, list] = {}
    n_ing, n_step, n_group = 0, 0, 0

    for s in samples:
        r = ad.map_recipe(s)
        fb_names = {f.split(":")[0].strip() for f in r.fallbacks}
        for k, v in r.values.items():
            if k in ("raw_json", "source") or k in fb_names:
                continue  # 폴백으로 채워진 값은 '매핑 성공' 이 아니다
            if v is not None:
                hit_c[k] += 1
                vals.setdefault(k, [])
                if len(vals[k]) < a.show and v not in vals[k]:
                    vals[k].append(v)
        for k, p in r.hit_paths.items():
            paths.setdefault(k, Counter())[p] += 1
        for f in r.fallbacks:
            fb_c[f.split(":")[0].strip()] += 1
        for m in r.missing_required:
            miss_c[m] += 1

        rows, ir = ad.map_ingredients(s)
        n_ing += len(rows)
        n_group += sum(1 for x in rows if x["group_name"])
        for f in ir.fallbacks:
            fb_c[f.split(":")[0].strip()] += 1
        for m in ir.missing_required:
            miss_c[m] += 1
        for k, p in ir.hit_paths.items():
            paths.setdefault(k, Counter())[p] += 1
        n_step += len(ad.map_steps(s))

    N = len(samples)

    # ── 매핑된 필드 ──────────────────────────────────────────────
    print("── 매핑 성공 ────────────────────────────────────────────")
    for k in ad.spec["recipe"]:
        c = hit_c.get(k, 0)
        if not c:
            continue
        p = paths.get(k, Counter()).most_common(1)
        sample = " · ".join(str(x)[:26] for x in vals.get(k, [])[: a.show])
        print(f"  ✓ {k:<15} {c / N:>5.0%}  ← {p[0][0] if p else '?':<22} {sample}")
    ic = paths.get("ingredients.container", Counter()).most_common(1)
    if ic:
        print(f"  ✓ {'ingredients':<15} {'':>5}  ← {ic[0][0]:<22} 레시피당 {n_ing / N:.1f}개")
    if n_step:
        print(f"  ✓ {'steps':<15} {'':>5}  {'':<22} 레시피당 {n_step / N:.1f}단계")

    # ── 그룹명 ───────────────────────────────────────────────────
    print("\n── 🔑 재료 그룹명 (role 판별 1차 근거) ───────────────────")
    if n_ing:
        rate = n_group / n_ing
        mark = "✓" if rate > 0.8 else ("△" if rate > 0.3 else "✗")
        print(f"  {mark} group_name 보유율 {rate:>5.0%}  ({n_group}/{n_ing})")
        if rate < 0.8:
            lv, msg = IMPACT["ingredients.group_name"]
            print(f"  {lv} {msg}")

    # ── 누락 ─────────────────────────────────────────────────────
    print("\n── 누락 · 폴백 ──────────────────────────────────────────")
    rows = []
    for k in list(ad.spec["recipe"]) + ["ingredients.group_name"]:
        if k == "ingredients.group_name":
            if n_group:
                continue
            rows.append((*IMPACT[k][:1], k, 0.0, IMPACT[k][1]))
            continue
        c = hit_c.get(k, 0)
        if c >= N:
            continue
        lv, msg = IMPACT.get(k, ("⚪", ""))
        fb = ad.spec["recipe"][k].get("fallback", "null")
        tag = f" → {fb}" if str(fb).startswith("const:") else ""
        rows.append((lv, k, c / N, (msg + tag).strip()))
    order = {"🔴": 0, "🟡": 1, "⚪": 2}
    for lv, k, rate, msg in sorted(rows, key=lambda x: order[x[0]]):
        print(f"  {lv} {k:<20} {rate:>5.0%}  {msg}")
    if not rows:
        print("  없음 — 전부 매핑됨")

    # ── raw_json ────────────────────────────────────────────────
    print("\n── 🔴 raw_json 원본 보존 ────────────────────────────────")
    top_keys = Counter(k for s in samples for k in (s.keys() if isinstance(s, dict) else []))
    print(f"  최상위 키 {len(top_keys)}개: {' '.join(list(top_keys)[:12])}")
    print("  → raw_json 에 전부 보존된다. 분류 4축 파싱은 나중에 해도 되지만")
    print("    수집은 미룰 수 없다 (설계 2-3-1).")

    # ── 판정 ─────────────────────────────────────────────────────
    crit = [k for k in miss_c] + [k for lv, k, _, _ in rows if lv == "🔴"]
    warn = [k for lv, k, _, _ in rows if lv == "🟡"]
    print("\n" + "─" * 56)
    if crit:
        print(f"❌ 치명 {len(crit)}건 — 적재 불가 또는 서비스 성립 불가")
        for k in crit:
            print(f"   {k}")
        print(f"\n   → sources/{a.source}.yaml 의 paths 를 실제 키에 맞게 고치거나,")
        print("     크롤링 담당자에게 해당 필드 수집을 요청한다.")
    elif warn:
        print(f"⚠️  경고 {len(warn)}건 — 적재는 되나 품질이 떨어진다")
    else:
        print("✅ 전부 매핑됨")
    return 1 if crit else 0


if __name__ == "__main__":
    sys.exit(main())
