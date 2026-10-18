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
dlq_app = typer.Typer(help="Dead-letter queues")
app.add_typer(dlq_app, name="dlq")
DLQ_SUFFIX = "-dlq"


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
    from collections_copilot.db.audit import head, verify_all, verify_tenant
    from collections_copilot.db.session import make_engine, make_sessionmaker

    async def run() -> tuple[dict[uuid.UUID, int | None], dict[uuid.UUID, tuple[int, str] | None]]:
        engine = make_engine(database_url or get_settings().database_url, pool_size=2)
        try:
            async with make_sessionmaker(engine)() as session:
                if tenant:
                    tid = uuid.UUID(tenant)
                    results = {tid: await verify_tenant(session, tid)}
                else:
                    results = await verify_all(session)
                return results, {t: await head(session, t) for t in results}
        finally:
            await engine.dispose()

    results, heads = asyncio.run(run())
    bad = {t: s for t, s in results.items() if s is not None}
    for tid, seq in sorted(bad.items(), key=lambda kv: kv[1] or 0):
        typer.echo(f"chain_ok=false tenant={tid} first_bad_seq={seq}")
    if bad:
        raise typer.Exit(1)
    typer.echo(f"chain_ok=true tenants={len(results)}")
    # Keep these lines somewhere else (daily): a rewritten chain no longer ends at a known head.
    for tid, last in sorted(heads.items(), key=lambda kv: str(kv[0])):
        if last is not None:
            typer.echo(f"head tenant={tid} seq={last[0]} hash={last[1]}")


@dlq_app.command("peek")
def dlq_peek(
    queue: str = typer.Option(..., "--queue", help="Dead-letter queue, e.g. cc-tasks-dlq"),
    max_messages: int = typer.Option(10, "--max", min=1, max=10),
) -> None:
    """List messages by id, kind, tenant and case (never their bodies); they stay in the queue."""
    from collections_copilot.integrations.queue.sqs import SqsQueueClient

    async def run() -> None:
        settings = get_settings()
        async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
            for m in await client.receive(queue, max_messages=max_messages, wait_seconds=1):
                b = m.body
                typer.echo(
                    f"task_id={b.get('task_id')} kind={b.get('kind')} tenant={b.get('tenant_id')} "
                    f"case={b.get('case_id')} receives={m.receive_count}"
                )
                await client.extend_visibility(queue, m.receipt_handle, 0)  # visible again

    asyncio.run(run())


@app.command("replay-dlq")
def replay_dlq(
    queue: str = typer.Option(..., "--queue", help="Dead-letter queue, e.g. cc-tasks-dlq"),
    max_messages: int = typer.Option(10, "--max", min=1, max=100),
) -> None:
    """Move messages back to the source queue unchanged (same task_id: handlers stay idempotent).
    Fix the cause first; a poison message only fills the DLQ again."""
    from collections_copilot.integrations.queue.sqs import SqsQueueClient

    if not queue.endswith(DLQ_SUFFIX):
        typer.echo(f"--queue must name a dead-letter queue ending in {DLQ_SUFFIX}", err=True)
        raise typer.Exit(2)
    source = queue.removesuffix(DLQ_SUFFIX)

    async def run() -> int:
        settings = get_settings()
        moved = 0
        async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
            while moved < max_messages:
                batch = await client.receive(
                    queue, max_messages=min(10, max_messages - moved), wait_seconds=1
                )
                if not batch:
                    break
                for m in batch:
                    await client.send(source, m.body)
                    await client.delete(queue, m.receipt_handle)  # only after the send succeeded
                    moved += 1
        return moved

    typer.echo(f"replayed={asyncio.run(run())} from={queue} to={source}")


@app.command("dev-token")
def dev_token(
    tenant: str = typer.Option(
        "5d52f1a8-7b91-5150-919c-ce3a391e5ea8", "--tenant", help="Tenant id (default: acme)"
    ),
    scope: list[str] = typer.Option(["ledger:read"], "--scope", help="Repeat for more scopes"),
) -> None:
    """A dev agent JWT from mcp-gateway's dev issuer, for calling the ledger through the gateway."""
    from collections_copilot.integrations.mcp.tokens import DevIssuerTokenProvider

    provider = DevIssuerTokenProvider(get_settings().mcpgw_issuer_url)
    typer.echo(asyncio.run(provider.token(tenant, list(scope))))


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
