# ☀️ Siseli Inverter Bridge for Home Assistant — now with a 100% local mode

[![Siseli Local Bridge](https://img.shields.io/badge/version-2.6.63-blue.svg?label=Siseli%20Local%20Bridge)](siseli_local_bridge/CHANGELOG.md)
[![Siseli Inverter Bridge](https://img.shields.io/badge/version-2.6.22-blue.svg?label=Siseli%20Inverter%20Bridge)](siseli_bridge/CHANGELOG.md)
[![CI](https://github.com/fadmaz/siseli-ha/actions/workflows/ci.yml/badge.svg)](https://github.com/fadmaz/siseli-ha/actions/workflows/ci.yml)
[![HA Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-green.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

<img src="siseli_bridge/icon.png" alt="" width="140" align="right">

A Home Assistant add-on that reads your Siseli-compatible solar inverter **locally**, by
decoding the telemetry it already sends to the vendor cloud — and publishes it to Home
Assistant through MQTT auto-discovery.

Your inverter keeps talking to the cloud, so the official mobile app carries on working.
The bridge only listens in.

**207 sensors across 7 devices**, 143 enabled on a fresh install. No cloud API, no
polling, no credentials for anything but your own broker.

> **Acknowledgment:** an expanded and generalized fork of the original work at
> [yuraantonov11/siseli-ha](https://github.com/yuraantonov11/siseli-ha). Huge thanks to
> the original author.

> **New: 100% local mode.** This repository also ships **Siseli Local Bridge**, which can
> replace the vendor cloud entirely: the WiFi dongle talks only to your Home Assistant
> host, telemetry refreshes every 15 seconds, and the inverter's settings can be changed
> from Home Assistant. See [Siseli Local Bridge — 100% local mode](#siseli-local-bridge--100-local-mode).

---

## Install

**Settings → Add-ons → Add-on Store → ⋮ → Repositories**, then add:

```
https://github.com/fadmaz/siseli-ha
```

Install **Siseli Inverter Bridge**, then follow
**[the documentation](siseli_bridge/DOCS.md)** — requirements, every configuration
option, network setup and troubleshooting. Home Assistant renders that same page on the
add-on's **Documentation** tab once it is installed.

---

## How it works

Your inverter's WiFi dongle publishes telemetry over MQTT to the Siseli cloud at
`8.212.18.157:1883`. The add-on:

1. **Puts itself in the path** using ARP interception, so the inverter's packets reach
   the Home Assistant host.
2. **Reassembles the TCP stream** and pulls out the MQTT PUBLISH frames.
3. **Decodes the payload** — base64 blocks keyed by four-character names (`2ONL`, `WdRR`,
   `Yavb`, …), each a list of space-separated values read by position.
4. **Publishes to your broker** with MQTT auto-discovery, so entities appear on their own.
5. **Forwards the traffic onward** to the cloud, unchanged.

It never terminates a connection and never opens a listening socket. It observes, decodes,
and relays.

**What this means in practice:** if the add-on stops, your inverter keeps working and the
vendor app keeps working. You lose the Home Assistant sensors, nothing else.

---

## Known limitations

**45 of the 207 sensors read `Unknown` and cannot be decoded.** Earlier versions filled them
with hardcoded constants — fault flags that could never report a fault, a `Mode` that was
a fixed string in the source. Those were removed in 2.6.1. The entities remain, disabled
by default, and publish an explicit "no value" rather than a comforting lie. If your
inverter emits blocks that would decode them, an issue with a capture is welcome.

If instead *every* sensor reads `Unknown`, that is a different situation entirely: your
inverter speaks a protocol this add-on does not decode. The log says so in one line —
see [Every sensor reads Unknown](https://github.com/fadmaz/siseli-ha/blob/main/siseli_bridge/DOCS.md#every-sensor-reads-unknown).

**Two current sources disagree, and there is no way to tell which is right.** The BMS and
the inverter's own ammeter can differ by a factor of two or more, in either direction. The
official app displays both and they disagree there too. The bridge uses the BMS figure and
logs `[ENERGY SOURCE DISAGREEMENT]` when the two diverge, rather than silently picking a
winner. Settling this needs a clamp meter on the DC bus.

**Block positions were reverse-engineered from one device.** There is no published schema.
Values are read by position from blocks whose meaning was inferred. Where a position was
not understood, nothing is published.

---

---

## Supported hardware

Anything using the Siseli IoT cloud platform, which includes inverters sold as:

Solar of Things · LUMINOUS NEO · SUN WISE · Queen Tech · LIB Life · Sun house · LeiLing ·
SunSaviour · ECOmenic · HC solar · 沐能低碳 · PowMr · Taico

**Verified in detail:** one device — `HPVINV04`, firmware `0010.11`, two inverters in
parallel with a 32-cell battery bank. Its captures are byte-faithful fixtures in the test
suite, and the decoded values are checked against the official app.

Other brands on that list are reported to work but are not covered by captures. If you
have one, a debug capture is the single most useful contribution you can make — see
[Troubleshooting](siseli_bridge/DOCS.md#your-inverter-is-not-decoded).

---

---

## Siseli Local Bridge — 100% local mode

**Siseli Local Bridge** is the second add-on in this repository. It decodes the same
telemetry as Siseli Inverter Bridge, and it can run in one of two modes:

- **Pass-through** (default, `LOCAL_CLOUD_IP` left empty) — it listens in exactly like
  Siseli Inverter Bridge: the dongle still talks to the vendor cloud and the official app
  keeps working.
- **100% local** (`LOCAL_CLOUD_IP` set) — it **impersonates the vendor cloud** on a spare
  IP address of your Home Assistant host. The dongle never reaches the internet, the
  bridge asks it for a full reading every 15 seconds instead of waiting for its own
  5-minute push, and the inverter's settings become Home Assistant controls.

**205 sensors across 7 devices**, 144 enabled on a fresh install, plus the controls listed
below. **42 of the 205 sensors read `Unknown`** and cannot be decoded; like the original
add-on, they stay disabled and publish no value rather than a made-up one.

**Trade-off of the 100% local mode:** the vendor cloud no longer hears from the dongle, so
the official app shows the inverter offline. To use the app again, go back to
pass-through (see the end of this section).

The full option reference, with every setting explained, is on the add-on's
**Documentation** tab: [`siseli_local_bridge/DOCS.md`](siseli_local_bridge/DOCS.md).

### How the local cloud works

1. The **Network IP Alias** add-on (also in this repository) gives the Home Assistant host
   one extra IPv4 address on your LAN. That address plays the vendor cloud.
2. **ARP interception** puts the Home Assistant host between the dongle and your router,
   as in pass-through mode.
3. When the dongle boots, it looks up `broker.mqtt.solar.siseli.com` and
   `dtu.access.solar.siseli.com`. The bridge **answers those DNS queries** with the extra
   address, and answers the dongle's **HTTP bootstrap calls** (check-in, device lookup,
   "which MQTT broker should I use"). It also answers the vendor's HTTP address the
   dongle has cached, since some dongles skip DNS for it.
4. The dongle then connects to a **small MQTT broker built into the bridge** on the extra
   address. The bridge acknowledges its messages as the real cloud would, and requests a
   full reading every `TELEMETRY_POLL_INTERVAL_SEC` seconds.
5. An **nftables rule** stops the host's own services (the Mosquitto add-on, for example)
   from answering on the extra address, so only the bridge does.
6. Decoded values go to **your** MQTT broker, and Home Assistant picks them up through
   MQTT discovery. Setting changes made in Home Assistant go back to the dongle on the
   same connection.

### Setting up the 100% local mode

The addresses below are **examples**; use your own.

1. Install the **Mosquitto broker** add-on and the MQTT integration, if you have not
   already.
2. On your router, give the WiFi dongle a **fixed address** (DHCP reservation), and pick
   one **free address** on the same LAN for the local cloud.
3. Add this repository (**Settings → Add-ons → Add-on Store → ⋮ → Repositories**):

   ```
   https://github.com/siselilocal/siseli-ha
   ```

4. Install **Network IP Alias** and give it the free address. On a Raspberry Pi running
   Home Assistant OS the LAN interface is `end0`; on most other hosts it is `eth0`.

   ```yaml
   INTERFACE: end0
   IP_ADDRESS: 192.168.1.200/24
   ```

5. Install **Siseli Local Bridge** and set at least these options (everything else can
   stay at its default):

   ```yaml
   MQTT_HOST: core-mosquitto
   MQTT_USER: your-mqtt-user
   MQTT_PASSWORD: your-mqtt-password
   INVERTER_IP: 192.168.1.50            # the dongle's fixed address
   ROUTER_IP: 192.168.1.1               # your gateway
   AUTO_INTERCEPT: true
   LOCAL_CLOUD_IP: 192.168.1.200        # same address as Network IP Alias, without /24
   LOCAL_CLOUD_PORT: 1883
   HTTP_STUB_PORT: 80
   HTTP_STUB_REAL_IPS: 8.212.16.60
   DNS_SPOOF_DOMAIN: broker.mqtt.solar.siseli.com,dtu.access.solar.siseli.com
   MQTT_BROKER_HOSTNAME: hongkong.broker.mqtt.solar.siseli.com
   TELEMETRY_POLL_INTERVAL_SEC: 15
   FORWARD_ALL_INVERTER_TRAFFIC: false
   ```

6. Start **Network IP Alias**, then **Siseli Local Bridge**, and enable **Start on boot**
   for both.
7. **Power-cycle the WiFi dongle.** The DNS answers and the HTTP bootstrap only happen
   when it boots; until then it stays connected to the real cloud.
8. Within a minute the entities appear and refresh every 15 seconds. The controls are on
   the **Siseli Local Inverter 1** device, in its **Configuration** card.

> **Tip — faster connection: redirect the two names in your router's DNS.** If your router
> (or Pi-hole, AdGuard Home…) lets you add local DNS records, point these two names at the
> local cloud address (`LOCAL_CLOUD_IP`, `192.168.1.200` in the example above):
>
> ```
> hongkong.broker.mqtt.solar.siseli.com  ->  192.168.1.200
> dtu.access.solar.siseli.com            ->  192.168.1.200
> ```
>
> The dongle then gets the local address straight from the router, instead of the bridge
> having to answer its DNS query faster than the router does. It connects sooner after a
> power cut or a dongle reboot, and reconnects even if it happens to boot before the
> add-on. **Remove these records before going back to pass-through**: while they exist,
> the dongle cannot reach the real cloud whatever the add-on's settings.

### Controlling the inverter from Home Assistant

Every control shows the value **read back from the inverter**, not the last value sent:
if the inverter refuses or adjusts a setting, the entity shows what it really holds. The
commands follow the Voltronic PI30 protocol the inverter speaks; each one below was
checked on the test hardware against the inverter's own front panel.

| Control | Manual programme | Values |
|---|---|---|
| Output Source Priority | 01 | SBU / SUB |
| Grid Working Range | 03 | UPS / Appliance |
| Battery Type | 05 | AGM … Growatt, Pylontech, … (10 types) |
| ECO Power Saving | 08 | on / off |
| Output Voltage | 10 | 220 / 230 / 240 V |
| Max Utility Charge Current | 11 | 2, 10–90 A |
| Back To Grid Voltage | 12 | 44–51 V |
| Back To Battery Voltage | 13 | 48–58 V |
| Charger Priority | 16 | OSO / CSO / SNU |
| Buzzer | 18 | on / off |
| Backlight | 20 | on / off |
| Equalization Voltage | 31 | 48.0–60.0 V |
| BMS Lock Machine SOC | 38 | 5–95 %, steps of 5 |
| Restore Mains Charging SOC | 39 | 5–95 %, steps of 5 |
| Restore Battery Discharging SOC | 40 | 5–95 %, steps of 5 |
| Inverter Startup SOC | 41 | 5–100 %, steps of 5 |
| Solar Supply Priority | 43 | BLU / LBU |
| Dual Output | 60 | on / off |

Plus a **BMS Communication Normal** sensor: when it reads `No`, the inverter has lost its
BMS and the state of charge it reports is its own estimate (it read 99–100 % against a
real 43 % in testing), so SOC-based automations should check it first.

**Handle with care:**

- **Battery Type.** Changing it can briefly cut the inverter's AC output; if the inverter
  also powers your Home Assistant host, Home Assistant goes down with it. A battery type
  the BMS does not speak raises inverter warning 61 and loses the BMS data.
- **BMS Lock Machine SOC (programme 38)** shuts the inverter down below it. The bridge
  refuses any value at or above the current SOC, and refuses it entirely while BMS
  communication is lost.
- The inverter only accepts the SOC thresholds in steps of 5, and raising programme 38
  makes it raise programmes 39 and 62 by itself; the entities show where it settles.
- Two vendor-app actions are **deliberately not exposed**, because they cannot be undone:
  "Control Parameters To Default Value" (factory reset of every setting) and "Reset PV
  Energy Storage".

### Hardware used for the 100% local tests

| Part | Model |
|---|---|
| Inverter | Datouboss 11 kW (manual 4811B), model code `HPVINV04`, firmware `0010.14`, 48 V battery system, 240 V / 50 Hz output |
| WiFi dongle | RWB1 (Solar of Things), firmware `V1.44.5_SolarV67` |
| Battery | WattCycle LiFePO4 3U rack server battery, 48 V 100 Ah (16 cells), BMS on the inverter's Growatt (GRO) protocol |
| Home Assistant host | Raspberry Pi 4, Home Assistant OS, Mosquitto broker add-on, Network IP Alias add-on on `end0` |
| PV | one string on PV1 |

Other devices that use the same cloud should behave the same way, but only this setup has
been tested in 100% local mode. Reports from other hardware are welcome.

### Going back to pass-through

Clear `LOCAL_CLOUD_IP` (and set `FORWARD_ALL_INVERTER_TRAFFIC: true` if the dongle does
not reconnect), remove the router DNS records if you added them, restart the add-on and
power-cycle the dongle. It finds the real cloud
again and the official app works; the Home Assistant controls stop working, since there is
no local connection to send them on, and the sensors fall back to the dongle's own
5-minute push.

---

---

## Contributing

Development setup, test conventions, how to add a capture from your own inverter, and the
release checklist are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

Release history is in [`siseli_bridge/CHANGELOG.md`](siseli_bridge/CHANGELOG.md), which
Home Assistant also renders on the add-on's **Changelog** tab.

Bug reports: use the [issue templates](.github/ISSUE_TEMPLATE/). Always include your
add-on version and a scrubbed log — this project's experience is that almost every real
defect was found by running it on someone's hardware, not by reading the code.

---

---

## License

[MIT](LICENSE), covering the contributions made in this repository.

The [upstream project](https://github.com/yuraantonov11/siseli-ha) this was forked from
carries no licence of its own, so that grant cannot extend to it. [`NOTICE`](NOTICE) sets
out the distinction, and lists the licences of the bundled dependencies.
