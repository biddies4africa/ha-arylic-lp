# Arylic - LP Series for Home Assistant

A local Home Assistant integration for **Arylic LP-series** network streamers (Libre Wireless LS8
based firmware, e.g. `AR241CE_*` / `AR241CP_*`) that:

- **Knows when the player is playing, from any source** (Spotify Connect, AirPlay, TIDAL, radio,
  Bluetooth…), without a shell, adb or cloud account.
- **Auto-powers your speakers:** switches a smart plug on when playback starts and off after a
  configurable period without playback.
- **Controls the player:** volume, mute, source, play/pause, next/previous as a normal HA
  `media_player`, plus bass/mid/treble, virtual bass and the front LED.

Add as many players as you like, each with its own plug and settings. Everything stays on your
LAN.

> **Compatibility:** so far this has only been tested with the **Arylic LP10**. It should work with
> other LP-series models, and other Arylic devices on the same newer Libre-based firmware, that
> expose the UART text API on TCP 2018, but that's untested. Reports welcome.

## Installation

**Prerequisites:** Home Assistant 2026.3 or newer, and a player HA can reach on TCP 2018 (and
optionally TCP 7000 and UDP 1800). Players on the same network as Home Assistant are discovered
automatically (mDNS) and their address is kept up to date if it changes, so no DHCP reservation is
needed. Players on another subnet can be added by IP, but then need a fixed address.

