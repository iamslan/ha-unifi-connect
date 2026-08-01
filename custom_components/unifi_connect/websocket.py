"""WebSocket client for UniFi Connect real-time EV power data.

Connects to the UniFi Connect application WebSocket to receive
real-time power statistics (kW, amps, volts, session energy)
that are NOT available through the REST API.

Protocol (reverse-engineered from SWAI framework):
  URL:       wss://{host}/proxy/connect/  (UDMP)
  Auth:      Cookie-based (same session as REST API)
  Handshake: Send JSON {"type":"request","action":"set_info",
                         "platform":"web","timestamp":<epoch_ms>}
  Messages:  Binary framed — each part has an 8-byte header
             [part_num, type, 0, 0, 0, 0, 0, length] + JSON payload.
  Events:    EV_POWER_STATS / MULTI_EV_POWER_STATS with fields:
             instantKW, instantA, instantV, meter, duration, startedAt, streaming
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from typing import Any, Callable

import aiohttp

from .const import CONTROLLER_UDMP

_LOGGER = logging.getLogger(__name__)

# How long to wait before reconnecting after a disconnect
RECONNECT_DELAY = 5
# Maximum reconnect delay (exponential backoff cap)
MAX_RECONNECT_DELAY = 60


def parse_binary_frame(data: bytes) -> list[dict[str, Any]]:
    """Parse the binary-framed WebSocket message into JSON payloads.

    Each part has an 8-byte header:
      byte 0: part number (1-based)
      byte 1: type/flags
      bytes 2-6: reserved (zeros)
      byte 7: payload length (single byte for payloads < 256)

    For payloads >= 256 bytes, the length is encoded as a 2-byte
    big-endian value at bytes 6-7 (observed with larger MULTI messages).
    """
    parts: list[dict[str, Any]] = []
    offset = 0
    while offset + 8 <= len(data):
        # Read 8-byte header
        header = data[offset : offset + 8]
        # Try single-byte length first
        payload_len = header[7]

        # If the high byte (header[6]) is non-zero, treat bytes 6-7
        # as a big-endian 16-bit length
        if header[6] != 0:
            payload_len = struct.unpack(">H", header[6:8])[0]

        payload_start = offset + 8
        payload_end = payload_start + payload_len

        if payload_end > len(data):
            # Remaining data is the payload
            payload_end = len(data)

        try:
            payload_str = data[payload_start:payload_end].decode("utf-8")
            payload_json = json.loads(payload_str)
            parts.append(payload_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            _LOGGER.debug(
                "Failed to parse WS frame part at offset %d: %s", offset, err
            )

        offset = payload_end

    return parts


class UnifiConnectWebSocket:
    """WebSocket client for real-time UniFi Connect power data."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        controller_type: str = CONTROLLER_UDMP,
        on_power_stats: Callable[[dict[str, Any]], None] | None = None,
        get_cookies: Callable[[], Any] | None = None,
    ):
        self._host = host
        self._session = session
        self._controller_type = controller_type
        self._on_power_stats = on_power_stats
        self._get_cookies = get_cookies

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._reconnect_delay = RECONNECT_DELAY

        # Latest power data per device ID
        self.power_data: dict[str, dict[str, Any]] = {}

    @property
    def connected(self) -> bool:
        """Return True if WebSocket is connected."""
        return self._ws is not None and not self._ws.closed

    def _get_ws_url(self) -> str:
        """Build the WebSocket URL."""
        if self._controller_type == CONTROLLER_UDMP:
            return f"wss://{self._host}/proxy/connect/"
        return f"wss://{self._host}/proxy/connect/"

    async def start(self) -> None:
        """Start the WebSocket listener loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        _LOGGER.info("UniFi Connect WebSocket listener started")

    async def stop(self) -> None:
        """Stop the WebSocket listener."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        _LOGGER.info("UniFi Connect WebSocket listener stopped")

    async def _run_loop(self) -> None:
        """Reconnecting WebSocket loop."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.warning(
                    "WebSocket connection error: %s. Reconnecting in %ds...",
                    err,
                    self._reconnect_delay,
                )

            if not self._running:
                break

            await asyncio.sleep(self._reconnect_delay)
            # Exponential backoff
            self._reconnect_delay = min(
                self._reconnect_delay * 2, MAX_RECONNECT_DELAY
            )

    def _build_cookie_header(self) -> dict[str, str] | None:
        """Build a Cookie header from the API's stored cookies.

        aiohttp's default CookieJar (unsafe=False) silently drops
        cookies for bare IP addresses per RFC 2109.  The REST API works
        because it passes cookies explicitly on every request, but
        ws_connect relies on the jar — so we must inject them manually.
        """
        if not self._get_cookies:
            return None
        cookies = self._get_cookies()
        if not cookies:
            return None
        try:
            cookie_str = "; ".join(
                f"{k}={v.value}" for k, v in cookies.items()
            )
            _LOGGER.debug(
                "Injecting %d cookie(s) into WebSocket headers", len(cookies)
            )
            return {"Cookie": cookie_str}
        except Exception as err:
            _LOGGER.warning("Failed to build cookie header: %s", err)
            return None

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and process messages."""
        url = self._get_ws_url()
        _LOGGER.debug("Connecting to UniFi Connect WebSocket: %s", url)

        headers = self._build_cookie_header() or {}

        try:
            self._ws = await self._session.ws_connect(
                url,
                ssl=False,
                heartbeat=30,
                headers=headers,
            )
        except Exception as err:
            _LOGGER.warning("WebSocket connection failed: %s", err)
            raise

        _LOGGER.info("UniFi Connect WebSocket connected")
        self._reconnect_delay = RECONNECT_DELAY  # Reset backoff on success

        # Send the handshake message
        handshake = json.dumps(
            {
                "type": "request",
                "action": "set_info",
                "platform": "web",
                "timestamp": int(time.time() * 1000),
            }
        )
        await self._ws.send_str(handshake)
        _LOGGER.debug("WebSocket handshake sent")

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    self._process_binary_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    self._process_text_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.warning(
                        "WebSocket error: %s", self._ws.exception()
                    )
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    _LOGGER.debug("WebSocket closed by server")
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("WebSocket read error: %s", err)

        if self._ws and not self._ws.closed:
            await self._ws.close()

    def _process_binary_message(self, data: bytes) -> None:
        """Parse a binary WebSocket message and extract power data."""
        parts = parse_binary_frame(data)
        if len(parts) < 2:
            return

        envelope = parts[0]
        payload = parts[1]
        event_name = envelope.get("name", "")

        if event_name == "EV_POWER_STATS":
            self._handle_power_stats(payload)
        elif event_name == "MULTI_EV_POWER_STATS":
            if isinstance(payload, list):
                for item in payload:
                    self._handle_power_stats(item)
            elif isinstance(payload, dict):
                self._handle_power_stats(payload)
        elif event_name == "DEVICE_UPDATED":
            _LOGGER.debug("Device updated event: %s", envelope.get("id"))

    def _process_text_message(self, data: str) -> None:
        """Handle a text WebSocket message (rarely used)."""
        try:
            msg = json.loads(data)
            _LOGGER.debug("WebSocket text message: %s", msg.get("type"))
        except json.JSONDecodeError:
            _LOGGER.debug("WebSocket non-JSON text: %s", data[:100])

    def _handle_power_stats(self, stats: dict[str, Any]) -> None:
        """Store power stats and notify callback."""
        device_id = stats.get("id")
        if not device_id:
            return

        self.power_data[device_id] = {
            "instantKW": stats.get("instantKW"),
            "instantA": stats.get("instantA"),
            "instantV": stats.get("instantV"),
            "meter": stats.get("meter"),
            "duration": stats.get("duration"),
            "startedAt": stats.get("startedAt"),
            "streaming": stats.get("streaming", False),
            "mac": stats.get("mac"),
        }

        if self._on_power_stats:
            self._on_power_stats(self.power_data[device_id])
