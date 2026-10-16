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
        "Please stop texting me",
        "STOP texting me about this invoice",
        "stop messaging me",
        "Stop contacting me, I already paid",
        "please unsubscribe me from these messages",
        "I want to UNSUBSCRIBE",
        "Por favor dejen de escribirme",
        "No me escriban más",
        "Quiero darme de baja",
        "BAJA por favor",
        "no más mensajes",
        "STOPALL",
        "opt out",
        # M2 re-review probes
        "STOP STOP",
        "STOP ALL",
        "Stop all",
        "Stop sending messages",
        "Stop the texts",
        "Don't text me again",
        "Do not contact me",
        "Remove me from your list",
        "opt me out",
        "Leave me alone",
        "STOP por favor",
        "Alto por favor",
        "Para",
        "Basta",
        "Deja de escribirme",
        "Paren de mandar mensajes",
        "Pare de escribirme",
        "No me molesten",
        "No quiero recibir mensajes",
        "Cancelar suscripción",
        "Me doy de baja",
        "Denme de baja",
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
        "End of month",
        "Cancel the invoice please",
        "Quit charging us late fees",
        "End of the week we pay",
        "Don't stop sending the invoices to AP",
        "The bus stop is where we receive deliveries",
        "Alto el fuego",
        # M2 re-review probes: these must not opt anyone out
        "No text received with the link",
        "I got no message with the payment link",
        "There was no SMS with the link",
        "no texts? I already paid",
        "Stop calling me, text me instead",
        "no me llamen, mejor escríbanme",
        "dont stop texting me, I will pay",
        "Para el viernes pago",
        "Do not stop the reminders",
    ],
)
def test_not_stop(body: str) -> None:
    assert not is_stop(body)
