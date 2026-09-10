"""배치가 쓰는 SQL. 정규화·피처·맛·인기도·실행기록이 여기 있습니다.

    from features.recommend.repository_ingest import load_recipe_ids

`repository.py` 에서 갈라져 나왔습니다 (09-10). 한 파일이 898줄이 되어 02 의
5.1(500줄 초과 시 반드시 분리)에 걸렸습니다.

## 왜 repository 계열로 남기나

03 의 5절이 **SQL 은 repository 밖으로 나가지 않는다**고 정합니다. 그래서
`ingest/` 로 옮기지 않고 이름을 나눴습니다.

## 어디서 갈랐나

    repository.py          조회·로그 — 요청이 지나가는 서빙 경로
    repository_ingest.py   배치 — 사람이 명령으로 돌리는 경로

둘은 수명이 다릅니다. 서빙 SQL 은 요청마다 돌고 지연시간이 계약이지만, 배치
SQL 은 하루에 몇 번 돌고 몇 분이 걸려도 됩니다. 같은 파일에 두면 "이 쿼리가
느려도 되나" 를 매번 다시 판단해야 합니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from infra.db import cursor

# ─────────────────────────────────────────────────────────────────
# 재료 사전 — ingest 의 P3 매칭이 운영 경로에서 쓴다
#
# 주의: SQL 이 여기 있는 이유. 03 의 5절이 "SQL 은 repository.py 밖으로 나가지
#    않는다" 고 정한다. 09-05 까지는 이 두 쿼리가 ingest/match.py 안에 있었다.
#    ingest 는 스테이지이고, 스테이지는 DB 접근을 여기에 위임한다.
# ─────────────────────────────────────────────────────────────────
#: 주의: 컬럼을 `i.` 로 한정한다. ingredient 와 ingredient_category 양쪽에
#:    id·name 이 있어 한정하지 않으면 AmbiguousColumn 으로 죽는다.
_DICT_SQL = """
SELECT i.id, i.name, i.is_staple, i.is_seasoning, c.path::text
FROM ingredient i LEFT JOIN ingredient_category c ON c.id = i.category_id
"""

_ALIAS_SQL = "SELECT alias, ingredient_id FROM ingredient_alias"


def load_dictionary_rows() -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """(재료 행, 별칭 행). 검수로 쌓인 `source='manual'` alias 까지 포함된다.

    한 커서에서 두 번 조회한다 — 사전 로드는 배치 시작에 1회뿐이라
    왕복 2회가 문제되지 않고, 조인하면 재료 하나가 별칭 수만큼 중복된다.
    """
    with cursor() as cur:
        cur.execute(_DICT_SQL)
        ingredients = cur.fetchall()
        cur.execute(_ALIAS_SQL)
        aliases = cur.fetchall()
    return ingredients, aliases


# ─────────────────────────────────────────────────────────────────
# 정규화 배치 — ingest/batch.py 가 읽고 쓴다 (A-2)
# ─────────────────────────────────────────────────────────────────
_RECIPE_IDS_SQL = "SELECT id FROM recipe ORDER BY id"

#: 주의: recipe_id 로 좁힌다. 배치가 레시피 단위로 끊어 돌기 때문이다.
#:    한 레시피의 원문이 두 청크로 갈리면 역할 우선순위 병합이 반쪽만 보고
#:    돌아, 같은 재료가 두 역할로 나뉜 경우를 파이썬이 못 접고 DB 의
#:    ON CONFLICT 에 떠넘기게 된다 — 그러면 결과가 실행마다 달라진다.
_RAW_SQL = """
SELECT recipe_id, id, position, raw_text
FROM recipe_ingredient_raw
WHERE recipe_id = ANY(%s)
ORDER BY recipe_id, position
"""

#: 주의: DELETE 가 아니라 TRUNCATE 다. 45만 행을 DELETE 하면 死행이 남아
#:    VACUUM 전까지 순차 스캔이 계속 그것을 읽는다. 이 테이블은 DDL 주석대로
#:    "재생성 가능" 이라 통째로 버려도 잃는 것이 없다.
_TRUNCATE_SQL = "TRUNCATE recipe_ingredient"

_INS_SQL = """
INSERT INTO recipe_ingredient
    (recipe_id, ingredient_id, raw_id, quantity, unit, quantity_g,
     role, match_method, match_score)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (recipe_id, ingredient_id) DO NOTHING
