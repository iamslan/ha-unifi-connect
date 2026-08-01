# UniFi Connect Integration — Development Journal

## v1.4.0 — WebSocket Real-Time Power Data (2026-03-12)

### Problem

The UniFi Connect REST API (`/proxy/connect/api/v2/devices`) returns `powerStats: null` for EV Station devices, even during active charging sessions. Real-time power data (kW, amps, voltage) that appears in the UniFi Connect web UI is **not available** through any REST endpoint.

### Investigation

Reverse-engineered the UniFi Connect web application (SWAI framework, `swai.3.24.13-22b194.js`, ~1.8MB) to discover how power data reaches the browser.

**Key findings:**

1. **Redux store** contains live power data at `state.devices.instantPowerStats`, updating every ~3 seconds during charging.
2. **`/api/ws/system`** (the UniFi OS-level WebSocket) delivers `SYSTEM` and `DEVICE_STATE_CHANGED` events but does **not** carry power statistics.
3. The SWAI framework uses a **separate WebSocket** at the application level for real-time data.

### WebSocket Protocol (Reverse-Engineered)

- **URL:** `wss://{host}/proxy/connect/` (for UDMP controllers)
- **Auth:** Cookie-based — reuses the same session cookies from REST API login
- **Handshake:** After connecting, send:
  ```json
  {"type": "request", "action": "set_info", "platform": "web", "timestamp": <epoch_ms>}
  ```
- **Messages:** Binary-framed with a custom format:
  - Each part has an **8-byte header**: `[part_num, type, 0, 0, 0, 0, 0, length]` + JSON payload
  - For payloads >= 256 bytes, bytes 6-7 encode a big-endian 16-bit length
  - Typical message has 2 parts: Part 1 = event envelope, Part 2 = data payload
- **Event types:**
  - `EV_POWER_STATS` — single device power update
  - `MULTI_EV_POWER_STATS` — array of device power updates
  - `DEVICE_UPDATED` — device state change notification
- **Power data fields:** `instantKW`, `instantA`, `instantV`, `meter` (session kWh), `duration` (session seconds), `startedAt`, `streaming` (bool), `mac`, `id`
- **Update frequency:** ~3 seconds during active charging

### Implementation

**New files:**
- `websocket.py` — WebSocket client with binary frame parser, auto-reconnect with exponential backoff (5s → 60s cap)

**Modified files:**
- `hub.py` — Initializes WebSocket alongside REST API; starts on setup, stops on unload
- `__init__.py` — Calls `hub.async_shutdown()` on integration unload for clean WebSocket teardown
- `entity.py` — Added `_get_power_data()` helper for all entities to access WebSocket data
- `sensor.py` — Added 5 real-time power sensors plus device info sensors
- `manifest.json` — Bumped to v1.4.0, changed `iot_class` from `local_polling` to `local_push`

**New sensors (real-time from WebSocket):**

| Sensor | Key | Unit | Description |
|--------|-----|------|-------------|
| Power | `instantKW` | kW | Real-time charging power |
| Current | `instantA` | A | Real-time current draw |
| Voltage | `instantV` | V | Real-time line voltage |
| Session Energy | `meter` | kWh | Energy delivered this session |
| Session Duration | `duration` | s | Time elapsed this session |

**New sensors (from device data):**

| Sensor | Source | Description |
|--------|--------|-------------|
| Firmware Version | `device.firmwareVersion` | Current firmware |
| IP Address | `device.ip` | Device IP |
| Connection Quality | `device.extraInfo.linkQuality` | Signal quality % |
| Connection Type | `device.extraInfo.connectionType` | Ethernet/WiFi |
| Uptime | `device.lastBootTimestamp` | Time since boot |
| Active Session | `device.chargingSession` | Session status |

### Architecture Notes

The WebSocket runs alongside the existing polling coordinator. REST API polling still handles device state, shadow data, and charge history. The WebSocket exclusively provides real-time power metrics that the REST API cannot deliver.

Power sensors return `0` for instantaneous values (kW, A, V) when not charging (`streaming=False`), and `None` for session-specific metrics. Each sensor exposes `streaming` and `ws_connected` as extra state attributes for diagnostics.

---

## v1.3.0 — Statistics & Cost Tracking

- Added TOU (Time-of-Use) rate calculation using Ontario electricity rates
- Reads rates from `input_number.tou_rate_*` helpers if available
- Added sensors: Total Charging Time, Average Session Time, Average Energy/Session
- Added Total Charging Cost sensor with per-period breakdown
- Added Charge History Log sensor with full session details and per-session costs

## v1.2.0 — Charge History

- Added charge session history via `/api/v2/evChargeHistory`
- Total Energy Delivered, Charge Sessions count, Last Charge Session sensors

## v1.1.0 — Control Entities

- Added writable entities: number (Max Output, Brightness), select (Station Mode, Fallback Security), switch (Status Light, Locating), text (Display Label), button (Reboot, Identify)

## v1.0.0 — Initial Release

- REST API client with cookie-based authentication
- Config flow with auto-detection of UDMP/CloudKey controllers
- Shadow state sensors (Charging Status, Max Current, Derating, Error Info)
- Binary sensors (Online, Charging Active)
