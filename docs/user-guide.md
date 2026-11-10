# Arylic - LP Series: user guide

This guide walks through installing the integration, adding a player, every part of the device
page, and setting up speaker auto-power. For a compact reference (entity IDs, how it works under
the hood, security), see the [README](../README.md).

**Contents**

1. [Before you start](#1-before-you-start)
2. [Install](#2-install)
3. [Add a player](#3-add-a-player)
4. [The device page](#4-the-device-page)
5. [Speaker auto-power](#5-speaker-auto-power)
6. [Changing settings later](#6-changing-settings-later)
7. [Automations](#7-automations)
8. [Troubleshooting](#8-troubleshooting)
9. [Removing the integration](#9-removing-the-integration)

## 1. Before you start

You need:

- **Home Assistant 2026.3 or newer.**
- **An Arylic LP-series streamer.** It's tested on the LP10. Other models on the same Libre-based
  firmware should work if they answer on TCP port 2018; if you try one, please send a
  [compatibility report](https://github.com/biddies4africa/ha-arylic-lp/issues/new/choose).
- **Network access from Home Assistant to the player:** TCP 2018 (control), and optionally
  TCP 7000 (AirPlay session check) and UDP 1800 (identity). On the same network as Home Assistant
  this just works. Across VLANs, allow that traffic in your router.
- **Optional: a smart plug for the speakers.** Any `switch`, `light` or `input_boolean` will do.

What the player's local interface can't provide, and so this integration can't show:

- **Track details** (title, artist, artwork).
- **Paused versus stopped.** Both show as *idle*.

## 2. Install

### With HACS (recommended)

1. Open HACS, choose **⋮ → Custom repositories**, and add
   `https://github.com/biddies4africa/ha-arylic-lp` with type **Integration**. (Or use the
   *Open in HACS* button in the README.)
2. Find **Arylic - LP Series**, choose **Download**, and restart Home Assistant.

HACS then tells you when a new version is out.

### Manually

1. Download the [latest release](https://github.com/biddies4africa/ha-arylic-lp/releases/latest).
2. Copy its `custom_components/arylic_lp` folder into your Home Assistant `config/custom_components/`
   folder, then restart Home Assistant.

## 3. Add a player

### A player Home Assistant has found

Players on the same network as Home Assistant announce themselves, so they usually appear under
**Settings → Devices & services → Discovered**. Choose **Add**, confirm, then continue with
[the speaker power step](#the-speaker-power-step).

### Adding a player by its address

Open **Settings → Devices & services → Arylic - LP Series** and choose **Add device**. (If the
integration isn't set up yet, use **Add integration** and search for *Arylic*.)

![The Arylic - LP Series integration page, listing two players](images/integration-page.png)

Enter the player's IP address. The name is optional; leave it empty to use the name the player
reports.

![The "Add an Arylic LP-series player" form, with IP address and optional name](images/add-player.png)

A player added by address on a **different network** from Home Assistant can't be followed if its
address changes, so give it a fixed address (a DHCP reservation) in your router. Players on the
same network are followed automatically.

### The speaker power step

Last, you're asked about speaker power. This is the same screen you get later from **Configure**.

![The Speaker power step: a speaker power switch picker and the AirPlay session option](images/speaker-power-options.png)

- **Speaker power switch:** the plug that powers the speakers. Leave it empty if you only want
  play state and controls; you can link one later from the device page.
- **Also use the AirPlay session flag to detect playback:** leave this on. It's a second way of
  noticing AirPlay playback.

Choose **Submit** and the player is ready.

## 4. The device page

Each player gets its own device page: open **Settings → Devices & services → Arylic - LP Series**
and choose the player. Home Assistant groups the entities into cards.

![The device page: Controls, Sensors and Diagnostic cards beside the Configuration card](images/device-page.png)

Every card has an **Add to dashboard** button, which is the quickest way to put the controls or
settings on a dashboard.

### Device info

![The Device info card: LP10 by Arylic, firmware version and a Visit link](images/device-info.png)

The model and firmware the player reports. **Visit** opens the player's own web page.

### Controls

![The Controls card: the player with a volume slider and a play button](images/device-controls.png)

The player itself, as a media player:

- **Volume** (in 2% steps from the volume buttons) and **mute**.
- **Play/pause, next and previous.** These go to whatever app is playing, as the player's own
  buttons do.
- **Source:** Network, Bluetooth, Line in or USB, from the player's more-info dialog.

It shows **Playing** or **Idle**. Changes made elsewhere, such as in the Arylic app or with the
player's buttons, usually show up straight away, and at most 30 seconds later.

### Sensors

![The Sensors card: Auto-power is Idle and Playing is Not running](images/device-sensors.png)

**Playing** is on while the player plays from any source. Home Assistant labels this kind of
sensor *Running* / *Not running*. Its attributes show what it's based on:

| Attribute | Meaning |
|---|---|
| `mcu_play_flag` | The player's own play flag |
| `airplay_session` | Whether an AirPlay session is active (if AirPlay detection is on) |
| `source` | The current source code (`NET`, `BT`, `LINE-IN`, `USBPLAY`) |

**Auto-power** only appears when a plug is linked. It shows what auto-power is doing:

| State | Meaning |
|---|---|
| Playing | Music is playing; the plug stays on |
| Idle | Nothing playing, and no countdown running (the plug is off, or auto-off doesn't apply) |
| Turning off soon | Nothing playing; the plug will be switched off at `turns_off_at` |
| Player unavailable | Home Assistant can't reach the player |

Its attributes add the linked plug (`power_switch`), whether auto-power switched it on
(`turned_on_by_autopower`), and the last action, when it happened, and any error.

### Configuration

![The Configuration card with all of a player's settings](images/device-configuration.png)

Home Assistant lists these alphabetically, so the names keep related settings together.

| Setting | What it does | Kept by |
|---|---|---|
| **AirPlay playback detection** | Also counts an active AirPlay session as playing. Takes effect at once | Home Assistant |
| **Auto-off** | Switches the plug off after the delay with nothing playing | Home Assistant |
| **Auto-off delay** | Minutes with nothing playing before switching off: 1–240, default 30 | Home Assistant |
| **Auto-off only if auto-on turned it on** | Leaves a plug you switched on by hand alone | Home Assistant |
| **Auto-on** | Switches the plug on when playback starts | Home Assistant |
| **Auto-power plug** | The plug auto-power drives, or *No plug* | Home Assistant |
| **EQ bass / mid / treble** | Tone, −10 to +10 dB | The player |
| **EQ virtual bass** | The player's bass enhancement | The player |
| **LED** | The front LED/display | The player |

The four **Auto-…** settings only appear once a plug is linked. Settings kept by the player are
the same ones the Arylic app changes, so a change in either place shows in both. Settings kept by
Home Assistant survive restarts.

The **Auto-power plug** list offers each switch, light and input_boolean that is a device's main
control. It leaves out other integrations' settings switches, hidden or disabled entities, and
this integration's own switches. If the plug you want isn't listed, link it from
[Configure](#6-changing-settings-later), which can pick any of them.

### Diagnostic

![The Diagnostic card: Connection drops is 3](images/device-diagnostic.png)

**Connection drops** counts how often the player has become unreachable, kept across restarts.
Its `last_drop` attribute says when it last happened. An occasional drop is normal: the LP10
restarts its network now and then. A count that keeps climbing points to a network problem, or to
something else hammering the player (see [Troubleshooting](#8-troubleshooting)).

### Activity

![The Activity card: auto-power going from Playing to Turning off soon to Idle](images/device-activity.png)

The logbook for this player. Besides state changes, every auto-power action is written here:

| Message | Meaning |
|---|---|
| *turning on Lounge Speakers Socket 1 (playback started)* | Auto-on is switching the plug on |
| *turning off Lounge Speakers Socket 1 (idle)* | The auto-off delay ran out |
| *turning off … (player unreachable)* | The player was unreachable for the whole (longer) countdown |
| *attempt 2 failed: no response* | The plug didn't respond; it will try again |
| *turned on … (attempt 3)* | It worked after retrying |
| *gave up after 5 attempts* | Every attempt failed; a Repairs notice explains |
| *stopped retrying: playback stopped* | The action was no longer wanted, so retries stopped |

## 5. Speaker auto-power

### Turning it on

Pick the plug in **Auto-power plug** on the device page. The auto-power settings and the
**Auto-power** sensor appear straight away, with sensible defaults: switch on at playback, switch
off 30 minutes after it stops.

### What happens

1. **Playback starts.** If the plug is off, auto-power switches it on. It only does this when
   playback *starts*, so if you switch the plug off by hand mid-song, it stays off.
2. **Playback stops.** The **Auto-power** sensor shows *Turning off soon* and the countdown
   starts (the **Auto-off delay**).
3. **Playback resumes before the countdown ends.** The countdown is cancelled.
4. **The countdown ends.** The plug is switched off.

Some situations are deliberately *not* treated as playback starting:

- **Home Assistant starting** while the player is already playing.
- **The player coming back** after being unreachable, still playing.

In both cases, a plug that's off stays off.

### Common setups

| You want | Set |
|---|---|
| Speakers on with the music, off half an hour later | The defaults |
| Off sooner, or later | **Auto-off delay**, 1–240 minutes |
| Switch on automatically, but you'll switch off yourself | **Auto-off** off |
| Switch off automatically, but you'll switch on yourself | **Auto-on** off |
| When you've switched the speakers on by hand (say, for the TV), leave them on | **Auto-off only if auto-on turned it on** on |

### Plugs that don't respond

Cloud-connected plugs sometimes take a while, or fail. Auto-power checks that the plug really did
switch. If not, it tries again for up to 20 seconds, as long as the action is still wanted. Each
attempt shows in [Activity](#activity). If every attempt fails, Home Assistant shows a **Repairs**
notice (*Could not turn on …*) with the error. It clears once auto-power succeeds again.

### When the player is unreachable

- **Short blips are ignored.** The last state is held for three failed checks (about six seconds)
  before the entities show as unavailable.
- **Outages don't cut the music.** While the player can't be reached, the auto-off countdown is
  at least 60 minutes. A brief network hiccup won't switch the speakers off, but a player that's
  been unplugged still switches them off eventually.
- **After 10 minutes unreachable**, a Repairs notice (*… is unreachable*) appears. It clears when
  the player is back.

## 6. Changing settings later

**On the device page:** everything in the [Configuration card](#configuration), including which
plug is linked and AirPlay detection.

**From Configure:** **Settings → Devices & services → Arylic - LP Series**, then the cog next to
the player. This is the same *Speaker power* screen as during setup, and it can link any switch,
light or input_boolean.

**The player's address:** players on the same network are followed automatically when their
address changes. For a player added by address, use **⋮ → Reconfigure** on its entry and enter the
new address.

## 7. Automations

The entities work like any others in automations. Some examples follow; replace the entity IDs
with your own.

**Do something when music starts:**

```yaml
automation:
  - alias: "Lounge: music mode"
    triggers:
      - trigger: state
        entity_id: binary_sensor.lounge_speakers_playing
        to: "on"
    actions:
      - action: scene.turn_on
        target:
          entity_id: scene.lounge_music
```

**Turn the volume down in the evening:**

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

**Switch to line in:**

```yaml
action: media_player.select_source
target:
  entity_id: media_player.lounge_speakers
data:
  source: Line in
```

**Get a notification when auto-power can't switch the plug:**

```yaml
automation:
  - alias: "Speakers: plug didn't switch"
    triggers:
      - trigger: event
        event_type: arylic_lp_autopower
        event_data:
          result: gave_up
    actions:
      - action: notify.notify
        data:
          message: >
            Couldn't {{ trigger.event.data.action | replace('_', ' ') }}
            {{ trigger.event.data.power_switch_name }}: {{ trigger.event.data.error }}
```

### The auto-power event

Every auto-power action fires an `arylic_lp_autopower` event, which you can use as a trigger:

| Field | Values |
|---|---|
| `action` | `turn_on`, `turn_off` |
| `result` | `requested`, `attempt_failed`, `succeeded`, `gave_up`, `cancelled` |
| `reason` | `playback_started`, `idle`, `player_unreachable` |
| `power_switch` / `power_switch_name` | The plug's entity ID and name |
| `attempt`, `error` | Which attempt, and what went wrong (when relevant) |
| `entity_id` | The player's **Auto-power** sensor |

A first-time success fires only `requested`; the plug's own state change follows. `succeeded`
is fired when an action works after a retry.

## 8. Troubleshooting

| Symptom | What to check |
|---|---|
| *Could not reach the player's control port* when adding it | Home Assistant must be able to open TCP 2018 on the player. Check the address, and across VLANs, allow the traffic in your router. |
| Entities keep going unavailable | Look at **Connection drops**. Make sure nothing else polls the player with a new connection every few seconds (see below). |
| Shows playing with nothing playing | The player's play flag can get stuck after a network restart mid-song. Switch the source to **Line in** and back. |
| Speakers didn't switch on | Check **Auto-on** is on, and look at **Activity** and the **Auto-power** sensor's `last_error`. Remember it switches on when playback *starts*. |
| Speakers didn't switch off | Check **Auto-off**, and whether **Auto-off only if auto-on turned it on** is on while the plug was switched on by hand. |
| The plug isn't in the Auto-power plug list | It's probably marked as a settings switch, or hidden. Link it from **Configure** instead. |

**Two quirks of the player's firmware**

- **Don't poll TCP 2018 with short-lived connections.** Opening a new connection for each query
  every few seconds makes the player's relay crash, and it then restarts its network (about 90
  seconds offline). This integration keeps one connection open; scripts that poll the other way
  will knock the player off.
- **The play flag can stick on "playing"** if the player's network restarts mid-song. Switching
  the source to Line in and back clears it.

### Getting help

1. On the player's device page, choose **⋮ → Download diagnostics**. The file leaves out the
   player's address, MAC and time zone, so it's safe to share.
2. [Open an issue](https://github.com/biddies4africa/ha-arylic-lp/issues/new/choose) and attach it.
   For problems with another model, use the **Compatibility report** form instead.

For more detail in Home Assistant's log, open **Arylic - LP Series** under Devices & services and
choose **⋮ → Enable debug logging**. Reproduce the problem, then disable it to download the log.

## 9. Removing the integration

1. **Settings → Devices & services → Arylic - LP Series**, then **⋮ → Delete** on each player.
2. Remove it in HACS (**⋮ → Remove**), or if you installed it by hand, delete
   `config/custom_components/arylic_lp`.
3. Restart Home Assistant.
