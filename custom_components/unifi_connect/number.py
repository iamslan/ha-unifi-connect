import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .actions import resolve_action_id, get_action_arg_key
from .const import (
    DOMAIN, DEVICE_PLATFORM_SE21, ACTION_BRIGHTNESS, ACTION_VOLUME,
    EV_ACTION_SET_MAX_OUTPUT, EV_ACTION_BRIGHTNESS,
)
from .coordinator import _is_ev_device
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)

# SE21 Display number entities
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

# EV Station number entities - action IDs resolved dynamically at init
EV_NUMBER_ENTITIES = [
    {
        "shadow_key": "maxOutput",
        "feature_key": "maxOutput",
        "name_suffix": "Maximum Output",
        "unique_suffix": "max_output_setting",
        "fallback_action_id": EV_ACTION_SET_MAX_OUTPUT,
        "action_name": "set_max_output_amp",
        "fallback_arg_key": "maxOutput",
        "default_min": 6,
        "default_max": 40,
        "step": 1,
        "icon": "mdi:current-ac",
        "unit": "A",
    },
    {
        "shadow_key": "brightness",
        "feature_key": "brightness",
        "name_suffix": "Brightness",
        "unique_suffix": "ev_brightness_ctrl",
        "fallback_action_id": EV_ACTION_BRIGHTNESS,
        "action_name": "brightness",
        "fallback_arg_key": "value",
        "default_min": 0,
        "default_max": 255,
        "step": 1,
        "icon": "mdi:brightness-6",
        "unit": None,
    },
]

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect number entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for device in hub.coordinator.data or []:
        platform = device.get("type", {}).get("platform")

        # SE21 Display entities
        if platform == DEVICE_PLATFORM_SE21:
            features = device.get("featureFlags", {})
            for config in NUMBER_ENTITIES:
                if config["feature_key"] in features:
                    entities.append(DisplayNumberSlider(hub, device, config))

        # EV Station entities
        if _is_ev_device(device):
            shadow = device.get("shadow", {})
            features = device.get("featureFlags", {})
            for config in EV_NUMBER_ENTITIES:
                if config["shadow_key"] in shadow:
                    entities.append(EVNumberSlider(hub, device, config, features))

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


class EVNumberSlider(UnifiConnectEntity, NumberEntity):
    """Number slider for EV Station controls using perform_action.

    Action IDs are resolved dynamically from device data to handle
    firmware updates that change UUIDs.
    """

    def __init__(self, hub: UnifiConnectHub, device: dict, config: dict, features: dict):
        super().__init__(hub, device, config["name_suffix"], config["unique_suffix"])
        self._shadow_key = config["shadow_key"]
        self._action_name = config["action_name"]
        self._attr_icon = config.get("icon")
        if config.get("unit"):
            self._attr_native_unit_of_measurement = config["unit"]

        # Dynamically resolve action ID from device data
        self._action_id = resolve_action_id(
            device, config["action_name"], config.get("fallback_action_id")
        )

        # Dynamically resolve the arg key name from the action's JSON schema
        dynamic_arg_key = get_action_arg_key(device, config["action_name"])
        self._arg_key = dynamic_arg_key or config.get("fallback_arg_key", "value")

        _LOGGER.debug(
            "EV number '%s': action_id=%s, arg_key='%s'",
            config["action_name"],
            self._action_id,
            self._arg_key,
        )

        # Use featureFlags for min/max if available, otherwise use defaults
        feature_range = features.get(config.get("feature_key", ""), {})
        if isinstance(feature_range, dict):
            self._attr_native_min_value = feature_range.get("min", config["default_min"])
            self._attr_native_max_value = feature_range.get("max", config["default_max"])
        else:
            self._attr_native_min_value = config["default_min"]
            self._attr_native_max_value = config["default_max"]

        self._attr_native_step = config.get("step", 1)
        self._attr_mode = "slider"

    @property
    def native_value(self):
        return self._get_shadow().get(self._shadow_key, 0)

    async def async_set_native_value(self, value: float):
        await self._hub.api.perform_action(
            self._device_id,
            self._action_id,
            self._action_name,
            {self._arg_key: int(value)},
        )
        await self.coordinator.async_request_refresh()
