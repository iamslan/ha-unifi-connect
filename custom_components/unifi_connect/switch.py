import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .actions import resolve_action_id
from .const import (
    DOMAIN, DEVICE_PLATFORM_SE21,
    ACTION_DISPLAY_ON, ACTION_DISPLAY_OFF,
    ACTION_ENABLE_AUTO_ROTATE, ACTION_DISABLE_AUTO_ROTATE,
    ACTION_ENABLE_AUTO_RELOAD, ACTION_DISABLE_AUTO_RELOAD,
    ACTION_ENABLE_SLEEP, ACTION_DISABLE_SLEEP,
    ACTION_ENABLE_AUTO_SLEEP, ACTION_DISABLE_AUTO_SLEEP,
    EV_ACTION_ENABLE_STATUS_LIGHT, EV_ACTION_DISABLE_STATUS_LIGHT,
    EV_ACTION_ENABLE_CHARGING, EV_ACTION_DISABLE_CHARGING,
    EV_ACTION_START_LOCATING, EV_ACTION_STOP_LOCATING,
)
from .coordinator import _is_ev_device
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)

# SE21 Display toggle switches
TOGGLE_SWITCHES = [
    {
        "shadow_key": "display",
        "name_suffix": "Display Power",
        "unique_suffix": "display_power",
        "action_on": ACTION_DISPLAY_ON,
        "action_off": ACTION_DISPLAY_OFF,
        "action_name_on": "display_on",
        "action_name_off": "display_off",
    },
    {
        "shadow_key": "autoRotate",
        "name_suffix": "Auto Rotate",
        "unique_suffix": "auto_rotate",
        "action_on": ACTION_ENABLE_AUTO_ROTATE,
        "action_off": ACTION_DISABLE_AUTO_ROTATE,
        "action_name_on": "enable_auto_rotate",
        "action_name_off": "disable_auto_rotate",
    },
    {
        "shadow_key": "autoReload",
        "name_suffix": "Auto Reload",
        "unique_suffix": "auto_reload",
        "action_on": ACTION_ENABLE_AUTO_RELOAD,
        "action_off": ACTION_DISABLE_AUTO_RELOAD,
        "action_name_on": "enable_auto_reload",
        "action_name_off": "disable_auto_reload",
    },
    {
        "shadow_key": "sleepMode",
        "name_suffix": "Sleep Mode",
        "unique_suffix": "sleep_mode",
        "action_on": ACTION_ENABLE_SLEEP,
        "action_off": ACTION_DISABLE_SLEEP,
        "action_name_on": "enable_sleep",
        "action_name_off": "disable_sleep",
    },
    {
        "shadow_key": "autoSleep",
        "name_suffix": "Auto Sleep",
        "unique_suffix": "auto_sleep",
        "action_on": ACTION_ENABLE_AUTO_SLEEP,
        "action_off": ACTION_DISABLE_AUTO_SLEEP,
        "action_name_on": "enable_auto_sleep",
        "action_name_off": "disable_auto_sleep",
    },
]

# EV Station toggle switches - action IDs resolved dynamically at init
EV_TOGGLE_SWITCHES = [
    {
        "shadow_key": "statusLightEnabled",
        "name_suffix": "Status Light",
        "unique_suffix": "status_light",
        "fallback_action_on": EV_ACTION_ENABLE_STATUS_LIGHT,
        "fallback_action_off": EV_ACTION_DISABLE_STATUS_LIGHT,
        "action_name_on": "enable_status_light",
        "action_name_off": "disable_status_light",
        "icon": "mdi:led-on",
        "source": "shadow",
    },
    {
        "shadow_key": "enabledCharging",
        "name_suffix": "Charging",
        "unique_suffix": "charging_enabled",
        "fallback_action_on": EV_ACTION_ENABLE_CHARGING,
        "fallback_action_off": EV_ACTION_DISABLE_CHARGING,
        "action_name_on": "enable_charging",
        "action_name_off": "disable_charging",
        "icon": "mdi:ev-plug-type2",
        "source": "relayShadow",
    },
    {
        "shadow_key": "locating",
        "name_suffix": "Locating",
        "unique_suffix": "locating",
        "fallback_action_on": EV_ACTION_START_LOCATING,
        "fallback_action_off": EV_ACTION_STOP_LOCATING,
        "action_name_on": "start_locating",
        "action_name_off": "stop_locating",
        "icon": "mdi:crosshairs-gps",
        "source": "shadow",
    },
]

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect switch entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    for device in hub.coordinator.data or []:
        platform = device.get("type", {}).get("platform")

        # SE21 Display switches
        if platform == DEVICE_PLATFORM_SE21:
            shadow = device.get("shadow", {})
            for config in TOGGLE_SWITCHES:
                if config["shadow_key"] in shadow:
                    entities.append(DisplayToggleSwitch(hub, device, config))

        # EV Station switches
        if _is_ev_device(device):
            shadow = device.get("shadow", {})
            relay_shadow = device.get("relayShadow", {})
            for config in EV_TOGGLE_SWITCHES:
                source = shadow if config.get("source") == "shadow" else relay_shadow
                if config["shadow_key"] in source:
                    entities.append(EVToggleSwitch(hub, device, config))

    async_add_entities(entities)


class DisplayToggleSwitch(UnifiConnectEntity, SwitchEntity):
    """On/Off switch for SE 21 controls."""

    def __init__(self, hub: UnifiConnectHub, device: dict, config: dict):
        super().__init__(hub, device, config["name_suffix"], config["unique_suffix"])
        self._shadow_key = config["shadow_key"]
        self._action_on = config["action_on"]
        self._action_off = config["action_off"]
        self._action_name_on = config["action_name_on"]
        self._action_name_off = config["action_name_off"]

    @property
    def is_on(self):
        return bool(self._get_shadow().get(self._shadow_key))

    async def async_turn_on(self, **kwargs):
        await self._hub.api.perform_action(
            self._device_id, self._action_on, self._action_name_on
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self._hub.api.perform_action(
            self._device_id, self._action_off, self._action_name_off
        )
        await self.coordinator.async_request_refresh()


class EVToggleSwitch(UnifiConnectEntity, SwitchEntity):
    """On/Off switch for EV Station controls using perform_action.

    Action IDs are resolved dynamically from device data to handle
    firmware updates that change UUIDs.
    """

    def __init__(self, hub: UnifiConnectHub, device: dict, config: dict):
        super().__init__(hub, device, config["name_suffix"], config["unique_suffix"])
        self._shadow_key = config["shadow_key"]
        self._action_name_on = config["action_name_on"]
        self._action_name_off = config["action_name_off"]
        self._attr_icon = config.get("icon")
        self._source = config.get("source", "shadow")

        # Dynamically resolve action IDs
        self._action_on = resolve_action_id(
            device, config["action_name_on"], config.get("fallback_action_on")
        )
        self._action_off = resolve_action_id(
            device, config["action_name_off"], config.get("fallback_action_off")
        )

        _LOGGER.debug(
            "EV switch '%s': on_id=%s, off_id=%s",
            config["name_suffix"],
            self._action_on,
            self._action_off,
        )

    @property
    def is_on(self):
        if self._source == "relayShadow":
            device = self._get_device()
            if device:
                return bool(device.get("relayShadow", {}).get(self._shadow_key))
            return None
        return bool(self._get_shadow().get(self._shadow_key))

    async def async_turn_on(self, **kwargs):
        await self._hub.api.perform_action(
            self._device_id, self._action_on, self._action_name_on
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self._hub.api.perform_action(
            self._device_id, self._action_off, self._action_name_off
        )
        await self.coordinator.async_request_refresh()
