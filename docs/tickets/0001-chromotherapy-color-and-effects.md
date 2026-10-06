# 0001 — Chromotherapy: color picker and effects

**Status:** ready to implement (needs device access to confirm behaviour)
**Area:** `light.py`, `button.py`, tests

## Goal

Make the `Cromoterapia` light behave like the official app:

- choose a **color**
- switch the **effect** (the app's "Efeito" control: the light blinks or stays solid)

The current integration exposes a full HS color wheel plus a separate "Efeitos" button. That may not match what the hardware can actually do, and the button may be sending the wrong value (see "Bug in the current button").

## What the device reports (Sense Duo, fw 1.3)

Device `Cromoterapia`, type `esp.device.light`:

| Param name | Type | Data | Props | Bounds |
|---|---|---|---|---|
| `ID` | `esp.param.name` | string | read, write | |
| `Status` | `esp.param.power` | bool | read, write | |
| `Brilho` | `esp.param.brightness` | int | read, write | 0–100 |
| `Saturação` | `esp.param.saturation` | int | read, write | 0–100 |
| `Cor` | `esp.param.hue` | int | read, write | 0–360 |
| `Efeitos` | `parametro_avanca_cromo` | bool | **write only** | |

Even though `Efeitos` is declared write-only, its current value **is** present in the node params (`"Efeitos": true` was observed), so its state can be read.

## What the app does (from the decompiled APK v1.0.5)

Source: `com.espressif.ui.adapters.ParamAdapter`.

### Color (`Cor`)

The widget is chosen by the param's `ui_type`:

- `esp.ui.hue-circle` → full color wheel (`holocolorpicker`). On selection the app converts the color to HSV and sends **only the hue** as an int:
  ```json
  {"Cromoterapia": {"Cor": 210}}
  ```
- `esp.ui.hue-slider` → hue bar, which sends `{"Cromoterapia": {"Cor": <int>}}`.

Brightness and saturation are generic sliders, shown only if their `ui_type` is `esp.ui.slider`. The app never sends saturation together with the hue.

### Effect (`Efeitos`)

- Rendered when the param's `ui_type` is `esp.ui.toggle` and its name contains `Efeitos`. It shows as a single "forward" button labelled **"Efeito"**.
- A tap sends the **inverse of the current value**: it toggles, it is not a pulse.
  ```java
  jsonObject.addProperty(param.getName(), Boolean.valueOf(!param.getSwitchStatus()));
  ```
- `Efeitos` is explicitly removed from the params offered in schedules (`ScheduleActionAdapter`).
- The app has no effect names in its strings (only `effect` = "Efeito"). The meaning of each value is decided by the firmware.

## Bug in the current button

`button.py` always writes `{"Efeitos": true}`. The app writes `!current`. If the firmware reacts to value *changes*, our button does nothing when the value is already `true`, and only works every other time. Confirm this on the device and fix it in any case: write `not current`.

## Open questions (verify on the device)

1. **`ui_type` of every Cromoterapia param.** Extend `tools/sinapse_discovery.py` to print `ui_type`. That tells us whether the app shows a wheel or a bar, and whether it shows brightness and saturation sliders at all.
2. **What `Efeitos` true/false means.** Field report: "blink or not blink". Toggle it and record which value is solid and which is blinking. Check whether there are more than two states (repeated presses cycling through several effects even though the value is a bool).
3. **Color resolution of the hardware.** Send hues 0, 30, 60 … 330 and look at the spots. Is the color continuous, or does it snap to a small palette? Do `Saturação` and `Brilho` change anything visible? If only a few colors exist, write down the hue ranges that map to each one.
4. **Does changing `Cor` reset `Efeitos`** (or the reverse)?

## Proposed implementation

1. **Effect on the light entity** (preferred over a separate button):
   - `supported_features |= LightEntityFeature.EFFECT`
   - `effect_list = ["Solid", "Blink"]`: confirm names and order in question 2, and add pt-BR translations via `translation_key`.
   - `effect` property reads `params["Cromoterapia"]["Efeitos"]` and maps it to the name.
   - `async_turn_on(effect=...)` writes `Efeitos` only when the target differs from the current value.
   - If question 2 shows the bool *cycles* more than two effects, keep a "Next effect" button instead, writing `not current`, and drop the effect list.
2. **Color:**
   - Continuous color: keep `ColorMode.HS`, but only send what changed (`Cor`, plus `Saturação` if supported). This matches the app.
   - Discrete palette: snap the requested hue to the nearest supported color before sending, and report the snapped hue back so the UI doesn't drift. Optionally expose the palette as `effect_list` entries or a `select`.
   - If `Saturação` and `Brilho` have no visible effect, stop sending them and use `ColorMode.HS` without brightness, or `ColorMode.ONOFF` plus a color `select`, whichever matches the hardware.
3. **Remove or adjust `button.py`.** The generic write-only-bool → button rule in `generic.py` would otherwise still create the `Efeitos` button. Add `parametro_avanca_cromo` to `HANDLED_BY_DEVICE[DEV_LIGHT]` in `const.py` once the light handles it.
4. **Tests** (`tests/test_init.py` fixtures already contain the Cromoterapia device):
   - setting an effect writes `Efeitos` with the right bool, and nothing when it is unchanged
   - the effect state follows the params
   - the color write payload matches the app (`{"Cromoterapia": {"Cor": <int>}}`)
   - no `button.*_efeitos` entity once the effect is on the light

## Acceptance criteria

- The user can choose any color the hardware supports and switch blink/solid from the HA light card and from automations (`light.turn_on` with `effect:`).
- The HA state matches the physical spots within one poll interval.
- `pytest` passes, and so do the HACS and hassfest CI jobs.
