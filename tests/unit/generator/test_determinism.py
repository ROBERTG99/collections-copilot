"""gen.v1 is deterministic for a seed and only emits synthetic phone numbers."""

from __future__ import annotations

from data.generator.gen import DEFAULT_SEED, GEN_VERSION, generate_tenant, standard_tenants


def test_same_seed_same_digest() -> None:
    a = generate_tenant("acme-distribution", 400, seed=DEFAULT_SEED)
    b = generate_tenant("acme-distribution", 400, seed=DEFAULT_SEED)
    assert a.digest() == b.digest()
    c = generate_tenant("acme-distribution", 400, seed=DEFAULT_SEED + 1)
    assert c.digest() != a.digest()
    assert GEN_VERSION == "gen.v1"


def test_standard_tenants_sizes_and_ids() -> None:
    tenants = standard_tenants()
    assert [len(t.accounts) for t in tenants] == [400, 40]
    assert tenants[0].tenant_id != tenants[1].tenant_id
    assert standard_tenants()[0].tenant_id == tenants[0].tenant_id


def test_only_synthetic_phones_and_valid_fields() -> None:
    for tenant in standard_tenants():
        refs = set()
        for acc in tenant.accounts:
            assert acc.phone_e164.startswith(("+1555", "+34555"))
            assert acc.language in ("en", "es")
            assert acc.segment in ("enterprise", "mid_market", "smb")
            assert acc.invoices, acc.external_ref
            assert all(inv.amount_cents > 0 for inv in acc.invoices)
            assert acc.external_ref not in refs
            refs.add(acc.external_ref)
            if acc.language == "es":
                assert acc.phone_e164.startswith("+34555")


def test_scenarios_cover_nba_actions() -> None:
    tenant = standard_tenants()[0]
    scenarios = {acc.scenario for acc in tenant.accounts}
    assert {"standard", "dispute", "hardship", "escalate", "paid_claim", "injection"} <= scenarios