"""


def load_recipe_ids(limit: int | None = None) -> list[int]:
    """정규화 대상 레시피 id. id 순으로 준다 — 부분 실행을 재현 가능하게 한다."""
    sql = _RECIPE_IDS_SQL + (" LIMIT %s" if limit else "")
    with cursor() as cur:
        cur.execute(sql, (limit,) if limit else None)
        return [r[0] for r in cur.fetchall()]


def load_raw_ingredients(recipe_ids: Sequence[int]) -> list[tuple[int, int, int, str]]:
    """(recipe_id, raw_id, position, raw_text). position 순으로 준다.

    청크 하나를 한 번의 왕복으로 읽는다. 레시피마다 조회하면 2,000 왕복이 된다.
    """
    with cursor() as cur:
        cur.execute(_RAW_SQL, (list(recipe_ids),))
        return cur.fetchall()


def truncate_recipe_ingredient() -> None:
    """정규화 결과를 비운다. 원문(`recipe_ingredient_raw`)은 건드리지 않는다."""
    with cursor(commit=True) as cur:
        cur.execute(_TRUNCATE_SQL)


def insert_recipe_ingredients(rows: Sequence[tuple[Any, ...]]) -> int:
    """넣은 행 수를 돌려준다.

    주의: 반환값을 반드시 쓴다. psycopg3 의 executemany 는 rowcount 에 전체
       합을 담으므로 청크가 나뉘어도 수가 맞다. psycopg2 시절 `execute_values` 는
       page_size 를 안 주면 마지막 청크만 보고해, 로더가 62만 건을 넣고도
       29,035 만 넣은 줄 알았던 전례가 있다.
    """
    if not rows:
        return 0
    with cursor(commit=True) as cur:
        cur.executemany(_INS_SQL, rows)
        return max(cur.rowcount, 0)


# ─────────────────────────────────────────────────────────────────
# recipe_feature 빌더 — ingest/feature_build.py 가 쓴다 (A-4)
# ─────────────────────────────────────────────────────────────────
#: 집계와 삽입을 한 문장으로 끝낸다. 46,353행을 파이썬으로 왕복시키면 배열까지
#: 실어 나르게 되는데, 계산이 전부 집합 연산이라 DB 안에서 끝내는 편이 싸다.
#:
#: 주의: is_staple 을 SQL 에서 한 번 더 건다. P4 가 이미 staple 을 seasoning 으로
#:    보내지만, 39종 중 15종(쌀·밀가루·물·얼음 등)은 is_staple 로만 걸린다.
#:    검수 alias 나 manual override 가 role 을 바꾸면 물·쌀이 essential_ids 에
#:    들어가고, 그러면 "물이 없어서 못 만드는 레시피" 가 된다. 두 겹으로 건다.
#:
#: 주의: LEFT JOIN 이다. 재료가 하나도 안 붙은 레시피도 행을 만든다 —
#:    n_total=0 으로 남겨야 조회의 ⓪ 관문이 그것을 걸러낸다. 빼 버리면
#:    "왜 이 레시피가 없지" 를 추적할 근거가 사라진다.
#:
#: 주의: n_unmatched 는 여기서 안 건드린다. 재삽입할 때마다 0 으로 덮으면
#:    배치가 넣어 둔 값이 조용히 사라진다. 갱신은 _UNMATCHED_SQL 이 따로 한다.
_FEATURE_SQL = """
INSERT INTO recipe_feature
    (recipe_id, essential_ids, all_ids, category_ids,
     n_essential, n_total, flavor_vec, cook_minutes, difficulty, feature_version)
