---
name: add-endpoint
description: "Add a REST endpoint: route, schema, RBAC row, tests and regenerated frontend types."
---
# Add an endpoint

1. Pydantic request/response models in `src/collections_copilot/api/schemas.py`.
2. Route in `api/routers/<area>.py` with `Depends(require_role(...))`; errors are RFC 9457 problem details; mutating routes accept `Idempotency-Key`.
3. Add the route to the RBAC matrix in `tests/integration/test_rbac.py` (the matrix test fails for unlisted routes).
4. Integration test for the happy path and each documented error.
5. `make gen-types` to regenerate `web/lib/api/schema.d.ts`; commit both.
