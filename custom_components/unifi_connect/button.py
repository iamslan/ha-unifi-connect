import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUPPORTED_PLATFORMS, ACTION_REFRESH_WEBSITE
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect buttons from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []

    for device in hub.coordinator.data or []:
        if device.get("type", {}).get("platform") in SUPPORTED_PLATFORMS:
            entities.append(ReloadWebButton(hub, device))

    async_add_entities(entities)


class ReloadWebButton(UnifiConnectEntity, ButtonEntity):
    """Button to reload the current web page."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Reload Web Page", "reload_web")

    async def async_press(self) -> None:
        await self._hub.api.perform_action(
            self._device_id, ACTION_REFRESH_WEBSITE, "refresh_website"
        )
        await self.coordinator.async_request_refresh()
