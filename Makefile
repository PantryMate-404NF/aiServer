# 냉장고 추천 시스템 — 개발 명령
# 전제: Docker Desktop(또는 OrbStack) + .venv (python 3.12)

SHELL   := /bin/bash
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file deploy/.env
PY      := .venv/bin/python
PSQL    := $(COMPOSE) exec -T postgres psql -U reco -d recodb

.DEFAULT_GOAL := help
.PHONY: help env up up-all down down-v ps logs psql wait \
        install requirements requirements-check \
        validate dry-run seed seed-reset verify smoke ddl-test review-sheet review-apply unmatched post-index bootstrap clean

help:  ## 명령 목록
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# EXTRA= 로 묶음을 하나 더 얹는다 (예: make install TRACK=B EXTRA=rank-v1).
# 🔴 EXTRA 는 트랙 묶음에 **더하는** 것이다. 다음번에 빼먹으면 uv sync 가 도로 지운다 —
#    한 번 얹었으면 계속 붙여야 한다.
EXTRA_ARG := $(if $(EXTRA),--extra $(EXTRA),)

install:  ## 트랙별 의존성 설치 (make install TRACK=A|B|C)
# 🔴 TRACK 없이 실행해도 **아무것도 설치하지 않는다.**
#    `uv sync` 는 락에 없는 패키지를 지운다 — 실수로 치면 남의 환경이 날아간다.
#    실제로 09-02 에 fastapi·numpy 가 사라져 mock 서버가 죽었다.
	@case "$(TRACK)" in \
	  A) uv sync --extra ml $(EXTRA_ARG) ;; \
	  B) uv sync --extra ml $(EXTRA_ARG) ;; \
	  C) uv sync --extra dash --extra ml --extra obs $(EXTRA_ARG) ;; \
	  *) echo "TRACK 을 주세요:  make install TRACK=A|B|C  [EXTRA=rank-v1|embed]"; \
	     echo "  A 데이터   ml"; \
	     echo "  B 엔진     ml   (fastapi·uvicorn 은 09-04 부터 기본 의존)"; \
	     echo "  C 관측     dash + ml + obs"; \
	     echo ""; \
	     echo "🔴 'uv sync' 를 맨손으로 치지 마세요 — 락에 없는 패키지를 지웁니다."; \
	     exit 1 ;; \
	esac

# ── requirements/ ───────────────────────────────────────────────
# uv 를 못 쓰는 사람(순정 pip·코랩·조교 채점 환경)을 위한 사본이다.
# 🔴 손으로 고치지 않는다. pyproject 를 고치고 이 타깃을 다시 돌린다.
#    손으로 고치면 락과 조용히 갈라지고, 갈라진 걸 아무도 모른다.
REQ_DIR := requirements
UVX     := uv export -q --no-hashes --no-emit-project --no-dev

requirements:  ## requirements/*.txt 재생성 (pyproject 고친 뒤 반드시)
	@uv lock
	@mkdir -p $(REQ_DIR)
	@$(UVX)                                        -o $(REQ_DIR)/base.txt
	@$(UVX) --extra ml                             -o $(REQ_DIR)/A.txt
	@$(UVX) --extra api --extra ml                 -o $(REQ_DIR)/B.txt
	@$(UVX) --extra dash --extra ml --extra obs    -o $(REQ_DIR)/C.txt
	@$(UVX) --extra api --extra dash --extra obs --extra ml --extra rank-v1 \
	                                               -o $(REQ_DIR)/all.txt
	@for f in base A B C all; do \
	   printf "  %-24s %3s개\n" "$(REQ_DIR)/$$f.txt" \
	     "$$(grep -cE '^[a-zA-Z0-9]' $(REQ_DIR)/$$f.txt)"; \
	 done
	@echo "🔴 embed(sentence-transformers) 는 어디에도 안 넣었습니다 — torch 2GB 를 끌고 옵니다."

requirements-check:  ## requirements/ 가 락과 어긋나면 실패 (CI 용)
	@tmp=$$(mktemp -d); cp -R $(REQ_DIR)/. $$tmp/ 2>/dev/null || true; \
	 $(MAKE) --no-print-directory requirements >/dev/null; \
	 if diff -rq $$tmp $(REQ_DIR) >/dev/null; then \
	   echo "✅ requirements/ 최신"; rm -rf $$tmp; \
	 else \
	   echo "🔴 requirements/ 가 pyproject 와 다릅니다. 'make requirements' 결과를 커밋하세요:"; \
	   diff -rq $$tmp $(REQ_DIR) || true; rm -rf $$tmp; exit 1; \
	 fi

