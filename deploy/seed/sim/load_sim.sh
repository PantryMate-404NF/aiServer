#!/usr/bin/env bash
# 시뮬 시드 적재.
#   psql 이 있는 PC:  DATABASE_URL=postgres://... bash deploy/seed/sim/load_sim.sh
#   psql 이 없는 PC:  PSQL_VIA_COMPOSE=1 bash deploy/seed/sim/load_sim.sh
#                    (deploy/docker-compose.yml 의 postgres 컨테이너 안 psql 을 stdin 으로 씁니다.
#                     사용자·DB 는 deploy/.env 의 POSTGRES_USER·POSTGRES_DB, 비면 reco·recodb)
# 전제: 01~04 init 적재 완료 + ingredient 시드 + recipe(status='published') 존재.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$HERE"

if [ -n "${PSQL_VIA_COMPOSE:-}" ] || ! command -v psql >/dev/null 2>&1; then
  [ -n "${PSQL_VIA_COMPOSE:-}" ] || echo "psql 이 없어 컨테이너의 psql 을 씁니다 (PSQL_VIA_COMPOSE=1 과 같음)"
  ENV_FILE="$REPO/deploy/.env"
  PG_USER="$( [ -f "$ENV_FILE" ] && grep -E '^POSTGRES_USER=.' "$ENV_FILE" | cut -d= -f2- || true)"
  PG_DB="$( [ -f "$ENV_FILE" ] && grep -E '^POSTGRES_DB=.' "$ENV_FILE" | cut -d= -f2- || true)"
  run_sql() {
    docker compose -f "$REPO/deploy/docker-compose.yml" --env-file "$ENV_FILE" \
      exec -T postgres psql -U "${PG_USER:-reco}" -d "${PG_DB:-recodb}" -v ON_ERROR_STOP=1 "$@"
  }
else
  : "${DATABASE_URL:?DATABASE_URL 을 설정하라}"
  run_sql() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 "$@"; }
fi

# 파일은 stdin 으로 넘깁니다 — 컨테이너에는 이 폴더가 마운트되어 있지 않습니다.
# 06_event_log 는 기본에서 뺍니다. recipe.status='published' 를 요구하는데 실측 0건이라
# (전량 'normalized') 예외로 멈춥니다. 상태를 올리는 단계가 아직 없습니다.
# 콜드→웜 시나리오는 00~05 로 충분합니다. 이벤트 로그가 필요해지면:
#   WITH_EVENTS=1 bash deploy/seed/sim/load_sim.sh
FILES="00_sim_persona 01_app_user 02_user_preference 03_user_vector 04_user_allergy 05_pantry_item"
[ -n "${WITH_EVENTS:-}" ] && FILES="$FILES 06_event_log"

for f in $FILES; do
  echo ">> $f"
  run_sql -q < "$f.sql"
done
run_sql < 99_verify.sql
