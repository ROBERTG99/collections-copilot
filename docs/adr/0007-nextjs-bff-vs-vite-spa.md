# 0007. Next.js BFF versus a Vite SPA

- Status: accepted
- Date: 2026-10-17

## Context

The console is used by analysts, supervisors and admins to approve SMS that go to debtors and promises that go to the ledger. An access token in the browser lets any script on the page act as that user. The identity provider is dev-oidc locally and Cognito in AWS; the console's Cognito client is public, with PKCE and no secret (docs/DECISIONS.md, 2026-10-17). The API accepts access tokens only, and in AWS the tenant reaches the access token through a Cognito Pre Token Generation trigger (`infra/terraform/modules/cognito/lambda/pre_token.py`; docs/DECISIONS.md, 2026-10-18). The API also serves SSE progress streams, which the console must read.

## Decision

- The console is a Next.js app that is also the backend-for-frontend (`web/`). It runs the authorization-code flow with PKCE and a nonce on the server, verifies the ID token's signature, audience and nonce (`web/lib/oidc.ts:80-106`), and keeps the tokens in an encrypted, httpOnly session cookie (JWE `dir` + A256GCM, `web/lib/session.ts:1-4`, `web/lib/server-session.ts:23-33`).
- The browser calls only `/api/*` on the BFF. The proxy adds the bearer token server-side, refuses `/api/v1/webhooks/*` and path tricks, requires same-origin plus an `x-cc-csrf` header on mutating requests, caps bodies at the API's limit, starts the trace, and streams responses through unchanged so SSE works (`web/app/api/[...path]/route.ts:35-77`, `web/lib/proxy.ts:1-16`).
- Token refresh is single-flight per refresh token, because dev-oidc and Cognito refresh tokens are single-use (`web/lib/oidc.ts:110-129`; docs/DECISIONS.md, 2026-10-17).
- In AWS the ALB sends everything except Twilio's webhooks to the web service (`infra/terraform/envs/dev/main.tf:218-232`), so the API has no public browser route. The image is the Next.js standalone build (`web/Dockerfile`).

## Alternatives considered

- **A Vite SPA calling the API directly.** Rejected per §5 (stack table: "server-side session"). With a public PKCE client, the SPA would hold the access and refresh tokens in browser memory or storage, where any injected script can read them. The API would need CORS and a public route on the ALB, and the CSRF, body-size and webhook-path checks now in the BFF would move to the API or disappear. Static hosting would also need its own infrastructure (a bucket and a CDN) alongside the ECS services.
- **A Vite SPA with a separate token-holding proxy.** Rejected. It is a BFF anyway, but split across two codebases and two deployables; Next.js gives the console and the BFF one build and one image, tested together by Vitest and Playwright.

## Consequences

- Tokens never reach client-side JavaScript, and the API can stay private behind the BFF.
- The BFF is stateful in one respect: the single-flight refresh map lives in one process, so several web tasks need sticky sessions or a shared store, and logout does not revoke the refresh token (docs/KNOWN_LIMITATIONS.md, BFF session).
- Next's App Router inlines bootstrap scripts, so the CSP keeps `script-src 'self' 'unsafe-inline'` (`web/next.config.ts:12`; docs/DECISIONS.md, 2026-10-17). The console renders no user HTML and holds no tokens, which limits what an injected script could do.
- The BFF sends a `traceparent` but exports no span of its own (docs/KNOWN_LIMITATIONS.md, Trace root).
- Every API change that the browser uses passes through the proxy; a new header the console needs must be added to its forward lists (`web/app/api/[...path]/route.ts:14-23`).
