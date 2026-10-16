---
paths:
  - "docs/**"
  - "README.md"
  - "*.md"
---
# Documentation rules

- Every number in README and docs cites the command or report that produced it.
- ADRs live in `docs/adr/NNNN-title.md` and name the rejected alternative; ambiguous choices go in `docs/DECISIONS.md`.
- Markdown passes `pnpm run lint:md`; English only.
