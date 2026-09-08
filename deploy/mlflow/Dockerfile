# 공식 mlflow 이미지에는 PostgreSQL 드라이버가 없어 백엔드 연결이 실패한다.
FROM ghcr.io/mlflow/mlflow:v2.17.2
RUN pip install --no-cache-dir psycopg2-binary==2.9.10
