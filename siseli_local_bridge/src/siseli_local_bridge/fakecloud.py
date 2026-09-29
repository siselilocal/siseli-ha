"""The MQTT protocol spoken by our impersonated cloud, on top of tcpstack.py.

Reuses the existing decode pipeline unchanged: once a PUBLISH frame's topic and
payload are pulled out of the reassembled byte stream, it goes straight into
SolarParser.parse_payload -- exactly what core.py's passive relay path already does
with payloads reconstructed from a sniffed TCP stream. Only how the bytes were
obtained differs; what they mean does not.

Protocol behaviour is inferred entirely from a real capture of the vendor cloud
(docs/architecture note: dongle_reboot.pcap, 2026-09-11) rather than from any spec
reading, so it only covers what that capture showed: CONNECT/CONNACK, SUBSCRIBE
(one filter, "dtu/<id>/sub/#") /SUBACK, PUBLISH/PUBLISH-reply at QoS 0, PINGREQ/
PINGRESP, DISCONNECT. Anything else (QoS 1/2, retained flag, will messages) is
accepted-and-ignored on the way in and never produced on the way out, because the
capture never exercised it.

The reply-PUBLISH shape is one generic rule, not per-topic special cases: every
inbound "dtu/<id>/pub/<kind>/<name>" the dongle sends gets an ack published to
"dtu/<id>/sub/<kind>/<name>_reply", echoing the envelope's own "c" and "t" fields,
a fresh random "s", "i" one higher than the request's, and "e":0. This one rule
covers dtu_prop_post, dev_prop_post and dev_event_post identically, because the
capture showed all three acked exactly that way with nothing topic-specific in the
reply's shape.

Deliberately not implemented: the periodic dev_rpc poll the real cloud sends every
~26s. The capture shows dev_prop_post already carries the same data on its own
schedule, so the dongle does not structurally need to be asked -- but whether it
tolerates never being asked at all across hours/days of runtime is untested. If
dongles are ever observed reconnecting in a loop or degrading after a long local-
cloud session, start here.
"""

import base64
import binascii
import json
import random
import re
import string
import time
from typing import Optional

from . import tcpstack
from .config import (
    LOG_MQTT_PAYLOAD_PREVIEW,
    LOG_MQTT_TOPICS,
    LOG_PACKETS,
    LOG_UNPARSED_PUBLISH,
    TELEMETRY_POLL_INTERVAL_SEC,
)
from .loggers import log, log_kv, log_payload_preview
from .parsers import SolarParser

MQTT_CONNECT = 1
MQTT_PUBLISH = 3
MQTT_SUBSCRIBE = 8
MQTT_PINGREQ = 12
MQTT_DISCONNECT = 14

_CONNECT_FLAG_USERNAME = 0x80
_CONNECT_FLAG_PASSWORD = 0x40
_CONNECT_FLAG_WILL = 0x04

_TOPIC_RE = re.compile(r"^dtu/([^/]+)/pub/([^/]+)/([^/]+)$")
_TOKEN_ALPHABET = string.ascii_letters + string.digits


def _random_token(length: int = 9) -> str:
    return "".join(random.choice(_TOKEN_ALPHABET) for _ in range(length))


def _decode_remaining_length(buf: bytes, offset: int):
    """Standard MQTT variable-byte integer. None if buf does not yet hold it all."""
    multiplier = 1
    value = 0
    i = offset
    while True:
        if i >= len(buf):
            return None
        b = buf[i]
        value += (b & 0x7F) * multiplier
        i += 1
        if not (b & 0x80):
            return value, i
        multiplier *= 128
        if multiplier > 128 ** 3:
            raise ValueError("MQTT remaining length field exceeds 4 bytes")


def _encode_remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            return bytes(out)


def _build_frame(control_byte: int, body: bytes) -> bytes:
    return bytes([control_byte]) + _encode_remaining_length(len(body)) + body


def extract_mqtt_frames(buffer: bytes):
    """(control_byte, body) for every complete frame at the front of buffer, plus
    whatever incomplete trailing bytes remain."""
    frames = []
    offset = 0
    while offset < len(buffer):
        if offset + 1 >= len(buffer):
            break
        control = buffer[offset]
        decoded = _decode_remaining_length(buffer, offset + 1)
        if decoded is None:
            break
        remaining_len, body_start = decoded
        frame_end = body_start + remaining_len
        if frame_end > len(buffer):
            break
        frames.append((control, buffer[body_start:frame_end]))
        offset = frame_end
    return frames, buffer[offset:]


def _log_connect(body: bytes) -> Optional[str]:
    """Parses and logs the CONNECT variable header. Returns the username -- which
    every capture so far shows is exactly the dtu_id -- so the caller can remember
    which device this connection belongs to (needed to address a dev_rpc poll at
    it later); None if parsing failed or no username was present."""
    try:
        proto_len = int.from_bytes(body[0:2], "big")
        offset = 2 + proto_len
        offset += 1  # protocol level
        connect_flags = body[offset]
        offset += 1
        offset += 2  # keep-alive

        client_id_len = int.from_bytes(body[offset:offset + 2], "big")
        offset += 2
        client_id = body[offset:offset + client_id_len].decode("utf-8", errors="replace")
        offset += client_id_len

        if connect_flags & _CONNECT_FLAG_WILL:
            will_topic_len = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2 + will_topic_len
            will_payload_len = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2 + will_payload_len

        username = None
        if connect_flags & _CONNECT_FLAG_USERNAME:
            user_len = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2
            username = body[offset:offset + user_len].decode("utf-8", errors="replace")
            offset += user_len

        has_password = bool(connect_flags & _CONNECT_FLAG_PASSWORD)
        log(f"[LOCAL CLOUD] CONNECT client_id={client_id!r} username={username!r} password_present={has_password}")
        return username
    except Exception:
        log("[LOCAL CLOUD] CONNECT (could not parse variable header)", level="warning")
        return None


def _handle_subscribe(body: bytes) -> bytes:
    if len(body) < 2:
        return b""
    packet_id = body[0:2]
    offset = 2
    granted = bytearray()
    while offset + 2 <= len(body):
        topic_len = int.from_bytes(body[offset:offset + 2], "big")
        offset += 2 + topic_len
        if offset >= len(body):
            break
        offset += 1  # requested QoS byte, ignored -- everything here is QoS 0
        granted.append(0x00)
    if not granted:
        granted.append(0x00)
    return _build_frame(0x90, bytes(packet_id) + bytes(granted))


