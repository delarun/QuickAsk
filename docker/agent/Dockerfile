# QuickAsk agent sandbox — образ собирает .github/workflows/docker.yml
# локально: docker build -t ghcr.io/delarun/quickask-agent:latest docker/agent
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl jq git rsync unzip ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp requests beautifulsoup4 mutagen

# runner монтируется с хоста (-v …/runner.py:/agent/runner.py:ro), чтобы обновлять без пересборки;
# копия внутрь — на случай запуска без монтирования
COPY runner.py /agent/runner.py
WORKDIR /work
ENTRYPOINT ["python3", "/agent/runner.py"]
