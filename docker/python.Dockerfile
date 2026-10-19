# syntax=docker/dockerfile:1.7
# Python images for collections-copilot: targets api, worker, ledger-mcp, dev-oidc, debtor-sim,
# mcp-gateway (fallback), tools (migrate/seed/tests). Non-root (UID 10001), offline at runtime.
ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE} AS builder
ARG HTTPS_PROXY
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# git fetches the pinned evalgate tag (a dev dependency: only the test stages install it).
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && trash /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY apps/dev-oidc/pyproject.toml apps/dev-oidc/pyproject.toml
COPY apps/ledger-mcp/pyproject.toml apps/ledger-mcp/pyproject.toml
COPY apps/debtor-sim/pyproject.toml apps/debtor-sim/pyproject.toml
COPY third_party/mcp-gateway/pyproject.toml third_party/mcp-gateway/pyproject.toml
RUN for d in apps/dev-oidc/src/dev_oidc apps/ledger-mcp/src/ledger_mcp apps/debtor-sim/src/debtor_sim \
      third_party/mcp-gateway/src/mcpgw_lite src/collections_copilot data; \
    do mkdir -p "$d" && touch "$d/__init__.py"; done \
 && uv sync --frozen --no-install-workspace --all-packages --no-dev
COPY src src
COPY data data
COPY apps apps
COPY third_party third_party
RUN uv sync --frozen --all-packages --no-dev

FROM builder AS test-builder
ARG HTTPS_PROXY
RUN uv sync --frozen --all-packages

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# The runtime never installs packages: drop the base image's pip and ensurepip, whose vendored
# copies of urllib3, msgpack and setuptools carry known CVEs (docs/security.md).
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app \
 && rm -rf /usr/local/lib/python3.13/site-packages/pip /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.13/ensurepip /usr/local/bin/pip /usr/local/bin/pip3*
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --chown=app:app prompts prompts
COPY --chown=app:app migrations migrations
COPY --chown=app:app alembic.ini alembic.ini
COPY --chown=app:app deploy/mcpgw deploy/mcpgw
USER 10001

FROM runtime AS api
EXPOSE 8310
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8310/healthz', timeout=2).status == 200 else 1)"]
# CC_API_WORKERS uvicorn processes on 8310 (collections_copilot/api/serve.py).
CMD ["python", "-m", "collections_copilot.api.serve"]

FROM runtime AS worker
EXPOSE 8311
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8311/healthz', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "collections_copilot.worker.main"]

FROM runtime AS ledger-mcp
EXPOSE 8343
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8343/healthz', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "ledger_mcp.server"]

FROM runtime AS dev-oidc
EXPOSE 8345
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8345/healthz', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "dev_oidc.app"]

FROM runtime AS debtor-sim
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
  CMD ["python", "-c", "import os,sys; sys.exit(0 if os.path.exists('/tmp/debtor-sim.alive') else 1)"]
CMD ["python", "-m", "debtor_sim.main"]

FROM runtime AS mcp-gateway
EXPOSE 8344 8348 8349
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8344/healthz', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "mcpgw_lite.server"]

FROM ${PYTHON_IMAGE} AS tools
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# The runtime never installs packages: drop the base image's pip and ensurepip, whose vendored
# copies of urllib3, msgpack and setuptools carry known CVEs (docs/security.md).
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app \
 && rm -rf /usr/local/lib/python3.13/site-packages/pip /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.13/ensurepip /usr/local/bin/pip /usr/local/bin/pip3* \
 && install -d -o app -g app /app
# The test suites write caches and scratch files under the repository root (/app).
WORKDIR /app
COPY --from=test-builder --chown=app:app /app /app
COPY --chown=app:app . .
# pip arrives only through pip-audit (a dev tool run on the host); drop it with its CVEs.
RUN rm -rf /app/.venv/lib/python3.13/site-packages/pip /app/.venv/lib/python3.13/site-packages/pip-*.dist-info
USER 10001
HEALTHCHECK NONE
CMD ["cc", "--help"]
