"""Save the inverter's settings to a YAML file and put them back.

The values are the ones Home Assistant shows for each control (read back from the
inverter, not the last value sent), so a file written here lists exactly what the
Configuration card displays. Restoring sends every value that differs through the
same handlers the Home Assistant controls use, one command every few seconds, and
checks the read-back a minute and a half later.

Never restored, whatever the file says: the battery type (changing it cuts the
inverter's output, which also powers Home Assistant). It is saved under
`read_only:` for information only.

The file is flat on purpose (`section:` then `  key: value  # comment`): no YAML
library is bundled, and it stays easy to edit by hand.
"""

import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import state as _state
from .config import DEVICE_ID, MQTT_DISCOVERY_PREFIX, SETTINGS_BACKUP_FILE, SETTINGS_DEVICE_MARKER_FILE
from .loggers import log

#: Seconds between two restore commands: the dongle takes one command at a time and
#: answers e=104 while another is pending, and the live reads share the same link.
RESTORE_SPACING_SEC = 3.0
#: Wait before the read-back is compared (the dongle refreshes its settings block
#: about once a minute).
VERIFY_DELAY_SEC = 90.0

#: Order matters: Programme 38 raises 39 and 62 by itself, so it goes first; the
#: second output is switched on last, once its thresholds are in place.
_RESTORE_ORDER = (
    "bms_lock_machine_soc", "bms_restore_mains_charging_soc", "bms_restore_battery_discharging_soc",
    "bms_inverter_startup_soc",
    "second_output_cutoff_soc", "second_output_restore_soc", "second_output_discharge_time",
    "second_output_delay_time",
    "back_to_grid_voltage", "back_to_battery_voltage", "equalization_voltage", "grid_tie_current",
    "max_charging_current",
    "output_source_priority", "charger_priority", "solar_supply_priority", "max_utility_charge_current",
    "grid_working_range", "output_voltage", "grid_regulation_mode",
    "ac_charging_start_time", "ac_charging_stop_time",
    "backlight", "buzzer", "display_return_to_homepage", "eco", "overload_restart",
    "over_temperature_restart", "primary_source_interrupt_alarm", "overload_bypass",
    "fault_code_record", "dual_output",
)
#: Saved for information, never written back.
_READ_ONLY = ("battery_type",)

#: Manual programme of each setting, only for the comments in the file.
_PROGRAMMES = {
    "bms_lock_machine_soc": "38", "bms_restore_mains_charging_soc": "39",
    "bms_restore_battery_discharging_soc": "40", "bms_inverter_startup_soc": "41",
    "second_output_cutoff_soc": "62", "second_output_restore_soc": "64",
    "second_output_discharge_time": "65, 0 = disabled", "second_output_delay_time": "66",
    "back_to_grid_voltage": "12", "back_to_battery_voltage": "13", "equalization_voltage": "31",
    "grid_tie_current": "56", "max_charging_current": "02",
    "output_source_priority": "01", "charger_priority": "16", "solar_supply_priority": "43",
    "max_utility_charge_current": "11", "grid_working_range": "03", "output_voltage": "10",
    "grid_regulation_mode": "50", "ac_charging_start_time": "46", "ac_charging_stop_time": "47",
    "backlight": "20", "buzzer": "18", "display_return_to_homepage": "19", "eco": "08",
    "overload_restart": "06", "over_temperature_restart": "07",
    "primary_source_interrupt_alarm": "22", "overload_bypass": "23", "fault_code_record": "25",
    "dual_output": "60", "battery_type": "05, read only: changing it cuts the output",
}

_LOCK = threading.Lock()
_status_text = ""


def _tables():
    from . import fakecloud, mqtt

    return mqtt, fakecloud


def _kind(setting: str) -> Optional[str]:
    mqtt, _ = _tables()
    if setting in mqtt._CONTROL_NUMBERS:
        return "number"
    if setting in mqtt._CONTROL_HOUR_SELECTS:
        return "hour"
    if setting in mqtt._CONTROL_SELECTS:
        return "select"
    if setting in mqtt._CONTROL_SWITCHES:
        return "switch"
    return None


