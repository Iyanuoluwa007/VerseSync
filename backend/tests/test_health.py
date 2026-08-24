"""Smoke test: FastAPI starts and the meta endpoints respond.

Run from backend/ with the dev requirements installed:
    pytest -v
"""
from fastapi.testclient import TestClient

from app import __version__
from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "versesync"
    # Asserted against the package constant, not a literal. The literal
    # is what let the reported version drift to 0.4.4 while the project
    # shipped 0.4.6.
    assert body["version"] == __version__


def test_health_advertises_projector_and_docs():
    body = client.get("/").json()
    assert body["projector"] == "/projector"
    assert body["docs"] == "/docs"


def test_healthz_probe():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


def test_openapi_schema_builds():
    """Every router must produce a valid schema.

    This is the cheapest possible guard against a malformed response
    model or a duplicated path taking the whole app down at import.
    """
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["version"] == __version__
    for path in ("/", "/healthz", "/translations", "/parse",
                 "/projector", "/projector/show", "/obs/status"):
        assert path in schema["paths"], f"{path} missing from OpenAPI schema"