env:  ## .env 생성 (없을 때만)
	@[ -f deploy/.env ] || (cp deploy/.env.example deploy/.env && echo "deploy/.env 생성됨")

# ── 컨테이너 ────────────────────────────────────────────────────
up: env  ## 핵심 기동 (postgres redis) — 약 440MB
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory wait

up-obs: env  ## + 관측 도구 (grafana mlflow) — 대시보드 트랙 시점
	$(COMPOSE) --profile obs up -d --build
	@$(MAKE) --no-print-directory wait

up-all: env  ## + 애플리케이션 (reco-api dashboard) — 산출물 D 이후
	$(COMPOSE) --profile obs --profile app up -d --build
	@$(MAKE) --no-print-directory wait

mlflow-ui:  ## MLflow UI 를 로컬에서 실행 (컨테이너 불필요)
	@echo "★ backend 는 반드시 mlflowdb. recodb 로 붙이면 reco 스키마가 오염된다."
	.venv/bin/mlflow ui --host 127.0.0.1 --port 5000 \
	  --backend-store-uri postgresql://reco:reco@localhost:5432/mlflowdb

down:  ## 정지 (데이터 보존)
	$(COMPOSE) down

down-v:  ## 정지 + 볼륨 삭제 (데이터 전부 소멸)
	$(COMPOSE) down -v

ps:  ## 컨테이너 상태
	$(COMPOSE) ps

logs:  ## 로그 추적
	$(COMPOSE) logs -f --tail=100

psql:  ## psql 접속
	$(COMPOSE) exec postgres psql -U reco -d recodb

wait:  ## postgres healthy 대기
	@echo -n "postgres 기동 대기"
	@for i in $$(seq 1 40); do \
	  if $(COMPOSE) exec -T postgres pg_isready -U reco -d recodb >/dev/null 2>&1; then \
	    echo " ✓"; exit 0; fi; echo -n "."; sleep 2; done; \
	echo " ✗ 시간 초과"; $(COMPOSE) logs --tail=40 postgres; exit 1

# ── 시드 ────────────────────────────────────────────────────────
validate:  ## 시드 정합성 검증 (DB 불필요)
	$(PY) seeds/validate.py

dry-run:  ## 적재 계획만 확인 (DB 불필요)
	$(PY) scripts/reco/migrate.py --dry-run

seed: validate  ## 시드 적재 (idempotent)
	$(PY) scripts/reco/migrate.py

seed-reset: validate  ## 시드 테이블 비우고 재적재
	$(PY) scripts/reco/migrate.py --reset

verify:  ## 적재 결과 확인
	$(PY) scripts/reco/migrate.py --verify

# ── 계약 · Mock ─────────────────────────────────────────────────
contract:  ## 스테이지·API 계약 검증 (DB 불필요)
	$(PY) -m tests.unit.recommend.test_contract

api-docs:  ## API 명세 재생성 (Mock 실호출 캡처 → 문서)
	$(PY) scripts/reco/api/capture.py
	$(PY) scripts/reco/api/render.py

mock:  ## Mock 추천 API 기동 — 대시보드가 엔진을 기다리지 않게
	@echo "  http://localhost:8000/docs  ← OpenAPI"
	.venv/bin/uvicorn main:create_app --factory --reload --port 8000

# ── 정규화 ──────────────────────────────────────────────────────
normalize-test:  ## P1·P2 fixture + P3 캐스케이드 검증 (DB 불필요)
	$(PY) -m tests.unit.recommend.run
	$(PY) -m tests.unit.recommend.test_match
	$(PY) -m tests.unit.recommend.test_role

normalize-demo:  ## 임의 문자열 파싱 결과 확인  (make normalize-demo T="대파 1대")
	@$(PY) -c "import sys; from features.recommend.ingest.parse import normalize; \
	[print(f'  {r.name!r:<16} qty={r.quantity} unit={r.unit} note={r.note} \
opt={r.is_optional_hint} amb={r.is_ambiguous_qty} subs={r.substitutes}') \
	 for r in normalize(sys.argv[1])]" "$(T)"

