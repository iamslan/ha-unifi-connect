import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN, DEVICE_PLATFORM_SE21, ACTION_LOAD_WEBSITE,
    EV_ACTION_SET_DISPLAY_LABEL, EV_ACTION_SET_ADMIN_MESSAGE,
)
from .coordinator import _is_ev_device
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)

# EV Station text controls - matches UniFi Connect UI fields
EV_TEXT_ENTITIES = [
    {
        "shadow_key": "displayLabel",
        "name_suffix": "Display Label",
        "unique_suffix": "display_label",
        "action_id": EV_ACTION_SET_DISPLAY_LABEL,
        "action_name": "set_display_label",
        "icon": "mdi:label",
        "max_length": 15,
        "args_builder": lambda value: {"enableDisplayLabel": True, "displayLabel": value},
    },
    {
        "shadow_key": "adminMessage",
        "name_suffix": "Support Information",
        "unique_suffix": "admin_message",
        "action_id": EV_ACTION_SET_ADMIN_MESSAGE,
        "action_name": "set_admin_message",
        "icon": "mdi:message-text",
        "max_length": 512,
        "args_builder": lambda value: {"adminMessage": value},
    },
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect text entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[TextEntity] = []

    for device in hub.coordinator.data or []:
        platform = device.get("type", {}).get("platform")

        # SE21 Display text entities
        if platform == DEVICE_PLATFORM_SE21:
            if device.get("shadow", {}).get("currentHomePage") is not None:
                entities.append(DisplayWebUrlText(hub, device))

        # EV Station text entities
        if _is_ev_device(device):
            shadow = device.get("shadow", {})
            for config in EV_TEXT_ENTITIES:
                if config["shadow_key"] in shadow:
                    entities.append(EVText(hub, device, config))

    async_add_entities(entities)


class DisplayWebUrlText(UnifiConnectEntity, TextEntity):
    """Web URL field for SE 21."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Web URL", "web_url")
        self._attr_native_min = 0
        self._attr_native_max = 512

    @property
    def native_value(self):
        return self._get_shadow().get("currentHomePage", "")

    async def async_set_value(self, value: str) -> None:
        await self._hub.api.perform_action(
            self._device_id,
            ACTION_LOAD_WEBSITE,
            "load_website",
            {"url": value},
        )
        await self.coordinator.async_request_refresh()


class EVText(UnifiConnectEntity, TextEntity):
    """Text input for EV Station settings using perform_action."""

    def __init__(self, hub: UnifiConnectHub, device: dict, config: dict):
        super().__init__(hub, device, config["name_suffix"], config["unique_suffix"])
        self._shadow_key = config["shadow_key"]
        self._action_id = config["action_id"]
        self._action_name = config["action_name"]
        self._attr_icon = config.get("icon")
        self._attr_native_min = 0
        self._attr_native_max = config.get("max_length", 256)
        self._args_builder = config.get("args_builder")

    @property
    def native_value(self):
        value = self._get_shadow().get(self._shadow_key)
        return str(value) if value is not None else ""

    async def async_set_value(self, value: str) -> None:
        args = self._args_builder(value) if self._args_builder else {"value": value}
        await self._hub.api.perform_action(
            self._device_id,
            self._action_id,
            self._action_name,
            args,
        )
        await self.coordinator.async_request_refresh()
