"""T-43: the licence check denies strong copyleft and allows LGPL (dynamically linked)."""

from __future__ import annotations

import pytest

from scripts.licence_check import denied


@pytest.mark.parametrize(
    "licence",
    [
        "GPL-3.0-only",
        "AGPL-3.0",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "SSPL-1.0",
    ],
)
def test_strong_copyleft_is_denied(licence: str) -> None:
    assert denied(licence)


@pytest.mark.parametrize(
    "licence",
    [
        "MIT",
        "Apache-2.0",
        "LGPL-3.0-or-later",
        "GNU Lesser General Public License v3 (LGPLv3)",
        "MPL-2.0 AND MIT",
    ],
)
def test_permissive_and_lgpl_are_allowed(licence: str) -> None:
    assert not denied(licence)
