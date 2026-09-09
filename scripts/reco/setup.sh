#!/usr/bin/env bash
#
# 개발 환경 준비 — 처음 받은 사람이 이것 하나만 돌리면 된다.
#
#   ./scripts/reco/setup.sh --track B          # A 데이터 · B 엔진 · C 관측
#   ./scripts/reco/setup.sh --track C --extra rank-v1
#   ./scripts/reco/setup.sh --check            # 진단만 (아무것도 안 바꾼다)
#   ./scripts/reco/setup.sh --track A --no-db  # 컨테이너 없이 파이썬 환경만
#
# 🔴 이 스크립트는 **지우지 않는다.**
#    이미 있는 .env · deploy/.env · .venv · DB 볼륨을 건드리지 않는다.
#    특히 .env 의 REVIEW_SALT 는 후기 624,422건의 작성자 해시를 만든 값이라
#    새로 만들면 "같은 사람이 쓴 후기" 판정이 과거와 어긋난다. 되돌릴 수 없다.
#    데이터를 지우는 명령(make clean · down-v · seed-reset · db-reset)은
#    이 스크립트 어디에도 없다. 필요하면 사람이 직접 친다.
#
set -euo pipefail

# 🔴 이 스크립트는 scripts/reco/ 에 있고 하는 일은 **저장소 루트 기준**이다.
#    (09-04 구조 개편 전에는 루트에 있어서 dirname 만으로 충분했다.)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# ── 출력 ────────────────────────────────────────────────────────
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi
step()  { STEP="$*"; printf "\n%s▸ %s%s\n" "$B" "$*" "$N"; }
ok()    { printf "  %s✓%s %s\n" "$G" "$N" "$*"; }
warn()  { printf "  %s⚠%s  %s\n" "$Y" "$N" "$*"; }
bad()   { printf "  %s✗%s %s\n" "$R" "$N" "$*"; }
note()  { printf "    %s%s%s\n" "$D" "$*" "$N"; }
die()   { printf "\n%s🔴 %s%s\n" "$R" "$*" "$N" >&2; exit 1; }

# 🔴 중간 단계(make up · make seed 등)에서 죽으면 하위 명령의 에러만 남고
#    "다시 돌려도 되나" 를 사람이 스스로 판단해야 했다. 그걸 말해 준다.
STEP="시작"
trap 'rc=$?; if [ $rc -ne 0 ] && [ -z "${FAILED:-}" ] && [ -z "${CLEAN_EXIT:-}" ]; then
  printf "\n%s🔴 [%s] 에서 멈췄습니다 (exit %d)%s\n" "$R" "$STEP" "$rc" "$N" >&2
  printf "   위 출력을 보고 고친 뒤 다시 돌리세요 — 다시 돌려도 안전합니다.\n" >&2
  printf "   지우는 명령(make clean · down-v · seed-reset)은 부르지 않았습니다.\n" >&2
fi' EXIT

# ── 인자 ────────────────────────────────────────────────────────
TRACK=""; EXTRA=""; NO_DB=0; CHECK_ONLY=0

usage() {
  cat <<'USAGE'
사용법:  ./scripts/reco/setup.sh --track A|B|C [옵션]

  --track A|B|C     자기 트랙. 트랙마다 깔리는 것이 다르다.
                      A 데이터   ml            (numpy · scikit-learn)
                      B 엔진     api + ml      (+ fastapi · uvicorn)
                      C 관측     dash + ml + obs (+ streamlit · mlflow)
  --extra NAME      묶음 하나 더 (rank-v1 · embed). 🔴 다음번에도 계속 붙여야 한다 —
                    빼먹으면 uv sync 가 도로 지운다.
  --no-db           컨테이너를 띄우지 않는다. 파이썬 환경만 준비.
  --check           진단만 하고 아무것도 바꾸지 않는다.
  -h, --help        이 도움말

무엇을 하나 (2~4단계는 이미 있으면 건너뛴다. 5~8단계는 매번 실행):
  1. 필수 도구 확인       uv · docker · make
  2. deploy/.env 생성         없을 때만 (있으면 손대지 않는다)
  3. .env 의 REVIEW_SALT  확인만 한다. 없어도 만들지 않는다 — 아래 설명 참조
  4. .venv 생성           없을 때만 (python 3.12 는 uv 가 확보)
  5. 트랙별 의존성 설치    make install TRACK=?
  6. 컨테이너 기동         postgres · redis (약 440MB)
  7. 시드 적재            make seed (여러 번 돌려도 안전)
  8. 검증                 계약(항상) · Retrieval(DB 있을 때)
USAGE
}

