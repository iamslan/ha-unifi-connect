# UniFi Connect for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub License](https://img.shields.io/github/license/danielsza/ha-unifi-connect)](LICENSE)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2.5%2B-blue)

A custom [Home Assistant](https://www.home-assistant.io/) integration for **UniFi Connect** devices, using the local UniFi Connect API. Supports the **UniFi Connect Display SE 21** and **UniFi EV Station** devices (EV Station, EV Station Pro, EV Station Lite).

## Prerequisites

This integration communicates directly with your UniFi Console over the local network. It **does not support cloud-only UniFi accounts or accounts with 2FA enabled**.

You must create a **local-only** UniFi OS account:

1. Open your UniFi Console (e.g. Dream Machine Pro).
2. Navigate to **Settings > Admins & Users > Add Admin**.
3. Select **Local Access Only**.
4. Grant **Full Management** access to UniFi Connect.
5. Use these credentials when configuring the integration.

## Supported Entities

### Display SE 21

| Platform | Entity | Description |
|----------|--------|-------------|
| Switch | Display Power | Turn the display on/off |
| Switch | Auto Rotate | Toggle automatic screen rotation |
| Switch | Auto Reload | Toggle periodic web page reload |
| Switch | Sleep Mode | Enable/disable sleep mode |
| Switch | Auto Sleep | Enable/disable automatic sleep |
| Number | Brightness | Adjust display brightness (slider) |
| Number | Volume | Adjust display volume (slider) |
| Select | Mode | Switch between Web and App mode |
| Select | App Selector | Choose which app to launch (in App mode) |
| Text | Web URL | Set the URL to display (in Web mode) |
| Button | Reload Web Page | Refresh the currently displayed web page |

### EV Station (EV Station, EV Station Pro, EV Station Lite)

#### Controls

| Platform | Entity | Description |
|----------|--------|-------------|
| Number | Maximum Output | Set the maximum output current in Amps (slider) |
| Number | Brightness | Adjust display/LED brightness |
| Switch | Charging | Enable or disable charging |
| Switch | Status Light | Toggle the LED status indicator |
| Switch | Locating | Toggle the locating indicator |
| Select | Station Mode | Set mode (plugAndCharge / noAccess / accessWithUniFiIdentity) |
| Select | Fallback Security | Set fallback security mode |
| Select | Breaker Amperage | Set breaker size (15A–70A), determines max output limit |
| Text | Display Label | Set the label shown on the charger display |
| Text | Support Information | Set the support/admin message |
| Button | Reboot | Reboot the EV Station |

#### Sensors

| Platform | Entity | Description |
|----------|--------|-------------|
| Sensor | Charging Status | Current state (Available, Charging, ReadyToCharge, ChargeComplete) |
| Sensor | Max Current | Active charging current (A, read-only) |
| Sensor | Derating | Whether thermal derating is active |
| Sensor | Error Info | Error code from the device |
| Sensor | Total Energy Delivered | Cumulative energy across all sessions (Wh) |
| Sensor | Charge Sessions | Total number of charge sessions |
| Sensor | Last Charge Session | Energy delivered in the most recent session (Wh) |
| Sensor | Shadow Data | Diagnostic: exposes all raw shadow keys (disabled by default) |

> **Note:** All EV Station controls use the same `perform_action` API as the Display SE 21, with action IDs from the device's `supportedActions` list. If your device exposes different actions or field names, enable the "Shadow Data" diagnostic entity or enable DEBUG logging and [open an issue](https://github.com/danielsza/ha-unifi-connect/issues).

## Installation

### HACS (Recommended)

1. Open **HACS > Integrations** in Home Assistant.
2. Click the menu (**⋮**) and select **Custom repositories**.
3. Add this repository:
   - **URL:** `https://github.com/danielsza/ha-unifi-connect`
   - **Category:** Integration
4. Search for **UniFi Connect** and install it.
5. Restart Home Assistant.

### Manual

1. Download or clone this repository.
2. Copy the `custom_components/unifi_connect` folder into your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**.
2. Search for **UniFi Connect**.
3. Enter your UniFi Console details:
   - **Host** - IP address or hostname of your UniFi Console
   - **Username / Password** - Local account credentials (see [Prerequisites](#prerequisites))
   - **Controller Type** - Select your console type (Dream Machine or Other)
   - **Port** - Defaults to `443`; only change if you use a non-standard port
4. The integration validates your credentials before completing setup.

## How It Works

- **Local polling** - The integration polls your UniFi Console every 30 seconds for device state updates. No cloud dependency.
- **Automatic re-authentication** - If the session expires, the integration re-authenticates transparently.
- **Retry on startup** - If the console is unreachable during Home Assistant startup, the integration retries automatically.
- **Action-based control** - All controls use the UniFi Connect `perform_action` API with device-specific action IDs.

## Troubleshooting

- **Cannot connect during setup** - Verify your console IP is reachable from the Home Assistant host and that you are using a local-only account.
- **Entities unavailable after restart** - Check that your UniFi Console is online. The integration will retry and recover automatically.
- **Stale entities after upgrade** - If you see "Unavailable" entities after upgrading, delete the integration, restart, and re-add it to clear old entity registrations.
- **Check logs** - Go to **Settings > System > Logs** and filter for `unifi_connect` to see detailed error messages.

### Debug Logging

Add this to your `configuration.yaml` for verbose logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.unifi_connect: debug
```

## Supported Devices

| Device | Platform ID |
|--------|-------------|
| UniFi Connect Display SE 21 | `UC-Display-SE-21` |
| UniFi EV Station | `EVS` |
| UniFi EV Station Pro | `EVS-Pro` |
| UniFi EV Station Lite | `EVS-Lite` |

EV devices are also detected by the presence of `chargingStatus` in their shadow or `power_stats_single` in their `supportedActions`, so new EV models should work automatically.

Additional UniFi Connect devices may work but are untested. If you have a different device and it works (or doesn't), please [open an issue](https://github.com/danielsza/ha-unifi-connect/issues).

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss your proposal.

## Credits

Originally created by [@iamslan](https://github.com/iamslan). EV Station support added by [@danielsza](https://github.com/danielsza).

## License

[MIT](LICENSE)