def _extract_envelope(payload: bytes) -> Optional[dict]:
    """The vendor's own PUBLISH payloads carry a stray leading byte before the JSON
    on every message observed so far (e.g. b'\\x00{"c":1,...' -- confirmed in a real
    capture's payload_hex, 2026-09-11), regardless of topic or QoS. parsers.py's
    SolarParser.parse_payload already tolerates this by searching for the first '{'
    rather than assuming payload[0] is one; mirrored here rather than assuming a
    clean payload, which is what silently produced an empty reply -- no ack, no
    error -- for every PUBLISH before this was found.
    """
    idx = payload.find(b"{")
    if idx == -1:
        return None
    raw = payload[idx:].decode("utf-8", errors="ignore")
    end = raw.rfind("}")
    if end != -1:
        raw = raw[: end + 1]
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _build_ack_publish(topic: str, payload: bytes) -> bytes:
    match = _TOPIC_RE.match(topic)
    if not match or not payload:
        return b""
    dtu_id, kind, name = match.groups()
    if name.endswith("_reply"):
        return b""

    envelope = _extract_envelope(payload)
    if envelope is None:
        return b""

    c = envelope.get("c")
    t = envelope.get("t")
    i = envelope.get("i")
    if c is None or t is None or not isinstance(i, int):
        return b""

    reply_topic = f"dtu/{dtu_id}/sub/{kind}/{name}_reply"
    reply_json = json.dumps(
        {"c": c, "t": t, "s": _random_token(), "i": i + 1, "e": 0},
        separators=(",", ":"),
    ).encode("utf-8")
    # The same leading \x00 every PUBLISH from this firmware carries before its own
    # JSON (see _extract_envelope) -- a real capture of the vendor cloud's own reply
    # to this exact message (2026-09-12) showed it one byte longer than ours and
    # carrying that same byte, confirming the dongle expects it symmetrically on
    # replies too. Without it here, the dongle retried dtu_prop_post three times
    # with backoff and never progressed to dev_prop_post.
    reply_body = b"\x00" + reply_json
    topic_bytes = reply_topic.encode("utf-8")
    publish_body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + reply_body
    return _build_frame(0x30, publish_body)


_FORCE_RECONNECT_TEST = False  # Tested 2026-09-12: forcing a fresh MQTT session right
# after a dev_prop_post does NOT make the next one arrive sooner. First forced cycle
# came back in 51s, but that was residual from the dongle's own power-on boot
# sequence (a real physical power-cycle preceded it) -- the SECOND forced cycle, on an
# already-running dongle, took 355s, slower than the unforced steady-state cadence
# (exactly 300s, confirmed separately). The dongle's ~5 min report timer runs
# independently of the MQTT session and is not reset by reconnecting it. Left in place
# (default off) rather than deleted, in case a future firmware version changes this.


def _handle_publish(control: int, body: bytes, conn: tcpstack.Connection) -> bytes:
    qos = (control >> 1) & 0x03
    if len(body) < 2:
        return b""
    topic_len = int.from_bytes(body[0:2], "big")
    topic = body[2:2 + topic_len].decode("utf-8", errors="replace")
    offset = 2 + topic_len
    if qos > 0:
        offset += 2  # packet id -- never seen in the capture this is modelled on
    payload = body[offset:]

    if LOG_MQTT_TOPICS:
        log_kv("[LOCAL CLOUD PUBLISH]", topic=topic, payload_len=len(payload))
    if payload and LOG_MQTT_PAYLOAD_PREVIEW:
        log_payload_preview("[LOCAL CLOUD PAYLOAD]", payload, topic=topic)

    if payload:
        parsed_ok = SolarParser.parse_payload(payload, source_topic=topic)
        if not parsed_ok and LOG_UNPARSED_PUBLISH:
            log_payload_preview("[LOCAL CLOUD PAYLOAD NOT PARSED]", payload, topic=topic)
        if (
            _FORCE_RECONNECT_TEST
            and parsed_ok
            and topic.endswith("/dev_prop_post")
            and not topic.endswith("_reply")
        ):
            conn.force_close_after_reply = True
        if topic.endswith("/dev_rpc_reply"):
            # DIAGNOSTIC (2026-09-13): pairs with poll_due_connections' stall
            # detection -- marks this poll as answered so the next stall (if any)
            # gets logged fresh instead of being suppressed by an old flag.
            conn.poll_reply_count = getattr(conn, "poll_reply_count", 0) + 1
            conn.last_reply_ts = time.monotonic()
            conn.poll_stall_logged = False
            _log_command_reply(payload, conn)

    return _build_ack_publish(topic, payload)


def _apply_qflag(text: str, conn: tcpstack.Connection) -> None:
    """Merge a QFLAG answer into the state and let the publish tick flush it.
    Logged only when it changes, since it is asked every minute."""
    from . import parsers as _parsers
    from . import state as _shared_state

    values = parse_qflag(text)
    if values != getattr(conn, "last_qflag", None):
        log(f"[CONTROL] inverter flags (QFLAG): {text} -> {values}", level="warning")
        conn.last_qflag = values
    if any(_shared_state.LAST_STATE.get(k) != v for k, v in values.items()):
        _shared_state.update_state(values)
        _parsers.PENDING_PUBLISH = True


def _log_command_reply(payload: bytes, conn: tcpstack.Connection) -> None:
    """Log the inverter's answer to a dev_rpc command ("(ACK9", "(NAKss", "^1",
    "^0", or a query's data). Telemetry replies carry a "ct" block list and are
    skipped; a command reply carries a single "co" field. The periodic QFLAG
    query's answer goes to _apply_qflag instead."""
    envelope = _extract_envelope(payload)
    body = envelope.get("b") if isinstance(envelope, dict) else None
    if not isinstance(body, dict) or "ct" in body or not body.get("co"):
        return
    try:
        raw = base64.b64decode(body["co"] + "==")
    except (ValueError, binascii.Error):
        return
    text = raw.decode("latin1").strip("\r\n")
    # Drop the trailing CRC bytes of PI30 answers when they are not printable.
    text = "".join(ch if 32 <= ord(ch) < 127 else "." for ch in text)
    command = getattr(conn, "last_command", "?")
    if _QFLAG_RE.match(text):
        # Recognised by its shape, not by last_command: a switch pressed in the
        # same second as the minute's QFLAG would otherwise swap the labels.
        _apply_qflag(text, conn)
        return
    log(f"[CONTROL] inverter answered {command}: {text}", level="warning")


