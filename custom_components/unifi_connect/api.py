from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import DEFAULT_PORT, CONTROLLER_UDMP, DEVICE_PLATFORM_SE21

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
        self._devices: dict[str, dict[str, Any]] = {}

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
        devices = result if isinstance(result, list) else []
        self._devices = {d["id"]: d for d in devices if "id" in d}
        return devices

    def resolve_action_id(self, device_id: str, action_name: str, fallback: str) -> str:
        """Return the console's own id for an action name on this device's model.

        The SE 21 keeps the ids hard-coded in const.py. Other models (e.g. Display Cast Pro) publish their action ids in
        ``type.supportedActions``, and those differ from the SE 21's, so they are looked up by name.
        """
        device = self._devices.get(device_id) or {}
        if (device.get("type") or {}).get("platform") == DEVICE_PLATFORM_SE21:
            return fallback
        for action in (device.get("type") or {}).get("supportedActions") or []:
            if action.get("name") == action_name and action.get("id"):
                return action["id"]
        return fallback

    async def perform_action(
        self,
        device_id: str,
        action_id: str,
        action_name: str,
        args: dict | None = None,
    ) -> bool:
        """Perform an action on a device."""
        path = f"api/v2/devices/{device_id}/status"
        payload: dict[str, Any] = {
            "id": self.resolve_action_id(device_id, action_name, action_id),
            "name": action_name,
        }
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
                "Failed to perform %s on %s: %s", action_name, device_id, err
            )
            return False

    async def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        extra_headers: dict | None = None,
        _retry: bool = True,
    ) -> Any:
        """Make an authenticated request with automatic 401 retry.

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
                            )
                        raise UnifiConnectAPIError("Re-authentication failed")
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("data", data) if isinstance(data, dict) else data
                    raise UnifiConnectAPIError(
                        f"{method} {path} returned status {resp.status}"
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

    def _get_login_url(self) -> str:
        if self._controller_type == CONTROLLER_UDMP:
            return f"https://{self._host}/api/auth/login"
        return f"https://{self._host}:{self._port}/api/login"

    def _get_api_url(self, path: str) -> str:
        if self._controller_type == CONTROLLER_UDMP:
            return f"https://{self._host}/proxy/connect/{path}"
        return f"https://{self._host}:{self._port}/{path}"
