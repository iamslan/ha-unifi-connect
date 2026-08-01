"""Sensor platform for UniFi Connect EV Station devices.

Exposes read-only charging status, charge session history, statistics,
and per-session cost estimates using Ontario TOU rates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import _is_ev_device
from .entity import UnifiConnectEntity
from .hub import UnifiConnectHub

_LOGGER = logging.getLogger(__name__)

# Ordered list of keys to try when extracting energy from a charge session.
# The stats/evs/chargingHistory endpoint uses "powerUsage" (kWh).
# The per-device chargeHistory endpoint uses "energy".
_ENERGY_KEYS = ("powerUsage", "energyDelivered", "energy", "totalEnergy", "kwh", "wh")


def _extract_energy(session: dict) -> float | None:
    """Extract energy value from a charge session dict.

    Uses explicit ``is not None`` checks so that a legitimate ``0``
    value is returned instead of falling through to the next key
    (plain ``or`` chains treat 0 as falsy).
    """
    for key in _ENERGY_KEYS:
        value = session.get(key)
        if value is not None:
            return value
    return None


def _extract_charge_start(session: dict) -> float:
    """Extract the charge start timestamp (Unix seconds) from a session.

    The stats endpoint uses ``date`` (Unix seconds).
    The per-device endpoint uses ``chargeStart`` (Unix seconds or ISO).
    """
    # stats endpoint: "date" is unix seconds
    ts = session.get("date")
    if ts is not None:
        try:
            return float(ts)
        except (ValueError, TypeError):
            pass
    # per-device endpoint
    ts = session.get("chargeStart", 0)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            return 0
    return float(ts) if ts else 0


def _extract_charge_end(session: dict) -> float:
    """Extract the charge end timestamp from a session."""
    # stats endpoint: date + totalTime
    start = session.get("date")
    total_time = session.get("totalTime")
    if start is not None and total_time is not None:
        try:
            return float(start) + float(total_time)
        except (ValueError, TypeError):
            pass
    # per-device endpoint
    return float(session.get("chargeEnd", 0) or 0)


def _extract_source(session: dict) -> str:
    """Extract the session source/mode."""
    return session.get("usageMode", session.get("source", ""))


def _parse_duration_seconds(value) -> float:
    """Parse a duration value into total seconds.

    Handles both:
      - ISO datetime-from-epoch like "1970-01-01T02:06:37+00:00" → 7597s
      - Numeric seconds directly
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            # Duration encoded as datetime from epoch
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            return (dt - epoch).total_seconds()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _format_duration(total_seconds: float) -> str:
    """Format seconds into Xh Ym Zs string."""
    total_seconds = int(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _get_tou_period(timestamp: int | float, tz_name: str = "America/Toronto") -> str:
    """Determine Ontario TOU period for a given Unix timestamp.

    Ontario TOU schedule:
    - Winter (Nov 1 - Apr 30):
      Off-peak: 7pm-7am weekdays, all day weekends/holidays
      Mid-peak: 11am-5pm weekdays
      On-peak:  7am-11am and 5pm-7pm weekdays
    - Summer (May 1 - Oct 31):
      Off-peak: 7pm-7am weekdays, all day weekends/holidays
      Mid-peak: 7am-11am and 5pm-7pm weekdays
      On-peak:  11am-5pm weekdays
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    dt = datetime.fromtimestamp(timestamp, tz=tz)

    # Weekends are always off-peak
    if dt.weekday() >= 5:
        return "off_peak"

    hour = dt.hour
    month = dt.month

    # Off-peak hours (all seasons): 7pm-7am
    if hour < 7 or hour >= 19:
        return "off_peak"

    # Determine season
    is_winter = month >= 11 or month <= 4

    if is_winter:
        # Winter: On-peak 7-11, Mid-peak 11-17, On-peak 17-19
        if 7 <= hour < 11 or 17 <= hour < 19:
            return "on_peak"
        return "mid_peak"  # 11-17
    else:
        # Summer: Mid-peak 7-11, On-peak 11-17, Mid-peak 17-19
        if 11 <= hour < 17:
            return "on_peak"
        return "mid_peak"  # 7-11, 17-19


def _get_tou_rate(period: str, hass=None) -> float:
    """Get TOU rate in $/kWh for the given period.

    Reads from input_number helpers if they exist, otherwise uses
    Ontario TOU defaults (as of 2025).

    Supports two helper naming conventions:
      - input_number.tou_rate_off_peak  (in ¢/kWh, divided by 100)
      - input_number.ev_rate_off_peak   (in $/kWh, used directly)
    """
    defaults = {
        "off_peak": 0.087,
        "mid_peak": 0.122,
        "on_peak": 0.180,
    }
    if hass:
        # Try ¢/kWh helpers first (existing house energy helpers)
        cents_map = {
            "off_peak": "input_number.tou_rate_off_peak",
            "mid_peak": "input_number.tou_rate_mid_peak",
            "on_peak": "input_number.tou_rate_on_peak",
        }
        entity_id = cents_map.get(period)
        if entity_id:
            state = hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    return float(state.state) / 100.0  # ¢ to $
                except (ValueError, TypeError):
                    pass

        # Fall back to $/kWh helpers
        dollars_map = {
            "off_peak": "input_number.ev_rate_off_peak",
            "mid_peak": "input_number.ev_rate_mid_peak",
            "on_peak": "input_number.ev_rate_on_peak",
        }
        entity_id = dollars_map.get(period)
        if entity_id:
            state = hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    return float(state.state)
                except (ValueError, TypeError):
                    pass
    return defaults.get(period, 0.087)


def _compute_session_cost(session: dict, hass=None) -> dict:
    """Compute cost for a single charge session.

    Returns dict with tou_period, rate, energy, cost.
    """
    energy = _extract_energy(session)
    if energy is None:
        energy = 0.0
    else:
        try:
            energy = float(energy)
        except (ValueError, TypeError):
            energy = 0.0

    charge_start = _extract_charge_start(session)
    period = _get_tou_period(charge_start)
    rate = _get_tou_rate(period, hass)
    cost = round(energy * rate, 2)

    return {
        "tou_period": period,
        "rate": rate,
        "energy_kwh": round(energy, 2),
        "cost": cost,
    }


# Read-only sensor definitions from EV Station shadow
EV_SENSOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name_suffix": "Charging Status",
        "unique_suffix": "charging_status",
        "shadow_key": "chargingStatus",
        "device_class": None,
        "state_class": None,
        "unit": None,
        "icon": "mdi:ev-station",
    },
    {
        "name_suffix": "Max Current",
        "unique_suffix": "max_current",
        "shadow_key": "maxCurrent",
        "device_class": SensorDeviceClass.CURRENT,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfElectricCurrent.AMPERE,
        "icon": "mdi:current-ac",
    },
    {
        "name_suffix": "Derating",
        "unique_suffix": "derating",
        "shadow_key": "derating",
        "device_class": None,
        "state_class": None,
        "unit": None,
        "icon": "mdi:thermometer-alert",
    },
    {
        "name_suffix": "Error Info",
        "unique_suffix": "error_info",
        "shadow_key": "errorInfo",
        "device_class": None,
        "state_class": None,
        "unit": None,
        "icon": "mdi:alert-circle",
    },
]



# ── Sensors from top-level device data ──

EV_DEVICE_SENSOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name_suffix": "Firmware Version",
        "unique_suffix": "firmware_version",
        "device_key": "firmwareVersion",
        "device_class": None,
        "state_class": None,
        "unit": None,
        "icon": "mdi:cellphone-arrow-down",
    },
    {
        "name_suffix": "IP Address",
        "unique_suffix": "ip_address",
        "device_key": "ip",
        "device_class": None,
        "state_class": None,
        "unit": None,
        "icon": "mdi:ip-network",
    },
]


# ── Sensors from device.extraInfo ──

EV_EXTRA_INFO_SENSOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name_suffix": "Connection Quality",
        "unique_suffix": "connection_quality",
        "extra_key": "linkQuality",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
        "icon": "mdi:signal",
    },
    {
        "name_suffix": "Connection Type",
        "unique_suffix": "connection_type",
        "extra_key": "connectionType",
        "device_class": None,
        "state_class": None,
        "unit": None,
        "icon": "mdi:ethernet",
    },
]


# ── Real-time power sensors from WebSocket ──

EV_REALTIME_POWER_SENSOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name_suffix": "Power",
        "unique_suffix": "realtime_power",
        "ws_key": "instantKW",
        "device_class": SensorDeviceClass.POWER,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfPower.KILO_WATT,
        "icon": "mdi:flash",
    },
    {
        "name_suffix": "Current",
        "unique_suffix": "realtime_current",
        "ws_key": "instantA",
        "device_class": SensorDeviceClass.CURRENT,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfElectricCurrent.AMPERE,
        "icon": "mdi:current-ac",
    },
    {
        "name_suffix": "Voltage",
        "unique_suffix": "realtime_voltage",
        "ws_key": "instantV",
        "device_class": SensorDeviceClass.VOLTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfElectricPotential.VOLT,
        "icon": "mdi:sine-wave",
    },
    {
        "name_suffix": "Session Energy",
        "unique_suffix": "session_energy",
        "ws_key": "meter",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:battery-charging",
    },
    {
        "name_suffix": "Session Duration",
        "unique_suffix": "session_duration",
        "ws_key": "duration",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfTime.SECONDS,
        "icon": "mdi:timer-outline",
    },
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UniFi Connect sensor entities from config entry."""
    hub: UnifiConnectHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for device in hub.coordinator.data or []:
        if not _is_ev_device(device):
            continue

        shadow = device.get("shadow", {})
        extra_info = device.get("extraInfo", {})
        _LOGGER.info(
            "Setting up EV sensors for %s (platform: %s)",
            device.get("name"),
            device.get("type", {}).get("platform"),
        )

        # Create read-only sensors for matching shadow keys
        for sensor_def in EV_SENSOR_DEFINITIONS:
            if sensor_def["shadow_key"] in shadow:
                entities.append(EVShadowSensor(hub, device, sensor_def))

        # Create sensors from top-level device keys
        for sensor_def in EV_DEVICE_SENSOR_DEFINITIONS:
            if device.get(sensor_def["device_key"]) is not None:
                entities.append(EVDeviceKeySensor(hub, device, sensor_def))

        # Create sensors from extraInfo
        for sensor_def in EV_EXTRA_INFO_SENSOR_DEFINITIONS:
            if extra_info.get(sensor_def["extra_key"]) is not None:
                entities.append(EVExtraInfoSensor(hub, device, sensor_def))

        # Uptime sensor (from lastBootTimestamp)
        if device.get("lastBootTimestamp"):
            entities.append(EVUptimeSensor(hub, device))

        # Active charging session sensor
        entities.append(EVActiveSessionSensor(hub, device))

        # Real-time power sensors (from WebSocket)
        for sensor_def in EV_REALTIME_POWER_SENSOR_DEFINITIONS:
            entities.append(EVRealtimePowerSensor(hub, device, sensor_def))

        # Charge history sensors
        entities.append(EVChargeHistoryEnergySensor(hub, device))
        entities.append(EVChargeHistoryCountSensor(hub, device))
        entities.append(EVLastSessionSensor(hub, device))

        # Stats sensors
        entities.append(EVTotalChargingTimeSensor(hub, device))
        entities.append(EVAverageSessionTimeSensor(hub, device))
        entities.append(EVAverageEnergyPerSessionSensor(hub, device))
        entities.append(EVTotalCostSensor(hub, device))
        entities.append(EVChargeHistoryLogSensor(hub, device))

        # Raw shadow dump sensor for debugging
        entities.append(EVShadowDumpSensor(hub, device))

    if entities:
        _LOGGER.info("Adding %d EV sensor entities", len(entities))
    async_add_entities(entities)


class EVShadowSensor(UnifiConnectEntity, SensorEntity):
    """Sensor that reads a value from the EV device's shadow state."""

    def __init__(
        self,
        hub: UnifiConnectHub,
        device: dict,
        sensor_def: dict[str, Any],
    ):
        super().__init__(hub, device, sensor_def["name_suffix"], sensor_def["unique_suffix"])
        self._shadow_key = sensor_def["shadow_key"]
        self._attr_device_class = sensor_def["device_class"]
        self._attr_state_class = sensor_def["state_class"]
        self._attr_native_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def.get("icon")

    @property
    def native_value(self):
        value = self._get_shadow().get(self._shadow_key)
        if value is None:
            return None
        if self._attr_device_class in (
            SensorDeviceClass.POWER,
            SensorDeviceClass.CURRENT,
            SensorDeviceClass.ENERGY,
        ):
            try:
                return round(float(value), 2)
            except (ValueError, TypeError):
                return None
        if isinstance(value, (dict, list)):
            return str(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self._get_shadow().get(self._shadow_key)
        attrs = {"shadow_key": self._shadow_key}
        if isinstance(raw, dict):
            attrs.update(raw)
        return attrs


class EVShadowDumpSensor(UnifiConnectEntity, SensorEntity):
    """Diagnostic sensor that exposes all shadow keys as attributes."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Shadow Data", "shadow_dump")
        self._attr_icon = "mdi:bug"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return len(self._get_shadow())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        shadow = self._get_shadow()
        attrs: dict[str, Any] = {}
        for key, value in shadow.items():
            if isinstance(value, (dict, list)):
                attrs[key] = str(value)
            else:
                attrs[key] = value
        return attrs


class EVChargeHistoryEnergySensor(UnifiConnectEntity, SensorEntity):
    """Total energy delivered across all charge sessions."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Total Energy Delivered", "total_energy_kwh")
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:lightning-bolt-circle"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return None
        total = 0.0
        for session in history:
            energy = _extract_energy(session)
            try:
                total += float(energy)
            except (ValueError, TypeError):
                continue
        return round(total, 2) if total > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.charge_history.get(self._device_id, [])
        attrs: dict[str, Any] = {"total_sessions": len(history)}
        if history:
            last = history[-1] if isinstance(history[-1], dict) else {}
            attrs["last_session_keys"] = list(last.keys())
        return attrs


class EVChargeHistoryCountSensor(UnifiConnectEntity, SensorEntity):
    """Number of charge sessions."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Charge Sessions", "charge_sessions")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        return len(history) if history else 0


class EVLastSessionSensor(UnifiConnectEntity, SensorEntity):
    """Most recent charge session details."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Last Charge Session", "last_session_kwh")
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:ev-plug-type2"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return None
        last = history[-1] if isinstance(history[-1], dict) else {}
        energy = _extract_energy(last)
        if energy is not None:
            try:
                return round(float(energy), 2)
            except (ValueError, TypeError):
                pass
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return {}
        last = history[-1] if isinstance(history[-1], dict) else {}
        attrs: dict[str, Any] = {}
        # Add cost info for last session
        cost_info = _compute_session_cost(last, self.hass)
        attrs["tou_period"] = cost_info["tou_period"]
        attrs["rate_per_kwh"] = cost_info["rate"]
        attrs["estimated_cost"] = cost_info["cost"]
        # Add formatted timestamps
        charge_start = _extract_charge_start(last)
        charge_end = _extract_charge_end(last)
        if charge_start:
            try:
                attrs["charge_start"] = datetime.fromtimestamp(
                    charge_start, tz=timezone.utc
                ).isoformat()
            except (ValueError, OSError):
                attrs["charge_start"] = charge_start
        if charge_end:
            try:
                attrs["charge_end"] = datetime.fromtimestamp(
                    charge_end, tz=timezone.utc
                ).isoformat()
            except (ValueError, OSError):
                attrs["charge_end"] = charge_end
        attrs["source"] = _extract_source(last)
        charge_time = last.get("chargeTime")
        if charge_time is not None:
            attrs["charge_time"] = _format_duration(
                _parse_duration_seconds(charge_time)
            )
        return attrs


