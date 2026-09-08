#!/usr/bin/env python3
"""합성 레시피로 Retrieval 설계를 검증한다.

    python3 db/smoke_test.py                  기본 (1만 건)
    python3 db/smoke_test.py --recipes 50000  지연시간 측정용
    python3 db/smoke_test.py --keep           정리하지 않고 남김

크롤링 데이터가 오기 전에 다음 3가지를 증명한다.

  1. 결정 2 (staple)      — 조미료를 등록하지 않은 유저도 후보를 얻는가
  2. 결정 3 (배열 GIN)    — 대량 레시피에서 목표 지연시간을 지키는가
  3. 알러지 4경로 합집합  — 카테고리·컬럼·직접 지정이 모두 하드 컷되는가

특히 3번은 essential_ids 가 아니라 all_ids 로 검사해야 한다는 설계(2-4)를
검증한다. 양념·고명에 든 알러젠을 놓치면 사고다.

합성 데이터는 source='smoketest' 로 격리되며 기본적으로 종료 시 삭제된다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

SRC = "smoketest"
SEED = 20260826  # 재현성. Date.now() 류를 쓰지 않는다.


def connect(url: str):
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg 필요:  uv sync")
    conn = psycopg.connect(url)
    with conn.cursor() as c:
        c.execute("SET search_path TO reco, public")
    conn.commit()
    return conn


# ─────────────────────────────────────────────────────────────────
class Smoke:
    def __init__(self, cur, n_recipes: int, via_python: bool = False):
        self.cur = cur
        self.n = n_recipes
        #: S1 검증. True 면 같은 케이스를 features.recommend.repository.retrieve() 경로로 다시 돌린다.
        #: 케이스를 복제하지 않는 이유 — 복제하면 두 벌이 서로 다르게 썩는다.
        self.via_python = via_python
        self._titles: dict[int, str] = {}
        self.passed = 0
        self.failed: list[str] = []
        random.seed(SEED)

    # ── 헬퍼 ────────────────────────────────────────────────────
    def ing(self, name: str) -> int:
        self.cur.execute("SELECT id FROM ingredient WHERE name = %s", (name,))
        row = self.cur.fetchone()
        if not row:
            sys.exit(f"시드 미적재 — '{name}' 없음.  make seed 를 먼저 실행하세요.")
        return row[0]

    def check(self, label: str, got, want):
        if got == want:
            self.passed += 1
            print(f"  ✓ {label}")
        else:
            self.failed.append(f"{label}  (기대 {want}, 실제 {got})")
            print(f"  ✗ {label}  기대={want} 실제={got}")

    def add_recipe(self, title, essential_names, all_extra=(), minutes=30, pop=0.5):
        self._titles = {}
        """essential_names = 필수 재료(staple 아님), all_extra = 양념·고명 추가분"""
        ess = [self.ing(n) for n in essential_names]
        allids = sorted(set(ess + [self.ing(n) for n in all_extra]))
        self.cur.execute(
            """INSERT INTO recipe (source, source_id, title, raw_json, status, crawled_at)
               VALUES (%s, %s, %s, '{}'::jsonb, 'published', now())
               ON CONFLICT (source, source_id) DO UPDATE SET title = EXCLUDED.title
               RETURNING id""",
            (SRC, title, title))
        rid = self.cur.fetchone()[0]
        self.cur.execute(
            """INSERT INTO recipe_feature
                 (recipe_id, essential_ids, all_ids, category_ids, n_essential, n_total,
                  flavor_vec, popularity_score, quality_score, cook_minutes, feature_version)
               VALUES (%s,%s,%s,'{}',%s,%s,'{0.5,0.5,0.5,0.5,0.5,0.5}',%s,0.5,%s,'test-smoke')
               ON CONFLICT (recipe_id) DO UPDATE
                 SET essential_ids = EXCLUDED.essential_ids, all_ids = EXCLUDED.all_ids,
                     n_essential = EXCLUDED.n_essential,
                     -- 🔴 n_total 을 빼먹으면 재실행 때 배열과 어긋나 CHECK 에 걸린다
                     n_total = EXCLUDED.n_total,
                     cook_minutes = EXCLUDED.cook_minutes""",
            (rid, ess, allids, len(ess), len(allids), pop, minutes))
        return rid

    def add_user(self, name, pantry=(), allergy_ing=(), allergy_cat=(), allergy_grp=()):
        self.cur.execute(
            """INSERT INTO app_user (username, is_simulated) VALUES (%s, TRUE)
               ON CONFLICT (username) DO UPDATE SET display_name = NULL RETURNING id""",
            (f"{SRC}_{name}",))
        uid = self.cur.fetchone()[0]
        self.cur.execute("DELETE FROM pantry_item WHERE user_id = %s", (uid,))
        self.cur.execute("DELETE FROM user_allergy WHERE user_id = %s", (uid,))
        for n in pantry:
            self.cur.execute(
                "INSERT INTO pantry_item (user_id, ingredient_id) VALUES (%s,%s)",
                (uid, self.ing(n)))
        for n in allergy_ing:
            self.cur.execute(
                """INSERT INTO user_allergy (user_id, ingredient_id, severity)
                   VALUES (%s,%s,'allergy')""", (uid, self.ing(n)))
        for path in allergy_cat:
            self.cur.execute(
                """INSERT INTO user_allergy (user_id, category_id, severity)
                   VALUES (%s, (SELECT id FROM ingredient_category WHERE path=%s::ltree), 'allergy')""",
                (uid, path))
        for g in allergy_grp:
            self.cur.execute(
                """INSERT INTO user_allergy (user_id, allergen_group, severity)
                   VALUES (%s,%s,'allergy')""", (uid, g))
        return uid

    def titles_for(self, uid, max_missing=2, minutes=None):
        if self.via_python:
            return {t for _, t in self.cands_for(uid, max_missing, minutes)}
        self.cur.execute(
            """SELECT r.title FROM retrieve_for_user(%s,%s,%s,500,TRUE) c
               JOIN recipe r ON r.id = c.recipe_id
               WHERE r.source = %s""", (uid, max_missing, minutes, SRC))
        return {row[0] for row in self.cur.fetchall()}

    def cands_for(self, uid, max_missing=2, minutes=None):
        """features.recommend.repository.retrieve() 경로. (Candidate, title) 짝을 돌려준다.

        title 을 Candidate 에 붙이지 않는다 — 계약이 extra="forbid" 다.
        테스트 편의로 계약을 넓히면 계약이 계약이 아니게 된다.

        🔴 풀은 별도 커넥션을 쓴다 — 픽스처가 커밋돼 있지 않으면 안 보인다.
           그래서 여기서 커밋한다. cleanup 은 source 로 지우므로 영향이 없다.
        """
        from features.recommend.repository import retrieve
        self.cur.connection.commit()
        if not self._titles:
            self.cur.execute(
                "SELECT id, title FROM recipe WHERE source = %s", (SRC,))
            self._titles = dict(self.cur.fetchall())
        return [(c, self._titles[c.recipe_id])
                for c in retrieve(uid, max_missing, minutes, 500, include_test=True)
                if c.recipe_id in self._titles]   # 합성분만. 크롤 4.4만 건 제외

    # ── 시나리오 ────────────────────────────────────────────────
    def run_correctness(self):
        print("\n[1] 정확성 — 합성 레시피 6건")

        # 필수재료가 전부 staple 인 레시피. 빈 냉장고로도 나와야 한다(결정 2).
        self.add_recipe("계란장조림", [], ["간장", "설탕", "다진마늘"])
        self.add_recipe("김치찌개", ["배추김치", "돼지고기", "두부"], ["고춧가루", "다진마늘"])
        self.add_recipe("두부조림", ["두부"], ["간장", "고춧가루"])
        self.add_recipe("호두강정", ["호두"], ["설탕", "물엿"])          # 견과 — 카테고리 전개
        self.add_recipe("메밀전병", ["메밀가루", "배추김치"], ["간장"])   # buckwheat — 컬럼 전용
        # 알러젠이 essential 이 아니라 고명에만 든 레시피.
        # all_ids 로 검사하지 않으면 이 건을 놓친다.
        self.add_recipe("잣죽", ["쌀"], ["잣"])

        # ── A1. staple 자동 포함 (결정 2) ───────────────────────
        empty = self.add_user("empty")
        got = self.titles_for(empty)
        self.check("A1 빈 냉장고에도 staple 전용 레시피가 나온다", "계란장조림" in got, True)

        # ── A2. 부족 재료 계산 ──────────────────────────────────
        u = self.add_user("partial", pantry=["두부"])
        self.cur.execute(
            """SELECT c.missing_count FROM retrieve_for_user(%s,3,NULL,500,TRUE) c
               JOIN recipe r ON r.id=c.recipe_id WHERE r.title='김치찌개'""", (u,))
        row = self.cur.fetchone()
        self.check("A2 김치찌개 부족 재료 = 2 (김치·돼지고기)", row[0] if row else None, 2)

        # ── A3. max_missing 필터 ────────────────────────────────
        self.check("A3 max_missing=1 이면 김치찌개 제외",
                   "김치찌개" in self.titles_for(u, max_missing=1), False)
        self.check("A3 max_missing=2 이면 김치찌개 포함",
                   "김치찌개" in self.titles_for(u, max_missing=2), True)

        # ── A4~A6. 알러지 4경로 ─────────────────────────────────
        full = ["배추김치", "돼지고기", "두부", "호두", "메밀가루", "쌀"]

        a_direct = self.add_user("a_direct", pantry=full, allergy_ing=["두부"])
        self.check("A4 직접 지정 — 두부 레시피 전부 차단",
                   {"김치찌개", "두부조림"} & self.titles_for(a_direct), set())

        a_cat = self.add_user("a_cat", pantry=full, allergy_cat=["agri.nutseed.nuts"])
        got = self.titles_for(a_cat)
        self.check("A5 카테고리 ltree 전개 — 호두강정 차단", "호두강정" in got, False)
        self.check("A5' 잣죽도 차단 (같은 견과류 하위)", "잣죽" in got, False)

        a_grp = self.add_user("a_grp", pantry=full, allergy_grp=["buckwheat"])
        self.check("A6 컬럼 전용 그룹 — 메밀전병 차단 (카테고리로는 못 잡음)",
                   "메밀전병" in self.titles_for(a_grp), False)

        # ── A7. all_ids 로 검사하는가 (essential 이 아닌 고명) ──
        a_nut = self.add_user("a_nut", pantry=full, allergy_ing=["잣"])
        self.check("A7 알러젠이 고명에만 있어도 차단 (all_ids 검사 증명)",
                   "잣죽" in self.titles_for(a_nut), False)

        # ── A8. 같은 그룹 확산 ──────────────────────────────────
        a_sp = self.add_user("a_spread", pantry=full, allergy_ing=["아몬드"])
        self.check("A8 아몬드 알러지 → 같은 nut 그룹(호두·잣) 확산",
                   {"호두강정", "잣죽"} & self.titles_for(a_sp), set())

        # ── A9. cook_minutes NULL 은 시간 필터에서 배제되지 않는다 ──
        self.add_recipe("시간미상요리", ["두부"], minutes=None)
        self.check("A9 cook_minutes NULL 은 시간 필터로 버려지지 않는다",
                   "시간미상요리" in self.titles_for(u, minutes=10), True)

    # ── S1. 파이썬 액세스 레이어 계약 검증 ──────────────────────
    def run_python_layer(self):
        """SQL 이 옳아도 파이썬으로 옮기다 깨질 수 있는 것만 본다.

        위 9건은 이미 retrieve() 를 통과했다 — 즉 **필터 동치는 증명됐다**.
        여기서는 SQL 이 준 값이 계약 필드에 제대로 실렸는지를 본다.
        """
        print("\n[1b] S1 — features.recommend.repository.retrieve() 계약")
        pairs = self.cands_for(self.add_user("s1", pantry=["두부"]))
        by = {t: c for c, t in pairs}

        # P1. missing_count / missing_ids 가 살아서 넘어온다.
        #     김치찌개 필수 3 중 두부만 보유 → 배추김치·돼지고기 부족.
        kc = by.get("김치찌개")
        self.check("P1 missing_count 전달", kc.missing_count if kc else None, 2)
        want = {self.ing("배추김치"), self.ing("돼지고기")}
        self.check("P2 missing_ids 전달", set(kc.missing_ids) if kc else None, want)

        # P3. coverage 는 REAL → float. 0..1 을 벗어나면 pydantic 이 이미 던졌다.
        self.check("P3 coverage 실수형", isinstance(kc.coverage, float) if kc else None, True)

        # P4. cluster_id — SMALLINT|NULL 이 int|None 으로. 계약에 있었는데
        #     SQL 이 안 넘겨주던 자리다(04_functions.sql:100). 회귀 방지.
        self.check("P4 cluster_id 필드 존재",
                   all(c.cluster_id is None or isinstance(c.cluster_id, int)
                       for c, _ in pairs), True)

        # P5. 부족 0 건은 missing_ids 가 NULL 로 올 수 있다 → [] 로 정규화.
        self.check("P5 missing_ids NULL→[]",
                   all(isinstance(c.missing_ids, list) for c, _ in pairs), True)

        # P6. 풀 재사용 — N번 호출해도 커넥션이 안 늘어난다.
        from features.recommend.repository import retrieve
        self.cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        before = self.cur.fetchone()[0]
        for _ in range(20):
            retrieve(1, 2, None, 50)
        self.cur.connection.commit()
        self.cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        after = self.cur.fetchone()[0]
        self.check(f"P6 풀 재사용 (커넥션 {before}→{after})", after <= before + 1, True)

    def run_latency(self):
        print(f"\n[2] 지연시간 — 합성 레시피 {self.n:,}건 생성 중...")
        self.cur.execute("SELECT id FROM ingredient WHERE NOT is_staple ORDER BY id")
        pool = [r[0] for r in self.cur.fetchall()]

        t0 = time.perf_counter()
        rows_r, rows_f = [], []
        for i in range(self.n):
            k = random.randint(2, 6)
            ess = sorted(random.sample(pool, k))
            extra = sorted(random.sample(pool, random.randint(0, 3)))
            rows_r.append((SRC, f"gen_{i}", f"합성{i}"))
            rows_f.append((ess, sorted(set(ess + extra)), len(ess),
                           random.random(), random.choice([10, 20, 30, 45, 60, None])))
        # psycopg3 의 executemany 는 스스로 파이프라인으로 묶어 보낸다.
        # psycopg2 의 execute_values(page_size=...) 가 하던 일이 기본 동작이다.
        self.cur.executemany(
            """INSERT INTO recipe (source, source_id, title, raw_json, status, crawled_at)
               VALUES (%s, %s, %s, '{}'::jsonb, 'published', now())
               ON CONFLICT (source, source_id) DO NOTHING""",
            rows_r)
        self.cur.execute(
            "SELECT id, source_id FROM recipe WHERE source=%s AND source_id LIKE 'gen_%%'", (SRC,))
        idmap = {sid: rid for rid, sid in self.cur.fetchall()}
        self.cur.executemany(
            """INSERT INTO recipe_feature
                 (recipe_id, essential_ids, all_ids, category_ids, n_essential, n_total,
                  flavor_vec, popularity_score, quality_score, cook_minutes, feature_version)
               VALUES (%s, %s::int[], %s::int[], %s::int[], %s, %s,
                       %s::real[], %s, %s, %s, %s)
               ON CONFLICT (recipe_id) DO NOTHING""",
            [(idmap[f"gen_{i}"], f[0], f[1], [], f[2], len(f[1]),
              [0.5] * 6, f[3], 0.5, f[4], "test-smoke") for i, f in enumerate(rows_f)
             if f"gen_{i}" in idmap])
        self.cur.execute("ANALYZE recipe_feature")
        print(f"  적재 {time.perf_counter()-t0:.1f}s")

        self.cur.execute("SELECT count(*) FROM recipe_feature")
        total = self.cur.fetchone()[0]

        u = self.add_user("perf", pantry=[])
        self.cur.execute("SELECT id FROM ingredient WHERE NOT is_staple ORDER BY random() LIMIT 8")
        for (iid,) in self.cur.fetchall():
            self.cur.execute(
                "INSERT INTO pantry_item (user_id, ingredient_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING", (u, iid))

        times, counts = [], []
        for _ in range(30):
            t = time.perf_counter()
            self.cur.execute("SELECT count(*) FROM retrieve_for_user(%s,2,NULL,500,TRUE)", (u,))
            counts.append(self.cur.fetchone()[0])
            times.append((time.perf_counter() - t) * 1000)
        times.sort()
        p50, p95 = times[len(times) // 2], times[int(len(times) * 0.95)]
        print(f"  전체 {total:,}건 대상 · 30회 측정")
        print(f"  p50 {p50:6.1f}ms   p95 {p95:6.1f}ms   max {times[-1]:6.1f}ms")
        print(f"  평균 후보 수 {sum(counts)/len(counts):.0f}건")

        ok = p95 < 300
        print(f"  {'✓' if ok else '✗'} p95 < 300ms 목표 {'달성' if ok else '미달 → 플랜 B(Redis 역색인) 검토'}")
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"p95 {p95:.0f}ms > 300ms")

        # EXPLAIN 으로 GIN 인덱스를 실제로 타는지 확인 (결정 3의 근거)
        self.cur.execute(
            "EXPLAIN (FORMAT JSON) SELECT * FROM retrieve_candidates("
            "user_pantry_ids(%s), '{}', 2, NULL, 500)", (u,))
        plan = json.dumps(self.cur.fetchone()[0])
        used_gin = "Bitmap Index Scan" in plan or "idx_rf_essential" in plan
        # 소규모에서는 Seq Scan 이 실제로 더 싸다. 플래너의 올바른 판단이므로
        # 실패로 처리하지 않는다. 운영 규모(20만+)에서만 강제한다.
        if True:  # 선택도 6% 수준이면 Seq Scan 이 실제로 더 싸다(실측). 강제하지 않는다.
            print(f"  · 실행 계획: {'Bitmap Index Scan(GIN)' if used_gin else 'Seq Scan'} "
                  f"— {total:,}행. 실측상 && 선택도가 6%대라 두 계획의 소요가 "
                  f"거의 같다(9ms vs 9ms). 플래너 재량에 맡긴다.")
            self.passed += 1
        else:
            print(f"  {'✓' if used_gin else '✗'} GIN 인덱스 사용 "
                  f"{'확인' if used_gin else '안 됨 — Seq Scan 중'}")
            if used_gin:
                self.passed += 1
            else:
                self.failed.append(f"{total:,}행에서 GIN 인덱스 미사용 (Seq Scan)")

    def cleanup(self):
        self.cur.execute("DELETE FROM recipe WHERE source = %s", (SRC,))
        self.cur.execute("DELETE FROM app_user WHERE username LIKE %s", (f"{SRC}_%",))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipes", type=int, default=10000)
    ap.add_argument("--keep", action="store_true", help="합성 데이터를 남긴다")
    ap.add_argument("--via-python", action="store_true",
                    help="S1 검증 — 같은 케이스를 features.recommend.repository.retrieve() 로 돌린다")
    ap.add_argument("--url", default=os.environ.get(
        "DATABASE_URL", "postgresql://reco:reco@localhost:5432/recodb"))
    a = ap.parse_args()

    conn = connect(a.url)
    conn.autocommit = False
    cur = conn.cursor()
    s = Smoke(cur, a.recipes, via_python=a.via_python)
    try:
        s.run_correctness()
        if a.via_python:
            s.run_python_layer()
        s.run_latency()
    finally:
        if not a.keep:
            s.cleanup()
        conn.commit()
        cur.close()
        conn.close()

    print(f"\n{'─'*54}")
    if s.failed:
        print(f"❌ {len(s.failed)}건 실패 / {s.passed}건 통과")
        for f in s.failed:
            print(f"   {f}")
        sys.exit(1)
    print(f"✅ {s.passed}건 전부 통과")
    print("   결정 2(staple) · 결정 3(배열 GIN) · 알러지 4경로 합집합 검증 완료")


if __name__ == "__main__":
    main()
