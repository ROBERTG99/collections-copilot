"""Shared compliance types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    blocking: bool = True

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}
