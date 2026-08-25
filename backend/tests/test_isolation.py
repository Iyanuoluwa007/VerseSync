"""Guards on test isolation itself.

Lives in a collected test file rather than in conftest.py, because
pytest does not collect tests from conftest -- a guard placed there
silently never runs.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import SETTINGS_IMPORTERS


def test_every_settings_importer_is_isolated():
    """Fail if a module imports `settings` without being listed above.

    Otherwise a new module could read the developer's real database and
    the suite would start depending on machine state again.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    found = set()
    for path in app_dir.rglob("*.py"):
        if "from app.core.config import settings" in path.read_text(
                encoding="utf-8"):
            rel = path.relative_to(app_dir.parent).with_suffix("")
            found.add(str(rel).replace("\\", ".").replace("/", "."))

    missing = found - set(SETTINGS_IMPORTERS)
    assert not missing, (
        f"these modules import settings but are not isolated in "
        f"tests/conftest.py: {sorted(missing)}"
    )


def test_isolated_database_is_not_the_real_one(isolate_database):
    """The autouse fixture must actually redirect the database."""
    assert "versesync-test.db" in str(isolate_database.db_path)


def test_bible_and_auth_agree_on_the_isolated_path(isolate_database):
    from app.auth import db as auth_db
    from app.bible import db as bible_db

    assert auth_db.settings.db_path == isolate_database.db_path
    assert bible_db.settings.db_path == isolate_database.db_path
