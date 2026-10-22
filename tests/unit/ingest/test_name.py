"""Defence in depth: an account name is a name, not markup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from collections_copilot.ingest.schemas import AgingRow
from tests.unit.ingest.test_amount import ROW


@pytest.mark.parametrize("name", ["Smith & Sons LLC", "O'Brien Trading", "Peña Hermanos S.L."])
def test_ordinary_names_are_accepted(name: str) -> None:
    assert AgingRow(**{**ROW, "name": name}, amount="10.00").name == name


@pytest.mark.parametrize(
    "name", ["<b>Acme</b>", "Acme {{system}}", "Back\\slash Co", "Acme\u0000Inc", "Acme‮Inc"]
)
def test_markup_and_control_characters_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError, match="name"):
        AgingRow(**{**ROW, "name": name}, amount="10.00")


@pytest.mark.parametrize(
    "raw", ["Acme\u00a0Corp", "Acme\u2009 Corp", "Acme\u3000Corp", "Acme  Corp"]
)
def test_unicode_spaces_become_one_plain_space(raw: str) -> None:
    """The note fence drops non-printable spaces, so a name with a
    no-break space no longer matched the redactor's deny list."""
    assert AgingRow(**{**ROW, "name": raw}, amount="10.00").name == "Acme Corp"
