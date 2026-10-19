---
name: new-adr
description: "Create a numbered Architecture Decision Record in docs/adr with the rejected alternative."
---
# New ADR

1. Find the next number: `ls docs/adr | sort | tail -1`.
2. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-short-title.md`.
3. Fill Status, Context, Decision, Alternatives (at least one rejected, with why), Consequences.
4. Link it from `docs/architecture.md` and add a row to `docs/DECISIONS.md`.