def _handle_frame(control: int, body: bytes, conn: tcpstack.Connection) -> bytes:
    ptype = (control >> 4) & 0x0F

    if ptype == MQTT_CONNECT:
        dtu_id = _log_connect(body)
        if dtu_id and TELEMETRY_POLL_INTERVAL_SEC > 0:
            # Attributes on tcpstack.Connection itself rather than a side dict --
            # simplest way to remember which device a poll needs to be addressed
            # to without a second lookup structure to keep in sync with
            # tcpstack.CONNECTIONS' own lifecycle (creation, FIN/RST, sweep_stale).
            conn.dtu_id = dtu_id
            conn.next_poll_ts = time.monotonic() + TELEMETRY_POLL_INTERVAL_SEC
            # DIAGNOSTIC (2026-09-13): counters for the "poll stall" seen in
            # production on 2026-09-13 -- dev_rpc kept going out every 15s, cleanly
            # TCP-ACKed (no loss, no desync -- ruled out the 2026-09-12 TCP fix), but
            # dev_rpc_reply stopped after the very first success of the session. Not
            # yet known whether this is poll-count-triggered or time-triggered; these
            # let poll_due_connections log whichever it turns out to be, once, the
            # next time it happens, instead of re-diagnosing from raw captures again.
            conn.poll_sent_count = 0
            conn.poll_reply_count = 0
            conn.connect_ts = time.monotonic()
            conn.last_reply_ts = conn.connect_ts
            conn.poll_stall_logged = False
        return _build_frame(0x20, b"\x00\x00")  # CONNACK: no session present, accepted

    if ptype == MQTT_SUBSCRIBE:
        return _handle_subscribe(body)

    if ptype == MQTT_PUBLISH:
        return _handle_publish(control, body, conn)

    if ptype == MQTT_PINGREQ:
        return _build_frame(0xD0, b"")

    if ptype == MQTT_DISCONNECT:
        tcpstack.CONNECTIONS.pop((conn.local_ip, conn.local_port, conn.peer_ip, conn.peer_port), None)
        conn.closed = True
        return b""

    if LOG_PACKETS:
        log(f"[LOCAL CLOUD] unhandled MQTT type={ptype} body_len={len(body)}")
    return b""


def _build_dev_rpc_request(dtu_id: str, rpc_id: int) -> bytes:
    """The same request the real cloud sends every ~26s in a real capture
    (dtu/<id>/sub/service/dev_rpc, {"c":5,...,"i":501,"b":{}}) to force a full
    reading. The dongle's own dev_rpc_reply is a normal PUBLISH handled by
    _handle_publish like any other -- it is not acked further (name.endswith
    "_reply" in _build_ack_publish already excludes replying to a reply), so no
    other wiring is needed once this is sent.

    rpc_id: a live test sending every request with "i":501 got a bare TCP ACK
    and nothing else past the first 1-2 polls, which was first blamed on the
    dongle deduplicating by "i". The real capture refutes that: the vendor cloud
    sends "i":501 on EVERY poll (dongle_reboot.pcap, t=0s and t=25.4s, same id,
    answered both times). The silent-ACK symptom was our own missing TCP
    retransmission (tcpstack.py) -- one lost poll desynchronised our send stream
    and every later poll sat beyond the hole, never delivered to the dongle's
    application. Kept constant at 501 to match the real cloud byte-for-byte.
    What WAS real: the leading \x00 byte _build_ack_publish already prefixes onto
    every reply (see _extract_envelope's docstring -- confirmed on every
    PUBLISH this firmware sends or accepts, regardless of topic or QoS). This
    request path was the one place in the file still building a payload
    without it.
    """
    topic = f"dtu/{dtu_id}/sub/service/dev_rpc"
    payload = json.dumps(
        {"c": 5, "t": _random_token(8), "s": _random_token(), "i": rpc_id, "b": {}},
        separators=(",", ":"),
    ).encode("utf-8")
    body_with_marker = b"\x00" + payload
    topic_bytes = topic.encode("utf-8")
    body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + body_with_marker
    return _build_frame(0x30, body)


# Confirmed working control commands (2026-09-13/14 investigation, see
# protocole-cloud-dongle/README.md section 6 for how each was captured from a real
# vendor-cloud session and validated by replaying it from this bridge with a
# physically-observed effect on the inverter). Each value is the raw serial command
# base64-encoded exactly as the real cloud sends it: `<mnemonic><state>` for the
# short (6-byte) family, or `PDAULC0<n>` for dual output, each with its own
# checksum baked in -- there is no need to compute one here.
CONTROL_COMMANDS = {
    "backlight": {"on": "UEV4U2gN", "off": "UER4YFkN"},
    "buzzer": {"on": "UEVh0HAN", "off": "UERh40EN"},
    # "Sortie double" (dual output): the real cloud has sent a DIFFERENT, broken
    # format (`DAULREMOTESW<n>`) since 2026-09-13, which fails even for the
    # official app -- always retried, never a real effect. This is the OLDER
    # format that worked once (2026-09-12) and still does when sent from here.
    "dual_output": {"on": "UERBVUxDMDGILw0=", "off": "UERBVUxDMDCYDg0="},
    # ECO / power saving, manual Programme 08 -- NOT captured: the vendor app
    # does not offer it. backlight and buzzer above decode to Voltronic PI30's
    # own flag commands (PEx/PDx, PEa/PDa, CRC16/XMODEM), and PI30 assigns flag
    # "j" to power saving, hence PEja\x1b\r / PDjR*\r. To be confirmed on the
    # front panel (SdS = disabled, SEn = enabled). With it enabled the inverter
    # may cut its output at low load on battery -- and it powers Home Assistant.
    "eco": {"on": "UEVqYRsN", "off": "UERqUioN"},
    # PI30 flags for Programmes 06 (u, overload restart), 07 (v, over-temperature
    # restart), 19 (k, LCD returns to the home page) and 23 (b, overload bypass).
    # v is captured from the vendor app (PEv/PDv, 2026-09-27, CRC 0xB2A6/0x8197);
    # u, k and b follow the same documented flag table. Each has a 93VQ read-back
    # (parsers.py); all four were toggled from HA and read back on 2026-09-27.
    "overload_restart": {"on": "UEV1gsUN", "off": "UER1sfQN"},
    "over_temperature_restart": {"on": "UEV2sqYN", "off": "UER2gZcN"},
    "display_return_to_homepage": {"on": "UEVrcToN", "off": "UERrQgsN"},
    "overload_bypass": {"on": "UEVi4BMN", "off": "UERi0yIN"},
    # PI30 flags y (Programme 22, beeps while the primary source is
    # interrupted) and z (Programme 25, fault code record). Same flag table;
    # read back through QFLAG, which answered "(EbuvxyDajkz" on 2026-09-27.
    "primary_source_interrupt_alarm": {"on": "UEV5Q0kN", "off": "UER5cHgN"},
    "fault_code_record": {"on": "UEV6cyoN", "off": "UER6QBsN"},
    # Not here: PI30MAX flag d, "solar feed to grid (reserved feature)". This
    # inverter answered PDd with "(NAKss" on 2026-09-27 and QFLAG never lists
    # d, so Programme 44 stays read-only (93VQ token 18).
}

