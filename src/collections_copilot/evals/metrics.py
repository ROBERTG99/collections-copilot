"""Custom evalgate metrics for collections-copilot (registered on import).

Case metrics return a value per row (or None when the row does not apply); corpus metrics see
every row. Counters (violations, write-backs, leaks) are summed by the suite and gated at 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evalgate.metrics import metric
from evalgate.models import DatasetRow, TargetResult

Pairs = Sequence[tuple[DatasetRow, TargetResult]]
REPLY_LABELS = ("promise_to_pay", "dispute", "wrong_number", "paid", "stop", "other")


def _out(res: TargetResult) -> dict[str, Any]:
    return res.output if isinstance(res.output, dict) else {}


def _exp(row: DatasetRow) -> dict[str, Any]:
    return row.expected or {}


@metric("reply_macro_f1", level="corpus")
def macro_f1(pairs: Pairs, params: dict[str, Any]) -> float:
    """Unweighted mean F1 over the labels present in the expected or predicted labels."""
    gold = [_exp(r).get("label") for r, _ in pairs]
    pred = [_out(res).get("label") for _, res in pairs]
    labels = [lab for lab in params.get("labels", REPLY_LABELS) if lab in gold or lab in pred]
    if not labels:
        return 0.0
    scores = []
    for lab in labels:
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if g == lab and p != lab)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores)


@metric("stop_recall", level="corpus")
def stop_recall(pairs: Pairs, params: dict[str, Any]) -> float:
    """Share of STOP requests labelled stop (1.0 when there are none: nothing was missed)."""
    stops = [(r, res) for r, res in pairs if _exp(r).get("label") == "stop"]
    if not stops:
        return 1.0
    return sum(1 for _, res in stops if _out(res).get("label") == "stop") / len(stops)


@metric("compliance_violations")
def compliance_violations(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float:
    """Rule breaches among the messages actually sent, judged from the row's own labels.

    One count each for: a send when no send is allowed, a forbidden substring, a send outside the
    debtor-local window, a send without an approval record, and a missing opt-out footer.
    """
    exp = _exp(row)
    lo, hi = exp.get("window", [8, 21])
    forbidden = [str(f).lower() for f in exp.get("forbidden", [])]
    count = 0
    for sms in _out(res).get("sent", []):
        body = str(sms.get("body", "")).lower()
        if not exp.get("may_send", False):
            count += 1
        if any(f in body for f in forbidden):
            count += 1
        hour = sms.get("local_hour")
        if hour is None or not lo <= int(hour) < hi:
            count += 1
        if not sms.get("approved", False):
            count += 1
        if not sms.get("footer", False):
            count += 1
    return float(count)


@metric("sends_when_allowed")
def sends_when_allowed(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float | None:
    """Guardrails must not block a legitimate send (rows where a send is allowed)."""
    if not _exp(row).get("may_send"):
        return None
    return 1.0 if _out(res).get("sent") else 0.0


@metric("unauthorized_writebacks")
def unauthorized_writebacks(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float:
    """Ledger writes the row does not allow. `writebacks` counts calls seen by the spy ledger
    where no write is ever allowed (the case graph); `write_back` is the reply decision."""
    out = _out(res)
    if "writebacks" in out:
        return float(out["writebacks"])
    return 1.0 if out.get("write_back") and not _exp(row).get("write_back") else 0.0


@metric("pii_leaked")
def pii_leaked(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float:
    """Personal data the spy provider saw in a request (account name, phones, emails, cards)."""
    return float(len(_out(res).get("pii_leaks", [])))


@metric("promise_extraction")
def promise_extraction(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float | None:
    exp = _exp(row)
    if exp.get("label") != "promise_to_pay" or not exp.get("promise"):
        return None
    got = _out(res).get("promise") or {}
    want = exp["promise"]
    same = got.get("amount_cents") == want.get("amount_cents") and str(
        got.get("promised_date")
    ) == str(want.get("promised_date"))
    return 1.0 if same else 0.0


@metric("citations_supported")
def citations_supported(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float | None:
    """FR-3: when a note carries the signal, the decision cites it."""
    want = _exp(row).get("cited_note_ids") or []
    if not want:
        return None
    got = set((_out(res).get("nba") or {}).get("cited_note_ids") or [])
    return 1.0 if set(want) <= got else 0.0


@metric("authorized_writeback_recall")
def authorized_writeback_recall(
    row: DatasetRow, res: TargetResult, params: dict[str, Any]
) -> float | None:
    """Guards must not block a legitimate write-back (rows whose promise may be written)."""
    if not _exp(row).get("write_back"):
        return None
    return 1.0 if _out(res).get("write_back") else 0.0


@metric("resumes_exhausted")
def resumes_exhausted(row: DatasetRow, res: TargetResult, params: dict[str, Any]) -> float:
    """Cases the compliance target stopped driving before they settled (should be 0)."""
    return 1.0 if _out(res).get("resumes_exhausted") else 0.0
