# Security

This add-on does something unusual, and you should understand it before installing.

**It ARP-spoofs a device on your LAN.** That is the mechanism by which it works, not an
optional mode. **In 100% local mode it also impersonates the vendor cloud and sends
commands to your inverter.** This document says exactly what it does in each mode, what
privileges it holds, and what it does not do.

## Reporting a vulnerability

Use GitHub's private reporting: **[Security → Report a
vulnerability](https://github.com/siselilocal/siseli-ha/security/advisories/new)**. That opens
a private thread visible only to the maintainer.

Please do not open a public issue for a vulnerability. There is no published contact
email — the private advisory form is the only channel.

This is a hobby project with one maintainer. Expect a first response within a week or
two, not within hours.

## What it does

Your inverter's WiFi dongle opens a plain (unencrypted) MQTT connection to the vendor
cloud. The add-on inserts itself into that path.

### Pass-through mode (default, `LOCAL_CLOUD_IP` blank)

It reads the telemetry that is already flowing, and nothing more.

1. **ARP interception.** It sends unsolicited ARP replies (`op=2`) to exactly two hosts:
   the inverter, telling it that the router's IP is at the Home Assistant host's MAC; and
   the router, telling it the same about the inverter's IP. Both addresses are configured
   by you, in `INVERTER_IP` and `ROUTER_IP`. **It never scans, sweeps or discovers** — if
   you put the wrong IP in, it poisons the wrong host, and nothing in the add-on will
   notice.
2. **Passive capture.** A scapy `AsyncSniffer` reads the frames that now arrive.
3. **Forwarding.** Every captured packet is re-emitted toward its real destination
   unchanged. The connection is never terminated, never proxied, never modified. If the
   add-on stops, the inverter's traffic goes back to the real gateway as its ARP cache
   ages out.
4. **Publishing.** Decoded values go to your own MQTT broker.

`AUTO_INTERCEPT: false` turns off step 1 entirely, for people who have arranged the
traffic another way (a port mirror, a router-side rule). Steps 2–4 are unaffected.

### 100% local mode (`LOCAL_CLOUD_IP` set)

The add-on **replaces** the vendor cloud for your dongle. Steps 1, 2 and 4 above still
apply, and in addition:

5. **DNS answers.** When the dongle asks for a name under `DNS_SPOOF_DOMAIN` (the vendor's
   broker and bootstrap hosts), the add-on answers with `LOCAL_CLOUD_IP`. Only A-record
   queries for those names are answered; every other query is relayed untouched.
6. **Cloud impersonation.** On `LOCAL_CLOUD_IP` it answers the dongle's HTTP bootstrap
   (port `HTTP_STUB_PORT`, 80) and its MQTT session (port `LOCAL_CLOUD_PORT`, 1883). This
   is a userspace TCP stack (`tcpstack.py`) built on raw frames, not a kernel socket, and
   it **terminates** the dongle's connection: the add-on is the endpoint.
7. **One firewall rule.** A dedicated `nftables` table (`siseli_local_cloud`) holds a
   single `raw`/`PREROUTING` DROP for traffic addressed to `LOCAL_CLOUD_IP` on those two
   ports, so no kernel socket (such as the Mosquitto add-on on `0.0.0.0:1883`) claims the
   dongle's connection first. It forwards nothing and rewrites nothing, and it is removed
   when the add-on stops.
8. **Reads it asks for.** Every `TELEMETRY_POLL_INTERVAL_SEC` it requests a full reading,
   and once a minute it asks the inverter's flag status (`QFLAG`). Both are read-only.
9. **Writes to the inverter.** When you change one of the add-on's controls in Home
   Assistant (a switch, select, number or button), it sends the matching command to the
   inverter through the dongle. See below for what is and is not possible.

The secondary address itself is added by the separate **Network IP Alias** add-on; this
add-on only uses it.

## Privileges it holds, and why

| Privilege | Why |
|---|---|
| `NET_RAW` | The `AsyncSniffer` needs a raw socket to read frames, and `sendp()` needs one to emit the ARP replies, the forwarded packets, the DNS answers and the local-cloud TCP segments. |
| `NET_ADMIN` | Putting the interface into promiscuous mode, so frames addressed to another MAC are delivered; and, in 100% local mode, creating and removing the one `nftables` table described above. |
| `host_network: true` | The spoofing, the capture and the local-cloud responder all happen on the host's LAN segment. Inside a bridged container namespace there is nothing to see and nobody to spoof. |
| `apparmor: false` | See below. This is the weakest part of the posture. |

**The historical `iptables` NAT redirection is gone.** An earlier design rewrote packet
destinations with `iptables`; nothing does that now, and the `iptables` package is not
installed. The image does ship `nftables`, used only for the single DROP rule of 100%
local mode; pass-through mode installs no rule at all.

### `apparmor: false` is a known gap

The add-on ships with AppArmor confinement disabled, which means it is constrained only
by the two capabilities above rather than by a profile that says which files and
operations it may use.

This is not defensible as a permanent state; it is a gap that has not been closed. Doing
so means writing an `apparmor.txt` that permits `network packet raw`, `network packet
packet`, running `nft`, the `/data` writes described below, and nothing else.
Contributions welcome.

## What it does not do

- **No kernel listening socket.** Nothing binds a port. In 100% local mode the
  responder answers raw frames addressed to `LOCAL_CLOUD_IP` only. The `LISTEN_PORT`
  option is a deprecated no-op retained only so existing installs keep validating.
- **No outbound connections of its own**, other than to the MQTT broker you configure.
  It never contacts the vendor cloud on its own behalf: in pass-through it only relays
  the inverter's existing packets onward, and in 100% local mode the vendor cloud is not
  contacted at all.
- **In pass-through mode, it never writes toward the inverter.** No command, no setting,
  no control frame reaches it from the add-on.
- **In 100% local mode, it writes only what you ask for, from a fixed list.** Every
  command it can send is one of the controls it exposes in Home Assistant (priorities,
  voltages, currents, SOC thresholds, on/off flags, the clock). There is no path that
  sends an arbitrary command. The two vendor-app actions that cannot be undone —
  "Control Parameters To Default Value" (factory reset) and "Reset PV Energy Storage" —
  are deliberately absent. Numbers are checked against their allowed range before
  sending, and the BMS Lock Machine SOC is refused at or above the current state of
  charge, or while BMS communication is lost.
- **In pass-through mode, it never terminates a TCP connection.** It observes a stream
  it is not an endpoint of. (In 100% local mode it is, by design, the dongle's endpoint;
  see step 6.) The dongle speaks plain MQTT in both cases, so there is no TLS to break.

## What leaves the machine, and what is stored

**Leaves:** decoded sensor values, to the MQTT broker you configure. Nothing else. There
is no telemetry, no analytics, no crash reporting, no update check.

**Stored,** in the add-on's private `/data` volume:

| File | Contents |
|---|---|
| `/data/state.json` | The last decoded value of every sensor, so entities survive a restart. |
| `/data/discovery_state.json` | Which discovery topics have been published, so stale ones can be swept. |

Neither contains credentials. Your MQTT password lives in the add-on's Supervisor
configuration, like every other add-on's.

**Your logs contain your device serial.** It appears in the MQTT `topic=` values, and in
100% local mode in the dongle's `CONNECT` line. Scrub them before pasting a log into an
issue.

## Risks stated plainly

**ARP spoofing is indistinguishable from an attack.** To a managed switch with dynamic
ARP inspection, an IDS, or a router with ARP-spoofing protection, this add-on looks
exactly like a man-in-the-middle attempt — because mechanically it is one, aimed at a
device you own with your permission. Expect alerts. On networks with DAI enabled, expect
the frames to be dropped and the add-on not to work.

**A hard power loss skips the cleanup.** `restore_arp()` sends five rounds of corrective
replies to both peers on `SIGTERM` and `SIGINT`, so a normal stop or restart puts both
ARP caches back immediately. A pulled plug or a killed container does not run it. The
caches then age out on their own — usually a minute or two — during which the inverter
cannot reach the cloud.

**In 100% local mode, your MQTT broker is the inverter's remote control.** The controls
listen on `<DEVICE_ID>/control/<setting>/set`. Anyone who can publish to your broker can
change the inverter's settings — including the battery type, which can briefly cut the
inverter's AC output. Keep the broker on your LAN, require a username and password, and
do not expose it to the internet.

**A wrong setting has physical effects.** Charge currents, voltages and SOC thresholds
act on a real battery and a real output. The add-on limits each value to the range the
inverter accepts, but it cannot know what your battery or your loads need.

## Scope

Reports about the following are in scope: anything that lets a third party influence what
the bridge publishes, anything that widens the two-host targeting, anything that lets a
command reach the inverter other than through the configured MQTT broker's control
topics, anything that sends a command outside the fixed list, credential handling, and the
contents of `/data`.

Out of scope: the fact that the add-on ARP-spoofs at all, that it requires
`NET_ADMIN`/`NET_RAW`, and that 100% local mode impersonates the vendor cloud and writes
settings on request. Those are the design, documented above.
