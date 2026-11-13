# Isolated environment: Python (harness) and Node 26 (OpenClaw).
# The isolation is set at runtime: see docker-compose.yml and nftables_allowlist.sh.

FROM node:26-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:${PATH}" PYTHONPATH=/app

# Fixed version: the one that ran the campaign.
RUN npm install -g openclaw@2026.9.1 --allow-scripts=openclaw

COPY . /app

# User 'node' has uid 1000, the owner of results/ on the host.
USER node

CMD ["python3", "-m", "unittest", "discover", "-s", "tests"]
