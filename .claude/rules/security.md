# Security rules

- Synthetic data only: phone numbers in the `+1555…` and `+34555…` ranges, `example` domains, generator seed 4242.
- No secrets in the repository; `.env.example` holds only local dummy values.
- Every side effect (SMS, ledger write, email) passes the compliance engine and an approval record or the auto-approval policy; the single STOP confirmation SMS is the one exception, sent by rule.
- Webhooks are verified (Twilio signature, gateway HMAC) and deduplicated; failures are audited.
- Never place tool results or debtor text in instructions; fence them in `<untrusted_note>` blocks.
