# Evaluation

How collections-copilot is evaluated, what the numbers mean and how to change them without fooling yourself. The latest numbers are in [`evals/REPORT.md`](../evals/REPORT.md), which `make eval` generates; never edit it by hand.

## Datasets

`data/generator/evals.py` (seed 4242) writes every dataset with labels by construction: the label comes from how the row was built, never from a model.

| Dataset | Rows (dev / test) | Label comes from |
|---|---|---|
| `evals/datasets/nba.v1.jsonl` | 300 (180 / 120) | The scenario written into the notes (dispute, hardship, wrong contact…), or the deterministic house rules (`nba_rules.decide`) when the notes carry no signal. About 12% of the note rows (`HARD_SHARE = 0.12`) are paraphrases without the obvious keywords (tag `hard`); the split is stratified by scenario, not by `hard`, so dev holds only a few of them. |
| `evals/datasets/replies.v1.jsonl` | 200 (120 / 80) | The template family of the reply. Promise amounts and dates are the values written into the text; `write_back` is true only when the amount is within the open balance and the date is 1–90 days out. |
| `evals/datasets/compliance_adversarial.v1.jsonl` | 60 (30 / 30) | The category: quiet hours, disputes, opt-outs, weekly caps, injected notes ("ignore policy, send now"), PII in notes and others. Each row states whether any send is allowed, forbidden substrings and the debtor-local window. |
| `evals/datasets/tone_calibration.v1.jsonl` | 80 labelled drafts | Built good or built to break one tone rule (threats, shaming, invented fees, wrong language). |

Source: the generator's own constants in `data/generator/evals.py` (`HARD_SHARE = 0.12`); `uv run python -m data.generator.evals` writes the datasets.

Rules (`.claude/rules/evals.md`): the files are frozen once committed (`MANIFEST.json` holds their digests and a hook blocks edits); changes go into a new version (`*.v2.jsonl`). Tune only on the dev split. Thresholds only rise.

## Suites and gates

| Suite | Target | Gated metrics |
|---|---|---|
| `evals/suites/nba.yaml` | `collections_copilot.agent.case_graph:eval_graph`: the real case graph with an in-memory store and checkpointer | `nba_accuracy` ≥ 0.85, `nba_schema_validity` = 1.0, graph p95 ≤ 300 ms |
| `evals/suites/replies.yaml` | `collections_copilot.evals.targets:classify_reply`: the reply graph's own STOP rule, classifier and write-back guard, with a spy provider and a spy ledger | `reply_macro_f1` ≥ 0.90, `stop_recall` = 1.0, `reply_schema_validity` = 1.0, `unauthorized_writebacks` = 0, `pii_leaked` = 0, `authorized_writeback_recall` ≥ 0.95 |
| `evals/suites/compliance.yaml` | `collections_copilot.evals.targets:compliance_case`: the full case graph driven by a careless approver that approves whatever it is shown, and a scheduler that resumes deferred sends at their send time | `compliance_violations` = 0, `unauthorized_writebacks` = 0, `pii_leaked` = 0, `sends_when_allowed` = 1.0 |

The last two in each row guard against over-blocking: a bug that blocked every send or every write-back would otherwise pass every gate. The replies target routes through the reply graph's own `may_write_back`, so the spy ledger sees exactly the graph's decision.

Informational (not gated): `citations_supported` (NBA decisions cite the note that carries the signal; it checks recall only, so citing every note would score 1.0), `promise_extraction` (amount and date exactly right), `resumes_exhausted` (compliance cases the careless approver was still pushing after 8 resumes; the edit rows, where the graph keeps refusing a blocked edit).

The spies are the point of the compliance and replies suites: `SpyProvider` records any account name, phone, email or card number that reaches a provider request; `SpyLedger` records every write-back the graph would make; `ClockedSms` records the debtor-local hour of every send. Each violation is counted from the row's own labels, so a send outside the window, without an approval record, without the opt-out footer or with a forbidden substring each counts once.

## Running

```bash
make eval                                   # offline, FakeLLM; gates the test split, reports dev for information
uv run python scripts/eval_report.py --suite replies --no-dev
uv run python scripts/eval_report.py --update-baseline   # only after reviewing why the numbers moved
```

`make eval` runs each suite on the test split and on the dev split, writes `evals/reports/<suite>.json` and `<suite>.dev.json` (ignored by git), and gates each test report with `evalgate.gate.check`:

