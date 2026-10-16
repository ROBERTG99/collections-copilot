"""`cc` command-line interface."""

from __future__ import annotations

import asyncio
import uuid

import typer

from collections_copilot.config import get_settings

app = typer.Typer(add_completion=False, help="collections-copilot operations CLI")
audit_app = typer.Typer(help="Audit log commands")
app.add_typer(audit_app, name="audit")


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


if __name__ == "__main__":
    app()
