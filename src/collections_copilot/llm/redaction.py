"""PII redaction before any provider call (NFR-10).

Presidio drives detection with offline recognizers only: regex recognizers for phone numbers and
emails (Presidio's own phone recognizer rejects the reserved 555 ranges, and its email recognizer
downloads the public-suffix list), its credit-card and IBAN recognizers, and an ad-hoc deny list
per account (company name, its distinctive prefix, contact names, the account phone). Detected
spans become stable placeholders; `restore` puts the account name back into model output.
"""

from __future__ import annotations

import json
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
# Phone-number boundaries and separators: not inside a word, an amount or an identifier.
_B = r"(?<!\w)(?<!\d[.,/-])"  # "Tel.555…" is a number; "1.555…" is part of one
_A = r"(?!\w|[.,/-]\d)"
_S = r"[\s./_\-\u2010-\u2015\u2212]"
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
                    # National forms (T-51 security audit M2): grouped with spaces, dots, dashes,
                    # slashes or Unicode dashes, or unseparated; a trailing "." or "," ends a
                    # sentence, while a following digit means an amount or a date.
                    Pattern("nanp", _B + r"\(?\d{3}\)?" + _S + r"?\d{3}" + _S + r"\d{4}" + _A, 0.6),
                    Pattern(
                        "nanp_3322", _B + rf"\d{{3}}{_S}\d{{3}}{_S}\d{{2}}{_S}\d{{2}}" + _A, 0.6
                    ),
                    Pattern(
                        "es_local_3222", _B + rf"\d{{3}}{_S}\d{{2}}{_S}\d{{2}}{_S}\d{{2}}" + _A, 0.6
                    ),
                    Pattern("es_local_333", _B + rf"\d{{3}}{_S}\d{{3}}{_S}\d{{3}}" + _A, 0.5),
                    Pattern("split_3_7", _B + rf"\d{{3}}{_S}\d{{7}}" + _A, 0.5),
                    Pattern("digits", _B + r"\+?\d{9,15}" + _A, 0.5),
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
    # The account's own identifiers (invoice numbers, reference): never redacted as phone
    # numbers, since drafts and citations need them (T-51 re-review).
    keep: tuple[str, ...] = ()

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
            digits = self.phone_e164.lstrip("+")
            terms.append(("PHONE_NUMBER", digits))
            for code in ("1", "34"):  # the national number, as debtors and notes write it
                if digits.startswith(code) and len(digits) - len(code) >= 9:
                    terms.append(("PHONE_NUMBER", digits[len(code) :]))
        # The prompt is redacted after rendering, which escapes the values: match each term the
        # way the JSON context and the note fence write it too (T-51 security audit M1).
        return [(e, form) for e, term in terms for form in dict.fromkeys(_escaped_forms(term))]


def _escaped_forms(term: str) -> list[str]:
    printable = "".join(ch for ch in term if ch.isprintable())  # what the note fence keeps
    fenced = printable.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    as_json = json.dumps(term, ensure_ascii=False)[1:-1]
    as_json = as_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    as_html = term.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return [term, as_json, as_html, printable, fenced]


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
            pattern = re.escape(term)
            if entity == "PHONE_NUMBER" and re.fullmatch(r"\+?\d+", term):
                # The account's own number in any spacing: up to two separators between digits.
                pattern = r"(?<!\d)\+?" + r"\D{0,2}".join(term.lstrip("+")) + r"(?!\d)"
            for m in re.finditer(pattern, text, re.I):
                spans.append((m.start(), m.end(), entity))
        results = _engine().analyze(
            text=text,
            language=language if language in LANGUAGES else "en",
            entities=list(self.entities),
            score_threshold=self.score_threshold,
        )
        keep = {k for k in pii.keep if k} if pii else set()
        spans += [
            (r.start, r.end, r.entity_type) for r in results if text[r.start : r.end] not in keep
        ]
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
