# aiServer — 영수증 OCR + 추천을 한 프로세스로 서빙하는 FastAPI 앱.
#
# 서비스가 하나라 이미지도 하나입니다. 빌드는 저장소 루트에서 합니다.
#
#     docker build -t reco-ai-server:local .
#     docker run --rm -p 8000:8000 --env-file .env reco-ai-server:local
#
# ─────────────────────────────────────────────────────────────────
# 이 저장소가 예시와 다른 점 셋 — 그대로 두면 빌드가 되지 않거나 조용히 틀립니다
# ─────────────────────────────────────────────────────────────────
# 1. Python 은 **3.12** 입니다. `pyproject.toml` 이 `>=3.12,<3.13` 이고 3.11 에서는
#    설치 자체가 거부됩니다.
# 2. `requirements.txt` 가 없습니다. 의존성 정본은 `uv.lock` 이고 `pip` 을 직접 쓰지
#    않습니다(01의 1절). 잠금 파일로 설치해야 개발·CI·운영이 같은 버전을 씁니다.
# 3. 소스 루트가 `src/` 입니다. import 가 `from features...` 형태라, 패키지를 설치해야
#    (hatchling 의 `sources = ["src"]`) `main:create_app` 이 보입니다.
#
# 런타임에 반드시 주입해야 하는 환경변수 6개 — 없으면 **기동 시점에** 멈춥니다(`config.py`).
#     DB_HOST · DB_NAME · DB_USER · DB_PASSWORD · INTERNAL_API_KEY · GEMINI_API_KEY
# 나머지는 기본값이 있습니다. 목록은 `.env.example` 입니다.

# ─────────────────────────────────────────────────────────────────
# 0단계 — 빌드와 실행이 함께 쓰는 바닥
# ─────────────────────────────────────────────────────────────────
# 🔴 시스템 라이브러리를 여기 한 번만 적습니다. 빌드 단계와 실행 단계에 따로 적으면
#    한쪽에만 있는 라이브러리가 생기고, 그러면 **빌드는 성공한 뒤 런타임에** 터집니다.
#    실제로 libGL 을 실행 단계에만 두었다가 모델 굽는 단계에서 걸렸습니다.
#      libGL·libglib  OpenCV(`import cv2`)
#      libgomp        Paddle 의 OpenMP 런타임
#      tzdata         TZ 를 줘도 이것이 없으면 조용히 UTC 로 돕니다
FROM python:3.12-slim AS base

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────
# 1단계 — 의존성과 애플리케이션 설치
# ─────────────────────────────────────────────────────────────────
FROM base AS builder

# uv 는 공식 이미지에서 실행 파일만 가져옵니다. 별도 설치 단계가 없어 빌드가 빠릅니다.
# 태그는 잠금 파일을 만든 버전에 맞춥니다.
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 잠금 파일만 먼저 넣고 의존성을 깝니다. 소스만 바뀐 빌드는 이 레이어를 재사용합니다
# (의존성 설치가 빌드 시간의 대부분입니다).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 애플리케이션 코드. `seeds/` 는 온보딩 제시 목록처럼 서빙이 읽는 값이 들어 있어 함께 넣습니다.
COPY src/ ./src/
COPY seeds/ ./seeds/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ─────────────────────────────────────────────────────────────────
# 2단계 — OCR 모델 가중치 굽기
# ─────────────────────────────────────────────────────────────────
# PaddleOCR 은 첫 사용 때 모델을 `~/.paddlex` 로 내려받습니다. 런타임에 맡기면
# ① 컨테이너가 뜰 때마다 약 98MB 를 받고 ② 외부 통신이 막힌 클러스터에서는 영영 받지
# 못해 `/health/ready` 가 계속 503 입니다 — 에러 없이 트래픽만 안 받습니다.
# 빌드 때 한 번 받아 이미지에 넣으면 같은 태그가 어디서 뜨든 같은 모델을 씁니다.
#
# 🔴 앱이 쓰는 함수(`_load_engine`)를 그대로 부릅니다. 여기서 인자를 따로 적으면
#    코드가 모델을 바꿨을 때 이미지에 구운 것과 실제로 쓰는 것이 갈라집니다.
FROM builder AS models

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src
RUN python -c "from features.receipt.pipeline.s2_ocr import _load_engine; _load_engine()" \
    && test -d /root/.paddlex/official_models

# ─────────────────────────────────────────────────────────────────
# 3단계 — 실행 이미지
# ─────────────────────────────────────────────────────────────────
FROM base AS runtime

# 헬스체크가 쓰는 것 하나만 더 깝니다. 빌드 도구는 이 이미지에 남기지 않습니다.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

# root 로 돌리지 않습니다. 홈 디렉터리를 두는 이유는 PaddleOCR 이 `~/.paddlex` 를 보기 때문입니다.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --from=builder --chown=app:app /app/seeds /app/seeds
COPY --from=models  --chown=app:app /root/.paddlex /home/app/.paddlex

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 컨테이너의 '오늘' 이 한국 날짜여야 합니다. UTC 로 두면 자정~오전 9시 사이에
    # 소비기한 임박(D-3) 판정이 하루씩 어긋납니다 (compose 의 postgres 와 같은 이유).
    TZ=Asia/Seoul \
    # 워커 하나가 상주 메모리 약 2.5GB 를 씁니다. 컨테이너 기본값은 1 로 두고,
    # 메모리를 넉넉히 준 노드에서만 올립니다.
    OCR_WORKERS=1

USER app
EXPOSE 8000

# 프로세스 생존만 봅니다. 모델 로딩이 끝났는지는 `/health/ready` 이고, 그쪽은
# 오케스트레이터의 readinessProbe 가 봐야 합니다 — 여기서 ready 를 보면 로딩 중에
# 컨테이너가 죽습니다.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

# 🔴 워커는 하나입니다. 앱이 프로세스 안에서 OCR 프로세스 풀을 따로 띄우므로,
#    uvicorn 워커를 늘리면 그 풀이 워커 수만큼 생겨 메모리가 배로 듭니다.
#    수평 확장은 레플리카로 합니다.
CMD ["uvicorn", "main:create_app", "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]