#: PI30 QFLAG ("(E<enabled letters>D<disabled letters>") -> state keys, for the
#: flags no telemetry block carries. ECO (j) moves no field of any of the 15
#: blocks (every field compared across ECO on/off, 2026-09-26 capture), so this
#: query is its only read-back. The other letters (a, b, k, u, v, x) are already
#: read from 93VQ and are left to it.
_QFLAG_KEYS = {
    "j": "power_saving_function",
    "y": "primary_source_interrupt_alarm",
    "z": "fault_code_record",
}
_QFLAG_INTERVAL_SEC = 60
_QFLAG_RE = re.compile(r"^\(E([a-z]*)D([a-z]*)")


def parse_qflag(text: str) -> dict:
    """"(EbuvxyDajkz.." -> {"power_saving_function": "Off", ...}; {} if the
    answer is not a QFLAG reply."""
    match = _QFLAG_RE.match(text)
    if not match:
        return {}
    enabled, disabled = match.groups()
    values = {}
    for letter, key in _QFLAG_KEYS.items():
        if letter in enabled:
            values[key] = "On"
        elif letter in disabled:
            values[key] = "Off"
    return values
_CLEAR_FAULT_CODE_CI = "RkFVTFRDR6YN"

# The one thing that changed between a failed replay (2026-09-13, "i" picked
# arbitrarily) and a working one (2026-09-14, "i":503): every real capture of a
# command dev_rpc uses exactly this "i", never anything else. An arbitrary "i" got
# a bare TCP ACK and no further effect, identical in shape to the telemetry-poll
# dedup theory that _build_dev_rpc_request's docstring already found was false for
# polls -- but for commands specifically, "i" does appear to matter.
_CONTROL_RPC_ID = 503


def _crc16_xmodem(data: bytes) -> bytes:
    """CRC-16/XMODEM, big-endian, over `data` -- poly 0x1021, init 0.

    Already used by pi30.py's `verify_frame` (there via binascii.crc_hqx directly);
    reused here as the same algorithm, confirmed byte-for-byte against every raw
    `ci` this bridge has actually captured from the vendor cloud: `PDAULC01` ->
    `\\x88/`, `PDAULC00` -> `\\x98\\x0e`, and `FAULTC` alone -> `G\\xa6` (which is
    exactly the "FAULTCG\\xa6" this bridge already sends as _CLEAR_FAULT_CODE_CI --
    the trailing `G` in that capture was never part of the mnemonic, it is this
    CRC's own high byte happening to be a printable ASCII letter). Cross-checked
    against github.com/filipsworks/SoT-RWB1-Server-Emulator, an independent
    reverse-engineering of the same RWB1 dongle protocol, which documents the same
    polynomial for its own `build_write_ci`.
    """
    return binascii.crc_hqx(data, 0).to_bytes(2, "big")


def build_write_ci(channel: str, value: str) -> str:
    """base64("<CHANNEL><VALUE><CRC16><\\r>") -- a generic dev_rpc write command.

    Every confirmed command so far (backlight/buzzer's `P<state><id>`, dual
    output's `PDAULC0<n>`, clear fault's `FAULTC`) fits this one shape: an ASCII
    channel/value string, this frame's CRC16, then a trailing CR. Those four stay
    hardcoded in CONTROL_COMMANDS/_CLEAR_FAULT_CODE_CI as captured bytes, since
    they already work; this builder is for NEW channels this bridge has not
    captured from a real cloud session, only ported from SoT-RWB1-Server-
    Emulator's independent reverse engineering (see
    protocole-cloud-dongle/README.md and the write-commands project memory) --
    each one wired through here should stay unverified-on-our-hardware until a
    physical effect (or, where available, a matching telemetry change) confirms
    it.
    """
    frame = f"{channel}{value}".encode("ascii")
    frame += _crc16_xmodem(frame)
    frame += b"\r"
    return base64.b64encode(frame).decode("ascii")