# 🔴 값이 필요한 옵션에 값이 없으면 `shift 2` 가 set -e 아래서 **아무 말 없이** 죽는다.
#    사용자는 빈 화면과 exit 1 만 본다. 먼저 확인하고 이유를 말한다.
need_val() { [ $# -ge 2 ] || die "$1 뒤에 값이 필요합니다.   (./scripts/reco/setup.sh --help)"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --track) need_val "$@"; TRACK="$2"; shift 2 ;;
    --track=*) TRACK="${1#*=}"; shift ;;
    --extra) need_val "$@"; EXTRA="$2"; shift 2 ;;
    --extra=*) EXTRA="${1#*=}"; shift ;;
    --no-db) NO_DB=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    A|B|C|a|b|c) TRACK="$1"; shift ;;          # ./scripts/reco/setup.sh B 도 받는다
    *) die "모르는 옵션: $1   (./scripts/reco/setup.sh --help)" ;;
  esac
done

TRACK="$(printf '%s' "$TRACK" | tr '[:lower:]' '[:upper:]')"

# 🔴 트랙은 **여기서** 검증한다. 5단계까지 미루면 .venv 를 만든 뒤에 죽는다 —
#    오타 하나에 앞 단계를 다 돌리고 나서야 알려주는 꼴이다.
case "$TRACK" in
  A|B|C|"") ;;
  *) printf "\n%s🔴 TRACK 은 A · B · C 중 하나입니다 (받은 값: '%s')%s\n" \
       "$R" "$TRACK" "$N" >&2; exit 1 ;;
esac

# 트랙을 물어볼 수 없는 상황(파이프·CI)이면 **여기서** 멈춘다.
# 5단계까지 가면 .venv 를 만들어 놓고 죽는다.
if [ -z "$TRACK" ] && [ "$CHECK_ONLY" -eq 0 ] && [ ! -t 0 ]; then
  printf "\n%s🔴 --track A|B|C 를 주세요 — 트랙마다 깔리는 것이 다릅니다.%s\n" "$R" "$N" >&2
  printf "     A 데이터   ml\n     B 엔진     api + ml\n     C 관측     dash + ml + obs\n" >&2
  exit 1
fi

if [ -n "$EXTRA" ] && [ -z "$TRACK" ]; then
  printf "\n%s🔴 --extra 는 --track 과 함께 씁니다 — EXTRA 는 트랙 묶음에 더하는 것입니다.%s\n" \
    "$R" "$N" >&2; exit 1
fi

printf "%s냉장고 추천 시스템 — 개발 환경 준비%s\n" "$B" "$N"
[ "$CHECK_ONLY" -eq 1 ] && printf "%s(진단만 — 아무것도 바꾸지 않는다)%s\n" "$D" "$N"

# ── 0. 여기가 저장소 루트인가 ───────────────────────────────────
[ -f Makefile ] && [ -f pyproject.toml ] && [ -d deploy/init ] \
  || die "저장소 루트를 못 찾았습니다. 저장소 안에서 ./scripts/reco/setup.sh 로 실행하세요."

# ── 1. 필수 도구 ────────────────────────────────────────────────
step "1. 필수 도구"
MISSING=0

if command -v uv >/dev/null 2>&1; then
  ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
else
  bad "uv 없음"
  note "brew install uv        (없으면: curl -LsSf https://astral.sh/uv/install.sh | sh)"
  MISSING=1
fi

command -v make >/dev/null 2>&1 && ok "make" || { bad "make 없음"; MISSING=1; }

if [ "$NO_DB" -eq 0 ]; then
  if docker info >/dev/null 2>&1; then
    ok "docker 데몬 응답함"
  elif command -v docker >/dev/null 2>&1; then
    bad "docker 는 있는데 데몬이 안 떠 있습니다"
    note "OrbStack(또는 Docker Desktop)을 먼저 실행하세요."
    MISSING=1
  else
    bad "docker 없음"
    note "brew install --cask orbstack     # Docker Desktop 대체, 별도 설치 불필요"
    note "컨테이너 없이 진행하려면:  ./scripts/reco/setup.sh --track ? --no-db"
    MISSING=1
  fi
fi

# 🔴 진단 모드에서는 여기서 죽지 않는다. 도구 하나가 없다고 1단계에서 멈추면
#    .env·.venv 상태를 못 알려준다 — 진단의 목적이 그건데.
if [ "$MISSING" -eq 1 ]; then
  if [ "$CHECK_ONLY" -eq 1 ]; then
    warn "위 도구가 없습니다 — 나머지 진단은 계속합니다"
  else
    die "위 도구를 먼저 설치하세요."
  fi
