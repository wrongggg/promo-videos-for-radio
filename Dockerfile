# HyperFrames' local render mode uses a bundled Puppeteer/Chromium, so we
# build on Puppeteer's own maintained image -- it's guaranteed to have the
# right system libraries for headless Chrome, which is by far the riskiest
# part of getting this to run in a container.
FROM ghcr.io/puppeteer/puppeteer:latest

USER root
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Pre-warm HyperFrames' own Chromium download during the image build (a
# network-dependent step) rather than on the first real render request.
# The version is read out of package.json, never written here: pre-warming a
# version the server does not run just moves the download back into the first
# render, and a hardcoded copy here is one `hyperframes upgrade` away from
# being wrong (the upgrade only rewrites package.json).
COPY package.json ./
RUN npx --yes "hyperframes@$(node -p "require('./package.json').scripts.render.match(/hyperframes@([0-9.]+)/)[1]")" --version || true

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

# /data is where a Railway volume should be mounted for renders/, analytics,
# admins.json and user cookies to survive redeploys -- see DATA_DIR in
# server/app.py, server/analytics.py, server/users.py.
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8080

# Single worker/process (the app keeps in-flight jobs in an in-process dict,
# not a database -- multiple worker processes would each have their own,
# breaking status polling). --threads lets concurrent requests still be
# served fine since Python releases the GIL around the I/O-bound work
# (subprocess calls to ffmpeg/yt-dlp/npx) that dominates a render job.
CMD gunicorn --chdir server --bind 0.0.0.0:${PORT:-8080} --worker-class gthread --workers 1 --threads 8 --timeout 180 app:app