#: Select-style settings ported from SoT-RWB1-Server-Emulator's
#: SELECT_SETTING_DEFINITIONS (github.com/filipsworks/SoT-RWB1-Server-Emulator,
#: custom_components/sot_rwb1/const.py). Deliberately starting with exactly one,
#: the lowest-risk of the three that source lists: an ordinary operating-mode
#: toggle already exposed in the vendor app (not a voltage/current/SOC setpoint
#: that could misconfigure the battery), instantly reversible, and independently
#: readable back afterwards from this bridge's own telemetry -- planned to be
#: PI30's pi30_output_source_priority, but this inverter does not speak PI30
#: (see the write-commands project memory), so that check never applied here;
#: confirmed instead by the user directly reading the inverter's own front
#: panel after a write (2026-09-23) -- cheap to notice and cheap to undo either
#: way.
#:
#: The source's own warning said the dongle-side encoding for POP0 (SBU=0,
#: SUB=1) is NOT the same ordering as the Siseli cloud API's enum for the same
#: setting. Physical test on our inverter (2026-09-23) found the dongle-side
#: encoding itself inverted from what that source documented for POP0: sending
#: "1" (its "SUB") made the front panel show SBU, and "0" (its "SBU") showed
#: SUB -- corrected below to match what our inverter actually does.
#:
#: charger_priority (PCP0) and grid_working_range (PGR0) carry the source's
#: documented values UNCHANGED -- given POP0 was wrong, these should be
#: treated as equally unverified until confirmed the same way (front panel
#: after each write), not assumed correct because the channel/CRC mechanism
#: itself is now proven.
SELECT_SETTINGS = {
    "output_source_priority": {
        "channel": "POP0",
        "options": {
            "solar_battery_first": "1",  # SBU (confirmed on our inverter's front panel)
            "solar_first": "0",  # SUB (confirmed on our inverter's front panel)
        },
    },
    # Not here: PI30MAX PBATCD<abc> (battery charge/discharge enable). Sent
    # twice as "PBATCD000" on 2026-09-27 and never answered -- not even
    # "(NAK" -- while telemetry kept flowing: this firmware does not take it.
    "charger_priority": {
        "channel": "PCP0",
        # Corrected 2026-09-23: the user read the inverter's own front panel
        # after each option and found the source's documented values (OSO=0,
        # CSO=1, SNU=2, SOR=3) rotated by one on this hardware -- sending "0"
        # showed CSO, "1" showed SNU, "2" showed OSO. Values below are what
        # actually produces each label on THIS inverter. "Solar Residual"
        # (SOR) has no entry at all: the user confirmed this priority mode
        # does not exist on their unit ("absent dans mon onduleur"), so it is
        # dropped rather than left in as a non-functional choice -- same
        # reasoning as output_source_priority only exposing 2 of the source's
        # documented options.
        "options": {
            "solar_only": "2",  # OSO (confirmed on our inverter's front panel)
            "solar_and_utility": "0",  # CSO (confirmed on our inverter's front panel)
            "solar_first": "1",  # SNU (confirmed on our inverter's front panel)
        },
    },
    "grid_working_range": {
        "channel": "PGR0",
        # Corrected 2026-09-24: a real capture (tcpdump on end0, LOCAL_CLOUD_IP
        # disabled + FORWARD_ALL_INVERTER_TRAFFIC=true so the dongle talked to
        # the real vendor cloud) caught the official app itself sending both
        # values -- user activated APL then UPS 2s later, and the wire showed
        # "PGR00" (APL) then "PGR01" (UPS): the source's documented SBU-style
        # mapping (UPS=0, APL=1) was inverted for this channel too, same class
        # of bug as output_source_priority. CRC16/XMODEM("PGR00")=0x29EB and
        # ("PGR01")=0x39CA both match the captured bytes exactly, confirming
        # the channel name and framing. The sequence ended on UPS and
        # telemetry (mains_input_range_code) read "11" (UPS) afterwards,
        # consistent with success; no telemetry snapshot exists for the brief
        # 9s spent on APL (well under the ~5min spontaneous push cycle), so
        # that leg rests on the app's own wire evidence rather than a
        # confirmed state change. Values below match the captured app
        # behavior, not the source's documented mapping.
        "options": {
            "ups": "1",  # confirmed by capture of the vendor app's own traffic, 2026-09-24
            "appliance": "0",  # APL -- confirmed by capture of the vendor app's own traffic, 2026-09-24
        },
    },
    "solar_supply_priority": {
        # New channel, not in SoT-RWB1-Server-Emulator's spec at all -- found
        # entirely from a capture of the vendor app's own traffic (2026-09-24,
        # same method as grid_working_range's fix), not ported from anywhere.
        # "PVENGUSE" + a 2-digit code, CRC16/XMODEM-verified byte-for-byte on
        # both captured frames (PVENGUSE01 -> 0x4E40 "N@", PVENGUSE00 ->
        # 0x5E61 "^a"). The app's own labels are "BLU" and "LBU" -- how PV
        # energy is allocated (Battery/Load/Utility ordering), distinct from
        # output_source_priority (which source powers the load) and
        # charger_priority (which source charges the battery). User activated
        # BLU then LBU 2s later; the capture showed "00" then "01" in that
        # order.
        "channel": "PVENGUSE",
        "options": {
            "blu": "00",  # confirmed by capture of the vendor app's own traffic, 2026-09-24
            "lbu": "01",  # confirmed by capture of the vendor app's own traffic, 2026-09-24
        },
    },
    "output_voltage": {
        # Manual Programme 10. Not offered by the vendor app, so not captured:
        # Voltronic's PI30 protocol documents "V<nnn>" (220/230/240 on HV
        # models), the same family as POP/PCP/PGR/PBT above, all confirmed on
        # this inverter. Read back from output_set_voltage (93VQ config pack
        # tail), which the 2026-09-26 factory reset moved 240 -> 230.
        "channel": "V",
        "options": {
            "220": "220",
            "230": "230",
            "240": "240",
        },
    },
    "max_utility_charge_current": {
        # Manual Programme 11 (2 A, then 10 A up in steps of 10). Not offered by
        # the vendor app: PI30 documents "MUCHGC<m><nn>", m = parallel machine
        # number (0 on a single unit), nn = amps. Stops at 90 A because above
        # 99 A the documented format changes to nnn, which cannot be checked
        # here. Read back from max_utility_charge_current (93VQ token 2), which
        # the 2026-09-26 factory reset moved 2 -> 30.
        "channel": "MUCHGC",
        "options": {f"{a}": f"0{a:02d}" for a in (2, 10, 20, 30, 40, 50, 60, 70, 80, 90)},
    },
    "grid_regulation_mode": {
        # Manual Programme 50 (the app's "Grid Connection Protocol Type"): the
        # grid voltage/frequency window the inverter accepts. Captured from the
        # vendor app 2026-09-27 (captures/2026-09-27_real-cloud_prog50.pcap):
        # "^S???RS03" + CRC16/XMODEM 0xB0D5 for Mode 4, answered "^1", and the
        # next read-back moved as expected. Mode n = code n-1. All five modes
        # are offered (the user's choice, 2026-09-28: other countries need
        # them); the select labels show each mode's window, so Mode 3's 57-62 Hz
        # is visible before anyone picks it on a 50 Hz grid.
        "channel": "^S???RS",
        "options": {
            "mode_1": "00",
            "mode_2": "01",
            "mode_3": "02",
            "mode_4": "03",
            "mode_5": "04",
        },
    },
    "battery_type": {
        # Manual Programme 05. Captured from the vendor app 2026-09-26:
        # "PBT04" (Pylontech) then "PBT06" (Growatt), CRC16/XMODEM 0x678A /
        # 0x47C8 matching the wire byte-for-byte, each answered "(ACK9" and
        # read back by the next HEEP1 as 93VQ code 4 then 6. The other eight
        # codes are NOT captured: they follow the manual's option order, which
        # both captured codes fit. Exposed on the user's explicit request.
        #
        # Caution: on this installation a battery type change once cut the
        # inverter's AC output, which powers Home Assistant itself (2026-09-25).
        # PYL also raises warning 61 (BMS communication lost) with this BMS and
        # makes the SOC read 99-100 %.
        "channel": "PBT0",
        "options": {
            "agm": "0",
            "flooded": "1",
            "user_defined": "2",
            "lia": "3",
            "pylontech": "4",  # confirmed by capture of the vendor app's own traffic, 2026-09-26
            "techfine": "5",
            "growatt": "6",  # confirmed by capture of the vendor app's own traffic, 2026-09-26
            "felicity": "7",
            "lib": "8",
            "third_party_lithium": "9",
        },
    },
}