SELECT r.id,
       ess, alls, cats,
       cardinality(ess), cardinality(alls),
       ARRAY[0,0,0,0,0,0]::REAL[],
       r.cook_minutes, r.difficulty, %s
FROM recipe r
CROSS JOIN LATERAL (
    SELECT COALESCE(array_agg(DISTINCT ri.ingredient_id)
                    FILTER (WHERE ri.role = 'essential' AND NOT i.is_staple),
                    '{}')::INTEGER[] AS ess,
           COALESCE(array_agg(DISTINCT ri.ingredient_id), '{}')::INTEGER[] AS alls,
           COALESCE(array_agg(DISTINCT i.category_id)
                    FILTER (WHERE i.category_id IS NOT NULL),
                    '{}')::INTEGER[] AS cats
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.id = ri.ingredient_id
    WHERE ri.recipe_id = r.id
) agg
ON CONFLICT (recipe_id) DO UPDATE SET
    essential_ids  = EXCLUDED.essential_ids,
    all_ids        = EXCLUDED.all_ids,
    category_ids   = EXCLUDED.category_ids,
    n_essential    = EXCLUDED.n_essential,
    n_total        = EXCLUDED.n_total,
    cook_minutes   = EXCLUDED.cook_minutes,
    difficulty     = EXCLUDED.difficulty,
    feature_version = EXCLUDED.feature_version,
    updated_at     = now()
"""

#: 배치가 센 레시피별 미매칭 수를 한 문장으로 반영한다.
#:
#: 주의: 이번에 처리한 레시피만 0 으로 되돌린다. 범위를 안 주고 전 행을 비우면
#:    `--limit 2000` 부분 실행 한 번이 나머지 44,353건의 n_unmatched 를 지운다.
#:    그 값은 배치 메모리에만 있었으므로 전량을 다시 돌리기 전에는 복구되지
#:    않고, D-10 은 미매칭 0 을 정상으로 읽어 조용히 꺼진다.
#:
#: 0 으로 되돌리는 것 자체는 필요하다. 사전이 좋아져 미매칭이 사라진 레시피가
#: 옛 값을 이고 있으면 고친 것이 안 고쳐진 것처럼 보인다.
_RESET_UNMATCHED_SQL = """
UPDATE recipe_feature SET n_unmatched = 0
WHERE  recipe_id = ANY(%s::BIGINT[]) AND n_unmatched <> 0
"""

_UNMATCHED_SQL = """
UPDATE recipe_feature rf
SET    n_unmatched = u.n
FROM   (SELECT unnest(%s::BIGINT[]) AS rid, unnest(%s::INT[]) AS n) u
WHERE  rf.recipe_id = u.rid AND rf.n_unmatched IS DISTINCT FROM u.n
"""

#: 정규화가 끝났음을 표시한다.
#:
#: 주의: 'raw' 만 건드린다. 출발 상태를 안 보면 사람이 검수해 'published' 로
#:    올린 레시피가 배치를 다시 돌릴 때마다 'normalized' 로 강등되고, 수동으로
#:    'rejected' 를 찍어 둔 것도 되살아난다. 이 빌더는 자기가 만든 상태만 쓴다.
#:
#: 주의: D-10 실패를 여기에 'rejected' 로 쓰지 않는다. D-14 가 거르는 곳을
#:    조회 함수로 정했다 — recipe_feature 46,353행 계약을 살리고, C 가 "몇 건을
#:    왜 버렸나" 를 셀 수 있게 하기 위해서다. status 로 거르면 조회가 recipe 를
#:    조인하지 않으므로 아무것도 걸리지 않는다.
_STATUS_SQL = """
UPDATE recipe r
SET    status = 'normalized'
WHERE  r.status = 'raw'
   AND EXISTS (SELECT 1 FROM recipe_feature rf
               WHERE rf.recipe_id = r.id AND rf.n_total > 0)
