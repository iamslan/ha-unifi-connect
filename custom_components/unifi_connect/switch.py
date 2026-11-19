import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SUPPORTED_PLATFORMS,
    ACTION_DISPLAY_ON,
    ACTION_DISPLAY_OFF,
    ACTION_ENABLE_AUTO_ROTATE,
    ACTION_DISABLE_AUTO_ROTATE,
    ACTION_ENABLE_AUTO_RELOAD,
    ACTION_DISABLE_AUTO_RELOAD,
    ACTION_ENABLE_SLEEP,
    ACTION_DISABLE_SLEEP,
    ACTION_ENABLE_AUTO_SLEEP,
    ACTION_DISABLE_AUTO_SLEEP,
)
from .entity import UnifiConnectEntity, supports_action
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)

TOGGLE_SWITCHES = [
    {
        "shadow_key": "autoRotate",
        "name_suffix": "Auto Rotate",
        "unique_suffix": "auto_rotate",
        "enable_action_id": ACTION_ENABLE_AUTO_ROTATE,
        "enable_action_name": "enable_auto_rotate",
        "disable_action_id": ACTION_DISABLE_AUTO_ROTATE,
        "disable_action_name": "disable_auto_rotate",
    },
    {
        "shadow_key": "autoReload",
        "name_suffix": "Auto Reload",
        "unique_suffix": "auto_reload",
        "enable_action_id": ACTION_ENABLE_AUTO_RELOAD,
        "enable_action_name": "enable_auto_reload",
        "disable_action_id": ACTION_DISABLE_AUTO_RELOAD,
        "disable_action_name": "disable_auto_reload",
    },
    {
        "shadow_key": "sleepMode",
        "name_suffix": "Sleep Mode",
        "unique_suffix": "sleep_mode",
        "enable_action_id": ACTION_ENABLE_SLEEP,
        "enable_action_name": "enable_sleep",
        "disable_action_id": ACTION_DISABLE_SLEEP,
        "disable_action_name": "disable_sleep",
    },
    {
        "shadow_key": "autoSleep",
        "name_suffix": "Auto Sleep",
        "unique_suffix": "auto_sleep",
        "enable_action_id": ACTION_ENABLE_AUTO_SLEEP,
        "enable_action_name": "enable_memorize_playlist",
        "disable_action_id": ACTION_DISABLE_AUTO_SLEEP,
        "disable_action_name": "disable_memorize_playlist",
    },
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect switch entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    for device in hub.coordinator.data or []:
        if device.get("type", {}).get("platform") not in SUPPORTED_PLATFORMS:
            continue

        entities.append(DisplayPowerSwitch(hub, device))

        shadow = device.get("shadow", {})
        for config in TOGGLE_SWITCHES:
            if config["shadow_key"] in shadow and supports_action(
                device, config["enable_action_name"], config["disable_action_name"]
            ):
                entities.append(ToggleSwitch(hub, device, config))

    async_add_entities(entities)


class DisplayPowerSwitch(UnifiConnectEntity, SwitchEntity):
    """Power control switch for UniFi Display."""

    @property
    def is_on(self):
        return self._get_shadow().get("display", False)

    async def async_turn_on(self, **kwargs):
        await self._send_command(ACTION_DISPLAY_ON, "display_on")

    async def async_turn_off(self, **kwargs):
        await self._send_command(ACTION_DISPLAY_OFF, "display_off")

    async def _send_command(self, action_id: str, action_name: str):
        success = await self._hub.api.perform_action(
            self._device_id, action_id, action_name
        )
        if success:
            # Device needs time to process display state changes
            await asyncio.sleep(2)
            await self.coordinator.async_request_refresh()


class ToggleSwitch(UnifiConnectEntity, SwitchEntity):
    """Generic toggle switch driven by configuration."""

    def __init__(self, hub: UnifiConnectHub, device: dict, config: dict):
        super().__init__(hub, device, config["name_suffix"], config["unique_suffix"])
        self._shadow_key = config["shadow_key"]
        self._enable_action_id = config["enable_action_id"]
        self._enable_action_name = config["enable_action_name"]
        self._disable_action_id = config["disable_action_id"]
        self._disable_action_name = config["disable_action_name"]

    @property
    def is_on(self):
        return self._get_shadow().get(self._shadow_key, False)

    async def async_turn_on(self, **kwargs):
        await self._hub.api.perform_action(
            self._device_id, self._enable_action_id, self._enable_action_name
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self._hub.api.perform_action(
            self._device_id, self._disable_action_id, self._disable_action_name
        )
        await self.coordinator.async_request_refresh()