#: Number-style settings: BMS SOC thresholds, manual Programmes 38-41.
#: Captured from the vendor app 2026-09-26
#: (captures/2026-09-26_real-cloud_prog38-41-soc.pcap): channel + 3-digit
#: percent ("BMSSDC015"), each accepted value answered "(ACK9" and read back by
#: the next HEEP1 at the listed 93VQ position; the inverter's own front panel
#: showed the same values. The inverter only accepts multiples of 5 (every
#: other value got "(NAKss"), and raising Programme 38 also raised 39 (and
#: Programme 62, dual output SOC) on its own to keep them 5 points above it.
#: Ranges are SoT-RWB1-Server-Emulator's, narrowed to the multiples of 5.
NUMBER_SETTINGS = {
    "bms_lock_machine_soc": {  # Programme 38 -- the inverter SHUTS DOWN below it
        "channel": "BMSSDC", "min": 5, "max": 95, "step": 5,
    },
    "bms_restore_mains_charging_soc": {  # Programme 39
        "channel": "BMSB2UC", "min": 5, "max": 95, "step": 5,
    },
    "bms_restore_battery_discharging_soc": {  # Programme 40
        "channel": "BMSU2BC", "min": 5, "max": 95, "step": 5,
    },
    "bms_inverter_startup_soc": {  # Programme 41
        "channel": "BMSSRC", "min": 5, "max": 100, "step": 5,
    },
    # Programmes 12 and 13 (48 V ranges from the manual, 1 V steps): battery
    # voltage at which SBU priority goes back to the grid, and back to the
    # battery. Not offered by the vendor app: PI30 documents PBCV<nn.n> and
    # PBDV<nn.n> (SoT-RWB1-Server-Emulator uses the same channel names). Read
    # back from dHrK tokens 4 and 5, which the 2026-09-26 factory reset moved
    # 44 -> 46 and 48 -> 54. Programme 13's "battery full" option (00.0) is
    # left out.
    "back_to_grid_voltage": {
        "channel": "PBCV", "min": 44, "max": 51, "step": 1, "format": "{:04.1f}", "unit": "V",
    },
    "back_to_battery_voltage": {
        "channel": "PBDV", "min": 48, "max": 58, "step": 1, "format": "{:04.1f}", "unit": "V",
    },
    # Programme 31, battery equalization voltage: 48.0-60.0 V in 0.1 V steps on
    # 48 V units (manual). PI30 documents PBEQV<nn.nn>. Only acts with a
    # Flooded / User-defined battery type and equalization enabled (Programme
    # 30). Read back from dHrK token 7, moved 56.0 -> 58.4 by the factory reset.
    "equalization_voltage": {
        "channel": "PBEQV", "min": 48.0, "max": 60.0, "step": 0.1, "format": "{:05.2f}", "unit": "V",
    },
    # Programme 56, grid-tie (feed-in) current limit. Captured from the vendor
    # app 2026-09-27 (captures/2026-09-27_real-cloud_prog56.pcap): "PGFC" + 3
    # digits in amps. PGFC002 got "(NAKss"; PGFC006, PGFC005 and PGFC004 got
    # "(ACK9" and 93VQ token 17 read back 006 then 005. The front panel steps by
    # 2 A but the command takes 1 A steps. 4 A is the lowest the inverter takes
    # (panel minimum too), 40 A the app's maximum. Only acts while Programme 44
    # allows feeding the grid.
    "grid_tie_current": {
        "channel": "PGFC", "min": 4, "max": 40, "step": 1, "unit": "A",
    },
    # Programme 02, maximum total (solar + utility) charging current. Captured
    # from the vendor app 2026-09-27 (captures/2026-09-27_real-cloud_prog02.pcap):
    # "MNCHGC" + 3 digits in amps. MNCHGC060 and MNCHGC050 got "(ACK9"; 058,
    # 059, 061 and 062 got "(NAKss" -- 10 A steps only, as the manual says. The
    # app shows no range; 10 A to 150 A is the manual's (11 kW model). The
    # battery's BMS still caps the charge at its own limit.
    "max_charging_current": {
        "channel": "MNCHGC", "min": 10, "max": 150, "step": 10, "unit": "A",
    },
}


def _lock_machine_refusal(value: int) -> Optional[str]:
    """Programme 38 turns the inverter off when the SOC is below it, and on this
    installation the inverter powers Home Assistant itself. Refuse any value at
    or above the current SOC, and refuse outright while the SOC cannot be
    trusted (BMS communication lost or never decoded: the inverter's own
    estimate then reads 99-100 % whatever the real charge)."""
    from . import state as _shared_state

    snapshot = _shared_state.snapshot_state()
    if snapshot.get("bms_communication_normal") != "Yes":
        return "BMS communication not confirmed, SOC cannot be trusted"
    soc = snapshot.get("bat_cap")
    if not isinstance(soc, (int, float)):
        return "current SOC unknown"
    if value >= soc:
        return f"{value} % is not below the current SOC ({soc} %): the inverter would shut down"
    return None


