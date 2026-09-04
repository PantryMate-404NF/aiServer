-- ═══════════════════════════════════════════════════════════════
-- 확장 + 스키마 네임스페이스
-- ═══════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════
-- 🔴 시간대 — 한국 기준 (09-03 확정)
-- ═══════════════════════════════════════════════════════════════
-- UTC 로 두면 한국 자정~오전 9시 사이에 `current_date` 가 **하루 전**이라
-- f_expiring 의 임박 판정이 하루씩 어긋난다. 유저가 "오늘 뭐 해먹지" 를
-- 물을 때의 '오늘' 은 한국 날짜다.
--
-- ⚠️ 컨테이너 환경변수(TZ·PGTZ)로는 **부족하다.** 그건 컨테이너 안에서만 유효하고
--    밖에서 붙는 psycopg2 연결에는 적용되지 않는다 — 실제로 09-03 에 겪었다.
--    DATABASE 수준으로 박아야 모든 연결이 따른다.
ALTER DATABASE recodb SET timezone = 'Asia/Seoul';

-- ★ 확장은 반드시 public 한 곳에만 설치한다.
--   스키마마다 따로 설치하면 vector·ltree 타입이 스키마별로 달라져
--   조인·비교가 실패한다. 공유 DB 에서 특히 치명적이다.
CREATE EXTENSION IF NOT EXISTS vector    SCHEMA public;
CREATE EXTENSION IF NOT EXISTS intarray  SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm   SCHEMA public;
CREATE EXTENSION IF NOT EXISTS ltree     SCHEMA public;

-- AI 파트 전용 네임스페이스.
-- recipe · app_user · event_log 는 극히 일반적인 이름이라
-- public 에 두면 백엔드팀 테이블과 반드시 충돌한다.
CREATE SCHEMA IF NOT EXISTS reco;
COMMENT ON SCHEMA reco IS '냉장고 기반 레시피 추천 — AI 파트 전용. 백엔드팀은 app 스키마 사용.';

-- 확장이 하나라도 없으면 Retrieval 단일 쿼리가 성립하지 않는다.
DO $$
DECLARE missing TEXT;
BEGIN
    SELECT string_agg(e, ', ') INTO missing
    FROM unnest(ARRAY['vector','intarray','pg_trgm','ltree']) AS e
    WHERE e NOT IN (SELECT extname FROM pg_extension);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '필수 확장 누락: %', missing;
    END IF;
    RAISE NOTICE '확장 4종 (public) + 스키마 reco 확인';
END $$;