"""


def rebuild_recipe_features(version: str) -> int:
    """recipe_feature 를 recipe_ingredient 로부터 다시 만든다. 멱등이다."""
    with cursor(commit=True) as cur:
        cur.execute(_FEATURE_SQL, (version,))
        return max(cur.rowcount, 0)


def set_unmatched_counts(counts: Mapping[int, int], scope: Sequence[int]) -> int:
    """레시피별 P3 미매칭 수를 반영한다.

    Args:
        counts: 미매칭이 있는 레시피만. 없는 레시피는 키가 없다.
        scope: 이번 배치가 실제로 처리한 레시피 전체. 이 범위 안에서만 0 으로
            되돌린다 — 범위 밖은 손대지 않아야 부분 실행이 남의 값을 지우지 않는다.

    한 트랜잭션에서 되돌리고 다시 채운다. 둘로 나뉘면 그 사이에 조회가 들어와
    미매칭 0 을 보고 D-10 이 통과시킨다.
    """
    ids = list(scope)
    with cursor(commit=True) as cur:
        cur.execute(_RESET_UNMATCHED_SQL, (ids,))
        n = max(cur.rowcount, 0)
        if counts:
            rids = list(counts.keys())
            cur.execute(_UNMATCHED_SQL, (rids, [counts[r] for r in rids]))
            n += max(cur.rowcount, 0)
        return n


def mark_recipe_status() -> int:
    """피처가 붙은 레시피를 'raw' 에서 'normalized' 로 올린다."""
    with cursor(commit=True) as cur:
        cur.execute(_STATUS_SQL)
        return max(cur.rowcount, 0)


_QUALITY_SQL = """
SELECT rf.recipe_id, rf.n_essential, rf.n_total, rf.n_unmatched
FROM recipe_feature rf
"""


def load_feature_quality() -> list[tuple[int, int, int, int]]:
    """(recipe_id, n_essential, n_total, n_unmatched). D-10 판정과 품질 보고용."""
    with cursor() as cur:
        cur.execute(_QUALITY_SQL)
        return cur.fetchall()


# ─────────────────────────────────────────────────────────────────
# flavor_vec 빌더 — ingest/flavor_build.py 가 쓴다 (A-5)
# ─────────────────────────────────────────────────────────────────
#: 강도 계산에 필요한 것을 한 번에 가져온다.
#:
#: 주의: n_total 은 *원문* 행 수다. 매칭된 행 수가 아니다. intensity 의 위치
#:    보정이 "재료 목록의 앞 1/3" 을 보는데, 못 붙은 재료도 목록에는 있었다.
#:    매칭분으로 세면 미매칭이 많은 레시피일수록 앞쪽 판정이 헐거워진다.
_FLAVOR_SRC_SQL = """
SELECT ri.recipe_id, r.title, i.name, c.path::text, ri.role, ri.unit,
       COALESCE(rr.position, 0), nt.n
FROM recipe_ingredient ri
JOIN recipe r      ON r.id = ri.recipe_id
JOIN ingredient i  ON i.id = ri.ingredient_id
LEFT JOIN ingredient_category c ON c.id = i.category_id
LEFT JOIN recipe_ingredient_raw rr ON rr.id = ri.raw_id
JOIN LATERAL (SELECT count(*) AS n FROM recipe_ingredient_raw x
              WHERE x.recipe_id = ri.recipe_id) nt ON TRUE