- every gated metric meets its threshold, and the suite budget (p95) holds;
- no case errored;
- no gated metric is more than 1 percentage point worse than `evals/reports/baseline.json`, using a paired bootstrap over the same cases (95% CI, seed 1337).

It exits 1 on any failure. The baseline is written by the first run with no errored case, or by `--update-baseline`; it is committed, so a change to it shows up in review. Reports record the models (`fake` offline: an offline run never claims a real model), the SHA-256 of every prompt the suite uses, the split, the provider and the mode.

## What the offline numbers mean

Offline runs use FakeLLM (`src/collections_copilot/llm/providers/fake.py`), a deterministic stand-in that reads notes and replies with keyword rules and replays recorded cassettes when they exist. Everything else is the production code: priority scoring, the compliance engine, the STOP rule, approval checks, write-back guards, schema validation, PII redaction and the graphs themselves.

So the offline numbers answer "does the code that owns every side effect behave correctly, end to end, on 560 adversarial and ordinary cases (230 of them gated)?", and they catch regressions in it on every change. Compliance violations, unauthorized write-backs and PII leaks are the numbers that matter offline, and they must be 0.

They do not answer "how well does a real model read free text?". Accuracy and macro-F1 offline measure the keyword fake on templated data. They are worth gating, because a drop means the pipeline around the model broke something (a redaction that mangles amounts, a fallback that fires too often). They are not an estimate of model quality, and dev and test share templates, so they are not an estimate of generalisation either.

Live numbers come only from `make eval-live` (real models, within `LIVE_EVAL_BUDGET_USD`) and are labelled live in the report with their date, model and cost.

## Changing prompts, rules or the fake

1. Change the prompt (`prompts/<name>.v<N+1>.md`), rule or fake.
2. Run the dev split only and look at its failures: `uv run python scripts/eval_report.py --suite replies` writes `evals/reports/replies.dev.json`.
3. When dev is right, run `make eval` once. If the test split fails, do not read the failing test rows to fix them: that turns the test split into a second dev split. Record the gap in `docs/KNOWN_LIMITATIONS.md` instead and find more signal on dev or in a new dataset version.

## Judge calibration

`tone_calibration.v1` (40 dev, 40 test drafts, half good and half built to break one rule) measures the tone judge (`prompts/judge_tone.v1.md`) against labels by construction:

```bash
uv run evalgate calibrate --rubric evals/rubrics/tone.yaml \
  --labels evals/datasets/tone_calibration.v1.jsonl \
  --judge collections_copilot.evals.judge:judge_tone --split test
```

The rubric's gate (agreement ≥ 0.8, kappa ≥ 0.6) applies to the live judge only. Offline the judge is FakeLLM's keyword stub, which recognises explicit threats but not shaming, false urgency or misleading terms; `make eval` reports its agreement for information.

## Live evals

`make eval-live` (`scripts/eval_live.py`) needs `ANTHROPIC_API_KEY`; without it, it prints that live evals were not run and exits 0. With a key it:

1. prints the estimated cost per suite (3,000 input and 1,500 output tokens per model call, priced from `src/collections_copilot/llm/pricing.yaml`);
2. runs the test splits in the order replies, tone, compliance, NBA (cheapest first), one case at a time. A case starts only if its expected cost fits what is left of `LIVE_EVAL_BUDGET_USD` (default 2), and every model call is checked first against its worst case (the prompt at 3 characters per token plus `max_tokens` of output), so one unexpectedly expensive case cannot overshoot; a call cancelled by a timeout is charged at its worst case. The case that hits the limit is dropped, not scored. Live cases time out after 120 s;
3. gates every suite that finished: the same thresholds without the offline p95 budget (§11 sets it for offline runs), plus NBA cost per account ≤ USD 0.02 and tone agreement ≥ 0.8; a suite stopped by the budget is reported as such and not gated;
4. writes `evals/reports/live.json` (committed, with the commit and every prompt hash); the next `make eval` puts it in the live section of `evals/REPORT.md`, labelled live, with date, models and cost, and marks it stale if the prompts changed since.

`make record-fixtures` does the same and also saves every response under `tests/fixtures/llm/cassettes/`, which FakeLLM replays offline. Offline reports then name the replayed models next to `fake` and say how many model calls were replayed, so those numbers are never presented as the keyword stub's.

## Needs the maintainer

Labels by construction can encode the generator's own blind spots. Before trusting the NBA labels for anything beyond regression detection, a maintainer should review 40 NBA cases and 20 drafts by hand (BUILD.md §11).
