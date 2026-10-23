# Shivuk: the Node hub plus the two Python agent services in one container.
# `node server.js` proxies /api/meta and /api/social to the Flask services and
# starts them from their venvs (meta-ads-agent/.venv, social-agent/.venv).
FROM node:24-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY . .
RUN for svc in meta-ads-agent social-agent; do \
      python3 -m venv $svc/.venv \
      && $svc/.venv/bin/pip install --no-cache-dir -r $svc/requirements.txt -e agentkit; \
    done \
 && mkdir -p data social-agent/data social-agent/media meta-ads-agent/logs social-agent/logs

ENV NODE_ENV=production PORT=4400 PYTHONUNBUFFERED=1
EXPOSE 4400
CMD ["node", "server.js"]