WHERE ri.recipe_id = ANY(%s)
ORDER BY ri.recipe_id, rr.position
"""

_FLAVOR_UPD_SQL = """
UPDATE recipe_feature rf
SET    flavor_vec = u.v
FROM   (SELECT unnest(%s::BIGINT[]) AS rid, unnest(%s::REAL[][]) AS v) u
WHERE  rf.recipe_id = u.rid
"""

_FLAVOR_ONE_SQL = "UPDATE recipe_feature SET flavor_vec = %s WHERE recipe_id = %s"

_MU_SQL = """
INSERT INTO feature_stats (flavor_mu, n_recipes, note)
VALUES (%s, %s, %s)
RETURNING stats_version
"""

_FLAVOR_ALL_SQL = "SELECT recipe_id, flavor_vec FROM recipe_feature"


def load_flavor_source(recipe_ids: Sequence[int]) -> list[tuple[Any, ...]]:
    """(recipe_id, title, 재료명, 분류경로, role, unit, position, 원문행수)."""
    with cursor() as cur:
        cur.execute(_FLAVOR_SRC_SQL, (list(recipe_ids),))
        return cur.fetchall()


def set_flavor_vectors(vectors: Mapping[int, Sequence[float]]) -> int:
    """레시피별 6축을 쓴다.

    주의: 원값을 그대로 넣는다. 코퍼스 평균을 미리 빼면 CHECK(길이 6)는 통과하고
       값도 그럴듯하지만, 유저 taste_vec 은 원좌표계라 f_taste 가 좌표계가
       어긋난 채 조용히 돈다. 게다가 스코어러가 규약대로 한 번 더 빼면 두 번
       빠진다. 빼기는 읽는 쪽이 양쪽에 같은 μ 로 한다 (A-5 규약).
    """
    if not vectors:
        return 0
    with cursor(commit=True) as cur:
        n = 0
        for rid, vec in vectors.items():
            cur.execute(_FLAVOR_ONE_SQL, (list(vec), rid))
            n += max(cur.rowcount, 0)
        return n


def load_all_flavor_vectors() -> list[tuple[int, list[float]]]:
    """μ 계산과 검증기가 읽는다. 46,353행이라 한 번에 올려도 된다."""
    with cursor() as cur:
        cur.execute(_FLAVOR_ALL_SQL)
        return cur.fetchall()


def insert_feature_stats(mu: Sequence[float], n_recipes: int, note: str) -> int:
    """새 stats_version 을 만든다. 기존 행은 지우지 않는다.

    주의: 덮어쓰지 않는다. recommendation_log 가 stats_version 을 싣기 때문에,
       옛 μ 를 지우면 과거 요청의 점수를 재현할 수 없다 — 소급이 안 된다.
    """
    with cursor(commit=True) as cur:
        cur.execute(_MU_SQL, (list(mu), n_recipes, note))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("feature_stats INSERT 가 stats_version 을 안 돌려줬습니다")
        return int(row[0])


_FLAVOR_LABEL_SQL = """
SELECT rf.recipe_id, r.title, rf.flavor_vec
FROM recipe_feature rf JOIN recipe r ON r.id = rf.recipe_id
WHERE rf.n_total > 0
"""


def load_flavor_with_titles() -> list[tuple[int, str, list[float]]]:
    """검증기(A-5)가 제목 라벨로 판별력을 잴 때 읽는다."""
    with cursor() as cur:
        cur.execute(_FLAVOR_LABEL_SQL)
        return cur.fetchall()


_LATEST_MU_SQL = """
SELECT stats_version, flavor_mu, n_recipes
FROM feature_stats ORDER BY stats_version DESC LIMIT 1
"""


def load_latest_mu() -> tuple[int, list[float], int] | None:
    """가장 최근 stats_version 의 μ. 없으면 None."""
    with cursor() as cur:
        cur.execute(_LATEST_MU_SQL)
        return cur.fetchone()


# ─────────────────────────────────────────────────────────────────
# popularity_score — ingest/popularity_build.py 가 쓴다 (A-6, D-9)
# ─────────────────────────────────────────────────────────────────
#: log1p 후 백분위 순위. min-max 를 쓰면 상위 1%(p99 181 vs max 1,079)가 나머지를
#: 0.02 근처로 눌러 popularity 가 사실상 이진 피처가 된다.
#:
#: 주의: percent_rank 가 아니라 row_number 다. 동점 블록이 8,470건이라
#:    percent_rank 는 3분위가 통째로 비고, 그러면 후보 500컷이 비결정적이 되어
#:    propensity 재현이 깨진다 (D-9). id 로 동점을 깨서 실행마다 같은 값이 나오게 한다.
#:
#: 주의: review_count 는 recipe 테이블의 원문 개수(627,605)를 쓴다. recipe_review
#:    실적재(624,422)를 쓰면 후기 파싱 규칙이 바뀔 때마다 인기도가 흔들린다.
#:    값이 둘 다 그럴듯해서 틀린 것을 알아채지 못한다.
_POPULARITY_SQL = """
WITH ranked AS (
    SELECT r.id,
           row_number() OVER (ORDER BY ln(1 + r.review_count), r.id)::REAL
             / count(*) OVER () AS score
    FROM recipe r
)
UPDATE recipe_feature rf
SET    popularity_score = ranked.score, updated_at = now()
FROM   ranked
WHERE  rf.recipe_id = ranked.id
"""

#: 평점 계열이 전부 비어 있어 만들 재료가 없다. 후기 개수로 만들면 popularity 와
#: 상관계수 1.0 인 가짜 축이 하나 늘 뿐이다 (A-6). 0 으로 두고 이유를 남긴다.
_QUALITY_ZERO_SQL = "UPDATE recipe_feature SET quality_score = 0 WHERE quality_score <> 0"

_QUALITY_COMMENT_SQL = """
COMMENT ON COLUMN recipe_feature.quality_score IS
'항상 0. 크롤에 평점이 없다 — rating_avg NOT NULL 0건 · rating_count>0 0건 ·
view_count>0 0건 (46,353건 전수, 2026-09-10). 후기 개수로 만들면 popularity 와
상관 1.0 인 가짜 축이 되므로 만들지 않는다. f_quality 가중치도 0.0 이다.'
"""

_POPULARITY_STATS_SQL = """
SELECT min(rf.popularity_score), max(rf.popularity_score),
       corr(rf.popularity_score, ln(1 + r.review_count)),
       count(*) FILTER (WHERE rf.quality_score <> 0)
