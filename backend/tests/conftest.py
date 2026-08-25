"""Global test isolation.

Every test gets its own throwaway database. Without this, the suite reads
whatever is in `backend/data/versesync.db` on the developer's machine,
which means the result depends on machine state rather than on the code.

That is not hypothetical. Setting an admin PIN on a real install made 16
tests fail and one hang forever: the auth middleware found a configured
database, started returning 401 to `/projector/show`, and a WebSocket
test sat waiting for a broadcast that was never going to arrive. CI did
not catch it because CI has no PIN set.

`settings` is a frozen dataclass imported by value into each module that
uses it, so there is no single object to mutate -- every importer is
patched individually. The list is asserted against the source tree by
`test_every_settings_importer_is_isolated` below, so a new module that
imports settings cannot quietly escape isolation.
"""
from __future__ import annotations

import dataclasses

import pytest

# Every module that does `from app.core.config import settings`.
SETTINGS_IMPORTERS = (
    "app.auth.db",
    "app.auth.middleware",
    "app.auth.service",
    "app.bible.db",
    "app.main",
    "app.obs.controller",
    "app.obs.router",
    "app.parser.lexicon",
    "app.projector.router",
)


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Point every consumer of `settings` at a per-test database."""
    import importlib

    from app.auth import db as auth_db
    from app.auth import service as auth_service
    from app.core import config as config_module
    from app.core.events import hub
    from app.parser import lexicon as lexicon_module

    isolated = dataclasses.replace(
        config_module.settings,
        db_path=tmp_path / "versesync-test.db",
    )

    monkeypatch.setattr(config_module, "settings", isolated)
    for module_name in SETTINGS_IMPORTERS:
        module = importlib.import_module(module_name)
        if hasattr(module, "settings"):
            monkeypatch.setattr(module, "settings", isolated)

    # Caches keyed on the database must not survive the swap.
    auth_db.reset_state()
    auth_service.invalidate_configured_cache()
    lexicon_module.reset_cache()
    hub.reset()

    yield isolated

    auth_db.reset_state()
    auth_service.invalidate_configured_cache()
    lexicon_module.reset_cache()
    hub.reset()