fi

# ── 2. deploy/.env — 컨테이너 설정 ──────────────────────────────────
step "2. deploy/.env — 컨테이너 설정"
if [ -f deploy/.env ]; then
  ok "이미 있음 — 손대지 않습니다"
elif [ "$CHECK_ONLY" -eq 1 ]; then
  warn "없음 (진단 모드라 만들지 않음)"
else
  make env >/dev/null
  ok "deploy/.env.example 에서 생성"
fi

# ── 3. .env 의 REVIEW_SALT ──────────────────────────────────────
#
# 🔴 여기가 이 스크립트에서 가장 조심하는 자리다.
#    새로 만들면 후기 624,422건의 작성자 해시가 전부 달라진다.
#    "같은 사람이 쓴 후기" 판정이 과거와 어긋나고, 되돌릴 방법이 없다.
#    그래서 **읽기만 하고, 없어도 만들지 않는다.**
#
step "3. .env — REVIEW_SALT (후기 작성자 해시의 열쇠)"
# 🔴 탐지를 넓게 한다. 처음 판은 `^REVIEW_SALT=` 만 봐서
#    `export REVIEW_SALT=...` 를 "없음" 으로 판정했다 — 하필 그 형식이
#    scripts/reco/load_recipes.py 가 사용자에게 직접 알려주는 형식이다. 그러고는
#    `> .env` 를 안내해서 **멀쩡한 salt 를 지우게 만들 뻔했다.**
SALT_RE='^[[:space:]]*(export[[:space:]]+)?REVIEW_SALT[[:space:]]*=[[:space:]]*[^[:space:]]'
if [ -f .env ] && grep -Eq "$SALT_RE" .env 2>/dev/null; then
  ok "있음 — 값은 건드리지 않습니다"
  note "🔴 이 값을 새로 만들면 후기 62만 건의 작성자 해시가 전부 달라집니다. 되돌릴 수 없습니다."
else
  warn "없습니다 — **자동으로 만들지 않습니다**"
  note "팀 채널에서 **기존 값**을 받으세요. 새로 만들면 안 됩니다 —"
  note "이미 적재된 후기의 작성자 해시가 그 값으로 만들어졌고, 바꾸면"
  note "같은 사람이 쓴 후기를 더 이상 같은 사람으로 못 봅니다."
  note ""
  if [ -f .env ]; then
    note "🔴 .env 가 **이미 있습니다.** 덮어쓰지 말고 줄만 더하세요 —"
    note "   '>' 를 쓰면 파일 안의 다른 키가 통째로 사라집니다."
  fi
  note "    printf 'REVIEW_SALT=팀에서받은값\\n' >> .env      # '>' 가 아니라 '>>'"
  note ""
  note "🔴 .env 에 적는 것만으로는 파이썬이 읽지 않습니다 — 이 저장소에는"
  note "   .env 를 불러오는 코드가 없습니다(dotenv 미사용). 로더를 돌릴 셸에서:"
  note "    set -a; . ./.env; set +a"
  note ""
  note "지금 막히는 곳은 크롤 데이터 적재(scripts/reco/load_recipes.py)뿐입니다."
  note "시드 적재·계약 검증·Retrieval 은 이 값 없이도 됩니다 — 계속 진행합니다."
fi

# ── 4. .venv ────────────────────────────────────────────────────
step "4. 파이썬 가상환경"
if [ -d .venv ]; then
  V="$(.venv/bin/python -V 2>/dev/null || echo '?')"
  case "$V" in
    *3.12*)
      ok "이미 있음 — $V" ;;
    '?')
      bad ".venv 는 있는데 인터프리터가 없습니다 (.venv/bin/python 실행 불가)"
      note "uv venv 를 중간에 끊었거나 python@3.12 가 갱신된 경우입니다."
      note "사람이 직접:  rm -rf .venv && uv venv --python 3.12" ;;
    *)
      # 🔴 여기서 멈춘다. 경고만 하고 넘어가면 바로 다음 5단계의 uv sync 가
      #    이 .venv 를 **지우고 다시 만든다** — "사람이 직접 지우세요" 라고
      #    말한 두 줄 뒤에 스크립트가 스스로 지우는 꼴이었다(실측 확인).
      if [ "$CHECK_ONLY" -eq 1 ]; then
        warn "$V — pyproject 는 3.12 를 요구합니다 (>=3.12,<3.13)"
        note "이대로 설치하면 uv sync 가 이 .venv 를 지우고 다시 만듭니다."
      else
        printf "\n%s🔴 .venv 가 %s 입니다. pyproject 는 3.12 를 요구합니다 (>=3.12,<3.13).%s\n" \
          "$R" "$V" "$N" >&2
        printf "   다음 단계의 uv sync 가 이 .venv 를 **지우고 다시 만듭니다.**\n" >&2
        printf "   안에 손으로 넣어둔 것(editable 설치 · 주피터 커널 등)이 있으면 먼저 빼내세요.\n" >&2
        printf "   그 뒤:  rm -rf .venv && uv venv --python 3.12 && ./scripts/reco/setup.sh --track %s\n" \
          "${TRACK:-?}" >&2
        exit 1
      fi ;;
  esac