class EVTotalChargingTimeSensor(UnifiConnectEntity, SensorEntity):
    """Total charging time across all sessions."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Total Charging Time", "total_charging_time")
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.HOURS
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:timer"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return None
        total_seconds = 0.0
        for session in history:
            charge_time = session.get("chargeTime")
            if charge_time is not None:
                total_seconds += _parse_duration_seconds(charge_time)
            else:
                # Fallback: compute from start/end using schema helpers
                start = _extract_charge_start(session)
                end = _extract_charge_end(session)
                if start and end:
                    total_seconds += max(0, end - start)
        hours = total_seconds / 3600.0
        return round(hours, 2) if hours > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.charge_history.get(self._device_id, [])
        total_seconds = 0.0
        for session in history:
            charge_time = session.get("chargeTime")
            if charge_time is not None:
                total_seconds += _parse_duration_seconds(charge_time)
        return {"formatted": _format_duration(total_seconds)}


class EVAverageSessionTimeSensor(UnifiConnectEntity, SensorEntity):
    """Average charging time per session."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Average Session Time", "avg_session_time")
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.HOURS
        self._attr_icon = "mdi:timer-outline"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return None
        total_seconds = 0.0
        count = 0
        for session in history:
            charge_time = session.get("chargeTime")
            if charge_time is not None:
                secs = _parse_duration_seconds(charge_time)
                if secs > 0:
                    total_seconds += secs
                    count += 1
        if count == 0:
            return None
        avg_hours = (total_seconds / count) / 3600.0
        return round(avg_hours, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.charge_history.get(self._device_id, [])
        total_seconds = 0.0
        count = 0
        for session in history:
            charge_time = session.get("chargeTime")
            if charge_time is not None:
                secs = _parse_duration_seconds(charge_time)
                if secs > 0:
                    total_seconds += secs
                    count += 1
        if count == 0:
            return {}
        avg_secs = total_seconds / count
        return {"formatted": _format_duration(avg_secs)}


class EVAverageEnergyPerSessionSensor(UnifiConnectEntity, SensorEntity):
    """Average energy delivered per session."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Average Energy Per Session", "avg_energy_session")
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return None
        total = 0.0
        count = 0
        for session in history:
            energy = _extract_energy(session)
            if energy is not None:
                try:
                    total += float(energy)
                    count += 1
                except (ValueError, TypeError):
                    continue
        if count == 0:
            return None
        return round(total / count, 2)


class EVTotalCostSensor(UnifiConnectEntity, SensorEntity):
    """Estimated total cost across all charge sessions using TOU rates."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Total Charging Cost", "total_charging_cost")
        self._attr_native_unit_of_measurement = "$"
        self._attr_icon = "mdi:currency-usd"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return None
        total_cost = 0.0
        for session in history:
            cost_info = _compute_session_cost(session, self.hass)
            total_cost += cost_info["cost"]
        return round(total_cost, 2) if total_cost > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return {}
        off_peak_kwh = 0.0
        mid_peak_kwh = 0.0
        on_peak_kwh = 0.0
        off_peak_cost = 0.0
        mid_peak_cost = 0.0
        on_peak_cost = 0.0
        for session in history:
            info = _compute_session_cost(session, self.hass)
            if info["tou_period"] == "off_peak":
                off_peak_kwh += info["energy_kwh"]
                off_peak_cost += info["cost"]
            elif info["tou_period"] == "mid_peak":
                mid_peak_kwh += info["energy_kwh"]
                mid_peak_cost += info["cost"]
            else:
                on_peak_kwh += info["energy_kwh"]
                on_peak_cost += info["cost"]
        return {
            "off_peak_kwh": round(off_peak_kwh, 2),
            "off_peak_cost": round(off_peak_cost, 2),
            "mid_peak_kwh": round(mid_peak_kwh, 2),
            "mid_peak_cost": round(mid_peak_cost, 2),
            "on_peak_kwh": round(on_peak_kwh, 2),
            "on_peak_cost": round(on_peak_cost, 2),
            "rate_off_peak": _get_tou_rate("off_peak", self.hass),
            "rate_mid_peak": _get_tou_rate("mid_peak", self.hass),
            "rate_on_peak": _get_tou_rate("on_peak", self.hass),
        }


