---
name: demo-capture
description: "Capture the demo video and screenshots with Playwright against the running stack."
---
# Demo capture

1. `make up && make seed`.
2. `cd web && pnpm exec playwright test tests/e2e/demo.spec.ts` (records `docs/images/demo.webm`).
3. `pnpm exec playwright test tests/e2e/screenshots.spec.ts` (writes the screenshots to `docs/images/`).
4. Never fabricate screenshots; commit only files Playwright produced.
