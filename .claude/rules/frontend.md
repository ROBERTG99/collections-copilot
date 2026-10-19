---
paths:
  - "web/**"
---
# Frontend conventions

- Next.js App Router, TypeScript strict, TanStack Query over the generated `web/lib/api/schema.d.ts` (run `make gen-types` after API changes).
- The browser never holds tokens: all `/api/*` calls go through the BFF route handlers with the encrypted httpOnly session cookie.
- Tokens live in `web/styles/tokens.css` (light and dark "ledger paper"); no hard-coded colours in components.
- Every screen has loading skeleton, empty (with next action), error (`correlation_id` + retry) and success states.
- WCAG 2.2 AA: keyboard-complete, visible focus, labelled controls; every Playwright spec runs axe.
- Model output carries the "AI draft" chip and a numeric confidence; fallbacks show "Template".