FROM recipe_feature rf JOIN recipe r ON r.id = rf.recipe_id
"""

#: 주의: 최댓값 1.0 을 그대로 넣으면 width_bucket 이 11 을 돌려준다 — 상한
#:    이상은 n+1 로 보내기 때문이다. 순위 1위 한 건이 11분위로 튀어 균등 검사가
#:    실패한다. 분포는 멀쩡한데 검사만 빨개지는 자리라 여기서 한 칸 당긴다.
_POPULARITY_DECILE_SQL = """
SELECT width_bucket(LEAST(popularity_score, 1 - 1e-6), 0, 1, 10) AS d, count(*)
FROM recipe_feature GROUP BY 1 ORDER BY 1
"""


def rebuild_popularity() -> int:
    """popularity_score 를 다시 만들고 quality_score 를 0 으로 둔다. 멱등이다."""
    with cursor(commit=True) as cur:
        cur.execute(_POPULARITY_SQL)
        n = max(cur.rowcount, 0)
        cur.execute(_QUALITY_ZERO_SQL)
        cur.execute(_QUALITY_COMMENT_SQL)
        return n


def load_popularity_stats() -> tuple[float, float, float, int]:
    """(min, max, 로그값과의 상관, quality<>0 행 수). A-6 완료 기준이 읽는다."""
    with cursor() as cur:
        cur.execute(_POPULARITY_STATS_SQL)
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("recipe_feature 가 비어 있습니다")
        return (float(row[0]), float(row[1]), float(row[2]), int(row[3]))


def load_popularity_deciles() -> list[tuple[int, int]]:
    """10분위 히스토그램. 백분위 순위라 균등해야 한다."""
    with cursor() as cur:
        cur.execute(_POPULARITY_DECILE_SQL)
        return cur.fetchall()


# ─────────────────────────────────────────────────────────────────
# batch_run — ingest/run_log.py 가 쓴다 (A-7, D-1)
#
# 주의: data_quality_snapshot 은 쓰지 않는다. D-1 이 C 전담으로 정했다.
#    같은 컬럼에 두 정의(A "파싱 언급" vs C "원문 행수")가 섞이면 시계열이
#    조용히 꺾인다 — 값은 둘 다 그럴듯하고 에러도 안 난다.
# ─────────────────────────────────────────────────────────────────
_RUN_START_SQL = """
INSERT INTO batch_run (job_name, status, params)
VALUES (%s, 'running', %s)
RETURNING id
"""

_RUN_FINISH_SQL = """
UPDATE batch_run
SET    status = %s, finished_at = now(),
       input_count = %s, output_count = %s, error_msg = %s, params = %s
