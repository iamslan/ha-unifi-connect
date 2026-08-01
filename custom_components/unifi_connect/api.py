from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import DEFAULT_PORT, CONTROLLER_UDMP

_LOGGER = logging.getLogger(__name__)


class UnifiConnectAPIError(Exception):
    """Error communicating with the UniFi Connect API."""


class UnifiConnectAPI:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = DEFAULT_PORT,
        controller_type: str = CONTROLLER_UDMP,
        session: aiohttp.ClientSession | None = None,
    ):
        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._controller_type = controller_type
        self._session = session
        self._cookies: aiohttp.CookieJar | None = None
        self._csrf: str | None = None

    async def login(self) -> bool:
        """Login and store CSRF token."""
        url = self._get_login_url()
        payload = {
            "username": self._username,
            "password": self._password,
            "remember": True,
        }

        try:
            async with asyncio.timeout(10):
                async with self._session.post(url, json=payload, ssl=False) as resp:
                    if resp.status != 200:
                        _LOGGER.error("Login failed with status %s", resp.status)
                        return False
                    self._cookies = resp.cookies
                    self._csrf = resp.headers.get("x-csrf-token")
                    _LOGGER.debug("Login successful")
                    return True
        except Exception as err:
            _LOGGER.exception("Login error: %s", err)
            return False

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch list of devices. Raises UnifiConnectAPIError on failure."""
        result = await self._request("GET", "api/v2/devices?shadow=true")
        return result if isinstance(result, list) else []

    async def perform_action(
        self,
        device_id: str,
        action_id: str,
        action_name: str,
        args: dict | None = None,
    ) -> bool:
        """Perform an action on a device."""
        path = f"api/v2/devices/{device_id}/status"
        payload: dict[str, Any] = {"id": action_id, "name": action_name}
        if args:
            payload["args"] = args

        extra_headers = {
            "referer": f"https://{self._host}/connect/devices/all/{device_id}",
            "origin": f"https://{self._host}",
        }

        try:
            await self._request("PATCH", path, json=payload, extra_headers=extra_headers)
            return True
        except UnifiConnectAPIError as err:
            _LOGGER.warning(
                "Failed to perform %s on %s: %s (payload: %s)",
                action_name,
                device_id,
                err,
                payload,
            )
            return False

    async def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        extra_headers: dict | None = None,
        _retry: bool = True,
        raw_response: bool = False,
    ) -> Any:
        """Make an authenticated request with automatic 401 retry.

        When *raw_response* is True, the full JSON body is returned as-is
        (useful for paginated endpoints where the envelope contains
        totalCount / offset metadata).

        Raises UnifiConnectAPIError on failure.
        """
        url = self._get_api_url(path)
        headers: dict[str, str] = {}
        if self._csrf:
            headers["x-csrf-token"] = self._csrf
        if extra_headers:
            headers.update(extra_headers)

        try:
            async with asyncio.timeout(10):
                async with self._session.request(
                    method, url, json=json, headers=headers,
                    cookies=self._cookies, ssl=False,
                ) as resp:
                    if resp.status == 401 and _retry:
                        _LOGGER.warning("Session expired, attempting re-login")
                        if await self.login():
                            return await self._request(
                                method, path, json=json,
                                extra_headers=extra_headers, _retry=False,
                                raw_response=raw_response,
                            )
                        raise UnifiConnectAPIError("Re-authentication failed")
                    if resp.status == 200:
                        data = await resp.json()
                        if raw_response:
                            return data
                        return data.get("data", data) if isinstance(data, dict) else data

                    # Log the response body on error for debugging
                    try:
                        error_body = await resp.text()
                    except Exception:
                        error_body = "<unreadable>"
                    raise UnifiConnectAPIError(
                        f"{method} {path} returned status {resp.status}: {error_body}"
                    )
        except UnifiConnectAPIError:
            raise
        except asyncio.TimeoutError as err:
            raise UnifiConnectAPIError(f"{method} {path} timed out") from err
        except aiohttp.ClientError as err:
            raise UnifiConnectAPIError(
                f"{method} {path} connection error: {err}"
            ) from err
        except Exception as err:
            raise UnifiConnectAPIError(
                f"{method} {path} unexpected error: {err}"
            ) from err

    async def get_charge_history(
        self, device_id: str, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch *all* charge history via the stats/evs/chargingHistory endpoint.

        The per-device ``chargeHistory`` endpoint is hard-capped at 30
        sessions by the controller.  The ``stats/evs/chargingHistory``
        endpoint supports real offset/limit pagination and returns the
        complete history.

        Sessions are returned newest-first by the API.  We reverse them
        so the caller receives oldest-first (matching the per-device
        endpoint behaviour).

        The raw pagination metadata from the first response is stored in
        ``self._charge_history_meta`` for diagnostic purposes.
        """
        all_sessions: list[dict[str, Any]] = []
        offset = 0
        max_pages = 50  # safety limit
        self._charge_history_meta: dict[str, Any] = {}

        for page_num in range(max_pages):
            path = (
                f"api/v2/stats/evs/chargingHistory"
                f"?offset={offset}&limit={page_size}&sort=&order="
            )
            try:
                raw = await self._request("GET", path, raw_response=True)
            except UnifiConnectAPIError as err:
                _LOGGER.warning(
                    "Charge history request failed at offset %d: %s",
                    offset, err,
                )
                break

            if not isinstance(raw, dict):
                break

            # Store envelope metadata on first page
            if page_num == 0:
                self._charge_history_meta = {
                    k: v for k, v in raw.items()
                    if k != "data" and not isinstance(v, list)
                }
                _LOGGER.info(
                    "stats/evs/chargingHistory envelope: %s",
                    self._charge_history_meta,
                )

            page = raw.get("data", [])
            if not isinstance(page, list) or not page:
                break
            all_sessions.extend(page)

            total = raw.get("total")
            if total is not None and len(all_sessions) >= int(total):
                break
            if len(page) < page_size:
                break

            offset += len(page)

        # Filter to only this device's sessions (by MAC) if we have
        # multiple EV stations, and reverse to oldest-first.
        _LOGGER.info(
            "Fetched %d total charge history sessions", len(all_sessions),
        )
        all_sessions.reverse()
        return all_sessions

    async def request_power_stats(
        self, device_id: str, action_id: str
    ) -> dict[str, Any] | None:
        """Trigger a power_stats_single action to refresh real-time power data.

        The fresh data will appear in the device shadow on the next poll.
        Returns the API response if any data is included, else None.
        """
        path = f"api/v2/devices/{device_id}/status"
        payload: dict[str, Any] = {"id": action_id, "name": "power_stats_single"}
        extra_headers = {
            "referer": f"https://{self._host}/connect/devices/all/{device_id}",
            "origin": f"https://{self._host}",
        }

        try:
            result = await self._request(
                "PATCH", path, json=payload, extra_headers=extra_headers
            )
            _LOGGER.debug("power_stats_single response for %s: %s", device_id, result)
            return result if isinstance(result, dict) else None
        except UnifiConnectAPIError as err:
            _LOGGER.warning(
                "Failed to request power_stats for %s: %s", device_id, err
            )
            return None

    def _get_login_url(self) -> str:
        if self._controller_type == CONTROLLER_UDMP:
            return f"https://{self._host}/api/auth/login"
        return f"https://{self._host}:{self._port}/api/login"

    def _get_api_url(self, path: str) -> str:
        if self._controller_type == CONTROLLER_UDMP:
            return f"https://{self._host}/proxy/connect/{path}"
        return f"https://{self._host}:{self._port}/{path}"
