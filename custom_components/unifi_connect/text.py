import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUPPORTED_PLATFORMS, ACTION_LOAD_WEBSITE
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect text entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[TextEntity] = []

    for device in hub.coordinator.data or []:
        if device.get("type", {}).get("platform") not in SUPPORTED_PLATFORMS:
            continue
        if device.get("shadow", {}).get("currentHomePage") is not None:
            entities.append(DisplayWebUrlText(hub, device))

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
