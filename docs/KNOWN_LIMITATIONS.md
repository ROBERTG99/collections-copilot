# Known limitations

What is not done, or done with a documented compromise. Updated as the build progresses.

| Area | Limitation | Why / mitigation |
|---|---|---|
| Sibling dependencies | evalgate and mcp-gateway are consumed through contract-subset fallbacks in `third_party/` because their `v0.1.0` tags and images do not exist yet. | Listed under "Needs the maintainer" in `PROGRESS.md`; switch to the published versions when they exist. |
| Data model | Child tables reference parents by `id` only; there are no composite `(tenant_id, id)` foreign keys, so the database alone does not stop a row from pointing at another tenant's parent. | Every write goes through tenant-scoped repositories that take the tenant from the authenticated principal (`tests/integration/db/test_tenant_scoping.py`). Composite FKs are a hardening follow-up. |
| Audit | The hash chain detects modified or reordered rows, but deleting trailing events with the trigger disabled (database owner) is only detectable if the head `(seq, hash)` was exported earlier. | Runbook step: export the head hash daily with `cc audit verify`; an external anchor (e.g. S3 Object Lock) is a production follow-up. |
| Seed loader | Reloading after the gen.v1 digest changes appends notes again instead of replacing them. | The digest is stable for seed 4242; a changed generator needs `make clean` first. |
| Ingest | Aging re-imports never reopen a disputed invoice; there is no API yet to resolve a dispute explicitly. | Disputes are cleared in the source ERP; resolving them in the console is out of scope (§3.3). |
