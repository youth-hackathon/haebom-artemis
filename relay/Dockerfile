# 해봄 artemis 서버. 저장소 맨 위에서 만든다: docker compose build artemis
FROM python:3.12-slim AS deps
# git: artemis의 의존성 일부를 저장소에서 받는다. libgl1/libglib2.0-0: OpenCV(artemis의 의존성).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
# 서버가 쓰는 파이썬 환경. artemis와 그 의존성을 고정된 버전으로 설치한다(relay/uv.lock).
COPY relay/pyproject.toml relay/uv.lock ./
RUN uv sync --frozen --no-dev

FROM deps
# 이 저장소의 artemis 코드를 쓴다. ARTEMIS_HOME이 설치된 패키지보다 앞에 놓으므로, artemis/ 를 고치면 서버에 반영된다.
COPY artemis/ artemis/
COPY third_party/ third_party/
COPY relay/ relay/
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 GUIDE_HOST=0.0.0.0 GUIDE_PORT=8765 GUIDE_LOG_DIR=/data/logs ARTEMIS_HOME=/app
EXPOSE 8765
CMD ["uv", "run", "--frozen", "--no-dev", "python", "relay/server.py"]
