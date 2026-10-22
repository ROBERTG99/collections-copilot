# 0005. Deterministic compliance outside the LLM

- Status: accepted
- Date: 2026-10-16

## Context

The model reads untrusted text (account notes and debtor replies) and writes SMS drafts. Each draft can become a real message to a debtor, and each reply can become a ledger write-back. The rules that make a message lawful are facts about the account and the ledger, not judgement: opt-out, a flagged contact, the 7-day contact cap, the debtor-local contact window, disputed invoices, amounts that exist in the ledger, the opt-out footer, allowed links and forbidden phrases (FR-5). A note can contain an injected instruction, and the model reports its own confidence (docs/DECISIONS.md, 2026-10-17).

## Decision

- Compliance is plain Python in `src/collections_copilot/compliance/`: `engine.precheck` (account rules and the contact window, `src/collections_copilot/compliance/engine.py:105`) and `engine.postcheck` (also every text rule, `src/collections_copilot/compliance/engine.py:110-150`), with one module per rule in `src/collections_copilot/compliance/rules/`. The contact window never blocks; it defers the send to the next allowed debtor-local time (`src/collections_copilot/compliance/engine.py:90-102`).
- The engine cannot depend on the model: an import-linter contract forbids `collections_copilot.compliance` and `collections_copilot.scoring` from importing `collections_copilot.llm` or `collections_copilot.agent` (`pyproject.toml:166-170`).
- The engine runs at every step that leads to a side effect, each time on facts re-read from the database: before the model is called (`src/collections_copilot/agent/case_graph.py:268-272`), on each draft (`src/collections_copilot/agent/case_graph.py:384-387`), on the approved text after a human decision (`src/collections_copilot/agent/case_graph.py:478`), at send time (`src/collections_copilot/agent/case_graph.py:541`), and in the approval API before a decision is stored (`src/collections_copilot/api/routers/approvals.py:104-107`).
- The text rules fail closed: a number must be an invoice number, date, identifier, small count or the account name, or equal an open amount; every host not on the allowlist is flagged; letters outside the Latin script and invisible format characters block a draft (docs/DECISIONS.md, 2026-10-16 and 2026-10-17).
- Other side-effect decisions are also code: STOP by rule before any model call, in the webhook and the reply graph (`src/collections_copilot/compliance/stop_keywords.py`; `src/collections_copilot/api/routers/webhooks.py:152`; `src/collections_copilot/agent/reply_graph.py:185-188`); a promise is written back only if the debtor wrote that amount (`src/collections_copilot/agent/reply_graph.py:168`); auto-approval needs the deterministic house rules to choose the same reminder (`src/collections_copilot/agent/policy.py:58-90`).

## Alternatives considered

- **Ask the model to follow the rules and to check its own drafts, or use an LLM judge as the compliance gate.** Rejected. The model reads text an attacker can write, so an injected note could talk it out of a rule. Its output is not reproducible, so a gate built on it cannot promise zero violations, and the offline evals with FakeLLM measure the pipeline, not a model (docs/DECISIONS.md, 2026-10-16). The tone judge stays an eval tool only (`judge_tone`).
- **Check once, when the draft is created, and trust the checkpointed state afterwards.** Rejected. A dispute or a STOP that arrived between approval and a deferred send still let a reminder go out, and a crash between saving and checkpointing a draft could send text that differed from the approved row (docs/DECISIONS.md, 2026-10-16).

## Consequences

- Rules are tested like any other code, offline and with fixed clocks: Hypothesis properties for the window, the cap and opt-out (`tests/unit/compliance/test_engine_properties.py`), per-rule tests (`tests/unit/compliance/test_rules.py`, `tests/unit/compliance/test_unicode.py`), and integration tests that no path sends without an approval record (`tests/integration/test_no_send_without_approval.py`, `tests/integration/test_send_guards.py`).
- The compliance eval suite gates zero violations and zero unauthorized write-backs, and also gates `sends_when_allowed`, so a change that blocks everything does not pass (`evals/suites/compliance.yaml`; docs/DECISIONS.md, 2026-10-17).
- Fail-closed rules can block a lawful message, for example a draft that quotes an amount the engine cannot match. The draft is redrafted once with the violations and then reaches a human marked `requires_edit`; it is never sent as is.
- A new rule is a code change with tests, not a prompt change. If analysts often have to edit drafts for violations the rules flag, the prompts or the rules need attention; the rule set itself must not move into the prompt.
