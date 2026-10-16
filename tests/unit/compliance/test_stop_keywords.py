"""FR-8: STOP keywords are recognised by rule in English and Spanish."""

import pytest

from collections_copilot.compliance.stop_keywords import is_stop


@pytest.mark.parametrize(
    "body",
    [
        "STOP",
        "stop",
        " Stop. ",
        "UNSUBSCRIBE",
        "cancel",
        "BAJA",
        "Alto",
        "parar",
        "STOP please",
        "No más",
        "dar de baja",
        "STOP!!!",
    ],
)
def test_stop_variants(body: str) -> None:
    assert is_stop(body)


@pytest.mark.parametrize(
    "body",
    [
        "I can pay 300 on Friday",
        "Please don't stop the service, I will pay",
        "",
        "   ",
        "We stopped the order because the goods were damaged and ask for credit",
    ],
)
def test_not_stop(body: str) -> None:
    assert not is_stop(body)