def send_control_number(setting: str, value: float) -> bool:
    """Write one NUMBER_SETTINGS value. Returns False (logged) when the value is
    out of range, not a multiple of the step, refused by the programme 38 guard,
    or when there is no local-cloud connection."""
    definition = NUMBER_SETTINGS.get(setting)
    if definition is None:
        log(f"[CONTROL] unknown number setting {setting!r}", level="error")
        return False
    step = definition["step"]
    if isinstance(step, float):
        # Decimal settings (e.g. 0.1 V): snap to the grid, refuse anything off it.
        n = round((value - definition["min"]) / step)
        on_grid = abs(definition["min"] + n * step - value) < 1e-6
        value = round(definition["min"] + n * step, 2)
    else:
        on_grid = value == int(value) and int(value) % step == 0
        if value == int(value):
            value = int(value)
    if not on_grid or not definition["min"] <= value <= definition["max"]:
        log(
            f"[CONTROL] {setting}: {value} refused, must be {definition['min']}-{definition['max']} "
            f"in steps of {definition['step']} (the inverter NAKs anything else)",
            level="warning",
        )
        return False
    if setting == "bms_lock_machine_soc":
        refusal = _lock_machine_refusal(value)
        if refusal:
            log(f"[CONTROL] {setting}: {value} refused -- {refusal}", level="warning")
            return False
    ok = _send_control_ci(build_write_ci(definition["channel"], definition.get("format", "{:03d}").format(value)))
    if ok:
        log(f"[CONTROL] {setting} -> {value}")
    return ok


def send_control_select(setting: str, option: str) -> bool:
    """Write one option of a SELECT_SETTINGS entry. Same success signal caveat
    as send_control_switch: the dongle's dev_rpc_reply carries no field this
    bridge has confirmed means success/failure, so "the send happened" is all
    that is reported."""
    definition = SELECT_SETTINGS.get(setting)
    if definition is None:
        log(f"[CONTROL] unknown select setting {setting!r}", level="error")
        return False
    value = definition["options"].get(option)
    if value is None:
        log(f"[CONTROL] unknown option {option!r} for select setting {setting!r}", level="error")
        return False
    ci = build_write_ci(definition["channel"], value)
    ok = _send_control_ci(ci)
    if ok:
        log(f"[CONTROL] {setting} -> {option}")
    return ok


def _first_established_connection() -> Optional[tcpstack.Connection]:
    for conn in list(tcpstack.CONNECTIONS.values()):
        if getattr(conn, "dtu_id", None) and not conn.closed:
            return conn
    return None