elif [ "$CHECK_ONLY" -eq 1 ]; then
  warn "없음 (진단 모드라 만들지 않음)"
else
  uv venv --python 3.12
  ok ".venv 생성"
fi

# ── 5. 트랙별 의존성 ────────────────────────────────────────────
step "5. 트랙별 의존성"
if [ -z "$TRACK" ]; then
  if [ "$CHECK_ONLY" -eq 1 ]; then
    warn "--track 이 없어 설치를 건너뜁니다"
  elif [ -t 0 ]; then
    # 🔴 맨몸 `read` 는 Ctrl-D 에서 set -e 로 **아무 말 없이** 죽는다.
    #    그리고 오타는 앞 단계를 다 돌린 뒤에야 걸린다 — 되물어서 끝낸다.
    while : ; do
      printf "  트랙을 고르세요 [A 데이터 / B 엔진 / C 관측]: "
      read -r TRACK || die "입력이 끊겼습니다 — ./scripts/reco/setup.sh --track A|B|C 로 다시 실행하세요."
      TRACK="$(printf '%s' "$TRACK" | tr '[:lower:]' '[:upper:]')"
      case "$TRACK" in
        A|B|C) break ;;
        *) warn "A · B · C 중 하나입니다 (받은 값: '$TRACK')" ;;
      esac
    done
  else
    die "--track A|B|C 를 주세요. (트랙마다 깔리는 것이 다릅니다)"
  fi
fi

case "$TRACK" in
  A|B|C)
    if [ "$CHECK_ONLY" -eq 1 ]; then
      warn "진단 모드 — 설치하지 않음 (하려면: make install TRACK=$TRACK)"
    else
      # 🔴 uv sync 를 직접 부르지 않는다. make install 이 트랙별 extras 를 붙인다 —
      #    맨손 uv sync 는 락에 없는 패키지를 지운다(09-02 에 fastapi·numpy 소실).
      if [ -n "$EXTRA" ]; then
        make install TRACK="$TRACK" EXTRA="$EXTRA"
        ok "TRACK=$TRACK + EXTRA=$EXTRA"
        warn "EXTRA 는 한 번 붙이고 끝이 아닙니다"
        note "다음번 make install 에서 빼먹으면 uv sync 가 '$EXTRA' 를 도로 지웁니다."
      else
        make install TRACK="$TRACK"
        ok "TRACK=$TRACK"
      fi
    fi
    ;;
  "") ;;
  *) die "TRACK 은 A · B · C 중 하나입니다 (받은 값: '$TRACK')" ;;   # 위에서 이미 걸러진다
esac

# ── 6~8. DB ─────────────────────────────────────────────────────
FAILED=""
if [ "$NO_DB" -eq 1 ]; then
  step "6~7. 컨테이너 · 시드 — 건너뜀 (--no-db)"
  note "나중에:  make up && make seed"
  if [ -n "$TRACK" ]; then
    step "8. 검증 (DB 없이 되는 것만)"
    # smoke-py 는 DB 가 필요하다. 여기서 부르면 반드시 실패한다.
    if out="$(make contract 2>&1)"; then
      ok "contract"
    else
      bad "contract 실패"
      printf '%s\n' "$out" | tail -20 | sed 's/^/      /'
      FAILED="$FAILED contract"
    fi
  fi
elif [ "$CHECK_ONLY" -eq 1 ]; then
  step "6. 컨테이너 — 진단 모드라 건너뜀"
  make ps 2>/dev/null | tail -n +2 || true
