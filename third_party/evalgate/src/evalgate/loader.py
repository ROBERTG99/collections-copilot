"""Suite and dataset loading with precise error locations (exit code 2 on invalid input)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from evalgate.models import DatasetRow, LabelRow, Rubric, Suite


class InvalidInputError(Exception):
    """Raised for invalid suites, datasets or rubrics; the CLI maps it to exit code 2."""


def load_suite(path: str | Path) -> Suite:
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise InvalidInputError(f"{p}: {exc}") from exc
    try:
        return Suite.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        raise InvalidInputError(f"{p}: {loc}: {first['msg']}") from exc


def load_dataset(path: str | Path, split: str | None = None) -> list[DatasetRow]:
    p = Path(path)
    rows: list[DatasetRow] = []
    seen: set[str] = set()
    for lineno, line in enumerate(p.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = DatasetRow.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidInputError(f"{p}:{lineno}: {exc}") from exc
        if row.id in seen:
            raise InvalidInputError(f"{p}:{lineno}: duplicate id {row.id!r}")
        seen.add(row.id)
        if split is None or row.split == split:
            rows.append(row)
    return rows


def load_rubric(path: str | Path) -> Rubric:
    p = Path(path)
    try:
        return Rubric.model_validate(yaml.safe_load(p.read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise InvalidInputError(f"{p}: {exc}") from exc


def load_labels(path: str | Path, split: str | None = None) -> list[LabelRow]:
    p = Path(path)
    out: list[LabelRow] = []
    for lineno, line in enumerate(p.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = LabelRow.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidInputError(f"{p}:{lineno}: {exc}") from exc
        if split is None or row.split == split:
            out.append(row)
    return out
