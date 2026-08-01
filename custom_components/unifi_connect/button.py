import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DEVICE_PLATFORM_SE21, ACTION_REFRESH_WEBSITE, EV_ACTION_REBOOT
from .coordinator import _is_ev_device
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect button entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []

    for device in hub.coordinator.data or []:
        platform = device.get("type", {}).get("platform")

        # SE21 Display buttons
        if platform == DEVICE_PLATFORM_SE21:
            entities.append(ReloadWebButton(hub, device))

        # EV Station buttons
        if _is_ev_device(device):
            entities.append(EVRebootButton(hub, device))

    async_add_entities(entities)


class ReloadWebButton(UnifiConnectEntity, ButtonEntity):
    """Reload Web Page button for SE 21."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Reload Web Page", "reload_web")

    async def async_press(self):
        await self._hub.api.perform_action(
            self._device_id, ACTION_REFRESH_WEBSITE, "refresh_website"
        )
        await self.coordinator.async_request_refresh()


class EVRebootButton(UnifiConnectEntity, ButtonEntity):
    """Reboot button for EV Station."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Reboot", "reboot")
        self._attr_icon = "mdi:restart"

    async def async_press(self):
        await self._hub.api.perform_action(
            self._device_id, EV_ACTION_REBOOT, "reboot"
        )
        await self.coordinator.async_request_refresh()