class EVChargeHistoryLogSensor(UnifiConnectEntity, SensorEntity):
    """Full charge session history log with per-session costs."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Charge History", "charge_history_log")
        self._attr_icon = "mdi:clipboard-text-clock"

    @property
    def native_value(self):
        history = self.coordinator.charge_history.get(self._device_id, [])
        return len(history) if history else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.charge_history.get(self._device_id, [])
        if not history:
            return {"sessions": []}

        sessions = []
        for session in reversed(history):  # Most recent first
            energy = _extract_energy(session)
            try:
                energy_val = round(float(energy), 2) if energy is not None else 0.0
            except (ValueError, TypeError):
                energy_val = 0.0

            charge_start = _extract_charge_start(session)
            charge_end = _extract_charge_end(session)
            charge_time_raw = session.get("chargeTime")
            charge_secs = _parse_duration_seconds(charge_time_raw) if charge_time_raw else 0

            # Format timestamps
            try:
                start_dt = datetime.fromtimestamp(charge_start, tz=timezone.utc)
                start_str = start_dt.isoformat()
            except (ValueError, OSError):
                start_str = str(charge_start)

            try:
                end_dt = datetime.fromtimestamp(charge_end, tz=timezone.utc)
                end_str = end_dt.isoformat()
            except (ValueError, OSError):
                end_str = str(charge_end)

            cost_info = _compute_session_cost(session, self.hass)

            sessions.append({
                "date": start_str,
                "end": end_str,
                "energy_kwh": energy_val,
                "charge_time": _format_duration(charge_secs),
                "tou_period": cost_info["tou_period"],
                "rate": cost_info["rate"],
                "cost": cost_info["cost"],
                "source": _extract_source(session),
            })

        attrs = {"sessions": sessions, "total_sessions": len(sessions)}
        # Expose raw API pagination metadata for debugging
        meta = getattr(self._hub.api, "_charge_history_meta", None)
        if meta:
            attrs["api_meta"] = meta
        return attrs


class EVDeviceKeySensor(UnifiConnectEntity, SensorEntity):
    """Sensor that reads a value from a top-level device key."""

    def __init__(
        self,
        hub: UnifiConnectHub,
        device: dict,
        sensor_def: dict[str, Any],
    ):
        super().__init__(hub, device, sensor_def["name_suffix"], sensor_def["unique_suffix"])
        self._device_key = sensor_def["device_key"]
        self._attr_device_class = sensor_def["device_class"]
        self._attr_state_class = sensor_def["state_class"]
        self._attr_native_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def.get("icon")

    @property
    def native_value(self):
        device = self._get_device()
        if not device:
            return None
        value = device.get(self._device_key)
        if isinstance(value, (dict, list)):
            return str(value)
        return value


class EVExtraInfoSensor(UnifiConnectEntity, SensorEntity):
    """Sensor that reads a value from device.extraInfo."""

    def __init__(
        self,
        hub: UnifiConnectHub,
        device: dict,
        sensor_def: dict[str, Any],
    ):
        super().__init__(hub, device, sensor_def["name_suffix"], sensor_def["unique_suffix"])
        self._extra_key = sensor_def["extra_key"]
        self._attr_device_class = sensor_def["device_class"]
        self._attr_state_class = sensor_def["state_class"]
        self._attr_native_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def.get("icon")

    @property
    def native_value(self):
        device = self._get_device()
        if not device:
            return None
        extra_info = device.get("extraInfo", {})
        value = extra_info.get(self._extra_key)
        if value is not None and self._attr_state_class is not None:
            try:
                return round(float(value), 1)
            except (ValueError, TypeError):
                return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self._get_device()
        if not device:
            return {}
        extra_info = device.get("extraInfo", {})
        attrs: dict[str, Any] = {}
        for key, value in extra_info.items():
            if isinstance(value, (dict, list)):
                attrs[key] = str(value)
            else:
                attrs[key] = value
        return attrs


class EVUptimeSensor(UnifiConnectEntity, SensorEntity):
    """Sensor showing device uptime based on lastBootTimestamp."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Uptime", "uptime")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-check-outline"

    @property
    def native_value(self):
        """Return the last boot time as a datetime.

        HA renders timestamp sensors as relative time ("X hours ago").
        """
        device = self._get_device()
        if not device:
            return None
        boot_ts = device.get("lastBootTimestamp")
        if boot_ts is None:
            return None
        try:
            ts = float(boot_ts)
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self._get_device()
        if not device:
            return {}
        boot_ts = device.get("lastBootTimestamp")
        attrs: dict[str, Any] = {}
        if boot_ts is not None:
            attrs["boot_timestamp"] = boot_ts
            try:
                ts = float(boot_ts)
                if ts > 1e12:
                    ts = ts / 1000
                boot_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                uptime_seconds = (datetime.now(tz=timezone.utc) - boot_dt).total_seconds()
                attrs["uptime_days"] = round(uptime_seconds / 86400, 1)
                attrs["uptime_hours"] = round(uptime_seconds / 3600, 1)
            except (ValueError, TypeError, OSError):
                pass
        return attrs


