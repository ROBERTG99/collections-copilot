# Upstream mcp-gateway OPA bundle for collections-copilot. The fallback gateway in
# third_party/mcp-gateway evaluates the equivalent collections.yaml.
package mcpgw.authz

import rego.v1

allowed_tools := {
	"ledger.get_balance": "ledger:read",
	"ledger.list_open_invoices": "ledger:read",
	"ledger.record_promise_to_pay": "ledger:write",
	"ledger.add_collection_note": "ledger:write",
}

default decision := {"effect": "deny", "reasons": ["not_allowed"]}

scope_ok if {
	input.agent == "cc-worker"
	allowed_tools[input.tool] in input.scopes
}

approval_reasons contains "amount_above_500000_cents" if {
	input.tool == "ledger.record_promise_to_pay"
	input.args.amount_cents > 500000
}

approval_reasons contains "promised_date_beyond_60_days" if {
	input.tool == "ledger.record_promise_to_pay"
	days := (time.parse_ns("2006-01-02", input.args.promised_date) - time.now_ns()) / 86400000000000
	days > 60
}

decision := {"effect": "require_approval", "reasons": approval_reasons} if {
	scope_ok
	count(approval_reasons) > 0
}

decision := {"effect": "allow", "reasons": []} if {
	scope_ok
	count(approval_reasons) == 0
}