WHERE  id = %s
"""

_RUN_RECENT_SQL = """
SELECT id, job_name, status, input_count, output_count,
       finished_at IS NOT NULL, error_msg
FROM batch_run ORDER BY id DESC LIMIT %s
"""


def start_batch_run(job_name: str, params: str | None = None) -> int:
    """'running' 행을 열고 id 를 돌려준다."""
    with cursor(commit=True) as cur:
        cur.execute(_RUN_START_SQL, (job_name, params))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("batch_run INSERT 가 id 를 안 돌려줬습니다")
        return int(row[0])


def finish_batch_run(
    run_id: int,
    status: str,
    input_count: int | None = None,
    output_count: int | None = None,
    error_msg: str | None = None,
    params: str | None = None,
) -> None:
    """'running' 행을 닫는다. 실패도 반드시 기록한다."""
    with cursor(commit=True) as cur:
        cur.execute(
            _RUN_FINISH_SQL,
            (status, input_count, output_count, error_msg, params, run_id),
        )


def load_recent_batch_runs(limit: int = 10) -> list[tuple[Any, ...]]:
    """(id, job_name, status, input, output, 닫혔나, error_msg)."""
    with cursor() as cur:
        cur.execute(_RUN_RECENT_SQL, (limit,))
        return cur.fetchall()


# ─────────────────────────────────────────────────────────────────
# 회귀 게이트 — ingest/feature_test.py 가 읽는다 (A-8)
# ─────────────────────────────────────────────────────────────────
#: 체크 8개 중 SQL 로 재는 7개를 한 번에 가져온다. 게이트는 자주 돌려야 의미가
#: 있어서, 왕복을 7번 하지 않고 한 문장으로 끝낸다.
_GATE_SQL = """
SELECT
  (SELECT count(*) FROM recipe)                                          AS n_recipe,
  (SELECT count(*) FROM recipe_feature)                                  AS n_feature,
  (SELECT count(*) FROM recipe_feature rf WHERE EXISTS (
       SELECT 1 FROM ingredient i
       WHERE i.is_staple AND i.id = ANY(rf.essential_ids)))              AS staple_in_ess,
  (SELECT count(*) FROM recipe_feature
       WHERE n_total <> cardinality(all_ids))                            AS n_total_mismatch,
  (SELECT count(*) FROM recipe_feature
       WHERE n_essential <> cardinality(essential_ids))                  AS n_ess_mismatch,
  (SELECT count(*) FROM recipe_feature
       WHERE NOT (essential_ids <@ all_ids))                             AS ess_not_subset,
  (SELECT count(*) FROM recipe_feature
       WHERE array_length(flavor_vec, 1) IS DISTINCT FROM 6)             AS bad_flavor_len,
  (SELECT count(*) FROM recipe_feature
       WHERE popularity_score < 0 OR popularity_score > 1)               AS pop_out_of_range,
  (SELECT count(*) FROM recipe_feature WHERE feature_version LIKE 'test-%')
                                                                          AS test_rows,
  (SELECT count(*) FROM recipe_feature WHERE quality_score <> 0)         AS quality_nonzero