_TEMPLATE_RE = re.compile(
    r"\{\{\s*value_json\.(\w+)(\s*\|\s*replace\('[^']*',\s*''\)\s*\|\s*int)?\s*\}\}\s*(\S*)\s*$"
)


def displayed_value(setting: str, snapshot: Optional[Dict[str, object]] = None) -> Optional[str]:
    """What Home Assistant shows for `setting`, from the bridge's own state, or None
    when the inverter has not reported it yet."""
    mqtt, _ = _tables()
    snapshot = snapshot if snapshot is not None else _state.snapshot_state()
    if _kind(setting) == "hour":
        # AC charging window: the select reads the sensor of the same name ("12:00").
        value = snapshot.get(setting)
        return value if isinstance(value, str) and value in mqtt._HOUR_OPTIONS else None
    entry = mqtt._CONTROL_TELEMETRY_STATE.get(setting)
    if entry is None:
        return None
    template = entry["value_template"]
    if setting == "grid_regulation_mode":
        raw = snapshot.get("grid_regulation_mode")
        found = re.fullmatch(r"Mode (\d)", str(raw)) if raw is not None else None
        if not found:
            return None
        from .config import LANGUAGE
        from .i18n import grid_mode_label

        return grid_mode_label(int(found.group(1)), LANGUAGE)
    found = _TEMPLATE_RE.search(template)
    if not found:
        return None
    raw = snapshot.get(found.group(1))
    if raw is None:
        return None
    if found.group(2):
        raw = str(raw).replace(" min", "").strip()
        if not raw.lstrip("-").isdigit():
            return None
        raw = int(raw)
    text = str(raw)
    if _kind(setting) == "switch":
        return text.strip().lower() if text.strip().lower() in ("on", "off") else None
    return f"{text} {found.group(3)}".strip() if found.group(3) else text


def _current_values(snapshot=None) -> Dict[str, str]:
    values = {}
    for setting in _RESTORE_ORDER + _READ_ONLY:
        value = displayed_value(setting, snapshot)
        if value is not None:
            values[setting] = value
    return values


# --- the file -----------------------------------------------------------------

_SECTIONS = (("numbers", "number"), ("selects", "select"), ("switches", "switch"), ("hours", "hour"))


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_file(values: Dict[str, str], saved_at: str) -> str:
    lines = [
        "# Inverter settings saved by Siseli Local Bridge, as Home Assistant shows them.",
        "# Edit a value, delete a line to leave that setting alone, then press",
        "# \"Restore Inverter Settings\". battery_type is never restored.",
        f"saved_at: {_quote(saved_at)}",
    ]
    sections = {
        "numbers": [s for s in _RESTORE_ORDER if _kind(s) == "number"],
        "selects": [s for s in _RESTORE_ORDER if _kind(s) == "select"],
        "hours": [s for s in _RESTORE_ORDER if _kind(s) == "hour"],
        "switches": [s for s in _RESTORE_ORDER if _kind(s) == "switch"],
        "read_only": list(_READ_ONLY),
    }
    for name, settings in sections.items():
        present = [s for s in settings if s in values]
        if not present:
            continue
        lines.append(f"{name}:")
        for setting in present:
            comment = f"  # programme {_PROGRAMMES[setting]}" if setting in _PROGRAMMES else ""
            lines.append(f"  {setting}: {_quote(values[setting])}{comment}")
    return "\n".join(lines) + "\n"


