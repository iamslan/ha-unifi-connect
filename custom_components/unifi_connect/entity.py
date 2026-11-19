from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DEVICE_PLATFORM_SE21


def supports_action(device: dict, *names: str) -> bool:
    """True if the device can perform all the named actions.

    The SE 21 is always treated as supporting its known actions. Other models are checked against the action list the console
    publishes for them, so we don't create controls that would only return errors.
    """
    t = device.get("type") or {}
    if t.get("platform") == DEVICE_PLATFORM_SE21:
        return True
    have = {a.get("name") for a in t.get("supportedActions") or []}
    return all(n in have for n in names)


class UnifiConnectEntity(CoordinatorEntity):
    """Base entity for UniFi Connect devices."""

    def __init__(self, hub, device, name_suffix="", unique_suffix=""):
        super().__init__(hub.coordinator)
        self._hub = hub
        self._device_id = device["id"]
        device_name = device.get("name", f"UniFi Device {device['id']}")
        self._attr_name = f"{device_name} {name_suffix}" if name_suffix else device_name
        self._attr_unique_id = (
            f"{device['id']}_{unique_suffix}" if unique_suffix else device["id"]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device["id"])},
            name=device_name,
            manufacturer="Ubiquiti",
            model=device.get("type", {}).get("fullName", "Unknown"),
        )

    def _get_shadow(self) -> dict:
        """Get the current shadow state for this device from coordinator data."""
        for d in self.coordinator.data or []:
            if d["id"] == self._device_id:
                return d.get("shadow", {})
        return {}
