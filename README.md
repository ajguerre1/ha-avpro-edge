# AVPro Edge for Home Assistant

Bring your AVPro Edge HDMI matrix into Home Assistant.

Pick which source plays on which display from a dashboard, a script, a scene or a voice assistant —
the same way you control anything else in your home. When someone changes a source at the matrix
itself, Home Assistant sees it straight away.

Works with the **AC-MX44-AUHD** and other switchers in the AUHD family. Installs and updates
through [HACS](https://hacs.xyz/).

> **Early release.** Developed and tested against firmware **V1.41**. Other firmware versions offer
> slightly different features; the integration checks what your unit actually supports rather than
> assuming, so anything unavailable simply does not appear.

---

## What you get

Each **output** becomes a media player with a source list — choose an input, the display follows.
Your own names for the inputs are used, so the list reads the way your matrix is labelled.

Alongside that, one device with:

| Control | What it does |
|---|---|
| **Output source** | Which input each display is showing |
| **Output stream** | Turn an output's picture on or off — the closest thing to "blank that screen" |
| **Signal detected** | Whether each port currently has a live picture on it |
| **Audio** | Separate audio routing, delay and the extracted-audio output, per output |
| **Picture** | Scaler mode, image enhancement and a test pattern, per output |
| **EDID** | What each input advertises to the source connected to it |
| **Input power** | Whether the matrix is driving each input's HDMI connection |
| **Hot plug reset** | Nudges a source to renegotiate when it has settled on the wrong picture |
| **Front panel** | Button lock, and how long the display stays lit |

Most of these are settings you adjust once, so they arrive switched off to keep your dashboards
tidy. Turn on any you want in **Settings → Devices & services → AVPro Edge → Entities**.

---

## Before you start

You need:

- An AVPro Edge AUHD-series matrix on the same network as Home Assistant
- Its IP address — you can find this on the matrix's front panel or in your router
- [HACS](https://hacs.xyz/) installed, if you want the easy route

A fixed IP address is worth setting up. If the matrix moves to a different address, Home Assistant
will lose track of it until you point it at the new one.

---

## Installing

### Through HACS

1. Open **HACS** in Home Assistant
2. Click the **⋮** menu (top right) → **Custom repositories**
3. Paste `https://github.com/ajguerre1/ha-avpro-edge` and choose category **Integration**
4. Find **AVPro Edge** in the list and click **Download**
5. **Restart Home Assistant**

### By hand

Copy the `custom_components/ha_avpro_edge/` folder into your Home Assistant
`config/custom_components/` folder, then restart Home Assistant.

### Adding your matrix

1. Go to **Settings → Devices & services**
2. Click **+ Add integration** and search for **AVPro Edge**
3. Enter the matrix's IP address

That is the only thing you are asked for. The integration reads the rest — model, how many inputs
and outputs, and the names you have given them — from the matrix itself.

---

## Settings

Click **Configure** on the integration to change these. They take effect immediately; nothing
restarts and no controls disappear while you do it.

| Setting | Default | What it means |
|---|---|---|
| **Connection** | Automatic | How Home Assistant talks to the matrix. See below. |
| **Update speed** | Balanced | How often Home Assistant checks in. Responsive is quicker, Gentle is lighter on the matrix. |
| **Allow changes** | On | Turn off to make the integration read-only. Useful if you want to watch it for a while before letting it control anything. |

### About the connection setting

The matrix offers two ways in, and the integration prefers the faster one.

**Automatic** (recommended) uses the matrix's control connection, which reports changes the instant
they happen and can see everything the matrix does. If that connection is unavailable, it falls
back to the web interface so you keep control either way, and tells you it has done so.

**Web interface only** never uses the control connection at all. Choose this if something else in
your system needs it — the matrix accepts only one control connection at a time. A few controls are
unavailable this way, because the web interface cannot report them.

---

## Actions

Two extra actions, for things that do not fit a single control.

**Route all outputs** sends every display to the same input in one go — handy for "everyone watch
this" scenes.

```yaml
action: ha_avpro_edge.route_all
data:
  config_entry_id: <your matrix>
  source: 2
```

**Send command** passes a command straight to the matrix, for anything this integration does not
already cover. It hands back whatever the matrix replied, so you can tell whether it was accepted.

```yaml
action: ha_avpro_edge.send_command
data:
  config_entry_id: <your matrix>
  endpoint: video
  command: O1I2
response_variable: result
```

For safety this cannot reach the matrix's network settings or factory reset. A wrong network
setting would take the matrix off the network entirely, which is not something an automation should
be able to do by accident.

---

## Troubleshooting

**The integration will not add my matrix.**
Check the IP address is right, and that you can open it in a browser from the same network. If the
page loads but Home Assistant still refuses, the unit may not be an AUHD-series matrix — the
integration checks what answers before accepting it.

**Everything shows as unavailable.**
The matrix is unreachable. Check it is powered on and on the network. Home Assistant reconnects on
its own once it comes back; you do not need to restart anything.

**A notice says the control connection is unavailable.**
Home Assistant expected the faster connection and could not get it, so it fell back to the web
interface. Everything still works, but a few controls are missing and changes take a little longer
to appear. The matrix accepts only one control connection at a time, so the usual causes are the
matrix restarting, a brief network problem, or another system holding that connection. Home
Assistant re-checks every minute and switches back on its own, and the notice clears itself.

**Some controls are missing.**
Two possible reasons. Most arrive switched off to keep dashboards tidy — turn on the ones you want
under **Entities**. If a control is missing entirely, either your firmware does not offer it or you
are on the web-interface-only connection setting.

**A source shows the wrong picture size, or nothing at all.**
Try the **hot plug reset** button for that input. It briefly disconnects and reconnects the input,
which makes the source work out what the display can accept all over again.

**A display went black.**
Check whether that output's **stream** control is switched off. That control deliberately stops the
picture; switch it back on.

**Changing something appears to do nothing.**
Check **Allow changes** is on in the integration's settings. When it is off, the integration will
not change anything on the matrix.

### Getting help

Open an issue at [github.com/ajguerre1/ha-avpro-edge/issues](https://github.com/ajguerre1/ha-avpro-edge/issues).

Please attach the diagnostics file — on the integration page, **⋮ → Download diagnostics**. It is
built to be safe to share: no names, no addresses, nothing identifying your setup, only what is
needed to understand the problem.

---

## Removing it

1. **Settings → Devices & services → AVPro Edge → ⋮ → Delete**

   This removes the matrix and all of its controls from Home Assistant. Nothing on the matrix
   itself is changed — your routing, names and settings stay exactly as they are.

2. If you also want the files gone, open **HACS → AVPro Edge → ⋮ → Remove**, then restart Home
   Assistant.

---

## For developers

Setup, testing and contribution notes are in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Trademarks

**AVPro Edge** and the AVPro Edge logo are trademarks of their owner. This project is independent
and is **not affiliated with, endorsed by, or supported by AVPro Edge**.

The logo appears here only to identify which product the integration works with, which is what an
integration icon is for in Home Assistant. The artwork remains the property of its owner.

## License

MIT, covering the code in this repository. The licence does not extend to the AVPro Edge marks; see
**Trademarks** above.
