# 0002 — Local control (LAN) with cloud fallback

**Status:** ready to implement (needs a host on the same LAN as the spa)
**Area:** new `local.py`, `coordinator.py`, `__init__.py`, `manifest.json`, tests, new tool

## Goal

Talk to the spa controller (ESP32, ESP RainMaker firmware) directly over the LAN, the same way the official app does when the phone is on the home Wi-Fi:

- faster commands and state updates (no cloud round-trip, poll every few seconds)
- keep working when the internet is down

The cloud stays: it supplies the node config, the local-control credentials (POP) and the fallback path.

## How the app does it (decompiled APK v1.0.5)

Relevant classes: `com.espressif.local_control.*` (`mDNSManager`, `EspLocalDevice`, `EspLocalSession`, `EspLocalTransport`, `LocalControlApiManager`), `com.espressif.provisioning.security.Security1`, `com.espressif.NetworkApiManager`, and the protobuf classes in `rm_local_ctrl.*`. The build flag `isLocalControlSupported = true` is set.

### 1. Credentials (from the cloud)

The node has a service `Local Control` (`esp.service.local_control`). Its values arrive with the normal cloud params:

```json
"Local Control": {"POP": "<8 hex chars>", "Type": 1}
```

- `POP` (`esp.param.local_control_pop`): proof of possession, a string.
- `Type` (`esp.param.local_control_type`): security scheme. `1` = **Security1**, `0` = Security0 (no encryption). The Sense Duo reports `1`.

> Never commit or log the POP. Read it at runtime from the coordinator data.

### 2. Discovery (mDNS)

- Service type: **`_esp_local_ctrl._tcp.local.`**
- The TXT record **`node_id`** holds the RainMaker node id. Match it to the cloud node (`node_details[].id`).
- Use the resolved host IP and port. Don't hard-code port 8080, although that is the ESP-IDF default.

In HA, declare `"zeroconf": ["_esp_local_ctrl._tcp.local."]` in `manifest.json` (or browse with `homeassistant.components.zeroconf.async_get_async_instance`) and keep a `node_id → (host, port)` map that updates when the IP changes.

### 3. Transport (HTTP)

- `POST http://<ip>:<port>/esp_local_ctrl/session`: security handshake
- `POST http://<ip>:<port>/esp_local_ctrl/control`: commands
- Headers: `Content-Type: application/x-www-form-urlencoded`, `Accept: text/plain`
- Body: raw protobuf bytes (encrypted after the handshake). Timeout 5 s.
- **Session cookie:** the device answers the first session request with `Set-Cookie`. Send that cookie on every following request (session and control). A new handshake gets a new cookie. Use one `aiohttp.ClientSession` with its own cookie jar per device, *not* HA's shared session.
- HTTP 200 means OK. Anything else counts as a failure: re-handshake once, then fall back to the cloud.

### 4. Security1 handshake (protocomm "sec1")

This is the same scheme as ESP-IDF `esp_prov`. Port `tools/esp_prov/security/security1.py` and `proto/session.proto`, `sec1.proto`, `constants.proto` from ESP-IDF (`components/protocomm/proto`).

1. Generate an X25519 key pair (client).
2. **Command0:** `SessionData{sec_ver=SecScheme1, sec1{msg=Session_Command0, sc0{client_pubkey}}}` → response `sr0{status, device_pubkey (32 B), device_random (16 B)}`
3. `shared = X25519(client_priv, device_pubkey)`. If a POP is set: `shared = shared XOR SHA256(pop)`.
4. Cipher = **AES-256-CTR**, key = `shared`, IV = `device_random`. Use **one cipher stream for the whole session**: the app calls `cipher.update()` for both encrypt and decrypt on the *same* object, so the counter keeps advancing across requests and responses. Encrypt the request, then decrypt the response, in that order, always.
5. **Command1:** `client_verify_data = encrypt(device_pubkey)` → response `sr1{status, device_verify_data}`. `decrypt(device_verify_data)` must equal `client_pubkey`, otherwise the POP is wrong.
6. The session is established. Each `/control` body is `encrypt(LocalCtrlMessage bytes)`, and each response is `decrypt(body)`.

`cryptography` (already a Home Assistant core dependency) provides X25519 and AES-CTR.

### 5. Messages (`esp_local_ctrl.proto`, ESP-IDF `components/esp_local_ctrl/proto`)

Field numbers confirmed from the APK:

