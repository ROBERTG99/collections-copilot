from typing import Any


def echo(case_input: dict[str, Any]) -> dict[str, Any]:
    return {"label": case_input["label"]}
