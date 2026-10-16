---
name: add-agent-tool
description: "Add a ledger tool through mcp-gateway: schema, gateway policy, idempotency key and eval case."
---
# Add an agent tool

1. Implement the tool in `apps/ledger-mcp/src/ledger_mcp/server.py` with a typed input schema and tenant from `_meta.mcpgw.tenant`.
2. Add it to `deploy/mcpgw/policy/collections.yaml` (and `collections.rego`) with its scope and approval rules.
3. Write tools need an idempotency key format (`<prefix>:<tenant>:<natural id>`) in `integrations/mcp/ledger.py`.
4. Contract test in `tests/contract/test_mcp_ledger_contract.py`; integration test through the gateway.
5. Add an eval case via the `add-eval-case` skill if the agent uses it. Models never receive tools directly.