def parse_file(text: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Read a file written by render_file (or edited by hand): returns the
    restorable {setting: value} and the saved_at stamp."""
    values: Dict[str, str] = {}
    saved_at = None
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1] in (" ", "\t")
        body = line.strip()
        key, _, rest = body.partition(":")
        key, rest = key.strip(), rest.strip()
        if rest.startswith('"'):
            end, out, i = None, [], 1
            while i < len(rest):
                ch = rest[i]
                if ch == "\\" and i + 1 < len(rest):
                    out.append(rest[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    end = i
                    break
                out.append(ch)
                i += 1
            value = "".join(out) if end is not None else rest.strip('"')
        else:
            value = rest.split("  #", 1)[0].split(" #", 1)[0].strip()
        if not indented:
            if rest == "" or rest.startswith("#"):
                section = key
            elif key == "saved_at":
                saved_at = value
            continue
        if section in ("numbers", "selects", "switches", "hours"):
            values[key] = value
    return values, saved_at


def _write_file(path: str, text: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError as exc:
            log(f"[SETTINGS] could not keep the previous file as .bak: {exc}", level="warning")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


# --- status in Home Assistant -----------------------------------------------------

STATUS_TOPIC = f"{DEVICE_ID}/settings_backup/status"


def _set_status(text: str) -> None:
    global _status_text
    _status_text = text
    try:
        from . import mqtt

        mqtt.client.publish(STATUS_TOPIC, text, retain=True)
    except Exception as exc:  # status is a convenience, never a reason to fail
        log(f"[SETTINGS] status not published: {exc}", level="debug")


def publish_status_discovery(client, device, name, entity_id, availability_topic) -> None:
    payload = {
        "name": name,
        "unique_id": f"{DEVICE_ID}_settings_backup_status",
        "default_entity_id": entity_id,
        "state_topic": STATUS_TOPIC,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device,
        "icon": "mdi:content-save-cog-outline",
    }
    import json

    client.publish(f"{MQTT_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/settings_backup_status/config",
                   json.dumps(payload), retain=True)


# --- the confirmation box -----------------------------------------------------------

#: A restore only starts while the "Warning: Confirm Restore Settings" switch is on;
#: the switch goes back off by itself after every press of the Restore button, or
#: after this long.
CONFIRM_WINDOW_SEC = 300.0
_CONFIRMED_AT: Optional[float] = None


def confirm_topics() -> Tuple[str, str]:
    from . import mqtt

    return mqtt.control_command_topic("confirm_restore_settings"), mqtt.control_state_topic("confirm_restore_settings")


def _publish_confirmation(on: bool) -> None:
    try:
        from . import mqtt

        mqtt.client.publish(confirm_topics()[1], "ON" if on else "OFF", retain=True)
    except Exception as exc:
        log(f"[SETTINGS] confirmation state not published: {exc}", level="debug")


def set_confirmation(on: bool) -> None:
    """The switch was turned on or off in Home Assistant."""
    global _CONFIRMED_AT
    _CONFIRMED_AT = time.monotonic() if on else None
    _publish_confirmation(on)
    if on:
        _set_status(f"Restore armed: press Restore Inverter Settings within {int(CONFIRM_WINDOW_SEC // 60)} min")


def _take_confirmation() -> bool:
    """True if the box is ticked (and not expired); either way it is cleared."""
    global _CONFIRMED_AT
    armed = _CONFIRMED_AT is not None and time.monotonic() - _CONFIRMED_AT <= CONFIRM_WINDOW_SEC
    _CONFIRMED_AT = None
    _publish_confirmation(False)
    return armed


def publish_confirm_discovery(client, device, name, entity_id, availability_topic) -> None:
    import json

    command, state = confirm_topics()
    payload = {
        "name": name,
        "unique_id": f"{DEVICE_ID}_confirm_restore_settings",
        "default_entity_id": entity_id,
        "command_topic": command,
        "state_topic": state,
        "payload_on": "ON",
        "payload_off": "OFF",
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device,
        "icon": "mdi:checkbox-marked-outline",
    }
    client.publish(f"{MQTT_DISCOVERY_PREFIX}/switch/{DEVICE_ID}/confirm_restore_settings/config",
                   json.dumps(payload), retain=True)
    # Never start a session ticked: an old retained ON must not arm a restore.
    client.publish(state, "OFF", retain=True)


# --- one-time move of the entities to their own device ---------------------------------

#: Seconds between clearing the old discovery configs and publishing the new ones, so
#: Home Assistant has removed the old entities before it sees the new ones.
MIGRATION_DELAY_SEC = 8.0
MIGRATION_ALLOWED_IN_TESTS = False


def _old_discovery_topics() -> List[str]:
    return [
        f"{MQTT_DISCOVERY_PREFIX}/button/{DEVICE_ID}/save_inverter_settings/config",
        f"{MQTT_DISCOVERY_PREFIX}/button/{DEVICE_ID}/restore_inverter_settings/config",
        f"{MQTT_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/settings_backup_status/config",
        f"{MQTT_DISCOVERY_PREFIX}/switch/{DEVICE_ID}/confirm_restore_settings/config",
    ]


def migrate_to_own_device_once(client, republish) -> bool:
    """Home Assistant keeps an entity on the device it was first created on, whatever
    a later discovery says. The four settings entities first appeared on the main
    device, so once (marker file) their discovery configs are cleared and, after a
    pause, published again by `republish`: they come back on the Settings Backup device.
    Returns True when the move was started."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not MIGRATION_ALLOWED_IN_TESTS:
        return False  # a test run must never leave a marker under /data
    marker_dir = os.path.dirname(SETTINGS_DEVICE_MARKER_FILE)
    if not marker_dir or not os.path.isdir(marker_dir) or os.path.exists(SETTINGS_DEVICE_MARKER_FILE):
        return False
    try:
        with open(SETTINGS_DEVICE_MARKER_FILE, "w", encoding="utf-8") as handle:
            handle.write("moved to the Settings Backup device\n")
    except OSError as exc:
        log(f"[SETTINGS] device move skipped, no marker file: {exc}", level="warning")
        return False

    def run() -> None:
        for topic in _old_discovery_topics():
            client.publish(topic, "", retain=True)
        time.sleep(MIGRATION_DELAY_SEC)
        republish()
        log("[SETTINGS] moved the settings entities to the Settings Backup device", level="warning")

    threading.Thread(target=run, daemon=True, name="settings-device-move").start()
    return True


# --- save / restore ---------------------------------------------------------------


def save_settings(path: Optional[str] = None) -> Optional[int]:
    """Write the file. Returns how many settings were saved, None on failure."""
    path = path or SETTINGS_BACKUP_FILE
    values = _current_values()
    restorable = [s for s in values if s in _RESTORE_ORDER]
    if not restorable:
        log("[SETTINGS] nothing to save: the inverter has not reported its settings yet", level="warning")
        _set_status("Nothing saved: no settings read from the inverter yet")
        return None
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        _write_file(path, render_file(values, stamp))
    except OSError as exc:
        log(f"[SETTINGS] could not write {path}: {exc}", level="warning")
        _set_status(f"Save failed: {exc}")
        return None
    missing = len(_RESTORE_ORDER) - len(restorable)
    note = f", {missing} not read yet" if missing else ""
    log(f"[SETTINGS] saved {len(restorable)} settings to {path}", level="warning")
    _set_status(f"Saved {stamp}: {len(restorable)} settings{note}")
    return len(restorable)


def _payload_for(setting: str, wanted: str) -> Optional[str]:
    """The control message that sets `setting` to `wanted`, or None if the value
    is not one the control accepts."""
    mqtt, _ = _tables()
    kind = _kind(setting)
    if kind == "number":
        number = re.match(r"-?\d+(?:\.\d+)?", wanted.strip())
        return number.group(0) if number else None
    if kind == "select":
        options = mqtt._CONTROL_SELECTS[setting][2]
        if wanted in options:
            return wanted
        if setting == "grid_regulation_mode":  # the label changes with LANGUAGE, the mode number does not
            number = re.match(r"Mode (\d)", wanted.strip())
            for label in options:
                if number and label.startswith(f"Mode {number.group(1)} "):
                    return label
        return None
    if kind == "hour":
        return wanted if wanted in mqtt._HOUR_OPTIONS else None
    if kind == "switch":
        return {"on": "ON", "off": "OFF"}.get(wanted.strip().lower())
    return None


def _same(setting: str, current: Optional[str], wanted: str) -> bool:
    if current is None:
        return False
    if _kind(setting) == "number":
        a = re.match(r"-?\d+(?:\.\d+)?", current.strip())
        b = re.match(r"-?\d+(?:\.\d+)?", wanted.strip())
        return bool(a and b and abs(float(a.group(0)) - float(b.group(0))) < 1e-6)
    return current.strip().lower() == wanted.strip().lower()


def plan_restore(values: Dict[str, str], snapshot=None) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """(to_send, skipped): to_send is [(setting, wanted, payload)] in a safe order,
    only for values that differ from what the inverter reports now."""
    to_send, skipped = [], []
    for setting in _RESTORE_ORDER:
        if setting not in values:
            continue
        wanted = values[setting]
        payload = _payload_for(setting, wanted)
        if payload is None:
            skipped.append(f"{setting}={wanted!r}")
            continue
        if _same(setting, displayed_value(setting, snapshot), wanted):
            continue
        to_send.append((setting, wanted, payload))
    for setting in values:
        if setting not in _RESTORE_ORDER:
            skipped.append(f"{setting} (not a restorable setting)")
    return to_send, skipped


def _connected() -> bool:
    """True while the dongle is connected to the bridge's local cloud: every command
    is sent on that connection, there is nothing to send them on otherwise."""
    _, fakecloud = _tables()
    return fakecloud._first_established_connection() is not None


def _restore_worker(to_send, values) -> None:
    mqtt, _ = _tables()
    try:
        sent = 0
        for index, (setting, wanted, payload) in enumerate(to_send, 1):
            if not _connected():
                log(f"[SETTINGS] restore stopped after {sent} of {len(to_send)}: the dongle disconnected", level="warning")
                _set_status(f"Restore stopped after {sent}/{len(to_send)}: the dongle is no longer connected")
                return
            _set_status(f"Restoring {index}/{len(to_send)}: {setting}")
            mqtt._handle_control_message(mqtt.control_command_topic(setting), payload.encode())
            sent += 1
            time.sleep(RESTORE_SPACING_SEC)
        _set_status(f"Restore sent ({sent} settings), checking in {int(VERIFY_DELAY_SEC)} s")
        time.sleep(VERIFY_DELAY_SEC)
        snapshot = _state.snapshot_state()
        wrong = [s for s, wanted, _ in to_send if not _same(s, displayed_value(s, snapshot), wanted)]
        if wrong:
            log(f"[SETTINGS] restore: {len(to_send) - len(wrong)}/{len(to_send)} confirmed, still different: "
                + ", ".join(wrong), level="warning")
            _set_status(f"Restored {len(to_send) - len(wrong)}/{len(to_send)}; still different: " + ", ".join(wrong))
        else:
            log(f"[SETTINGS] restore: all {len(to_send)} settings confirmed by the inverter", level="warning")
            _set_status(f"Restored: all {len(to_send)} settings confirmed")
    except Exception as exc:
        log(f"[SETTINGS] restore failed: {exc}", level="warning")
        _set_status(f"Restore failed: {exc}")
    finally:
        _LOCK.release()


def restore_settings(path: Optional[str] = None, wait: bool = False) -> bool:
    """Start a restore from the file. Returns False when it could not start."""
    path = path or SETTINGS_BACKUP_FILE
    if not _take_confirmation():
        log("[SETTINGS] restore refused: the confirmation box is not ticked", level="warning")
        _set_status("Restore refused: tick \"Warning: Confirm Restore Settings\" first (it overwrites the inverter settings), then press Restore")
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            values, saved_at = parse_file(handle.read())
    except OSError as exc:
        log(f"[SETTINGS] cannot read {path}: {exc}", level="warning")
        _set_status(f"Restore refused: cannot read the file ({exc})")
        return False
    if not values:
        log(f"[SETTINGS] {path} holds no settings", level="warning")
        _set_status("Restore refused: the file holds no settings")
        return False
    if not _connected():
        log("[SETTINGS] restore refused: the dongle is not connected to the local cloud", level="warning")
        _set_status("Restore refused: the dongle is not connected to the local cloud (local mode needed)")
        return False
    to_send, skipped = plan_restore(values)
    if skipped:
        log("[SETTINGS] not restored: " + ", ".join(skipped), level="warning")
    if not to_send:
        log("[SETTINGS] restore: the inverter already holds every saved value", level="warning")
        _set_status("Nothing to restore: the inverter already holds every saved value")
        return True
    if not _LOCK.acquire(blocking=False):
        log("[SETTINGS] a restore is already running", level="warning")
        return False
    log(f"[SETTINGS] restoring {len(to_send)} settings from {path} (saved {saved_at}): "
        + ", ".join(s for s, _, _ in to_send), level="warning")
    worker = threading.Thread(target=_restore_worker, args=(to_send, values), daemon=True, name="settings-restore")
    worker.start()
    if wait:
        worker.join()
    return True
