"""Tests for the obs-websocket v5 client.

No OBS instance is involved. A fake transport plays the server side of
the protocol, so the handshake, the auth derivation and the request /
response framing are all exercised against the shape documented in
obs-websocket's protocol.md.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.obs.client import (
    OP_HELLO,
    OP_IDENTIFIED,
    OP_REQUEST_RESPONSE,
    REQUEST_STATUS_SUCCESS,
    OBSError,
    OBSWebSocketClient,
    build_auth_string,
    password_from_env,
)

# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------

# The example password, salt and challenge published in the obs-websocket
# v5 protocol document ("Creating an authentication string"). The document
# gives the inputs and the algorithm but not the expected output, so the
# expected value below is pinned from our implementation of the documented
# four steps. It exists to catch a regression in the derivation, not to
# certify interoperability -- that is confirmed against a live OBS.
SPEC_PASSWORD = "supersecretpassword"
SPEC_SALT = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
SPEC_CHALLENGE = "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
EXPECTED_AUTH = "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4="


def test_auth_string_matches_documented_algorithm():
    assert build_auth_string(SPEC_PASSWORD, SPEC_SALT, SPEC_CHALLENGE) == \
        EXPECTED_AUTH


def test_auth_string_is_deterministic():
    first = build_auth_string(SPEC_PASSWORD, SPEC_SALT, SPEC_CHALLENGE)
    second = build_auth_string(SPEC_PASSWORD, SPEC_SALT, SPEC_CHALLENGE)
    assert first == second


@pytest.mark.parametrize("password,salt,challenge", [
    ("other", SPEC_SALT, SPEC_CHALLENGE),
    (SPEC_PASSWORD, "c2FsdA==", SPEC_CHALLENGE),
    (SPEC_PASSWORD, SPEC_SALT, "Y2hhbGxlbmdl"),
])
def test_auth_string_changes_with_every_input(password, salt, challenge):
    assert build_auth_string(password, salt, challenge) != EXPECTED_AUTH


def test_auth_string_is_base64_sha256_length():
    # 32-byte digest -> 44 base64 characters including padding.
    assert len(build_auth_string("p", "s", "c")) == 44


def test_password_from_env_absent(monkeypatch):
    monkeypatch.delenv("OBS_WS_PASSWORD", raising=False)
    assert password_from_env() is None


def test_password_from_env_empty_is_none(monkeypatch):
    monkeypatch.setenv("OBS_WS_PASSWORD", "")
    assert password_from_env() is None


def test_password_from_env_present(monkeypatch):
    monkeypatch.setenv("OBS_WS_PASSWORD", "hunter2")
    assert password_from_env() == "hunter2"


# ---------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------

class FakeOBS:
    """Plays the OBS server side of the protocol over an in-memory queue."""

    def __init__(self, *, require_auth=False, hello_op=OP_HELLO,
                 identify_response_op=OP_IDENTIFIED, responder=None):
        self.require_auth = require_auth
        self.hello_op = hello_op
        self.identify_response_op = identify_response_op
        self.responder = responder
        self.sent: list[dict] = []
        self.closed = False
        self._outbox: asyncio.Queue[str] = asyncio.Queue()

        hello_data = {
            "obsStudioVersion": "30.1.2",
            "obsWebSocketVersion": "5.4.2",
            "rpcVersion": 1,
        }
        if require_auth:
            hello_data["authentication"] = {
                "challenge": SPEC_CHALLENGE,
                "salt": SPEC_SALT,
            }
        self._outbox.put_nowait(json.dumps({"op": hello_op, "d": hello_data}))

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        op = message.get("op")
        if op == 1:  # Identify
            await self._outbox.put(json.dumps({
                "op": self.identify_response_op,
                "d": {"negotiatedRpcVersion": 1},
            }))
        elif op == 6:  # Request
            data = message["d"]
            reply = (self.responder or _default_responder)(data)
            await self._outbox.put(json.dumps(reply))

    async def recv(self) -> str:
        return await self._outbox.get()

    async def close(self) -> None:
        self.closed = True


def _default_responder(data: dict) -> dict:
    return {
        "op": OP_REQUEST_RESPONSE,
        "d": {
            "requestType": data["requestType"],
            "requestId": data["requestId"],
            "requestStatus": {"result": True, "code": REQUEST_STATUS_SUCCESS},
            "responseData": {"ok": True, "echo": data.get("requestData")},
        },
    }


def make_client(fake: FakeOBS, password=None, timeout=2.0) -> OBSWebSocketClient:
    async def factory(url):
        return fake
    return OBSWebSocketClient("ws://test:4455", password=password,
                              timeout=timeout, connect_factory=factory)


# ---------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_without_auth():
    fake = FakeOBS(require_auth=False)
    client = make_client(fake)
    await client.connect()

    assert client.is_connected
    assert client.obs_version == "30.1.2"
    assert client.negotiated_rpc_version == 1

    identify = fake.sent[0]
    assert identify["op"] == 1
    assert identify["d"]["rpcVersion"] == 1
    # No password configured and none demanded: no auth field at all.
    assert "authentication" not in identify["d"]
    await client.close()


@pytest.mark.asyncio
async def test_connect_with_auth_sends_derived_string():
    fake = FakeOBS(require_auth=True)
    client = make_client(fake, password=SPEC_PASSWORD)
    await client.connect()

    identify = fake.sent[0]
    assert identify["d"]["authentication"] == EXPECTED_AUTH
    await client.close()


@pytest.mark.asyncio
async def test_connect_when_auth_required_but_no_password():
    fake = FakeOBS(require_auth=True)
    client = make_client(fake, password=None)
    with pytest.raises(OBSError, match="OBS_WS_PASSWORD"):
        await client.connect()
    assert not client.is_connected
    assert fake.closed


@pytest.mark.asyncio
async def test_password_set_but_auth_not_required_still_connects():
    fake = FakeOBS(require_auth=False)
    client = make_client(fake, password="unused")
    await client.connect()
    assert client.is_connected
    assert "authentication" not in fake.sent[0]["d"]
    await client.close()


@pytest.mark.asyncio
async def test_first_message_must_be_hello():
    fake = FakeOBS(hello_op=5)
    client = make_client(fake)
    with pytest.raises(OBSError, match="Expected Hello"):
        await client.connect()


@pytest.mark.asyncio
async def test_rejected_identify_raises():
    # OBS closes the socket on a bad password; a server that replies with
    # anything other than Identified must be treated as a failure.
    fake = FakeOBS(require_auth=True, identify_response_op=7)
    client = make_client(fake, password="wrong")
    with pytest.raises(OBSError, match="rejected the Identify handshake"):
        await client.connect()


@pytest.mark.asyncio
async def test_connect_is_idempotent():
    fake = FakeOBS()
    client = make_client(fake)
    await client.connect()
    await client.connect()
    assert sum(1 for m in fake.sent if m["op"] == 1) == 1
    await client.close()


@pytest.mark.asyncio
async def test_connect_timeout_is_not_fatal_to_the_caller():
    async def slow_factory(url):
        await asyncio.sleep(5)

    client = OBSWebSocketClient("ws://test:4455", timeout=0.05,
                                connect_factory=slow_factory)
    with pytest.raises(asyncio.TimeoutError):
        await client.connect()
    assert not client.is_connected


# ---------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_returns_response_data():
    fake = FakeOBS()
    client = make_client(fake)
    await client.connect()

    result = await client.request("GetVersion")
    assert result["ok"] is True

    request = next(m for m in fake.sent if m["op"] == 6)
    assert request["d"]["requestType"] == "GetVersion"
    assert request["d"]["requestId"]
    await client.close()


@pytest.mark.asyncio
async def test_request_before_connect_raises():
    client = make_client(FakeOBS())
    with pytest.raises(OBSError, match="Not connected"):
        await client.request("GetVersion")


@pytest.mark.asyncio
async def test_failed_request_status_raises_with_comment():
    def responder(data):
        return {
            "op": OP_REQUEST_RESPONSE,
            "d": {
                "requestType": data["requestType"],
                "requestId": data["requestId"],
                "requestStatus": {"result": False, "code": 600,
                                  "comment": "No source was found by the name"},
                "responseData": {},
            },
        }

    fake = FakeOBS(responder=responder)
    client = make_client(fake)
    await client.connect()
    with pytest.raises(OBSError, match="No source was found"):
        await client.set_input_text("Missing Source", "text")
    await client.close()


@pytest.mark.asyncio
async def test_set_input_text_uses_documented_shape():
    fake = FakeOBS()
    client = make_client(fake)
    await client.connect()
    await client.set_input_text("Verse Ref", "John 3:16 (KJV)")

    request = [m for m in fake.sent if m["op"] == 6][-1]["d"]
    assert request["requestType"] == "SetInputSettings"
    assert request["requestData"]["inputName"] == "Verse Ref"
    assert request["requestData"]["inputSettings"] == {"text": "John 3:16 (KJV)"}
    assert request["requestData"]["overlay"] is True
    await client.close()


@pytest.mark.asyncio
async def test_set_scene_item_enabled_resolves_id_first():
    def responder(data):
        payload = {"ok": True}
        if data["requestType"] == "GetSceneItemId":
            payload = {"sceneItemId": 7}
        return {
            "op": OP_REQUEST_RESPONSE,
            "d": {
                "requestType": data["requestType"],
                "requestId": data["requestId"],
                "requestStatus": {"result": True, "code": REQUEST_STATUS_SUCCESS},
                "responseData": payload,
            },
        }

    fake = FakeOBS(responder=responder)
    client = make_client(fake)
    await client.connect()
    await client.set_scene_item_enabled("Live", "Verse Overlay", True)

    requests = [m["d"] for m in fake.sent if m["op"] == 6]
    assert requests[0]["requestType"] == "GetSceneItemId"
    assert requests[0]["requestData"] == {"sceneName": "Live",
                                          "sourceName": "Verse Overlay"}
    assert requests[1]["requestType"] == "SetSceneItemEnabled"
    assert requests[1]["requestData"] == {
        "sceneName": "Live", "sceneItemId": 7, "sceneItemEnabled": True,
    }
    await client.close()


@pytest.mark.asyncio
async def test_missing_scene_item_id_raises():
    def responder(data):
        return {
            "op": OP_REQUEST_RESPONSE,
            "d": {
                "requestType": data["requestType"],
                "requestId": data["requestId"],
                "requestStatus": {"result": True, "code": REQUEST_STATUS_SUCCESS},
                "responseData": {},   # no sceneItemId
            },
        }

    fake = FakeOBS(responder=responder)
    client = make_client(fake)
    await client.connect()
    with pytest.raises(OBSError, match="did not return a sceneItemId"):
        await client.get_scene_item_id("Live", "Nope")
    await client.close()


@pytest.mark.asyncio
async def test_events_between_request_and_response_are_skipped():
    """OBS may interleave protocol traffic; we must not mistake it for
    our reply."""
    def responder(data):
        return {
            "op": OP_REQUEST_RESPONSE,
            "d": {
                "requestType": data["requestType"],
                "requestId": data["requestId"],
                "requestStatus": {"result": True, "code": REQUEST_STATUS_SUCCESS},
                "responseData": {"ok": True},
            },
        }

    fake = FakeOBS(responder=responder)
    original_send = fake.send

    async def send_with_noise(raw):
        message = json.loads(raw)
        if message.get("op") == 6:
            await fake._outbox.put(json.dumps({"op": 5, "d": {"eventType": "X"}}))
        await original_send(raw)

    fake.send = send_with_noise

    client = make_client(fake)
    await client.connect()
    assert (await client.request("GetVersion"))["ok"] is True
    await client.close()


@pytest.mark.asyncio
async def test_malformed_json_from_obs_raises_obs_error():
    fake = FakeOBS()
    client = make_client(fake)
    await client.connect()
    await fake._outbox.put("not json at all")
    with pytest.raises(OBSError, match="malformed JSON"):
        await client._recv()
    await client.close()


@pytest.mark.asyncio
async def test_close_is_safe_to_call_twice():
    fake = FakeOBS()
    client = make_client(fake)
    await client.connect()
    await client.close()
    await client.close()
    assert not client.is_connected
    assert fake.closed
