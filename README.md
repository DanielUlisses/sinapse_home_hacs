# Sinapse Home for Home Assistant

A custom integration, installable through HACS, for **Sinapse Industrial** spa and hot tub heaters and controllers that use the **Sinapse Home** app.

The Sinapse Home app is a Sinapse-branded build of Espressif's ESP RainMaker. This integration talks to the same cloud API the app uses, logging in with your account.

## Entities

Entities are created from the configuration the device itself reports. On a **Sense Duo**, the app's devices map like this (device and parameter names are the Portuguese ones the device reports):

| Device in the app | Home Assistant entity |
|---|---|
| Painel (panel), Hidro 1 (jet pump), Borbulhador (air blower) | `switch` |
| Aquecedor (heater) | `climate` (target temperature 20–40 °C, heating/idle action) |
| Aquecedor · Temperatura | `sensor` (°C, with history) |
| Aquecedor · Status / Nível (water level) / Refrigerando (cooling) | `binary_sensor` |
| Cromoterapia (chromotherapy lights) | `light` (on/off, brightness, HS color) |
| Cromoterapia · Efeitos | `button` (advances to the next light effect) |

Other models should work without changes: unknown parameters become a `binary_sensor`, `sensor` or `button` depending on their type.

## Installation

1. HACS → ⋮ menu → **Custom repositories** → `https://github.com/DanielUlisses/sinapse_home_hacs`, category **Integration**.
2. Install **Sinapse Home** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Sinapse Home**, using the same e-mail and password as the app.

## How it works

- The cloud is polled every 30 s. After a command, the state updates immediately and is confirmed by a new poll 3 s later.
- The session is renewed automatically with the refresh token. If your password changes, Home Assistant asks you to re-authenticate.
- The integration ships its own icon and logo (`brand/`), shown by Home Assistant 2026.3 or later.
- `iot_class: cloud_polling`: without internet access, control from Home Assistant stops (the physical panel keeps working).

## Known limitations

These come from the device firmware, not the integration:

- **Target temperature set on the physical panel is not reported to the cloud.** Setting it from Home Assistant (or the app) works and shows on the panel. But if you change it with the panel's +/- buttons, Home Assistant keeps showing the last value set remotely. The panel also keeps counting from its own last value: with the panel at 29 and a remote change to 27, pressing + on the panel jumps to 30, not 28. The official app behaves the same way.
- **Heater state right after turning it off on the panel** may still show as on for a while: after switching off, the heater cools down first (`Refrigerando`). This is still being investigated.

## Roadmap

- [ ] Local control (`esp_local_ctrl` over mDNS `_esp_local_ctrl._tcp`, Security 1 using the POP fetched from the cloud), with the cloud only as a fallback.
- [ ] Configurable polling interval.

## Tools

`tools/sinapse_setpoint_probe.py` watches the heater params in the cloud every 5 s and can write a setpoint first, to check how the firmware handles it.

`tools/sinapse_discovery.py` logs in, lists the nodes on the account and prints every parameter. It is read-only and useful for mapping new models. The JSON file it writes contains the local-control POP, so don't publish it.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

## Disclaimer

Independent project, not affiliated with Sinapse Industrial or Espressif. Use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
