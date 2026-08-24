"""Tests for the projector overlay -- the OBS Browser Source surface.

These run against an in-memory SQLite database seeded with a handful of
verses, so they exercise the real query path without needing the 93k-verse
production database.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.bible.db import connect, seed_books, transaction
from app.core.events import hub
from app.main import app


@pytest.fixture(autouse=True)
def seeded_db(tmp_path, monkeypatch):
    """Point every query at a temporary DB holding a few known verses."""
    db_path = tmp_path / "projector.db"
    conn = connect(db_path)
    seed_books(conn)
    with transaction(conn):
        conn.execute(
            """INSERT INTO translations
                   (code, name, language, license, ingested_at)
               VALUES ('KJV', 'King James Version', 'en',
                       'Public Domain', '2026-01-01T00:00:00Z')"""
        )
        conn.executemany(
            "INSERT INTO verses (translation, book, chapter, verse, text) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("KJV", "JHN", 3, 16, "For God so loved the world..."),
                ("KJV", "ROM", 8, 28, "And we know that all things..."),
                ("KJV", "ROM", 8, 29, "For whom he did foreknow..."),
                ("KJV", "ROM", 8, 30, "Moreover whom he did predestinate..."),
            ],
        )
    conn.close()

    import app.bible.query as query_module
    original = query_module.connect
    monkeypatch.setattr(query_module, "connect",
                        lambda db=None: original(db_path))
    yield
    hub.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------

def test_projector_page_renders(client):
    r = client.get("/projector")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "VerseSync Projector" in body
    assert 'id="verses"' in body


def test_projector_page_injects_server_defaults(client):
    """The page must not need a second round trip before it can draw."""
    body = client.get("/projector").text
    assert "__VERSESYNC_DEFAULTS__" not in body, "template token not substituted"
    marker = "window.VERSESYNC_DEFAULTS = "
    raw = body.split(marker, 1)[1].split(";", 1)[0]
    defaults = json.loads(raw)
    assert defaults["theme"] in ("lowerthird", "caption", "fullscreen")
    assert isinstance(defaults["hold"], int | float)
    assert defaults["translation"] == "KJV"


def test_projector_page_is_not_cached(client):
    """OBS caches Browser Sources hard; stale CSS after an upgrade is a
    miserable thing to debug on a Sunday morning."""
    r = client.get("/projector")
    assert "no-store" in r.headers["cache-control"]


def test_projector_page_references_no_external_hosts(client):
    """An OBS machine on a locked-down church network has no internet."""
    body = client.get("/projector").text
    for marker in ("http://", "https://", "//fonts.", "cdn."):
        assert marker not in body, f"page reaches out to {marker}"


def test_static_assets_are_served(client):
    for name, content_type in (("projector.css", "text/css"),
                               ("projector.js", "application/javascript")):
        r = client.get(f"/projector/static/{name}")
        assert r.status_code == 200
        assert content_type in r.headers["content-type"]
        assert len(r.text) > 100


@pytest.mark.parametrize("attempt", [
    "../config.py",
    "..%2Fconfig.py",
    "projector.html",     # the template is rendered, never served raw
    "nope.css",
])
def test_static_whitelist_rejects_everything_else(client, attempt):
    assert client.get(f"/projector/static/{attempt}").status_code == 404


# ---------------------------------------------------------------------
# Config and OBS URL helper
# ---------------------------------------------------------------------

def test_config_lists_themes_and_parameters(client):
    body = client.get("/projector/config").json()
    assert body["themes"] == ["lowerthird", "caption", "fullscreen"]
    assert "transparent" in body["backgrounds"]
    assert "hold" in body["query_parameters"]


def test_obs_url_uses_the_requesting_host(client):
    body = client.get("/projector/obs-url").json()
    assert body["url"].endswith("/projector")
    assert body["obs_browser_source_settings"]["width"] == 1920
    assert body["obs_browser_source_settings"]["height"] == 1080
    assert body["obs_browser_source_settings"][
        "shutdown_source_when_not_visible"] is True


def test_obs_url_carries_theme_and_bg(client):
    body = client.get("/projector/obs-url?theme=caption&bg=green").json()
    assert "theme=caption" in body["url"]
    assert "bg=green" in body["url"]


@pytest.mark.parametrize("query", ["theme=sparkly", "bg=chartreuse"])
def test_obs_url_rejects_unknown_options(client, query):
    r = client.get(f"/projector/obs-url?{query}")
    assert r.status_code == 400


# ---------------------------------------------------------------------
# Manual control
# ---------------------------------------------------------------------

def test_show_by_explicit_reference(client):
    r = client.post("/projector/show",
                    json={"book": "JHN", "chapter": 3, "verse_start": 16})
    assert r.status_code == 200
    payload = r.json()["payload"]
    assert payload["type"] == "detection"
    assert payload["reference"]["book"] == "JHN"
    assert payload["reference"]["book_name"] == "John"
    assert payload["reference"]["source"] == "manual"
    assert payload["verses"][0]["text"].startswith("For God so loved")


def test_show_accepts_a_full_book_name(client):
    r = client.post("/projector/show",
                    json={"book": "Romans", "chapter": 8, "verse_start": 28})
    assert r.status_code == 200
    assert r.json()["payload"]["reference"]["book"] == "ROM"


def test_show_by_spoken_text_uses_the_parser(client):
    r = client.post("/projector/show",
                    json={"text": "turn to Romans eight twenty-eight",
                          "use_llm": False})
    assert r.status_code == 200
    reference = r.json()["payload"]["reference"]
    assert reference["book"] == "ROM"
    assert reference["chapter"] == 8
    assert reference["verse_start"] == 28


def test_show_renders_a_range(client):
    r = client.post("/projector/show",
                    json={"book": "ROM", "chapter": 8,
                          "verse_start": 28, "verse_end": 30})
    verses = r.json()["payload"]["verses"]
    assert [v["verse"] for v in verses] == [28, 29, 30]


def test_show_retains_state_for_the_next_client(client):
    client.post("/projector/show",
                json={"book": "JHN", "chapter": 3, "verse_start": 16})
    state = client.get("/projector/state").json()
    assert state["retained"]["reference"]["book"] == "JHN"
    assert state["retained_age_s"] >= 0


def test_clear_empties_retained_state(client):
    client.post("/projector/show",
                json={"book": "JHN", "chapter": 3, "verse_start": 16})
    assert client.post("/projector/clear").status_code == 200
    assert client.get("/projector/state").json()["retained"] is None


def test_show_with_unparseable_text_is_422(client):
    r = client.post("/projector/show",
                    json={"text": "good morning everyone", "use_llm": False})
    assert r.status_code == 422


def test_show_with_neither_text_nor_reference_is_422(client):
    assert client.post("/projector/show", json={}).status_code == 422


def test_show_with_unknown_book_is_400(client):
    r = client.post("/projector/show",
                    json={"book": "Hobbits", "chapter": 1, "verse_start": 1})
    assert r.status_code == 400


def test_show_with_out_of_range_chapter_is_422(client):
    """John has 21 chapters. This is the same bounds check the parser
    uses to reject Whisper-induced nonsense."""
    r = client.post("/projector/show",
                    json={"book": "JHN", "chapter": 99, "verse_start": 1})
    assert r.status_code == 422


def test_show_with_reversed_range_is_422(client):
    r = client.post("/projector/show",
                    json={"book": "ROM", "chapter": 8,
                          "verse_start": 30, "verse_end": 28})
    assert r.status_code == 422


def test_show_for_a_missing_translation_is_404_with_a_fix(client):
    r = client.post("/projector/show",
                    json={"book": "JHN", "chapter": 3, "verse_start": 16,
                          "translation": "WEB"})
    assert r.status_code == 404
    assert "ingest_bibles" in r.json()["detail"]


# ---------------------------------------------------------------------
# The live channel
# ---------------------------------------------------------------------

def test_websocket_receives_a_pushed_verse(client):
    with client.websocket_connect("/ws/transcripts") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "connected"

        client.post("/projector/show",
                    json={"book": "JHN", "chapter": 3, "verse_start": 16})

        message = ws.receive_json()
        assert message["type"] == "detection"
        assert message["reference"]["book_name"] == "John"
        assert message["verses"][0]["text"].startswith("For God so loved")


def test_websocket_replays_current_verse_on_connect(client):
    """This is what makes an OBS scene change not blank the overlay."""
    client.post("/projector/show",
                json={"book": "JHN", "chapter": 3, "verse_start": 16})

    with client.websocket_connect("/ws/transcripts") as ws:
        greeting = ws.receive_json()
        assert greeting["has_retained"] is True
        replay = ws.receive_json()
        assert replay["type"] == "detection"
        assert replay["replayed"] is True
        assert replay["reference"]["book"] == "JHN"


def test_websocket_receives_clear(client):
    with client.websocket_connect("/ws/transcripts") as ws:
        ws.receive_json()  # greeting
        client.post("/projector/clear")
        assert ws.receive_json()["type"] == "clear"
