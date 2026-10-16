"""Tenant-scoped repositories. All data access outside `collections_copilot.db` goes through here."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from collections_copilot.db.repositories.accounts import AccountsRepo, InvoicesRepo, NotesRepo
from collections_copilot.db.repositories.cases import ApprovalsRepo, CasesRepo, DraftsRepo
from collections_copilot.db.repositories.ingest import IngestRepo
from collections_copilot.db.repositories.ops import (
    ContactsRepo,
    FollowUpsRepo,
    MessagesRepo,
    OptOutsRepo,
    PromisesRepo,
    StepsRepo,
)
from collections_copilot.db.repositories.settings import SettingsRepo
from collections_copilot.db.repositories.tenants import TenantsRepo


class Repos:
    """All repositories bound to one session (one unit of work)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tenants = TenantsRepo(session)
        self.accounts = AccountsRepo(session)
        self.invoices = InvoicesRepo(session)
        self.notes = NotesRepo(session)
        self.cases = CasesRepo(session)
        self.drafts = DraftsRepo(session)
        self.approvals = ApprovalsRepo(session)
        self.settings = SettingsRepo(session)
        self.ingest = IngestRepo(session)
        self.promises = PromisesRepo(session)
        self.messages = MessagesRepo(session)
        self.contacts = ContactsRepo(session)
        self.opt_outs = OptOutsRepo(session)
        self.follow_ups = FollowUpsRepo(session)
        self.steps = StepsRepo(session)


__all__ = ["Repos"]