class EVActiveSessionSensor(UnifiConnectEntity, SensorEntity):
    """Sensor showing the active charging session status."""

    def __init__(self, hub: UnifiConnectHub, device: dict):
        super().__init__(hub, device, "Active Session", "active_session")
        self._attr_icon = "mdi:ev-plug-type2"

    @property
    def native_value(self):
        """Return charging source type if session active, else 'idle'."""
        device = self._get_device()
        if not device:
            return None
        session = device.get("chargingSession")
        if session and isinstance(session, dict) and session.get("id"):
            return session.get("source", "active")
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self._get_device()
        if not device:
            return {}
        session = device.get("chargingSession")
        if not session or not isinstance(session, dict):
            return {"session_active": False}
        attrs: dict[str, Any] = {"session_active": bool(session.get("id"))}
        for key, value in session.items():
            attrs[f"session_{key}"] = value
        return attrs


class EVRealtimePowerSensor(UnifiConnectEntity, SensorEntity):
    """Sensor that reads real-time power data from the WebSocket stream.

    Updated every ~3 seconds when the EV Station is actively charging.
    Returns None when no active charging session (streaming=False).
    """

    def __init__(
        self,
        hub: UnifiConnectHub,
        device: dict,
        sensor_def: dict[str, Any],
    ):
        super().__init__(hub, device, sensor_def["name_suffix"], sensor_def["unique_suffix"])
        self._ws_key = sensor_def["ws_key"]
        self._attr_device_class = sensor_def["device_class"]
        self._attr_state_class = sensor_def["state_class"]
        self._attr_native_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def.get("icon")
        # Real-time data should update frequently
        self._attr_suggested_display_precision = 2

    @property
    def available(self) -> bool:
        """Available when coordinator data exists (WS data may be empty when idle)."""
        return self.coordinator.data is not None

    @property
    def native_value(self):
        """Return the real-time value from WebSocket data."""
        power_data = self._get_power_data()
        if not power_data or not power_data.get("streaming"):
            # Not actively charging — return 0 for power/current/voltage,
            # None for session-specific metrics
            if self._ws_key in ("instantKW", "instantA", "instantV"):
                return 0 if power_data.get("streaming") is False else None
            return None

        value = power_data.get(self._ws_key)
        if value is None:
            return None
        try:
            return round(float(value), 2)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose all WebSocket power data as attributes."""
        power_data = self._get_power_data()
        if not power_data:
            return {
                "source": "websocket",
                "streaming": False,
                "ws_connected": self._hub.websocket.connected,
            }

        attrs: dict[str, Any] = {"source": "websocket"}
        attrs["streaming"] = power_data.get("streaming", False)
        attrs["ws_connected"] = self._hub.websocket.connected

        # Include session start time as ISO format
        started_at = power_data.get("startedAt")
        if started_at:
            try:
                ts = float(started_at)
                if ts > 1e12:
                    ts = ts / 1000
                attrs["session_started"] = datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError, OSError):
                attrs["session_started_raw"] = started_at

        return attrs
