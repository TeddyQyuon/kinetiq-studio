# Build from the repository root: docker build -f workers/render/Dockerfile .
FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update && apt-get install -y --no-install-recommends chromium ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/kinetiq
COPY package*.json ./
COPY apps/web/package.json apps/web/
COPY packages packages
RUN npm ci --workspace @kinetiq/remotion-studio --no-audit --no-fund \
    && npm install --no-save --package-lock=false --no-audit --no-fund --workspaces=false @remotion/cli@4.0.547 \
    && npm cache clean --force

COPY workers/agent-core workers/agent-core
RUN pip install --no-cache-dir ./workers/agent-core
COPY workers/render workers/render
ENV REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium
CMD ["python", "workers/render/runner.py"]
