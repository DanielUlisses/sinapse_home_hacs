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

- **Local control first.** Like the app on the home Wi-Fi, the integration talks to the spa controller directly over the LAN (ESP local control, Security 1, with the proof of possession the cloud supplies). The controller is found over mDNS (`_esp_local_ctrl._tcp`); if mDNS doesn't reach Home Assistant (VLANs, for example), set its IP address in the integration's options. While the LAN path works, params are polled every 5 s and commands go straight to the controller.
- **Cloud fallback.** The cloud login stays: it supplies the node configuration and the local-control credentials, refreshed every 30 min. After 3 failed local polls, or a local command that certainly didn't reach the spa (no connection, session refused), the integration falls back to the cloud (polled every 30 s) and retries the LAN on every poll. A command that got no answer raises an error and is not sent through the cloud (see below). The diagnostic sensor **Connection** shows `local` or `cloud`.
- After a command, the state updates immediately. On the cloud the spa takes ~20 s to report a change, so the written value is kept until a poll confirms it, for up to 90 s. If it's never confirmed, the polled value wins.
- The session is renewed automatically with the refresh token. If your password changes, Home Assistant asks you to re-authenticate.
- The integration ships its own icon and logo (`brand/`), shown by Home Assistant 2026.3 or later.
- `iot_class: local_polling`: without internet access, Home Assistant keeps reading and controlling the spa over the LAN. The last good node configuration, local-control credentials (POP) and LAN address are kept in the integration's config entry (next to the account password), so Home Assistant can also start while the internet is down, as long as the spa answers on the LAN.
- **Commands are never sent twice.** If a command sent over the LAN gets no answer (a timeout, or the connection drops after it was sent), the spa may or may not have applied it, so the integration reports an error instead of resending it locally or through the cloud: some commands, like the light-effect button, would act twice. It falls back to the cloud only when the LAN command certainly wasn't processed (no connection, session refused). This is stricter than the app, which resends through the cloud on any local failure.
- Diagnostics (Settings → Devices & services → Sinapse Home → Download diagnostics) redact the e-mail, the password and the local-control POP.

## Roadmap

- [x] Local control (`esp_local_ctrl` over mDNS `_esp_local_ctrl._tcp`, Security 1 using the POP fetched from the cloud), with the cloud only as a fallback.
- [ ] Configurable polling interval.

## Tools

`tools/sinapse_discovery.py` logs in, lists the nodes on the account and prints every parameter. It is read-only and useful for mapping new models. The JSON file it writes contains the local-control POP, so don't publish it.

`tools/sinapse_local_probe.py` logs in to get the node id and POP, finds the controller over mDNS (or `--host`), runs the local-control handshake and prints the `config` and `params` properties with the POP redacted. `--watch 120` prints every local param change for 2 minutes (handy to time how fast panel changes show up), and `--set '{"Cromoterapia": {"Status": true}}'` writes one param. It needs `cryptography` and is read-only unless `--set` is given.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

## Disclaimer

Independent project, not affiliated with Sinapse Industrial or Espressif. Use at your own risk.

## License

MIT — see [LICENSE](LICENSE).
