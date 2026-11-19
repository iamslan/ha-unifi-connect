import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUPPORTED_PLATFORMS, ACTION_MODE_SWITCH, ACTION_LAUNCH_APP
from .entity import UnifiConnectEntity, supports_action
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect select entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []

    for device in hub.coordinator.data or []:
        if device.get("type", {}).get("platform") not in SUPPORTED_PLATFORMS:
            continue

        features = device.get("featureFlags", {})
        if "mode" in features:
            entities.append(DisplayModeSelect(hub, device))

        if "appList" in device.get("shadow", {}) and supports_action(device, "launch_app"):
            entities.append(AppSelect(hub, device))

    async_add_entities(entities)


class DisplayModeSelect(UnifiConnectEntity, SelectEntity):
    """Mode selector for SE 21."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Mode", "mode")
        self._attr_options = (
            device.get("featureFlags", {}).get("mode", {}).get("enum", [])
        )

    @property
    def current_option(self):
        return self._get_shadow().get("mode", "web")

    async def async_select_option(self, option: str):
        await self._hub.api.perform_action(
            self._device_id,
            ACTION_MODE_SWITCH,
            "switch",
            {"mode": option},
        )
        await self.coordinator.async_request_refresh()


class AppSelect(UnifiConnectEntity, SelectEntity):
    """Dropdown to launch an app in app mode."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "App Selector", "app_select")

    @property
    def options(self):
        """Dynamically return current app list from coordinator data."""
        apps = self._get_shadow().get("appList", [])
        return [app["packageName"] for app in apps if app.get("packageName")]

    @property
    def current_option(self):
        for app in self._get_shadow().get("appList", []):
            if app.get("selected"):
                return app.get("packageName")
        return None

    async def async_select_option(self, option: str):
        await self._hub.api.perform_action(
            self._device_id,
            ACTION_LAUNCH_APP,
            "launch_app",
            {"packageName": option},
        )
        await self.coordinator.async_request_refresh()
