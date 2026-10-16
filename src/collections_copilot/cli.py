"""`cc` command-line interface."""

from __future__ import annotations

import asyncio
import uuid

import typer

from collections_copilot.config import get_settings

app = typer.Typer(add_completion=False, help="collections-copilot operations CLI")
audit_app = typer.Typer(help="Audit log commands")
app.add_typer(audit_app, name="audit")
prompts_app = typer.Typer(help="Versioned prompts")
app.add_typer(prompts_app, name="prompts")


@prompts_app.command("list")
def prompts_list() -> None:
    """Print every prompt with its version, route, schema and SHA-256 (logged on each call)."""
    from collections_copilot.agent.prompts import load_prompt, prompt_names

    for name in prompt_names():
        p = load_prompt(name)
        typer.echo(f"{p.id:32} route={p.route:22} schema={p.schema_name:20} sha256={p.sha256}")


@audit_app.command("verify")
def audit_verify(
    tenant: str | None = typer.Option(None, "--tenant", help="Only verify this tenant id"),
    database_url: str | None = typer.Option(None, "--database-url", envvar="CC_DATABASE_URL"),
) -> None:
    """Verify the hash chain; exit 1 with the first bad sequence number."""
    from collections_copilot.db.audit import verify_all, verify_tenant
    from collections_copilot.db.session import make_engine, make_sessionmaker

    async def run() -> dict[uuid.UUID, int | None]:
        engine = make_engine(database_url or get_settings().database_url, pool_size=2)
        try:
            async with make_sessionmaker(engine)() as session:
                if tenant:
                    tid = uuid.UUID(tenant)
                    return {tid: await verify_tenant(session, tid)}
                return await verify_all(session)
        finally:
            await engine.dispose()

    results = asyncio.run(run())
    bad = {t: s for t, s in results.items() if s is not None}
    for tid, seq in sorted(bad.items(), key=lambda kv: kv[1] or 0):
        typer.echo(f"chain_ok=false tenant={tid} first_bad_seq={seq}")
    if bad:
        raise typer.Exit(1)
    typer.echo(f"chain_ok=true tenants={len(results)}")


@app.command()
def seed(
    database_url: str | None = typer.Option(None, "--database-url", envvar="CC_DATABASE_URL"),
) -> None:
    """Load the synthetic dataset (gen.v1, seed 4242): 2 tenants with 400 and 40 accounts."""
    from collections_copilot.agent.checkpointer import libpq_url
    from collections_copilot.db.session import make_engine, make_sessionmaker
    from data.generator.gen import standard_tenants
    from data.generator.load import load_ledger, load_tenant

    async def run() -> None:
        url = database_url or get_settings().database_url
        engine = make_engine(url, pool_size=2)
        try:
            sm = make_sessionmaker(engine)
            for spec in standard_tenants():
                loaded = await load_tenant(sm, spec)
                await load_ledger(libpq_url(url), spec)
                state = "loaded" if loaded else "unchanged"
                typer.echo(
                    f"{spec.name} tenant_id={spec.tenant_id} accounts={len(spec.accounts)} "
                    f"digest={spec.digest()[:16]} {state}"
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    app()