1. Copy `custom_components/arylic_lp` into your HA `config/custom_components/` folder. (This
   repository is private for now, so HACS can't install it yet.)
2. Restart Home Assistant.
3. Discovered players appear under **Settings → Devices & services**; click **Add**. Otherwise use
   **Add integration → Arylic - LP Series** and enter the player's IP address (and optionally a name).
4. Optionally pick the smart plug that powers the speakers. Leave it empty for play state and
   controls only.

### Configuration options

| Option | Where | Meaning |
|---|---|---|
| IP address | Setup; later **Reconfigure** | The player's address |
| Name | Setup | Defaults to the name the player reports |
| Speaker power switch | Setup; later **Configure** | Any `switch`, `light` or `input_boolean`. Empty = no auto-power |
| Also use the AirPlay session flag | Setup; later **Configure** | A second playback signal for AirPlay (on by default) |

## Entities (per player)

| Entity | Purpose |
|---|---|
| `media_player.<name>` | Volume, mute, source, play/pause, next/previous |
| `binary_sensor.<name>_playing` | On while playing (attributes: MCU play flag, AirPlay session, source) |
| `number.<name>_bass` / `_mid` / `_treble` | Tone, −10…+10 dB (stored on the player) |
| `switch.<name>_virtual_bass` | Virtual bass on/off (stored on the player) |
| `switch.<name>_led` | Front LED/display on/off (stored on the player) |
| `sensor.<name>_connection_drops` | Diagnostic: how often the player has become unreachable, with `last_drop` |

With a power switch linked, also:

| Entity | Purpose |
|---|---|
| `sensor.<name>_auto_power` | `playing` / `idle` / `off_pending` / `player_unavailable`, with `turns_off_at`, last action and last error |
| `switch.<name>_auto_on` | Turn the plug on when playback starts |
| `switch.<name>_auto_off` | Turn the plug off after the delay with no playback |
| `switch.<name>_only_turn_off_if_auto_on_turned_it_on` | Leave a plug you switched on by hand alone |
| `number.<name>_auto_off_delay` | Minutes without playback before switching off (1–240, default 30) |

Auto-power settings and the drop counter survive restarts.

## How it works (data updates)

The player exposes Arylic's plain-text UART API on **TCP 2018** (relayed to the MCU). The
integration keeps **one persistent connection** per player:

- `PLA;` (play state) is polled every 2 s.
- `STA;` (volume, source, mute, treble, bass, LED) plus `MID;` and `VBS;` are read every 30 s.
- The player broadcasts every reply to every connected client, so when another app changes a
  setting, the change arrives on our connection and is applied **immediately**.
- Optionally, the AirPlay receiver's session flag (`http://<ip>:7000/info`) is used as a second
  playback signal.
- Device identity comes from the player's discovery responder on UDP 1800.

This interface does **not** provide track metadata (title/artist/artwork), and paused vs. stopped
can't be told apart, so the player shows *playing* or *idle*.

## Behaviour and resilience

- **Turn-on happens when playback starts**, not whenever it's playing, so switching the plug off by
  hand mid-song is respected. Playback already running when HA starts doesn't switch a plug on, and
  neither does a player coming back online after an outage.
- **Short network blips are ignored:** the last state is held for up to 3 failed polls before the
  player goes unavailable. The client reconnects with exponential backoff (1 s → 30 s).
- **Outages don't cut the music:** while the player is unreachable, the auto-off countdown is at
  least 60 minutes, so a brief outage doesn't switch the speakers off, but a player that's been
  unplugged still does eventually. After 10 minutes unreachable, a **Repairs** issue is raised; it
  clears on recovery.
- **Plug commands are retried** (3 attempts) and **confirmed** against the plug's state; cloud plugs
  can be slow. If all attempts fail, a Repairs issue is raised with the error.
- **IP changes:** re-adding the same player at a new address updates the existing entry (matched by
  device ID), or use *Reconfigure*.

## Example automation

Turn the volume down in the evening:

```yaml
automation:
  - alias: "Lounge: quieter after 9pm"
    triggers:
      - trigger: time
        at: "21:00:00"
    actions:
      - action: media_player.volume_set
        target:
          entity_id: media_player.lounge_speakers
        data:
          volume_level: 0.15
```

## Known device quirks

- **Don't poll TCP 2018 with short-lived connections.** On the LP10, opening a new connection per
  query (connect, send, close) every few seconds makes the relay daemon crash, and the player then
  restarts its network (~90 s offline). This integration uses one persistent connection; avoid
  running other scripts that hammer the port.
- **The play flag can get stuck on "playing".** It comes from the MCU, which only knows what the
  streaming module last told it. If the module's network restarts mid-playback, the MCU can keep
  reporting playing with nothing playing, so auto-off never starts. Switching the source to
  *Line in* and back clears it.

## Troubleshooting

| Symptom | What to check |
|---|---|
| "Could not reach the player's control port" during setup | HA must be able to open TCP 2018 on the player. Across subnets/VLANs, allow the traffic in your router. |
| Entities keep going unavailable | Look at the **Connection drops** sensor. Check nothing else is polling TCP 2018 with short-lived connections (see quirks above). |
| Shows *playing* with nothing playing | The stuck play flag (see quirks): switch the source to *Line in* and back. |
| Speakers didn't switch on | Check **Auto-on** is on and the **Auto-power** sensor's `last_error`. Turn-on only happens when playback *starts*. |
| Anything else | Settings → Devices → the player → **Download diagnostics**, and open an issue with it (addresses and device IDs are redacted). |

## Removal

1. Settings → Devices & services → **Arylic - LP Series** → ⋮ on each entry → **Delete**.
2. Delete `config/custom_components/arylic_lp` and restart Home Assistant.

## Security note

TCP 2018 on these players is **unauthenticated**: anything on your LAN can change volume or source.
That's a property of the firmware, not of this integration. Don't expose the players to untrusted
networks.

## Tested with

| Model | Firmware | Result |
|---|---|---|
| LP10 | `AR241CP_8285.9.1` | Play state (native Spotify Connect), volume, mute, source, tone, LED, auto-power confirmed |
| LP10 | `AR241CE_8530.23.2` | Status, volume, source, tone and LED read correctly; play state not yet confirmed during playback. TCP 2018 stopped answering on this unit once; a full power-cycle (not a soft reboot) fixed it |

Not yet verified: `PLA` for AirPlay, Bluetooth and line-in (the AirPlay fallback covers AirPlay).

## Development

```sh
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

Tests use `pytest-homeassistant-custom-component` with a fake player TCP server. CI runs ruff,
pytest, hassfest and HACS validation.

## Licence

MIT. Not affiliated with Arylic or Libre Wireless. The icon is an original drawing of a generic
streamer front panel, not Arylic's logo.
