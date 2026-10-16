"""PII redaction before any provider call (NFR-10).

Presidio drives detection with offline recognizers only: regex recognizers for phone numbers and
emails (Presidio's own phone recognizer rejects the reserved 555 ranges, and its email recognizer
downloads the public-suffix list), its credit-card and IBAN recognizers, and an ad-hoc deny list
per account (company name, its distinctive prefix, contact names, the account phone). Detected
spans become stable placeholders; `restore` puts the account name back into model output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngine
from presidio_analyzer.predefined_recognizers import CreditCardRecognizer, IbanRecognizer

LANGUAGES = ("en", "es")
LEGAL_SUFFIXES = re.compile(
    r"\s+(LLC|Inc\.?|Co\.?|Group|Partners|S\.L\.?|Ltd\.?|S\.A\.?|GmbH)$", re.I
)
PLACEHOLDER_PREFIX = {
    "ACCOUNT_NAME": "ACCOUNT_NAME",
    "PERSON": "PERSON",
    "PHONE_NUMBER": "PHONE",
    "EMAIL_ADDRESS": "EMAIL",
    "CREDIT_CARD": "CARD",
    "IBAN_CODE": "IBAN",
}


class _NoNlpEngine(NlpEngine):  # type: ignore[misc]
    """Pattern recognizers need no NLP model; keeps Presidio fully offline and deterministic."""

    def load(self) -> None:
        return None

    def is_loaded(self) -> bool:
        return True

    def process_text(self, text: str, language: str) -> NlpArtifacts:
        no_tokens: Any = []  # pattern recognizers never read tokens
        return NlpArtifacts(
            entities=[],
            tokens=no_tokens,
            tokens_indices=[],
            lemmas=[],
            nlp_engine=self,
            language=language,
        )

    def process_batch(
        self,
        texts: Iterable[str],
        language: str,
        batch_size: int = 1,
        n_process: int = 1,
        **kwargs: Any,
    ) -> Iterator[tuple[str, NlpArtifacts]]:
        for text in texts:
            yield text, self.process_text(text, language)

    def is_stopword(self, word: str, language: str) -> bool:
        return False

    def is_punct(self, word: str, language: str) -> bool:
        return False

    def get_supported_entities(self) -> list[str]:
        return []

    def get_supported_languages(self) -> list[str]:
        return list(LANGUAGES)


@cache
def _engine() -> AnalyzerEngine:
    registry = RecognizerRegistry(supported_languages=list(LANGUAGES))
    for lang in LANGUAGES:
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                supported_language=lang,
                patterns=[
                    Pattern("e164", r"\+\d[\d\s().-]{7,18}\d", 0.7),
                    Pattern("nanp", r"(?<!\w)\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)", 0.6),
                    # Spanish local format without +34: "555 12 34 56", "555 123 456"
                    Pattern("es_local_3222", r"(?<![\w.,])\d{3} \d{2} \d{2} \d{2}(?![\w.,])", 0.6),
                    Pattern("es_local_333", r"(?<![\w.,])\d{3} \d{3} \d{3}(?![\w.,])", 0.5),
                ],
            )
        )
        registry.add_recognizer(
            PatternRecognizer(
                supported_entity="EMAIL_ADDRESS",
                supported_language=lang,
                patterns=[Pattern("email", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", 0.9)],
            )
        )
        registry.add_recognizer(CreditCardRecognizer(supported_language=lang))
        registry.add_recognizer(IbanRecognizer(supported_language=lang))
    return AnalyzerEngine(
        registry=registry, nlp_engine=_NoNlpEngine(), supported_languages=list(LANGUAGES)
    )


@dataclass(frozen=True)
class AccountPII:
    name: str = ""
    phone_e164: str = ""
    contacts: tuple[str, ...] = ()

    def deny_terms(self) -> list[tuple[str, str]]:
        terms: list[tuple[str, str]] = []
        if self.name:
            terms.append(("ACCOUNT_NAME", self.name))
            short = LEGAL_SUFFIXES.sub("", self.name).strip()
            if short and short != self.name:
                terms.append(("ACCOUNT_NAME", short))
            words = short.split()
            if len(words) >= 2:
                terms.append(("ACCOUNT_NAME", " ".join(words[:2])))
        terms += [("PERSON", c) for c in self.contacts if c]
        if self.phone_e164:
            terms.append(("PHONE_NUMBER", self.phone_e164))
        return terms


@dataclass
class Redactor:
    """Replaces PII with placeholders; one instance can serve many calls (the engine is cached)."""

    score_threshold: float = 0.5
    entities: tuple[str, ...] = field(
        default=("PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IBAN_CODE")
    )

    def redact(
        self, text: str, pii: AccountPII | None = None, language: str = "en"
    ) -> tuple[str, dict[str, str]]:
        spans: list[tuple[int, int, str]] = []
        for entity, term in pii.deny_terms() if pii else []:
            for m in re.finditer(re.escape(term), text, re.I):
                spans.append((m.start(), m.end(), entity))
        results = _engine().analyze(
            text=text,
            language=language if language in LANGUAGES else "en",
            entities=list(self.entities),
            score_threshold=self.score_threshold,
        )
        spans += [(r.start, r.end, r.entity_type) for r in results]
        # Longest span wins on overlap.
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        merged: list[tuple[int, int, str]] = []
        for span in spans:
            if merged and span[0] < merged[-1][1]:
                continue
            merged.append(span)
        mapping: dict[str, str] = {}
        by_value: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        out, last = [], 0
        for start, end, entity in merged:
            original = text[start:end]
            prefix = PLACEHOLDER_PREFIX.get(entity, entity)
            if entity == "ACCOUNT_NAME":
                placeholder = "<ACCOUNT_NAME>"
                mapping.setdefault(placeholder, pii.name if pii else original)
            else:
                key = (entity, original.lower())
                if key not in by_value:
                    counters[prefix] = counters.get(prefix, 0) + 1
                    by_value[key] = f"<{prefix}_{counters[prefix]}>"
                placeholder = by_value[key]
                mapping[placeholder] = original
            out.append(text[last:start])
            out.append(placeholder)
            last = end
        out.append(text[last:])
        return "".join(out), mapping

    @staticmethod
    def restore(
        text: str, mapping: dict[str, str], allowed: tuple[str, ...] = ("<ACCOUNT_NAME>",)
    ) -> str:
        """Put back only allowed placeholders (the account name) in model output; others are removed."""
        for placeholder, original in mapping.items():
            text = text.replace(placeholder, original if placeholder in allowed else "")
        return re.sub(r"<(?:PHONE|EMAIL|CARD|IBAN|PERSON)_\d+>", "", text)
