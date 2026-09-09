-- ═══════════════════════════════════════════════════════════════
-- 역할 분리 — 공유 DB 에서 사고를 막는 장치
--
-- ⚠️ 아래 비밀번호는 로컬 개발용이다. 원격 공유 DB 에서는 DB 관리자가
--    다른 값으로 생성한다. 이 파일을 그대로 원격에 적용하지 말 것.
-- ═══════════════════════════════════════════════════════════════

SET search_path TO reco, public;

DO $$
BEGIN
    -- 앱: 읽기 + 쓰기. TRUNCATE·DDL 불가.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reco_app') THEN
        CREATE ROLE reco_app LOGIN PASSWORD 'reco_app_dev';
    END IF;
    -- 배치: + TRUNCATE, 스키마 객체 생성
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reco_batch') THEN
        CREATE ROLE reco_batch LOGIN PASSWORD 'reco_batch_dev';
    END IF;
    -- Grafana: 읽기 전용
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reco_ro') THEN
        CREATE ROLE reco_ro LOGIN PASSWORD 'reco_ro_dev';
    END IF;
END $$;

GRANT USAGE ON SCHEMA reco, public TO reco_app, reco_batch, reco_ro;

-- ── reco_app ────────────────────────────────────────────────────
-- TRUNCATE 를 주지 않는 것이 핵심이다.
-- 정규화 재실행(설계 4-1)이 TRUNCATE 기반이라, 앱 계정에 권한이 있으면
-- 버그 하나로 크롤링 데이터 전체가 날아간다.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA reco TO reco_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA reco TO reco_app;
GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA reco TO reco_app;

-- ── reco_batch ──────────────────────────────────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES
                                     ON ALL TABLES    IN SCHEMA reco TO reco_batch;
GRANT USAGE, SELECT, UPDATE          ON ALL SEQUENCES IN SCHEMA reco TO reco_batch;
GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA reco TO reco_batch;
GRANT CREATE ON SCHEMA reco TO reco_batch;

-- ── reco_ro (Grafana) ───────────────────────────────────────────
-- 대시보드가 쓰기 권한을 들고 있으면 언젠가 사고가 난다.
GRANT SELECT                         ON ALL TABLES    IN SCHEMA reco TO reco_ro;
GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA reco TO reco_ro;

-- ── 이후 생성될 객체에도 자동 적용 ──────────────────────────────
ALTER DEFAULT PRIVILEGES IN SCHEMA reco
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO reco_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reco
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO reco_batch;
ALTER DEFAULT PRIVILEGES IN SCHEMA reco
    GRANT SELECT ON TABLES TO reco_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA reco
    GRANT USAGE, SELECT ON SEQUENCES TO reco_app, reco_batch;
ALTER DEFAULT PRIVILEGES IN SCHEMA reco
    GRANT EXECUTE ON FUNCTIONS TO reco_app, reco_batch, reco_ro;

-- ── 연결 시 search_path 자동 설정 ───────────────────────────────
-- 공유 DB 에서는 DATABASE 수준으로 바꾸면 다른 팀에 영향을 준다.
-- 반드시 ROLE 수준으로 설정한다.
ALTER ROLE reco_app   SET search_path TO reco, public;
ALTER ROLE reco_batch SET search_path TO reco, public;
ALTER ROLE reco_ro    SET search_path TO reco, public;

-- ── 🔴 타임아웃 — 원격 분리(06 8-D) 대비 ────────────────────────
-- 한 대일 때는 유닉스 소켓이라 클라이언트가 죽으면 커널이 즉시 소켓을 닫았다.
-- TCP 는 서버가 세션 사망을 모른다. 그동안 TRUNCATE 의 ACCESS EXCLUSIVE 락이
-- 유지되고, 열린 트랜잭션의 xmin 이 인스턴스 전체 autovacuum 을 멈춘다.
-- 공유 DB 라면 백엔드팀까지 번진다.
--
-- DATABASE 수준이 아니라 ROLE 수준으로 거는 것이 핵심이다 (위 search_path 와 같은 이유).

-- 앱: 온라인 경로. 실측 p95 30ms 이므로 5초면 충분히 관대하다.
ALTER ROLE reco_app   SET statement_timeout = '5s';
ALTER ROLE reco_app   SET idle_in_transaction_session_timeout = '30s';

-- 배치: 임베딩 적재·인덱스 생성이 길다. statement_timeout 을 걸지 않는다.
-- 대신 '트랜잭션을 열어둔 채 노는 것'을 막는다 — autovacuum 을 멈추는 것이 이쪽이다.
ALTER ROLE reco_batch SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE reco_batch SET lock_timeout = '10s';

-- 대시보드: 무거운 집계가 DB 를 오래 잡지 않게.
ALTER ROLE reco_ro    SET statement_timeout = '30s';
ALTER ROLE reco_ro    SET idle_in_transaction_session_timeout = '30s';

DO $$ BEGIN RAISE NOTICE '역할 3종 생성: reco_app(쓰기) reco_batch(+TRUNCATE) reco_ro(읽기전용)'; END $$;