```
LocalCtrlMessage { msg=1 (enum); cmd_get_prop_count=10; resp_get_prop_count=11;
                   cmd_get_prop_vals=12; resp_get_prop_vals=13;
                   cmd_set_prop_vals=14; resp_set_prop_vals=15 }
LocalCtrlMsgType  TypeCmdGetPropertyCount=0  TypeRespGetPropertyCount=1
                  TypeCmdGetPropertyValues=4 TypeRespGetPropertyValues=5
                  TypeCmdSetPropertyValues=6 TypeRespSetPropertyValues=7
RespGetPropertyCount { status=1; count=2 }
CmdGetPropertyValues { repeated uint32 indices=1 }
RespGetPropertyValues{ status=1; repeated PropertyInfo props=2 }
PropertyInfo         { status=1; name=2; type=3; flags=4; value=5 (bytes) }
CmdSetPropertyValues { repeated PropertyValue props=1 }
PropertyValue        { index=1; value=2 (bytes) }
RespSetPropertyValues{ status=1 }
Status: Success=0, InvalidSecScheme=1, InvalidProto=2, …, InvalidArgument=4, …, InvalidSession=7
```

Either vendor the generated `*_pb2.py` (Apache-2.0, with attribution) or hand-encode these few messages. They are small, and hand-encoding avoids version issues with the `protobuf` package in HA.

### 6. RainMaker properties over local control

The RainMaker firmware exposes **two properties**, both holding JSON strings:

| Index | Name | Content |
|---|---|---|
| 0 | `config` | same structure as the cloud `node_details[].config` (devices, params, services; includes `node_id`) |
| 1 | `params` | same structure as the cloud params: `{"Aquecedor": {...}, "Cromoterapia": {...}, ...}` |

- **Read:** `GetPropertyCount` → `GetPropertyValues(indices=[0..count-1])` → JSON-parse each `value` by `name`. Polling only needs index 1 (`params`).
- **Write:** `SetPropertyValues(props=[{index: 1, value: <JSON>}])`. The JSON is **exactly the cloud payload**, for example `{"Hidro 1": {"Status": true}}`. Success when `resp_set_prop_vals.status == Success`.

### 7. App routing (behaviour to mirror)

`NetworkApiManager.updateParamValue`: if the node is known locally, write locally. On failure, drop the local device and use the cloud. Reads work the same way.

## Proposed implementation

1. **`tools/sinapse_local_probe.py` first** (standalone, stdlib + `cryptography`). Log in to the cloud to get node id and POP, browse mDNS, handshake, dump `config` and `params`, and optionally set one param. Validate the whole protocol on the real device before touching the integration.
2. **`custom_components/sinapse_home/local.py`:** `SinapseLocalClient(host, port, pop, sec_type)` with `async_connect()`, `async_get_params()`, `async_set_params(payload)`, automatic re-handshake on `InvalidSession`, HTTP error or cipher mismatch, and an `asyncio.Lock` so request and response stay in order on the shared CTR stream.
3. **Coordinator:**
   - keep the cloud login. Take node config, POP and type from `get_nodes()`; refresh them about every 30 min or after a local auth failure.
   - **local path:** poll `params` every ~5 s and merge into `self.data[node_id]["params"]`. Mark the node connected while local works.
   - **fallback:** after N consecutive local failures, use the cloud poll (30 s) and retry local in the background.
   - `async_set`: local first, cloud on failure (same payload).
4. **Discovery:** zeroconf matcher in `manifest.json`, plus an optional manual host override in the options flow for networks without mDNS (VLANs).
5. **`manifest.json`:** `iot_class` → `local_polling`, add `zeroconf`. `requirements` stays empty if we rely on `cryptography` from core.
6. **Diagnostics:** expose "connection: local/cloud" as a diagnostic sensor or attribute.
7. **Tests:** handshake against a fake device built from the same primitives (fixed keys, known vectors), message encode/decode, coordinator fallback (local fails → cloud used → local recovers), and that writes use the same payload on both paths.

## Acceptance criteria

- With internet disconnected, HA still reads and controls every entity.
- A state change on the panel shows up in HA in under ~10 s on LAN, if the firmware reports it locally sooner than to the cloud. Measure and note the result.
- Losing the LAN path falls back to the cloud with no user action, and local resumes when available.
- The POP never appears in logs or diagnostics (redacted).
- `pytest` passes, and so do the HACS and hassfest CI jobs.

## References

- ESP-IDF `components/esp_local_ctrl/proto/esp_local_ctrl.proto`
- ESP-IDF `components/protocomm/proto/{session,sec1,constants}.proto`
- ESP-IDF `tools/esp_prov/security/security1.py` and `tools/esp_prov/transport/transport_http.py`
- ESP-IDF `examples/protocols/esp_local_ctrl/scripts/esp_local_ctrl.py`: a complete Python client for this protocol
