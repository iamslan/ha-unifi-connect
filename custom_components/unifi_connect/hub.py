from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import UnifiConnectAPI
from .coordinator import UnifiConnectCoordinator
from .const import DEFAULT_PORT, CONTROLLER_UDMP
from .websocket import UnifiConnectWebSocket


class UnifiConnectHub:
    """Manages connection and data coordination with UniFi Connect."""

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry

        session = async_create_clientsession(hass, verify_ssl=False)
        self.api = UnifiConnectAPI(
            host=entry.data["host"],
            username=entry.data["username"],
            password=entry.data["password"],
            port=entry.data.get("port", DEFAULT_PORT),
            controller_type=entry.data.get("controller_type", CONTROLLER_UDMP),
            session=session,
        )

        self.coordinator = UnifiConnectCoordinator(hass=hass, api=self.api)

        # WebSocket for real-time EV power data
        # Pass a cookie getter so the WS can authenticate even when the
        # session cookie jar drops cookies for bare IP addresses.
        self.websocket = UnifiConnectWebSocket(
            host=entry.data["host"],
            session=session,
            controller_type=entry.data.get("controller_type", CONTROLLER_UDMP),
            get_cookies=lambda: self.api._cookies,
        )

    async def async_initialize(self):
        """Log in, fetch initial data, and start the WebSocket listener."""
        if not await self.api.login():
            raise ConfigEntryNotReady("Unable to log in to UniFi Connect")
        await self.coordinator.async_config_entry_first_refresh()

        # Start WebSocket for real-time power data
        await self.websocket.start()

    async def async_shutdown(self):
        """Stop WebSocket listener on unload."""
        await self.websocket.stop()
