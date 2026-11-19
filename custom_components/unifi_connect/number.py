import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUPPORTED_PLATFORMS, ACTION_BRIGHTNESS, ACTION_VOLUME
from .entity import UnifiConnectEntity, supports_action
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)

NUMBER_ENTITIES = [
    {
        "feature_key": "brightness",
        "shadow_key": "brightness",
        "name_suffix": "Brightness",
        "unique_suffix": "brightness",
        "action_id": ACTION_BRIGHTNESS,
        "action_name": "brightness",
        "default_min": 0,
        "default_max": 255,
    },
    {
        "feature_key": "volume",
        "shadow_key": "volume",
        "name_suffix": "Volume",
        "unique_suffix": "volume",
        "action_id": ACTION_VOLUME,
        "action_name": "volume",
        "default_min": 0,
        "default_max": 40,
    },
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect number entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for device in hub.coordinator.data or []:
        if device.get("type", {}).get("platform") not in SUPPORTED_PLATFORMS:
            continue

        features = device.get("featureFlags", {})
        for config in NUMBER_ENTITIES:
            if config["feature_key"] in features and supports_action(device, config["action_name"]):
                entities.append(DisplayNumberSlider(hub, device, config))

    async_add_entities(entities)


class DisplayNumberSlider(UnifiConnectEntity, NumberEntity):
    """Configurable number slider for SE 21 controls."""

    def __init__(self, hub: UnifiConnectHub, device: dict, config: dict):
        super().__init__(hub, device, config["name_suffix"], config["unique_suffix"])
        self._shadow_key = config["shadow_key"]
        self._action_id = config["action_id"]
        self._action_name = config["action_name"]

        feature_range = device.get("featureFlags", {}).get(config["feature_key"], {})
        self._attr_native_min_value = feature_range.get("min", config["default_min"])
        self._attr_native_max_value = feature_range.get("max", config["default_max"])
        self._attr_native_step = 1
        self._attr_mode = "slider"

    @property
    def native_value(self):
        return self._get_shadow().get(self._shadow_key, 0)

    async def async_set_native_value(self, value: float):
        await self._hub.api.perform_action(
            self._device_id,
            self._action_id,
            self._action_name,
            {"value": int(value)},
        )
        await self.coordinator.async_request_refresh()
