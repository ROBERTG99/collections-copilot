# syntax=docker/dockerfile:1.7
# The web console and BFF: a Next.js standalone build on node:22-trixie-slim (Debian 13, like the
# Python images; bookworm's perl-base carries unfixed-in-image CVEs), run as UID 10001.
# Build context is the repository root (the pnpm workspace and its lockfile live there).
ARG NODE_IMAGE=node:22-trixie-slim

FROM ${NODE_IMAGE} AS deps
ARG HTTPS_PROXY
ENV PNPM_HOME=/pnpm NEXT_TELEMETRY_DISABLED=1
ENV PATH=$PNPM_HOME:$PATH
RUN npm install -g --no-fund --no-audit pnpm@10.28.0
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json web/package.json
RUN pnpm install --frozen-lockfile --filter collections-copilot-web...

FROM deps AS build
COPY web web
RUN pnpm --filter collections-copilot-web run build

FROM ${NODE_IMAGE} AS web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=8300
# The runtime never installs packages: drop npm, npx, corepack and yarn with their vendored modules.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app \
 && rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
      /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
COPY --from=build --chown=app:app /repo/web/.next/standalone ./
COPY --from=build --chown=app:app /repo/web/.next/static ./web/.next/static
USER 10001
EXPOSE 8300
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:8300/healthz').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "web/server.js"]
