---
name: release
description: "Prepare a local release: changelog, version bump and a local tag only (never pushed from a session)."
---
# Release (local only)

1. `make verify` must be green.
2. Update `CHANGELOG.md` (Keep a Changelog) and versions in `pyproject.toml` files and `web/package.json`.
3. Commit `chore(release): vX.Y.Z` and create a local annotated tag `git tag -a vX.Y.Z -m "vX.Y.Z"`.
4. Do not push tags; a project owner pushes them, which triggers `.github/workflows/release.yml`.