# ── 크롤링 데이터 ───────────────────────────────────────────────
review-sheet:  ## 검수 시트 생성 — 스프레드시트로 판단 (make review-sheet TOP=300)
	$(PY) scripts/reco/bench/review_sheet.py --top $(or $(TOP),300)

review-apply:  ## 채운 시트를 시드에 반영 (--write 없이는 미리보기)
	$(PY) scripts/reco/bench/review_apply.py $(if $(WRITE),--write,)

unmatched:  ## 미매칭 표현을 빈도순으로 덤프 (약 12분)
	$(PY) scripts/reco/bench/unmatched_dump.py

coverage:  ## 실제 크롤 데이터로 P1→P2→P3 커버리지 측정 (설계 4-8)
	$(PY) -m features.recommend.evaluation.coverage

probe:  ## 크롤링 샘플 진단  (make probe SAMPLE=경로.json)
	$(PY) scripts/reco/probe.py $(or $(SAMPLE),tests/fixtures/responses/best_case.json)

probe-all:  ## 합성 샘플 3종으로 어댑터 자체를 검증
	@for f in tests/fixtures/responses/*.json; do \
	  echo "=== $$f ==="; $(PY) scripts/reco/probe.py $$f | tail -4; echo; done

# ── 검증 ────────────────────────────────────────────────────────
doc-check:  ## 문서 수치가 실제 DB·코드와 맞는지 대조
	PYTHONPATH=. $(PY) scripts/reco/doc_check.py

log-test:  ## S2 — 라이터 종단 검증 (mock 출력 → 실제 DB)
	PYTHONPATH=. $(PY) -m tests.unit.recommend.test_writer

smoke-py:  ## S1 — 같은 케이스를 features.recommend.repository.retrieve() 경로로 검증
	PYTHONPATH=. $(PY) tests/integration/test_smoke.py --via-python --recipes 3000

smoke:  ## 합성 레시피로 Retrieval 정확성·지연시간 측정
	$(PY) tests/integration/test_smoke.py

smoke-big:  ## 5만 건 규모로 지연시간 측정
	$(PY) tests/integration/test_smoke.py --recipes 50000

schema-remote:  ## 원격 DB 에 스키마 적용 (DATABASE_URL 필요)
	./deploy/apply_schema.sh "$${DATABASE_URL:?DATABASE_URL 을 설정하세요}"

ddl-test:  ## DDL 개정분 검증 — 소급 불가 컬럼 왕복 (07 E-3)
	$(PY) tests/integration/test_ddl.py

post-index:  ## HNSW 인덱스 생성 (대량 적재 후 1회)
	$(PSQL) -f /post/post_index.sql

# ── 한 번에 ─────────────────────────────────────────────────────
bootstrap: up seed smoke  ## 기동 → 시드 → 검증 (핵심만)
	@echo ""
	@echo "  준비 완료 — 스키마 · 시드 · Retrieval 검증됨"
	@echo "    psql        make psql"
	@echo "    MLflow UI   make mlflow-ui      (컨테이너 불필요)"
	@echo "    관측 도구   make up-obs         (Grafana 필요해질 때)"

clean: down-v  ## 전부 삭제 후 재부트스트랩 준비
	@echo "볼륨 삭제됨. 'make bootstrap' 으로 재구축."

# ── 문서 ────────────────────────────────────────────────────────
MERMAID_TMP := /tmp/reco-mermaid

diagrams:  ## 문서의 mermaid 다이어그램이 실제로 렌더되는지 검증
	@mkdir -p $(MERMAID_TMP)
	@cd $(MERMAID_TMP) && [ -d node_modules/mermaid ] || \
	  npm i --silent --no-fund --no-audit mermaid@11 jsdom
	@cp scripts/reco/check_mermaid.mjs $(MERMAID_TMP)/
	@cd $(MERMAID_TMP) && node check_mermaid.mjs $(CURDIR)/docs

# ── 판단 근거 시뮬레이션 ────────────────────────────────────────
bench-quick:  ## 설계 판단 근거 시뮬레이션 (q3 는 --quick, 나머지는 전량 · 약 12분→3분)
	$(PY) scripts/reco/bench/q3_linear_vs_gam.py --quick
	$(PY) scripts/reco/bench/serendipity_mix.py
	$(PY) scripts/reco/bench/kmeans_worth_it.py

db-reset: clean bootstrap  ## 볼륨 삭제 후 재구축 (문서가 인용하는 이름)