def _send_control_ci(ci_b64: str, conn: Optional[tcpstack.Connection] = None) -> bool:
    if conn is None:
        conn = _first_established_connection()
    if conn is None:
        log("[CONTROL] no established local-cloud connection to send on", level="warning")
        return False
    # Remembered so _log_command_reply can say which command an answer is for.
    frame = base64.b64decode(ci_b64)
    conn.last_command = frame[:-3].decode("ascii", errors="replace")
    topic = f"dtu/{conn.dtu_id}/sub/service/dev_rpc"
    payload = json.dumps(
        {
            "c": 5,
            "t": _random_token(8),
            "s": _random_token(),
            "i": _CONTROL_RPC_ID,
            "b": {"ci": ci_b64, "no": 0, "rs": 0},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    topic_bytes = topic.encode("utf-8")
    body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + b"\x00" + payload
    conn.reply(_build_frame(0x30, body))
    return True


def send_control_switch(setting: str, turn_on: bool) -> bool:
    """Send one of CONTROL_COMMANDS. Returns False (logged) if there is no
    established local-cloud connection right now -- the caller (mqtt.py's command
    handler) is expected to leave the HA switch's state untouched in that case
    rather than report a success that did not happen."""
    commands = CONTROL_COMMANDS.get(setting)
    if commands is None:
        log(f"[CONTROL] unknown setting {setting!r}", level="error")
        return False
    ci = commands["on" if turn_on else "off"]
    ok = _send_control_ci(ci)
    if ok:
        log(f"[CONTROL] {setting} -> {'on' if turn_on else 'off'}")
    return ok


def send_clear_fault_code() -> bool:
    ok = _send_control_ci(_CLEAR_FAULT_CODE_CI)
    if ok:
        log("[CONTROL] clear fault code sent")
    return ok


def send_clock_sync() -> bool:
    """Set the inverter's clock (manual Programmes 51-55) to this host's local
    time. NOT captured -- the vendor app has no clock setting. The inverter
    already takes "^S???RS0<n>" (Programme 50, captured), a Voltronic PI18-style
    frame, and PI18 sets the date and time with DAT<YYMMDDhhmmss>. A wrong guess
    is answered "^0" and changes nothing; the COST block's system_time_ymd /
    system_time_hm read-back shows whether the clock followed."""
    stamp = time.strftime("%y%m%d%H%M%S", time.localtime())
    ok = _send_control_ci(build_write_ci("^S???DAT", stamp))
    if ok:
        log(f"[CONTROL] clock sync sent: {stamp}")
    return ok


def _current_ac_charging_hour(key: str) -> Optional[int]:
    """Last read-back of one AC charger hour ("12:00" -> 12), None when unknown."""
    from . import state as _shared_state

    value = _shared_state.snapshot_state().get(key)
    if isinstance(value, str) and re.fullmatch(r"\d{2}:00", value) and int(value[:2]) <= 23:
        return int(value[:2])
    return None


def send_ac_charging_window(start_hour: Optional[int] = None, stop_hour: Optional[int] = None) -> bool:
    """Programmes 46/47, the AC charger's start and stop hour: ^S???ACCT<HHMM>,<HHMM>.

    Not offered by the vendor app; found 2026-09-29 by probing the Voltronic PI17
    family (the inverter already takes ^S???RS and ^S???DAT): ^P???ACCT answered
    "^D0121200,1300" (the front panel's 12:00-13:00), ^S???ACCT1200,1400 answered
    "^1" and read back 1200,1400 -- HA showed 14:00 -- then 1200,1300 restored it.
    Both hours travel in one command, so the one not being changed is taken from
    the last read-back (dHrK token 11); refused while that is unknown, rather than
    guess it. Outside the window the grid does not charge the battery; 00-00 means
    no restriction. Not to confuse with ACLT (AC supply load time), never sent."""
    if start_hour is None:
        start_hour = _current_ac_charging_hour("ac_charging_start_time")
    if stop_hour is None:
        stop_hour = _current_ac_charging_hour("ac_charging_stop_time")
    if start_hour is None or stop_hour is None:
        log("[CONTROL] AC charging window refused: the other hour has not been read back yet", level="warning")
        return False
    if not (0 <= start_hour <= 23 and 0 <= stop_hour <= 23):
        log(f"[CONTROL] AC charging window refused: {start_hour}-{stop_hour} is not 0-23", level="warning")
        return False
    ok = _send_control_ci(build_write_ci("^S???ACCT", f"{start_hour:02d}00,{stop_hour:02d}00"))
    if ok:
        log(f"[CONTROL] AC charging window -> {start_hour:02d}:00-{stop_hour:02d}:00")
    return ok


def send_manual_refresh() -> bool:
    """On-demand telemetry poll -- the exact same i=501, empty-body dev_rpc
    request poll_due_connections sends on its own timer. Confirmed byte-for-byte
    identical to what the vendor app's own "refresh" pull sends (packet capture
    2026-09-14, see protocole-cloud-dongle/README.md): {"i":501,"b":{}}. Exposed
    as a button so a stalled poll cycle can be kicked without waiting for the
    next scheduled tick, and to let the state_diff/state_snapshot debug logs show
    exactly what a manual refresh actually returns."""
    conn = _first_established_connection()
    if conn is None:
        log("[CONTROL] no established local-cloud connection to send on", level="warning")
        return False
    try:
        conn.reply(_build_dev_rpc_request(conn.dtu_id, 501))
        log(f"[CONTROL] manual refresh sent to {conn.peer_ip}:{conn.peer_port} i=501")
        return True
    except Exception as exc:
        log(f"[CONTROL] manual refresh failed: {exc}", level="error")
        return False


def poll_due_connections() -> None:
    """Called periodically from core.py's health_logger tick. Sends a dev_rpc
    request to every established local-cloud connection whose interval has
    elapsed. A no-op when TELEMETRY_POLL_INTERVAL_SEC is 0 (nothing ever sets
    conn.next_poll_ts in that case, so the getattr below finds nothing to do)."""
    if TELEMETRY_POLL_INTERVAL_SEC <= 0:
        return
    now = time.monotonic()
    for conn in list(tcpstack.CONNECTIONS.values()):
        dtu_id = getattr(conn, "dtu_id", None)
        next_poll_ts = getattr(conn, "next_poll_ts", None)
        if dtu_id is None or next_poll_ts is None or conn.closed:
            continue
        if now < next_poll_ts:
            continue
        conn.next_poll_ts = now + TELEMETRY_POLL_INTERVAL_SEC

        # DIAGNOSTIC (2026-09-13): if every poll sent so far has NOT been answered
        # (sent > replied), the previous poll(s) stalled -- log it once, with the
        # numbers needed to tell a count-triggered stall from a time-triggered one,
        # rather than re-deriving this from a tcpdump capture each time it recurs.
        sent = getattr(conn, "poll_sent_count", 0)
        replied = getattr(conn, "poll_reply_count", 0)
        if sent > replied and not getattr(conn, "poll_stall_logged", False):
            since_reply = now - getattr(conn, "last_reply_ts", now)
            since_connect = now - getattr(conn, "connect_ts", now)
            log(
                f"[LOCAL CLOUD DIAG] poll stall {conn.peer_ip}:{conn.peer_port}: "
                f"{sent - replied} poll(s) unanswered since last dev_rpc_reply, "
                f"{since_reply:.0f}s since last reply, {since_connect:.0f}s since "
                f"CONNECT, {sent} sent total, {replied} replied total",
                level="warning",
            )
            conn.poll_stall_logged = True

        conn.poll_sent_count = sent + 1
        try:
            # "i":501 on every poll, exactly like the real cloud (see
            # _build_dev_rpc_request's docstring).
            conn.reply(_build_dev_rpc_request(dtu_id, 501))
            if LOG_PACKETS:
                log(f"[LOCAL CLOUD] dev_rpc poll sent to {conn.peer_ip}:{conn.peer_port} i=501")
        except Exception as exc:
            log(f"[LOCAL CLOUD ERROR] dev_rpc poll failed: {exc}", level="error")

        # PI30 flag status (read only) for ECO and Programmes 22/25, which no
        # telemetry block carries. Once a minute, on the poll tick.
        if now >= getattr(conn, "next_qflag_ts", 0.0):
            conn.next_qflag_ts = now + _QFLAG_INTERVAL_SEC
            try:
                _send_control_ci(build_write_ci("QFLAG", ""), conn=conn)
            except Exception as exc:
                log(f"[LOCAL CLOUD ERROR] QFLAG query failed: {exc}", level="error")


def _on_established(conn: tcpstack.Connection) -> None:
    log(f"[LOCAL CLOUD] TCP connected from {conn.peer_ip}:{conn.peer_port}")


def _on_data(conn: tcpstack.Connection) -> None:
    frames, conn.recv_buffer = extract_mqtt_frames(conn.recv_buffer)
    if not frames:
        return
    reply = b"".join(_handle_frame(control, body, conn) for control, body in frames)
    if not conn.closed:
        conn.reply(reply)
    if _FORCE_RECONNECT_TEST and getattr(conn, "force_close_after_reply", False) and not conn.closed:
        # TEMPORARY test (2026-09-12): does forcing a fresh MQTT session right after a
        # spontaneous dev_prop_post make the dongle's NEXT one arrive sooner than its
        # own steady 5 min cadence (confirmed exactly 300s apart, unforced, same
        # session)? One data point so far (connect-to-first-push = 53s once) is not
        # enough to tell a connect-reset timer from a lucky phase coincidence.
        key = (conn.local_ip, conn.local_port, conn.peer_ip, conn.peer_port)
        log(f"[LOCAL CLOUD TEST] forcing reconnect after dev_prop_post to measure recovery time ({conn.peer_ip}:{conn.peer_port})")
        conn.close(reason="forced test reconnect")
        tcpstack.CONNECTIONS.pop(key, None)


def handle_packet(pkt, local_ip: str, local_port: int, peer_mac: str, iface: Optional[str]) -> None:
    tcpstack.handle_tcp(
        pkt,
        local_ip=local_ip,
        local_port=local_port,
        peer_mac=peer_mac,
        iface=iface,
        on_established=_on_established,
        on_data=_on_data,
    )
