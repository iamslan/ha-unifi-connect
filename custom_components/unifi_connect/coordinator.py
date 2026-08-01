import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UnifiConnectAPI, UnifiConnectAPIError
from .const import (
    DOMAIN,
    DEFAULT_REFRESH_INTERVAL,
    EV_DEVICE_PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


def _is_ev_device(device: dict) -> bool:
    """Check if a device is an EV Station (by platform or supported actions)."""
    platform = device.get("type", {}).get("platform", "")
    if platform in EV_DEVICE_PLATFORMS:
        return True
    # Also detect by shadow keys unique to EV devices
    shadow = device.get("shadow", {})
    if "chargingStatus" in shadow or "evStationMode" in shadow:
        return True
    # Also detect by supported actions for unknown platform IDs
    actions = device.get("supportedActions", [])
    action_names = [a.get("name", "") if isinstance(a, dict) else "" for a in actions]
    return "power_stats_single" in action_names


def _get_action_id(device: dict, action_name: str) -> str | None:
    """Find the action ID for a named action from the device's supportedActions."""
    for action in device.get("supportedActions", []):
        if isinstance(action, dict) and action.get("name") == action_name:
            return action.get("id")
    return None


class UnifiConnectCoordinator(DataUpdateCoordinator):
    """Class to manage fetching UniFi Connect data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: UnifiConnectAPI,
        update_interval: int = DEFAULT_REFRESH_INTERVAL,
    ):
        self.api = api
        self.charge_history: dict[str, list] = {}
        self._first_run = True
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self):
        """Fetch data from UniFi Connect API."""
        try:
            devices = await self.api.get_devices()

            # Log device info on first run for debugging
            if self._first_run:
                _LOGGER.info(
                    "DEVICES FOUND: %s",
                    [
                        {
                            "name": d.get("name"),
                            "platform": d.get("type", {}).get("platform"),
                            "shadow_keys": list(d.get("shadow", {}).keys()),
                            "actions": [
                                a.get("name")
                                for a in d.get("supportedActions", [])
                                if isinstance(a, dict)
                            ],
                        }
                        for d in devices or []
                    ],
                )

            # For EV devices, fetch charge history and optionally trigger power stats
            for device in devices or []:
                if not _is_ev_device(device):
                    continue

                device_id = device.get("id")
                if not device_id:
                    continue

                # Log shadow values on first run
                if self._first_run:
                    shadow = device.get("shadow", {})
                    _LOGGER.info(
                        "EV device %s shadow values: %s",
                        device.get("name"),
                        shadow,
                    )

                # Trigger power_stats_single if available
                action_id = _get_action_id(device, "power_stats_single")
                if action_id:
                    await self.api.request_power_stats(device_id, action_id)

                # Fetch charge history from stats endpoint (all devices)
                try:
                    all_history = await self.api.get_charge_history(device_id)
                    # Filter sessions for this device by MAC address
                    device_mac = device.get("mac", "").upper()
                    if device_mac:
                        history = [
                            s for s in all_history
                            if s.get("mac", "").upper() == device_mac
                        ]
                    else:
                        history = all_history
                    self.charge_history[device_id] = history
                    if self._first_run:
                        _LOGGER.info(
                            "EV device %s charge history: %d sessions "
                            "(from %d total across all devices)",
                            device.get("name"),
                            len(history),
                            len(all_history),
                        )
                except Exception as err:
                    if self._first_run:
                        _LOGGER.info(
                            "Could not fetch charge history for %s: %s",
                            device.get("name"),
                            err,
                        )

            self._first_run = False
            return devices
        except UnifiConnectAPIError as err:
            raise UpdateFailed(f"Error fetching UniFi Connect data: {err}") from err