"""

_GATE_STATS_SQL = """
SELECT stats_version, array_length(flavor_mu, 1), n_recipes
FROM feature_stats ORDER BY stats_version DESC LIMIT 1
"""


def load_gate_counts() -> tuple[int, ...]:
    """회귀 게이트가 보는 수치 10개를 한 번에."""
    with cursor() as cur:
        cur.execute(_GATE_SQL)
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("게이트 집계가 빈 결과를 돌려줬습니다")
        return tuple(int(x) for x in row)


def load_gate_stats() -> tuple[int, int | None, int] | None:
    """최신 feature_stats 한 행. 없으면 None."""
    with cursor() as cur:
        cur.execute(_GATE_STATS_SQL)
        return cur.fetchone()


# ─────────────────────────────────────────────────────────────────
# ingredient.freq_count — ingest/freq_build.py 가 쓴다 (A-14)
# ─────────────────────────────────────────────────────────────────
#: 재료가 몇 개의 *레시피*에 나오는가. 언급 수가 아니라 레시피 수다.
#:
#: 주의: count(DISTINCT recipe_id) 다. recipe_ingredient 의 기본키가
#:    (recipe_id, ingredient_id) 라 한 레시피에 같은 재료가 두 번 들어갈 수
#:    없으므로 지금은 count(*) 와 결과가 같다. 그래도 DISTINCT 를 쓴다 —
#:    기본키가 바뀌면 조용히 부풀기 때문이다.
#:
#: 주의: 안 나오는 재료를 0 으로 되돌린다. 사전이 좋아져 어떤 재료가 더는
#:    안 잡히면 옛 값이 남아 IDF 분모를 잘못 만든다. 흔한 재료로 오해된
#:    희귀 재료는 f_cooccur 에서 가중치를 못 받는다.
_FREQ_SQL = """
UPDATE ingredient i
SET    freq_count = COALESCE(c.n, 0)
FROM   (SELECT id FROM ingredient) all_ing
LEFT JOIN (SELECT ingredient_id, count(DISTINCT recipe_id) AS n
           FROM recipe_ingredient GROUP BY 1) c ON c.ingredient_id = all_ing.id
WHERE  i.id = all_ing.id AND i.freq_count IS DISTINCT FROM COALESCE(c.n, 0)
"""

_FREQ_STATS_SQL = """
SELECT count(*), count(*) FILTER (WHERE freq_count > 0), max(freq_count),
       round(avg(freq_count)::numeric, 1)
FROM ingredient
"""

_FREQ_TOP_SQL = """
SELECT i.name, i.freq_count, c.path::text
FROM ingredient i LEFT JOIN ingredient_category c ON c.id = i.category_id
ORDER BY i.freq_count DESC, i.id LIMIT %s
"""


def rebuild_freq_count() -> int:
    """재료별 등장 레시피 수를 다시 센다. 멱등이다."""
    with cursor(commit=True) as cur:
        cur.execute(_FREQ_SQL)
        return max(cur.rowcount, 0)


def load_freq_stats() -> tuple[int, int, int, float]:
    """(재료 수, freq_count > 0 인 수, 최댓값, 평균)."""
    with cursor() as cur:
        cur.execute(_FREQ_STATS_SQL)
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("ingredient 가 비어 있습니다")
        return (int(row[0]), int(row[1]), int(row[2]), float(row[3]))


def load_freq_top(limit: int = 10) -> list[tuple[str, int, str | None]]:
    """상위 재료. 상식과 맞는지 눈으로 확인하는 용도다."""
    with cursor() as cur:
        cur.execute(_FREQ_TOP_SQL, (limit,))
        return cur.fetchall()
