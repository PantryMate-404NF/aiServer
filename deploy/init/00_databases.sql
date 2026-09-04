-- MLflow 백엔드용 DB 분리 (로컬 전용).
-- 공유 원격 DB 에서는 DB 관리자가 별도로 만들거나, mlflowdb 대신
-- 기존 DB 의 mlflow 스키마를 쓰도록 BACKEND_URI 를 조정한다.
SELECT 'CREATE DATABASE mlflowdb'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'mlflowdb')
\gexec
