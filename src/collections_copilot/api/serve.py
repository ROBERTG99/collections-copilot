"""API container entrypoint: `CC_API_WORKERS` uvicorn processes behind one port.

The API is CPU-bound per process (NFR-1, docs/DECISIONS.md), so the container runs several. With
more than one, Prometheus multiprocess mode is switched on before the workers start, and values a
previous run left in the directory (its *.db files) are removed so counters start from zero with the container.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from collections_copilot.config import Settings, get_settings

BIND_ALL = "0.0.0.0"  # noqa: S104 - container port, published only on localhost by compose
PORT = 8310
METRICS_DIR = "/tmp/cc-prometheus"  # noqa: S108 - the container's writable /tmp volume


def _reset(path: Path) -> None:
    """Remove the previous run's value files only: the directory may be one the launcher does
    not own (PROMETHEUS_MULTIPROC_DIR set from outside), so nothing else in it is touched."""
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    for stale in path.glob("*.db"):
        stale.unlink(missing_ok=True)


def main(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.api_workers > 1 or os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        path = Path(os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", METRICS_DIR))
        _reset(path)
    uvicorn.run(
        "collections_copilot.api.main:app",
        factory=True,
        host=BIND_ALL,
        port=PORT,
        workers=settings.api_workers,
        proxy_headers=True,
        server_header=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
