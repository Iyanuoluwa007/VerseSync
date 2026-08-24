"""Minimal obs-websocket v5 client.

Implements just the slice of the protocol VerseSync needs: connect,
authenticate, and issue a handful of requests. Written against the
official protocol document
(https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)
rather than pulling in a third-party SDK, because the dependency budget
for "optionally toggle a source" should be roughly zero and the
handshake is ~40 lines.

Protocol shape used here:

    OpCode 0  Hello           server -> client   (may carry an auth challenge)
    OpCode 1  Identify        client -> server
    OpCode 2  Identified      server -> client
    OpCode 6  Request         client -> server
    OpCode 7  RequestResponse server -> client   (requestStatus.code 100 == OK)

Everything is best-effort by design. OBS being closed, restarted or
misconfigured must degrade to "the overlay still works, the scene item
just does not get toggled" -- never to an exception that interrupts a
live service.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# OpCodes from the obs-websocket v5 protocol document.
OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7

RPC_VERSION = 1
REQUEST_STATUS_SUCCESS = 100

# We issue requests but do not consume OBS events, so subscribe to none.
# This keeps OBS from pushing a high-frequency event stream at us.
EVENT_SUBSCRIPTIONS_NONE = 0


class OBSError(RuntimeError):
    """An OBS request was rejected, or the handshake failed."""


def build_auth_string(password: str, salt: str, challenge: str) -> str:
    """Derive the obs-websocket v5 `authentication` string.

    Per the protocol document:

      1. Concatenate the password with the salt.
      2. SHA256 it, base64-encode the digest -> the "base64 secret".
      3. Concatenate the base64 secret with the challenge.
      4. SHA256 that, base64-encode the digest -> the auth string.
    """
    secret = base64.b64encode(
        hashlib.sha256((password + salt).encode("utf-8")).digest()
    ).decode("ascii")
    return base64.b64encode(
        hashlib.sha256((secret + challenge).encode("utf-8")).digest()
    ).decode("ascii")


class OBSWebSocketClient:
    """A single connection to OBS Studio's WebSocket server.

    `connect_factory` exists so tests can substitute a fake transport;
    in production it defaults to `websockets.connect`.
    """

    def __init__(self,
                 url: str,
                 password: str | None = None,
                 timeout: float = 5.0,
                 connect_factory: Callable[..., Any] | None = None):
        self.url = url
        self._password = password
        self.timeout = timeout
        self._connect_factory = connect_factory
        self._ws: Any = None
        self._identified = False
        self._lock = asyncio.Lock()
        self.obs_version: str = ""
        self.negotiated_rpc_version: int | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._identified

    async def connect(self) -> None:
        """Open the socket and complete the Identify handshake."""
        if self.is_connected:
            return

        factory = self._connect_factory
        if factory is None:
            try:
                import websockets
            except ImportError as exc:  # pragma: no cover - dependency present
                raise OBSError(
                    "The `websockets` package is required for OBS WebSocket "
                    "support. Install it with: pip install -r requirements.txt"
                ) from exc
            factory = websockets.connect

        self._ws = await asyncio.wait_for(factory(self.url), timeout=self.timeout)
        try:
            await self._handshake()
        except Exception:
            await self.close()
            raise

    async def _handshake(self) -> None:
        hello = await self._recv()
        if hello.get("op") != OP_HELLO:
            raise OBSError(
                f"Expected Hello (op 0) from OBS, got op {hello.get('op')!r}. "
                f"Is {self.url} really an obs-websocket v5 server?"
            )

        data = hello.get("d") or {}
        self.obs_version = str(data.get("obsStudioVersion", ""))

        identify: dict[str, Any] = {
            "rpcVersion": RPC_VERSION,
            "eventSubscriptions": EVENT_SUBSCRIPTIONS_NONE,
        }

        auth = data.get("authentication")
        if auth:
            if not self._password:
                raise OBSError(
                    "OBS requires a WebSocket password but none is set. "
                    "Put it in OBS_WS_PASSWORD, or turn off authentication "
                    "in OBS under Tools > WebSocket Server Settings."
                )
            identify["authentication"] = build_auth_string(
                self._password, auth.get("salt", ""), auth.get("challenge", "")
            )
        elif self._password:
            logger.info(
                "OBS_WS_PASSWORD is set but OBS is not requiring "
                "authentication; connecting without it."
            )

        await self._send({"op": OP_IDENTIFY, "d": identify})

        identified = await self._recv()
        if identified.get("op") != OP_IDENTIFIED:
            raise OBSError(
                "OBS rejected the Identify handshake (wrong password?). "
                f"Expected op 2, got op {identified.get('op')!r}."
            )
        self.negotiated_rpc_version = (
            (identified.get("d") or {}).get("negotiatedRpcVersion")
        )
        self._identified = True
        logger.info("Connected to OBS %s at %s (rpc v%s)",
                    self.obs_version or "?", self.url,
                    self.negotiated_rpc_version)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        self._identified = False
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            logger.debug("Error closing OBS socket; ignoring", exc_info=True)

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise OBSError("Not connected to OBS")
        await asyncio.wait_for(self._ws.send(json.dumps(message)),
                               timeout=self.timeout)

    async def _recv(self) -> dict[str, Any]:
        if self._ws is None:
            raise OBSError("Not connected to OBS")
        raw = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
        if isinstance(raw, bytes | bytearray):
            raw = raw.decode("utf-8")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OBSError(f"OBS sent malformed JSON: {raw[:120]!r}") from exc
        if not isinstance(message, dict):
            raise OBSError(f"OBS sent a non-object message: {raw[:120]!r}")
        return message

    # ------------------------------------------------------------------
    # requests
    # ------------------------------------------------------------------

    async def request(self, request_type: str,
                      request_data: dict[str, Any] | None = None
                      ) -> dict[str, Any]:
        """Issue one request and return its `responseData`.

        Serialised behind a lock: this client uses a simple
        send-then-read-the-reply model, so two concurrent callers would
        otherwise be able to read each other's responses.
        """
        if not self.is_connected:
            raise OBSError("Not connected to OBS")

        request_id = str(uuid.uuid4())
        async with self._lock:
            await self._send({
                "op": OP_REQUEST,
                "d": {
                    "requestType": request_type,
                    "requestId": request_id,
                    "requestData": request_data or {},
                },
            })

            # Skip any events that arrive between our request and its
            # response. We do not subscribe to events, but OBS may still
            # send protocol-level traffic.
            for _ in range(10):
                message = await self._recv()
                if message.get("op") != OP_REQUEST_RESPONSE:
                    continue
                data = message.get("d") or {}
                if data.get("requestId") != request_id:
                    continue
                status = data.get("requestStatus") or {}
                if status.get("code") != REQUEST_STATUS_SUCCESS:
                    raise OBSError(
                        f"OBS rejected {request_type}: "
                        f"code={status.get('code')} "
                        f"{status.get('comment') or ''}".strip()
                    )
                return data.get("responseData") or {}

        raise OBSError(f"No response from OBS for {request_type}")

    # ------------------------------------------------------------------
    # convenience wrappers
    # ------------------------------------------------------------------

    async def get_version(self) -> dict[str, Any]:
        """Probe the connection. Cheap and always available."""
        return await self.request("GetVersion")

    async def set_input_text(self, input_name: str, text: str) -> None:
        """Set the text of an OBS text source (GDI+ or FreeType)."""
        await self.request("SetInputSettings", {
            "inputName": input_name,
            "inputSettings": {"text": text},
            "overlay": True,
        })

    async def get_scene_item_id(self, scene_name: str,
                                source_name: str) -> int:
        response = await self.request("GetSceneItemId", {
            "sceneName": scene_name,
            "sourceName": source_name,
        })
        item_id = response.get("sceneItemId")
        if item_id is None:
            raise OBSError(
                f"OBS did not return a sceneItemId for {source_name!r} "
                f"in scene {scene_name!r}"
            )
        return int(item_id)

    async def set_scene_item_enabled(self, scene_name: str, source_name: str,
                                     enabled: bool) -> None:
        """Show or hide a source within a scene."""
        item_id = await self.get_scene_item_id(scene_name, source_name)
        await self.request("SetSceneItemEnabled", {
            "sceneName": scene_name,
            "sceneItemId": item_id,
            "sceneItemEnabled": enabled,
        })


def password_from_env() -> str | None:
    """Read the OBS WebSocket password.

    Deliberately read at the point of use and never stored in `Settings`,
    so it cannot show up in a dataclass repr, a log line or the
    `/obs/status` response.
    """
    password = os.getenv("OBS_WS_PASSWORD", "")
    return password or None