else
  step "6. 컨테이너 기동 (postgres · redis)"
  make up          # env → up → wait 까지 Makefile 이 한다

  step "7. 시드 적재"
  note "여러 번 돌려도 안전합니다 (idempotent). 기존 데이터를 지우지 않습니다."
  make seed

  step "8. 검증"
  # 🔴 출력을 삼키되 **실패는 절대 삼키지 않는다.**
  #    처음 판은 `make contract >/dev/null && ok ...` 였는데, 계약이 깨져도
  #    "준비 완료" 를 찍고 exit 0 으로 끝났다. 준비가 안 됐는데 됐다고 말하는
  #    스크립트는 없느니만 못하다.
  for chk in contract smoke-py; do
    if out="$(make "$chk" 2>&1)"; then
      ok "$chk"
    else
      bad "$chk 실패"
      printf '%s\n' "$out" | tail -20 | sed 's/^/      /'
      FAILED="$FAILED $chk"
    fi
  done
fi

# ── 마무리 ──────────────────────────────────────────────────────
FAILED="${FAILED:-}"
if [ "$CHECK_ONLY" -eq 1 ] && [ "$MISSING" -eq 1 ]; then
  printf "\n%s진단 끝 — 빠진 도구가 있습니다.%s 위 설치 명령을 먼저 실행하세요.\n" "$Y" "$N"
  exit 1
fi
if [ -n "$FAILED" ]; then
  printf "\n%s🔴 준비가 끝나지 않았습니다 —%s 실패:%s%s\n" "$R" "$N" "$FAILED" "$N" >&2
  printf "   위 출력을 보고 고친 뒤 다시 돌리세요. 다시 돌려도 안전합니다.\n" >&2
  printf "   개별 확인:  make%s\n" "$FAILED" >&2
  exit 1
fi

CLEAN_EXIT=1
if [ "$CHECK_ONLY" -eq 1 ]; then
  printf "\n%s진단 끝 — 위 ⚠ 를 처리한 뒤 --check 없이 다시 돌리세요%s\n" "$B" "$N"
else
  printf "\n%s준비 완료%s\n" "$B" "$N"
  if [ "$NO_DB" -eq 1 ]; then
    printf "  %s컨테이너는 안 띄웠습니다. DB 가 필요해지면:  make up && make seed%s\n" "$D" "$N"
  else
    printf "  %s지금 DB 에는 재료 사전만 있습니다 — recipe·recipe_review 는 0행입니다.%s\n" "$D" "$N"
    printf "  %s실데이터는 raw_data 크롤 파일 + REVIEW_SALT + scripts/reco/load_recipes.py 가 필요합니다.%s\n" "$D" "$N"
  fi
fi

# 트랙마다 쓸 수 있는 명령이 다르다 — A 에는 uvicorn 이 없어 make mock 이 실패한다
# 트랙마다 쓸 수 있는 명령이 다르다. 빈 줄이 남지 않게 한 줄씩 쌓는다.
CMDS="    make help            전체 목록
    make contract        계약 검증        (DB 불필요)
    make doc-check       문서 ↔ 코드 대조"
add_cmd() { CMDS="$CMDS
$1"; }
[ "$NO_DB" -eq 0 ] && [ "$CHECK_ONLY" -eq 0 ] && add_cmd "    make psql            DB 접속"
case "${TRACK:-}" in
  A) add_cmd "    make coverage        P1→P2→P3 커버리지 측정"
     add_cmd "    make unmatched       미매칭 표현 덤프" ;;
  B) add_cmd "    make mock            Mock 추천 API (http://localhost:8000/docs)"
     add_cmd "    make log-test        라이터 종단 검증" ;;
  C) add_cmd "    make mock            Mock 추천 API (http://localhost:8000/docs)"
     add_cmd "    make mlflow-ui       MLflow UI"
     add_cmd "    make up-obs          관측 도구 (Grafana)" ;;
esac

cat <<EOF

  ${B}자기 트랙 지시서를 먼저 읽으세요${N}
    docs/reco/draft/03_작업분담_공통.md          공통 규칙 (먼저)
    docs/reco/draft/*_작업분담_${TRACK:-?}_*.md   자기 트랙
    docs/reco/draft/05_작업분담_결정사항.md      혼자 정하면 안 되는 것들

  ${B}자주 쓰는 명령${N}
${CMDS}

  ${B}🔴 하지 말 것${N}
    uv sync              맨손으로 치면 락에 없는 패키지를 지웁니다
                         → make install TRACK=? 로만
    .env 재생성          REVIEW_SALT 가 바뀌면 후기 62만 건의
                         작성자 해시가 전부 달라집니다
    make clean           볼륨을 지웁니다. 남의 작업 결과도 같이 사라집니다
    DDL 단독 변경        스키마는 셋이 모여서 (D-8)

EOF
