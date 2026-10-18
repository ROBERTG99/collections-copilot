"""T-50 (§18, rule 6): the documents exist, every reported number cites what produced it, the
design FAQ points at real code, ADRs name the rejected alternative, and relative links resolve."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SECTION_18 = [
    "README.md",
    "docs/architecture.md",
    "docs/ai-development.md",
    "docs/evaluation.md",
    "evals/REPORT.md",
    "docs/security.md",
    "docs/runbook.md",
    "docs/DEVELOPMENT.md",
    "docs/design-faq.md",
    "docs/KNOWN_LIMITATIONS.md",
    "docs/verification.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "LICENSE",
]
ADRS = {
    "0001": "bedrock",
    "0002": "chatbedrock",
    "0003": "fargate",
    "0004": "sqs",
    "0005": "compliance",
    "0006": "checkpointer",
    "0007": "bff",
    "0008": "terraform",
    "0009": "elasticmq",
}
# Documents that report results. DECISIONS, KNOWN_LIMITATIONS and PROGRESS are logs that carry
# their own evidence; REPORT.md is generated.
REPORTING = [
    "README.md",
    "docs/architecture.md",
    "docs/ai-development.md",
    "docs/evaluation.md",
    "docs/design-faq.md",
    "docs/runbook.md",
    "docs/verification.md",
    "docs/security.md",
    "docs/DEVELOPMENT.md",
]
# A measured result: a percentage, a duration, a rate, or a score with three or more decimals.
# A number right after a comparison sign is a budget or threshold, not a result.
METRIC = re.compile(
    r"(?<![<>≤≥=~])(?<![<>≤≥=~] )\b\d+(?:[.,]\d+)?\s?(?:%|ms\b|req/s\b|s\b)|(?<![\d.])0\.\d{3,}\b"
)
CITATION = re.compile(
    r"`(?:make|uv run|pnpm|terraform|docker|k6|cd web|\./scripts/)[^`]*`"
    r"|evals/REPORT\.md|evals/reports/|docs/verification\.md|PROGRESS\.md|Source:"
)
FILE_LINE = re.compile(r"`([\w./\[\]()-]+\.(?:py|ts|tsx|tf|hcl|yaml|yml|md|sh)):(\d+)(?:-(\d+))?`")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def sections(text: str) -> list[str]:
    """Markdown sections split at headings; fenced code is dropped."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.split(r"(?m)^#{1,6} ", text)


def test_every_section_18_document_exists() -> None:
    assert [f for f in SECTION_18 if not (ROOT / f).is_file()] == []


def test_adrs_name_the_rejected_alternative() -> None:
    adrs = sorted((ROOT / "docs/adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    found = {p.name[:4]: p for p in adrs if p.name[:4] != "0000"}
    for number, topic in ADRS.items():
        assert number in found, f"ADR {number} ({topic}) is missing"
        text = found[number].read_text()
        assert topic in found[number].name.lower(), found[number].name
        assert "## Alternatives considered" in text, found[number].name
        assert "Rejected" in text.split("## Alternatives considered", 1)[1], found[number].name


def test_numbers_cite_sources() -> None:
    uncited = []
    for name in REPORTING:
        for section in sections((ROOT / name).read_text()):
            if METRIC.search(section) and not CITATION.search(section):
                heading = section.splitlines()[0][:60] if section.strip() else "(top)"
                uncited.append(f"{name} § {heading}: {METRIC.search(section).group(0)!r}")  # type: ignore[union-attr]
    assert uncited == []


def test_design_faq_answers_point_at_code() -> None:
    text = (ROOT / "docs/design-faq.md").read_text()
    questions = re.split(r"(?m)^## ", text)[1:]
    assert len(questions) == 15
    for q in questions:
        refs = FILE_LINE.findall(q)
        assert refs, f"no file:line reference under: {q.splitlines()[0]}"
        for path, start, end in refs:
            target = ROOT / path
            assert target.is_file(), path
            length = len(target.read_text().splitlines())
            assert int(end or start) <= length, f"{path}:{start} is past the end ({length} lines)"


def test_relative_links_resolve() -> None:
    docs = [ROOT / n for n in SECTION_18 if n.endswith(".md")]
    docs += sorted((ROOT / "docs/adr").glob("*.md"))
    broken = []
    for doc in docs:
        for target in LINK.findall(doc.read_text()):
            if re.match(r"^[a-z]+:", target):
                continue  # http(s), mailto
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(ROOT)} → {target}")
    assert broken == []


def test_readme_shows_the_reviewer_path() -> None:
    text = (ROOT / "README.md").read_text()
    for needed in (
        "make up",
        "http://localhost:8300",
        "ana.analyst",
        "docker compose up -d --wait",
        "docker compose run --rm seed",
        "docs/images/demo.webm",
    ):
        assert needed in text, needed
