#!/usr/bin/env bash
# 외부 DB 에 스키마를 적용한다.
#
#   ./db/apply_schema.sh "postgresql://user:pw@host:5432/dbname"
#
# /docker-entrypoint-initdb.d 자동 실행은 "컨테이너가 볼륨을 처음 만들 때"만
# 동작한다. 원격 DB 에는 그 메커니즘이 없으므로 이 스크립트가 유일한 경로다.
#
# 로컬 psql 이 없어도 되도록 일회용 컨테이너로 실행한다.
set -euo pipefail

URL="${1:-${DATABASE_URL:-}}"
[ -n "$URL" ] || { echo "사용법: $0 <DATABASE_URL>"; exit 1; }

DIR="$(cd "$(dirname "$0")" && pwd)"
FILES=(01_extensions.sql 02_schema.sql 03_indexes.sql 04_functions.sql)

echo "대상: ${URL##*@}"
echo "⚠️  05_roles.sql 은 포함하지 않는다 — 원격 역할·비밀번호는 DB 관리자가 생성한다."
echo "⚠️  00_databases.sql 도 포함하지 않는다 (CREATE DATABASE 권한이 없을 수 있다)."
echo "    → MLflow 백엔드용 mlflowdb 가 만들어지지 않는다. 둘 중 하나를 반드시 하라:"
echo "      (a) DB 관리자에게 mlflowdb 생성을 요청한다"
echo "      (b) MLFLOW_BACKEND_URI 를 별도 스키마로 돌린다"
echo "    🔴 안 하면 MLflow 가 테이블 15개를 reco 스키마에 만든다 (설계 1-6)."
read -rp "계속? [y/N] " ok; [ "$ok" = "y" ] || exit 1

for f in "${FILES[@]}"; do
  echo "  → $f"
  docker run --rm -i --network host \
    -v "$DIR/init:/init:ro" pgvector/pgvector:pg16 \
    psql "$URL" -v ON_ERROR_STOP=1 -q -f "/init/$f"
done
echo "✅ 스키마 적용 완료. 이어서:  DATABASE_URL='$URL' make seed"
