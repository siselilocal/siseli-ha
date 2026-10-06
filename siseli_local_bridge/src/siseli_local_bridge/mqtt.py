import json
import re
import time
from typing import Dict

import paho.mqtt.client as mqtt

from . import state as _state
from .config import *
from .i18n import (
    GRID_MODES,
    SENSOR_PROGRAMMES,
    control_name,
    grid_mode_label,
    translate_group_title,
    translate_name,
    with_programme,
)
from .loggers import log, log_error_always
from .sensors import (
    SENSOR_GROUP_TITLES,
    SENSORS,
    get_group_title,
    get_sensor_group,
)

_SECTION_PREFIXES = (
    "Device Info - ",
    "Battery Status - ",
    "BMS Status - ",
    "Grid Status - ",
    "Load Status - ",
    "PV Panel Status - ",
    "Settings - ",
)


def _trim_section_prefix(name: str) -> str:
    for prefix in _SECTION_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def display_sensor_name(base_name: str, programme: str = "") -> str:
    """`programme`: the front-panel programme the sensor reads, shown in French."""
    trimmed = with_programme(translate_name(_trim_section_prefix(base_name), LANGUAGE), programme, LANGUAGE)
    return f"{ENTITY_PREFIX} {trimmed}".strip() if ENTITY_PREFIX else trimmed


def display_control_name(setting: str, label: str) -> str:
    """display_sensor_name for a control: adds its programme number in French."""
    name = control_name(setting, label, LANGUAGE)
    return f"{ENTITY_PREFIX} {name}".strip() if ENTITY_PREFIX else name


def default_entity_id(domain: str, group: str, base_name: str) -> str:
    """The entity_id Home Assistant would build from the ENGLISH device and entity
    names, whatever LANGUAGE is: "<domain>.<device name>_<entity name>", slugified.

    HA builds a new entity's id from its displayed names, so with LANGUAGE=fr a
    sensor created after the switch got a French id (…_diagnostic_siseli_heure_de_…)
    while every older one kept its English id. Sent as `default_entity_id`, which
    HA only reads when it first creates an entity: existing ids never move.
    """
    device = DEVICE_NAME if group == "main" else f"{DEVICE_NAME} {get_group_title(group)}"
    name = _trim_section_prefix(base_name)
    if ENTITY_PREFIX:
        name = f"{ENTITY_PREFIX} {name}"
    slug = re.sub(r"[^a-z0-9]+", "_", f"{device} {name}".lower()).strip("_")
    return f"{domain}.{slug}"


def device_id_for_group(group: str) -> str:
    if group == "main":
        return DEVICE_ID
    return f"{DEVICE_ID}_{group}"


def state_topic_for_group(group: str) -> str:
    if group == "main":
        return STATE_TOPIC
    if STATE_TOPIC.endswith("/state"):
        return f"{STATE_TOPIC[:-6]}/{group}/state"
    return f"{STATE_TOPIC}/{group}"


def availability_topic_for_group(group: str) -> str:
    if group == "main":
        return AVAILABILITY_TOPIC
    if AVAILABILITY_TOPIC.endswith("/availability"):
        return f"{AVAILABILITY_TOPIC[:-13]}/{group}/availability"
    return f"{AVAILABILITY_TOPIC}/{group}"


def wire_identity() -> Dict[str, object]:
    """Identity the inverter reports about itself, for the HA device registry.

    Home Assistant shows these on the device page header, which is where a user looks
    for a firmware version -- the bridge decodes one the vendor portal itself leaves
    blank, and it was buried in a diagnostic sensor. model is left as the configured
    MODEL_NAME because the user chose it; the wire's model code goes to hw_version so
    nothing configured is overridden.

    Read from the shared state, which load_cached_state has already populated by the
    time discovery is published. On a first-ever start there is no cache and these are
    simply omitted; the next reconnect republishes discovery with them.
    """
    snapshot = _state.snapshot_state()
    fields = {
        "sw_version": snapshot.get("firmware_version"),
        "hw_version": snapshot.get("model_code"),
        "serial_number": snapshot.get("dtu_id"),
    }
    return {k: str(v) for k, v in fields.items() if v}


def device_info(group: str) -> Dict[str, object]:
    if group == "main":
        return {
            "identifiers": [DEVICE_ID],
            "name": DEVICE_NAME,
            "manufacturer": MANUFACTURER,
            "model": MODEL_NAME,
            **wire_identity(),
        }
    group_title = translate_group_title(get_group_title(group), LANGUAGE)
    group_device_id = device_id_for_group(group)
    return {
        "identifiers": [group_device_id],
        "name": f"{DEVICE_NAME} {group_title}".strip(),
        "manufacturer": MANUFACTURER,
        "model": MODEL_NAME,
        "via_device": DEVICE_ID,
    }


#: Entities of the settings file (settings_backup.py) live on a device of their own, so
#: the two buttons, the confirmation switch and the status sensor sit together on one
#: page, as visible controls rather than under Configuration / Diagnostic.
_SETTINGS_DEVICE_BUTTONS = ("save_inverter_settings", "restore_inverter_settings")


def settings_device_info() -> Dict[str, object]:
    title = translate_group_title("Settings Backup", LANGUAGE)
    return {
        "identifiers": [f"{DEVICE_ID}_settings_backup"],
        "name": f"{DEVICE_NAME} {title}".strip(),
        "manufacturer": MANUFACTURER,
        "model": MODEL_NAME,
        "via_device": DEVICE_ID,
    }


def create_mqtt_client() -> mqtt.Client:
    try:
        c = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=f"{DEVICE_ID}_bridge",
            protocol=mqtt.MQTTv311,
        )
    except Exception:
        c = mqtt.Client(client_id=f"{DEVICE_ID}_bridge", protocol=mqtt.MQTTv311)

    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    c.reconnect_delay_set(min_delay=5, max_delay=30)
    c.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    return c


client = create_mqtt_client()


def publish_sensor_discovery(key: str) -> None:
    if key not in SENSORS:
        return

    meta = SENSORS[key]
    group = get_sensor_group(key)
    group_device_id = device_id_for_group(group)
    topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{group_device_id}/{key}/config"
    payload = {
        "name": display_sensor_name(str(meta["name"]), SENSOR_PROGRAMMES.get(key, "")),
        "unique_id": f"{group_device_id}_{key}",
        "default_entity_id": default_entity_id("sensor", group, str(meta["name"])),
        "state_topic": state_topic_for_group(group),
        "value_template": f"{{{{ value_json.{key} }}}}",
        # Every entity points at the single last-will topic. paho supports exactly
        # one will, so per-group availability meant a broker-detected disconnect
        # marked only the 12 main-group entities unavailable while the other ~191
        # kept showing their last values as though they were live.
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info(group),
        "icon": meta.get("icon"),
    }

    if meta.get("unit"):
        payload["unit_of_measurement"] = meta["unit"]
    if meta.get("device_class"):
        payload["device_class"] = meta["device_class"]
    if meta.get("state_class"):
        payload["state_class"] = meta["state_class"]
    if meta.get("entity_category"):
        payload["entity_category"] = meta["entity_category"]
    if "enabled_by_default" in meta:
        payload["enabled_by_default"] = bool(meta["enabled_by_default"])
    if EXPIRE_AFTER_SEC:
        # Backstop for the watchdog itself dying. Every publish rewrites all groups
        # from one snapshot, so no group can expire while telemetry is flowing.
        payload["expire_after"] = EXPIRE_AFTER_SEC

    client.publish(topic, json.dumps(payload), retain=True)
    _state.PUBLISHED_SENSOR_KEYS.add(key)


#: setting name -> (label, icon). Kept in one place so the discovery config and the
#: incoming-topic map below can never drift apart -- add a new confirmed working
#: control here and both follow automatically.
_CONTROL_SWITCHES = {
    "backlight": ("Backlight", "mdi:lightbulb-outline"),
    "buzzer": ("Buzzer", "mdi:volume-high"),
    "dual_output": ("Dual Output", "mdi:electric-switch"),
    # Programme 08. No telemetry block carries it; read back through the
    # minute's PI30 QFLAG query instead (fakecloud._QFLAG_KEYS).
    "eco": ("ECO Power Saving", "mdi:leaf"),
    "overload_restart": ("Overload Automatic Restart", "mdi:restart"),  # Programme 06
    "over_temperature_restart": ("Over Temperature Automatic Restart", "mdi:thermometer-alert"),  # Programme 07
    "display_return_to_homepage": ("Display Returns To Homepage", "mdi:monitor"),  # Programme 19
    "primary_source_interrupt_alarm": ("Beeps While Primary Source Interrupted", "mdi:volume-high"),  # Programme 22
    "overload_bypass": ("Overload To Bypass", "mdi:swap-horizontal"),  # Programme 23
    "fault_code_record": ("Fault Code Record", "mdi:file-document-alert-outline"),  # Programme 25
}
#: button suffix -> (label, icon, fakecloud function name to call on press). Kept
#: as a dict so discovery/subscribe/dispatch below can never drift apart, same
#: reasoning as _CONTROL_SWITCHES above.
_CONTROL_BUTTONS = {
    "clear_fault_code": ("Clear Fault Code", "mdi:alert-remove-outline", "send_clear_fault_code"),
    "refresh_telemetry": ("Refresh Telemetry", "mdi:refresh", "send_manual_refresh"),
    # Programmes 51-55 in one go; see fakecloud.send_clock_sync.
    "sync_inverter_clock": ("Sync Inverter Clock", "mdi:clock-check-outline", "send_clock_sync"),
    # Settings file (settings_backup.py): the values Home Assistant shows, to a YAML
    # file and back. battery_type is never written back.
    "save_inverter_settings": ("Save Inverter Settings", "mdi:content-save-outline", "save_inverter_settings"),
    "restore_inverter_settings": ("Restore Inverter Settings", "mdi:backup-restore", "restore_inverter_settings"),
}
#: setting name -> (label, icon, {HA-displayed option -> fakecloud.SELECT_SETTINGS
#: option key}). Unlike _CONTROL_SWITCHES/_CONTROL_BUTTONS, this dispatches a
#: generic write built from fakecloud.build_write_ci rather than a pre-captured
#: `ci`. output_source_priority is confirmed working (front panel checked
#: 2026-09-23, see fakecloud.SELECT_SETTINGS); charger_priority and
#: grid_working_range are NOT yet confirmed -- output_source_priority's own
#: values were wrong on the first try, so treat these the same way until
#: someone reads the front panel after using them.
_CONTROL_SELECTS = {
    "output_source_priority": (
        "Output Source Priority", "mdi:transmission-tower",
        {
            "Solar+Battery First (SBU)": "solar_battery_first",
            "Solar First (SUB)": "solar_first",
        },
    ),
    "charger_priority": (
        # "Solar Residual (SOR)" dropped 2026-09-23: confirmed absent on this
        # inverter (see fakecloud.SELECT_SETTINGS), same reasoning as
        # output_source_priority only exposing its 2 confirmed options.
        "Charger Priority", "mdi:battery-sync",
        {
            "Solar Only (OSO)": "solar_only",
            "Solar + Utility (CSO)": "solar_and_utility",
            "Solar First (SNU)": "solar_first",
        },
    ),
    "grid_working_range": (
        "Grid Working Range", "mdi:sine-wave",
        {
            "UPS": "ups",
            "Appliance (APL)": "appliance",
        },
    ),
    "solar_supply_priority": (
        # Labels match the vendor app's own ("BLU"/"LBU") rather than
        # expanding them -- see fakecloud.SELECT_SETTINGS, their exact
        # meaning (a Battery/Load/Utility ordering) is inferred, not
        # confirmed by the app's own UI text.
        "Solar Supply Priority", "mdi:solar-power-variant",
        {
            "BLU": "blu",
            "LBU": "lbu",
        },
    ),
    "output_voltage": (
        # Programme 10. Labels match the read-back template below.
        "Output Voltage", "mdi:sine-wave",
        {
            "220 V": "220",
            "230 V": "230",
            "240 V": "240",
        },
    ),
    "max_utility_charge_current": (
        # Programme 11. Labels match the read-back template below.
        "Max Utility Charge Current", "mdi:current-ac",
        {f"{a} A": f"{a}" for a in (2, 10, 20, 30, 40, 50, 60, 70, 80, 90)},
    ),
    "grid_regulation_mode": (
        # Programme 50. Each option shows the mode's accepted voltage and
        # frequency (i18n.GRID_MODES); the read-back maps parsers.py's
        # "Mode n" onto the same label (_CONTROL_TELEMETRY_STATE below).
        "Grid Regulation Mode", "mdi:transmission-tower",
        {grid_mode_label(n, LANGUAGE): f"mode_{n}" for n in GRID_MODES},
    ),
    "battery_type": (
        # Labels are parsers.py's battery_type values verbatim, so the
        # telemetry read-back below matches an option. Only PYL and GRO are
        # captured; see fakecloud.SELECT_SETTINGS for the others and for why
        # changing this can cut the inverter's output.
        "Battery Type", "mdi:battery-sync",
        {
            "AGM": "agm",
            "Flooded (FLD)": "flooded",
            "User-defined (USE)": "user_defined",
            "LIA protocol (LIA)": "lia",
            "Pylontech (PYL)": "pylontech",
            "Techfine (TQF)": "techfine",
            "Growatt (GRO)": "growatt",
            "Felicity (FEL)": "felicity",
            "LIB protocol (LIB)": "lib",
            "Third-party lithium (LIC)": "third_party_lithium",
        },
    ),
}


#: setting name -> (label, icon). Limits and step come from
#: fakecloud.NUMBER_SETTINGS so the HA slider can never offer a value the
#: bridge (or the inverter) would refuse. Labels are the vendor app's own.
_CONTROL_NUMBERS = {
    "bms_lock_machine_soc": ("BMS Lock Machine SOC", "mdi:battery-off-outline"),
    "bms_restore_mains_charging_soc": ("Restore Mains Charging SOC", "mdi:transmission-tower-import"),
    "bms_restore_battery_discharging_soc": ("Restore Battery Discharging SOC", "mdi:battery-arrow-down"),
    "bms_inverter_startup_soc": ("Inverter Startup SOC", "mdi:power"),
    "back_to_grid_voltage": ("Back To Grid Voltage", "mdi:transmission-tower-import"),
    "back_to_battery_voltage": ("Back To Battery Voltage", "mdi:battery-arrow-up"),
    "equalization_voltage": ("Equalization Voltage", "mdi:battery-sync"),
    "grid_tie_current": ("Grid-Tie Current", "mdi:transmission-tower-export"),  # Programme 56
    "max_charging_current": ("Max Charging Current", "mdi:battery-charging-high"),  # Programme 02
    # Programmes 62, 64, 65, 66 (see fakecloud.NUMBER_SETTINGS)
    "second_output_cutoff_soc": ("Second Output Cut-off SOC", "mdi:battery-arrow-down"),
    "second_output_restore_soc": ("Second Output Restore SOC", "mdi:battery-arrow-up"),
    "second_output_discharge_time": ("Second Output Discharge Time", "mdi:timer-sand"),
    "second_output_delay_time": ("Second Output Restore Delay", "mdi:timer-outline"),
}


#: Number controls that shipped once and were withdrawn: their retained discovery
#: config is cleared on every discovery so Home Assistant drops the entity.
_WITHDRAWN_CONTROL_NUMBERS = ("second_output_restore_voltage",)  # 2.6.87, Programme 63


#: setting name -> real telemetry read-back, for the handful of controls whose
#: written value is ALSO decoded from the inverter's own spontaneous telemetry
#: (parsers.py), rather than only the write-and-hope optimistic state every
#: other control in this file uses.
#:
#: History: "dual_output" (`dual_output_mode`), "buzzer" (`buzzer_function`)
#: and "grid_working_range" (then `mains_input_range` from WdRR) were all tried
#: on 2026-09-23 and reverted, each stuck on one fixed value. At the time the
#: shared mechanism below was suspected. Captures of 2026-09-25 showed the
#: grid_working_range failure was the source field instead: the WdRR token
#: reads "11" under both UPS and APL, while 93VQ's config-pack first digit
#: follows the setting (1=UPS, 0=APL). mains_input_range is now decoded from
#: that digit, so grid_working_range is back here. The buzzer failure was the
#: same kind: buzzer_function read 93VQ token 6, which is the LCD backlight
#: (buzzer is token 5) -- found by toggling each in the vendor app with a
#: capture running, 2026-09-25. Both switches are back here on the corrected
#: fields. dual_output stays out until its own field is checked the same way
#: (change that one setting in the vendor app, capture, diff 93VQ) -- do not
#: re-add it on code review alone.
#:
#: The value_template must render exactly one of the select's option labels
#: (see _CONTROL_SELECTS), which is why parsers.py emits "UPS" and
#: "Appliance (APL)" verbatim.
#:
#: A setting listed here is left out of `_handle_control_message`'s optimistic
#: state publish -- publishing a second, competing value to a topic no
#: discovery config points at any more would just be dead weight.
_CONTROL_TELEMETRY_STATE = {
    "grid_working_range": {
        "group": get_sensor_group("mains_input_range"),
        "value_template": "{{ value_json.mains_input_range }}",
    },
    "output_voltage": {
        "group": get_sensor_group("output_set_voltage"),
        "value_template": "{{ value_json.output_set_voltage }} V",
    },
    "max_utility_charge_current": {
        "group": get_sensor_group("max_utility_charge_current_a"),
        "value_template": "{{ value_json.max_utility_charge_current_a }} A",
    },
    "grid_regulation_mode": {
        "group": get_sensor_group("grid_regulation_mode"),
        # "Mode 1" (telemetry) -> the select's full label for that mode.
        "value_template": (
            "{{ " + json.dumps({f"Mode {n}": grid_mode_label(n, LANGUAGE) for n in GRID_MODES}, ensure_ascii=False)
            + ".get(value_json.grid_regulation_mode, value_json.grid_regulation_mode) }}"
        ),
    },
    "battery_type": {
        "group": get_sensor_group("battery_type"),
        "value_template": "{{ value_json.battery_type }}",
    },
    # Programmes 38-41: 93VQ tokens 10-13, confirmed against the front panel.
    "bms_lock_machine_soc": {
        "group": get_sensor_group("bms_low_power_soc"),
        "value_template": "{{ value_json.bms_low_power_soc }}",
    },
    "bms_restore_mains_charging_soc": {
        "group": get_sensor_group("bms_returns_to_mains_mode_soc"),
        "value_template": "{{ value_json.bms_returns_to_mains_mode_soc }}",
    },
    "bms_restore_battery_discharging_soc": {
        "group": get_sensor_group("bms_returns_to_battery_mode_soc"),
        "value_template": "{{ value_json.bms_returns_to_battery_mode_soc }}",
    },
    "bms_inverter_startup_soc": {
        "group": get_sensor_group("bms_auto_start_soc_after_low"),
        "value_template": "{{ value_json.bms_auto_start_soc_after_low }}",
    },
    # Programmes 12-13: dHrK tokens 4-5.
    "back_to_grid_voltage": {
        "group": get_sensor_group("return_to_mains_mode_voltage_v"),
        "value_template": "{{ value_json.return_to_mains_mode_voltage_v }}",
    },
    "back_to_battery_voltage": {
        "group": get_sensor_group("return_to_battery_mode_voltage_v"),
        "value_template": "{{ value_json.return_to_battery_mode_voltage_v }}",
    },
    "equalization_voltage": {
        "group": get_sensor_group("battery_equalization_voltage_v"),
        "value_template": "{{ value_json.battery_equalization_voltage_v }}",
    },
    # Programme 56: 93VQ token 17, followed PGFC006/PGFC005 on 2026-09-27.
    "grid_tie_current": {
        "group": get_sensor_group("grid_connected_current_a"),
        "value_template": "{{ value_json.grid_connected_current_a }}",
    },
    # Programme 02: 93VQ token 1 (060 = the factory 60 A). To be confirmed by
    # a change from Home Assistant: the app's 50 A lasted no read-back.
    "max_charging_current": {
        "group": get_sensor_group("maximum_total_charging_current_a"),
        "value_template": "{{ value_json.maximum_total_charging_current_a }}",
    },
    # Programmes 62, 64, 65, 66: dHrK token 2, token 16 (64 before its last three
    # digits, 65 in them) and token 13; all four confirmed by a write.
    "second_output_cutoff_soc": {
        "group": get_sensor_group("parallel_mode_turn_off_soc"),
        "value_template": "{{ value_json.parallel_mode_turn_off_soc }}",
    },
    "second_output_restore_soc": {
        "group": get_sensor_group("second_output_battery_capacity"),
        "value_template": "{{ value_json.second_output_battery_capacity }}",
    },
    # The sensors hold "10 min"; a number entity needs the bare figure.
    "second_output_discharge_time": {
        "group": get_sensor_group("second_output_discharge_time"),
        "value_template": "{{ value_json.second_output_discharge_time | replace(' min', '') | int }}",
    },
    "second_output_delay_time": {
        "group": get_sensor_group("second_delay_time"),
        "value_template": "{{ value_json.second_delay_time | replace(' min', '') | int }}",
    },
    # 93VQ token 0, aux pack digit 2 and config pack digit 3: each moved to the
    # exact value sent (POP01, PCP02, PVENGUSE01) on 2026-09-26 and matched the
    # user's settings before the factory reset.
    "output_source_priority": {
        "group": get_sensor_group("output_source_priority"),
        "value_template": "{{ value_json.output_source_priority }}",
    },
    "charger_priority": {
        "group": get_sensor_group("charger_priority"),
        "value_template": "{{ value_json.charger_priority }}",
    },
    "solar_supply_priority": {
        "group": get_sensor_group("solar_supply_priority"),
        "value_template": "{{ value_json.solar_supply_priority }}",
    },
    # dHrK token 0 (not the 93VQ digit tried on 2026-09-23, which never moved).
    "dual_output": {
        "group": get_sensor_group("dual_output_mode"),
        "value_template": "{{ value_json.dual_output_mode }}",
        "state_on": "On",
        "state_off": "Off",
    },
    # Programmes 06, 07, 19, 23 -- 93VQ positions proven 2026-09-27.
    "overload_restart": {
        "group": get_sensor_group("overload_restart_function"),
        "value_template": "{{ value_json.overload_restart_function }}",
        "state_on": "On", "state_off": "Off",
    },
    "over_temperature_restart": {
        "group": get_sensor_group("over_temperature_restart_function"),
        "value_template": "{{ value_json.over_temperature_restart_function }}",
        "state_on": "On", "state_off": "Off",
    },
    "display_return_to_homepage": {
        "group": get_sensor_group("display_return_to_homepage"),
        "value_template": "{{ value_json.display_return_to_homepage }}",
        "state_on": "On", "state_off": "Off",
    },
    "overload_bypass": {
        "group": get_sensor_group("overload_to_bypass_function"),
        "value_template": "{{ value_json.overload_to_bypass_function }}",
        "state_on": "On", "state_off": "Off",
    },
    "buzzer": {
        "group": get_sensor_group("buzzer_function"),
        "value_template": "{{ value_json.buzzer_function }}",
        "state_on": "On",
        "state_off": "Off",
    },
    "backlight": {
        "group": get_sensor_group("lcd_back_lighting"),
        "value_template": "{{ value_json.lcd_back_lighting }}",
        "state_on": "On",
        "state_off": "Off",
    },
    # Programmes 08, 22, 25: PI30 QFLAG letters j, y, z, asked once a minute.
    "eco": {
        "group": get_sensor_group("power_saving_function"),
        "value_template": "{{ value_json.power_saving_function }}",
        "state_on": "On", "state_off": "Off",
    },
    "primary_source_interrupt_alarm": {
        "group": get_sensor_group("primary_source_interrupt_alarm"),
        "value_template": "{{ value_json.primary_source_interrupt_alarm }}",
        "state_on": "On", "state_off": "Off",
    },
    "fault_code_record": {
        "group": get_sensor_group("fault_code_record"),
        "value_template": "{{ value_json.fault_code_record }}",
        "state_on": "On", "state_off": "Off",
    },
}


def control_command_topic(setting: str) -> str:
    return f"{DEVICE_ID}/control/{setting}/set"


def control_state_topic(setting: str) -> str:
    return f"{DEVICE_ID}/control/{setting}/state"


#: command_topic -> setting name, built once from _CONTROL_SWITCHES so on_message
#: never has to string-parse a topic to recover which switch it belongs to.
_CONTROL_SWITCH_TOPICS = {
    control_command_topic(setting): setting for setting in _CONTROL_SWITCHES
}
#: command_topic -> fakecloud function name, same idea for the buttons.
_CONTROL_BUTTON_TOPICS = {
    control_command_topic(suffix): fn_name
    for suffix, (_, _, fn_name) in _CONTROL_BUTTONS.items()
}
#: command_topic -> setting name, same idea for the selects.
_CONTROL_SELECT_TOPICS = {
    control_command_topic(setting): setting for setting in _CONTROL_SELECTS
}
#: command_topic -> setting name, same idea for the numbers.
_CONTROL_NUMBER_TOPICS = {
    control_command_topic(setting): setting for setting in _CONTROL_NUMBERS
}
#: Programmes 46/47 (fakecloud.send_ac_charging_window): setting -> (label, icon,
#: which hour). Selects of their own, not _CONTROL_SELECTS entries, because both
#: hours go out in one command. Options are the read-back's own format, so the
#: telemetry value_template renders an option label as is.
_CONTROL_HOUR_SELECTS = {
    "ac_charging_start_time": ("AC Charging Start Time", "mdi:clock-start", "start"),
    "ac_charging_stop_time": ("AC Charging Stop Time", "mdi:clock-end", "stop"),
}
_HOUR_OPTIONS = [f"{hour:02d}:00" for hour in range(24)]
#: command_topic -> setting name, same idea for the hour selects.
_CONTROL_HOUR_TOPICS = {
    control_command_topic(setting): setting for setting in _CONTROL_HOUR_SELECTS
}


def publish_control_discovery() -> None:
    """Switches + buttons that send a real dev_rpc command to the dongle
    (fakecloud.send_control_switch / send_clear_fault_code / send_manual_refresh) --
    confirmed working by physically observing the inverter react, see
    protocole-cloud-dongle/README.md section 6. Only meaningful with LOCAL_CLOUD_IP
    set, since that is what the send functions need an established connection from;
    gating that is the caller's job (core.py), same as every other
    LOCAL_CLOUD_IP-only code path."""
    for setting, (label, icon) in _CONTROL_SWITCHES.items():
        topic = f"{MQTT_DISCOVERY_PREFIX}/switch/{DEVICE_ID}/{setting}/config"
        telemetry = _CONTROL_TELEMETRY_STATE.get(setting)
        payload = {
            "name": display_control_name(setting, label),
            "unique_id": f"{DEVICE_ID}_{setting}",
            "default_entity_id": default_entity_id("switch", "main", label),
            "command_topic": control_command_topic(setting),
            "state_topic": (
                state_topic_for_group(telemetry["group"]) if telemetry
                else control_state_topic(setting)
            ),
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info("main"),
            "icon": icon,
            "entity_category": "config",
        }
        if telemetry:
            payload["value_template"] = telemetry["value_template"]
            payload["state_on"] = telemetry["state_on"]
            payload["state_off"] = telemetry["state_off"]
        client.publish(topic, json.dumps(payload), retain=True)

    for suffix, (label, icon, _) in _CONTROL_BUTTONS.items():
        topic = f"{MQTT_DISCOVERY_PREFIX}/button/{DEVICE_ID}/{suffix}/config"
        on_settings_device = suffix in _SETTINGS_DEVICE_BUTTONS
        payload = {
            "name": display_control_name(suffix, label),
            "unique_id": f"{DEVICE_ID}_{suffix}",
            "default_entity_id": default_entity_id("button", "main", label),
            "command_topic": control_command_topic(suffix),
            "payload_press": "PRESS",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": settings_device_info() if on_settings_device else device_info("main"),
            "icon": icon,
            "entity_category": "config",
        }
        if on_settings_device:
            payload.pop("entity_category")
        client.publish(topic, json.dumps(payload), retain=True)

    from . import settings_backup

    settings_backup.publish_status_discovery(
        client, settings_device_info(), display_sensor_name("Settings Backup Status"),
        default_entity_id("sensor", "main", "Settings Backup Status"), AVAILABILITY_TOPIC,
    )
    settings_backup.publish_confirm_discovery(
        client, settings_device_info(), display_sensor_name("Warning: Confirm Restore Settings"),
        default_entity_id("switch", "main", "Warning: Confirm Restore Settings"), AVAILABILITY_TOPIC,
    )

    for setting, (label, icon, options) in _CONTROL_SELECTS.items():
        topic = f"{MQTT_DISCOVERY_PREFIX}/select/{DEVICE_ID}/{setting}/config"
        telemetry = _CONTROL_TELEMETRY_STATE.get(setting)
        payload = {
            "name": display_control_name(setting, label),
            "unique_id": f"{DEVICE_ID}_{setting}",
            "default_entity_id": default_entity_id("select", "main", label),
            "command_topic": control_command_topic(setting),
            "state_topic": (
                state_topic_for_group(telemetry["group"]) if telemetry
                else control_state_topic(setting)
            ),
            "options": list(options.keys()),
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info("main"),
            "icon": icon,
            "entity_category": "config",
        }
        if telemetry:
            payload["value_template"] = telemetry["value_template"]
        client.publish(topic, json.dumps(payload), retain=True)

    from . import fakecloud

    for setting, (label, icon) in _CONTROL_NUMBERS.items():
        limits = fakecloud.NUMBER_SETTINGS[setting]
        topic = f"{MQTT_DISCOVERY_PREFIX}/number/{DEVICE_ID}/{setting}/config"
        telemetry = _CONTROL_TELEMETRY_STATE.get(setting)
        payload = {
            "name": display_control_name(setting, label),
            "unique_id": f"{DEVICE_ID}_{setting}",
            "default_entity_id": default_entity_id("number", "main", label),
            "command_topic": control_command_topic(setting),
            "state_topic": (
                state_topic_for_group(telemetry["group"]) if telemetry
                else control_state_topic(setting)
            ),
            "min": limits["min"],
            "max": limits["max"],
            "step": limits["step"],
            "mode": "box",
            "unit_of_measurement": limits.get("unit", "%"),
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info("main"),
            "icon": icon,
            "entity_category": "config",
        }
        if telemetry:
            payload["value_template"] = telemetry["value_template"]
        client.publish(topic, json.dumps(payload), retain=True)

    for setting in _WITHDRAWN_CONTROL_NUMBERS:
        client.publish(f"{MQTT_DISCOVERY_PREFIX}/number/{DEVICE_ID}/{setting}/config", "", retain=True)

    for setting, (label, icon, _) in _CONTROL_HOUR_SELECTS.items():
        topic = f"{MQTT_DISCOVERY_PREFIX}/select/{DEVICE_ID}/{setting}/config"
        payload = {
            "name": display_control_name(setting, label),
            "unique_id": f"{DEVICE_ID}_{setting}",
            "default_entity_id": default_entity_id("select", "main", label),
            "command_topic": control_command_topic(setting),
            # Read back from the sensor of the same key (dHrK token 11).
            "state_topic": state_topic_for_group(get_sensor_group(setting)),
            "value_template": f"{{{{ value_json.{setting} }}}}",
            "options": _HOUR_OPTIONS,
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info("main"),
            "icon": icon,
            "entity_category": "config",
        }
        client.publish(topic, json.dumps(payload), retain=True)


    # The settings entities first appeared on the main device: move them, once.
    settings_backup.migrate_to_own_device_once(client, publish_control_discovery)

def subscribe_control_topics() -> None:
    """Re-subscribes every reconnect (on_connect calls this) -- a broker does not
    remember a clean-session client's subscriptions across a disconnect."""
    for topic in (list(_CONTROL_SWITCH_TOPICS) + list(_CONTROL_BUTTON_TOPICS)
                  + list(_CONTROL_SELECT_TOPICS) + list(_CONTROL_NUMBER_TOPICS)
                  + list(_CONTROL_HOUR_TOPICS)):
        client.subscribe(topic)


#: Minimum seconds between two accepted commands for the same control topic --
#: nothing upstream of this rate-limits a switch/button, so a stuck automation or
#: a flaky Lovelace binding retrying rapidly would otherwise hammer the dongle
#: with one dev_rpc write per message. Keyed by topic, written from the paho
#: network thread and from a settings restore (settings_backup, one command every
#: few seconds); a lost update between the two would only skip one rate check.
_CONTROL_MIN_INTERVAL_SEC = 1.0
_CONTROL_LAST_SENT: Dict[str, float] = {}


def _control_rate_limited(topic: str) -> bool:
    now = time.monotonic()
    last = _CONTROL_LAST_SENT.get(topic)
    if last is not None and now - last < _CONTROL_MIN_INTERVAL_SEC:
        return True
    _CONTROL_LAST_SENT[topic] = now
    return False


def _handle_control_message(topic: str, raw_payload: bytes) -> None:
    # Deferred import: fakecloud.py imports parsers.py at module load, and
    # parsers.py already imports this module (mqtt.py) from inside a function for
    # the same reason -- importing fakecloud at mqtt.py's own module level would
    # risk the same cycle depending on which module happens to be imported first.
    from . import fakecloud

    payload = raw_payload.decode("utf-8", errors="replace").strip()

    if _control_rate_limited(topic):
        log(f"[HA MQTT] control message on {topic!r} ignored: rate limit", level="warning")
        return

    hour_setting = _CONTROL_HOUR_TOPICS.get(topic)
    if hour_setting is not None:
        if payload not in _HOUR_OPTIONS:
            log(f"[HA MQTT] control message on {topic!r} ignored: unexpected option {payload!r}", level="warning")
            return
        # Read back from telemetry only: a refused or failed write leaves the
        # select on the real value.
        which = _CONTROL_HOUR_SELECTS[hour_setting][2]
        fakecloud.send_ac_charging_window(**{f"{which}_hour": int(payload[:2])})
        return

    if topic == control_command_topic("confirm_restore_settings"):
        from . import settings_backup

        if payload.upper() in ("ON", "OFF"):
            settings_backup.set_confirmation(payload.upper() == "ON")
        return

    fn_name = _CONTROL_BUTTON_TOPICS.get(topic)
    if fn_name is not None:
        getattr(fakecloud, fn_name)()
        return

    select_setting = _CONTROL_SELECT_TOPICS.get(topic)
    if select_setting is not None:
        _, _, options = _CONTROL_SELECTS[select_setting]
        option_key = options.get(payload)
        if option_key is None:
            log(f"[HA MQTT] control message on {topic!r} ignored: unexpected option {payload!r}", level="warning")
            return
        if fakecloud.send_control_select(select_setting, option_key) and select_setting not in _CONTROL_TELEMETRY_STATE:
            # Same optimistic-state reasoning as the switch case below. Skipped
            # for a setting in _CONTROL_TELEMETRY_STATE: its discovery state_topic
            # now points at real telemetry, not control_state_topic, so writing
            # here would only retain a value on a topic nothing reads any more.
            client.publish(control_state_topic(select_setting), payload, retain=True)
        return

    number_setting = _CONTROL_NUMBER_TOPICS.get(topic)
    if number_setting is not None:
        try:
            value = float(payload)
        except ValueError:
            log(f"[HA MQTT] control message on {topic!r} ignored: not a number {payload!r}", level="warning")
            return
        # Read back from telemetry only (all four are in _CONTROL_TELEMETRY_STATE):
        # a refused or NAKed write simply leaves the entity on the real value.
        fakecloud.send_control_number(number_setting, value)
        return

    setting = _CONTROL_SWITCH_TOPICS.get(topic)
    if setting is None:
        return
    payload_upper = payload.upper()
    if payload_upper not in ("ON", "OFF"):
        # Anything else -- including an empty message -- used to fall through to
        # the `!= "ON"` comparison below and silently sent OFF no matter what was
        # actually published. Unknown payloads are now ignored outright instead.
        log(f"[HA MQTT] control message on {topic!r} ignored: unexpected payload {payload!r}", level="warning")
        return
    turn_on = payload_upper == "ON"
    if fakecloud.send_control_switch(setting, turn_on) and setting not in _CONTROL_TELEMETRY_STATE:
        # Optimistic: the dongle's dev_rpc_reply to a command carries no field this
        # bridge has confirmed means success/failure (see README section 6), so
        # "the send happened" is the only signal available. Left unpublished on
        # failure (no connection) so the switch keeps showing its last known state
        # rather than a state that was never actually reached. Skipped for a
        # setting in _CONTROL_TELEMETRY_STATE -- same reasoning as the select
        # case above.
        client.publish(control_state_topic(setting), "ON" if turn_on else "OFF", retain=True)


def on_message(_client, _userdata, msg) -> None:
    try:
        _handle_control_message(msg.topic, msg.payload)
    except Exception as exc:
        log_error_always(f"[HA MQTT ERROR] control message on {msg.topic!r} failed: {exc}")


#: Sensors that shipped once and were removed (2.6.64 renamed them after their real
#: programmes): (group, key). stale_discovery_topics only sweeps keys still in
#: SENSORS, so their retained configs stayed on the broker and Home Assistant kept
#: them as frozen entities. Emptied on every discovery, which removes them.
_WITHDRAWN_SENSORS = (
    ("diagnostics", "eco"),  # Programme 06, now overload_restart_function
    ("diagnostics", "charging_priority_order"),  # Programme 50, now grid_regulation_mode
    ("load", "parallel_mode"),  # Programme 19, now display_return_to_homepage
    ("pv", "power_supply_from_pv_to_load_in_ac_state"),  # Programme 23, now overload_to_bypass_function
    ("load", "does_machine_have_output"),  # Programme 07, now over_temperature_restart_function
)


def withdrawn_sensor_topics() -> list:
    return [
        f"{MQTT_DISCOVERY_PREFIX}/sensor/{device_id_for_group(group)}/{key}/config"
        for group, key in _WITHDRAWN_SENSORS
    ]


def publish_discovery() -> None:
    for key in sorted(SENSORS.keys()):
        publish_sensor_discovery(key)
    for topic in withdrawn_sensor_topics():
        client.publish(topic, "", retain=True)

    # The watchdog's current verdict, never a literal. on_connect calls this on every
    # reconnect, so publishing True here re-marked a stale bridge as available and the
    # edge-triggered watchdog could never take it back.
    publish_availability(_state.AVAILABILITY_ONLINE)
    _state.DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published", level="info")


def publish_availability(online: bool) -> None:
    """Set the single availability topic that every entity references."""
    client.publish(AVAILABILITY_TOPIC, "online" if online else "offline", retain=True)


def stale_discovery_topics(device_id=None) -> set:
    """Discovery topics this configuration will never publish to again.

    A key's group is baked into both the discovery topic and the unique_id, so when
    the grouping changed (v2.5.21 moved the calculated sensors onto the main device)
    the old retained config stayed on the broker and Home Assistant kept the orphan
    entity alive alongside the new one, frozen at its last value.

    Scope is deliberately narrow: regrouping orphans only. Every sensor that is still
    declared keeps its config, including the ones with no decode path.
    """
    target = device_id or DEVICE_ID
    # Built from the real mapping: "main" maps to the bare device id, so a literal
    # "<id>_main" is a topic no version has ever written and must not be swept.
    group_ids = {
        target if group == "main" else "%s_%s" % (target, group)
        for group in SENSOR_GROUP_TITLES
    }
    candidates = {
        "%s/sensor/%s/%s/config" % (MQTT_DISCOVERY_PREFIX, gid, key)
        for key in SENSORS
        for gid in group_ids
    }
    if target != DEVICE_ID:
        return candidates
    live = {
        "%s/sensor/%s/%s/config"
        % (MQTT_DISCOVERY_PREFIX, device_id_for_group(get_sensor_group(key)), key)
        for key in SENSORS
    }
    return candidates - live


def _read_discovery_marker() -> Dict[str, object]:
    try:
        with open(DISCOVERY_MARKER_FILE, "r") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def cleanup_stale_discovery() -> int:
    """Clear retained discovery configs orphaned by a regrouping or a rename.

    Idempotent and gated by a marker file so it runs once rather than on every
    reconnect. The marker lives outside state.json, because that file is merged
    wholesale into LAST_STATE at boot and any key added here would be republished as
    though it were a sensor reading.
    """
    if not DISCOVERY_CLEANUP or _state.DISCOVERY_CLEANED:
        return 0

    marker = _read_discovery_marker()
    previous_id = marker.get("device_id")
    already_done = (
        marker.get("schema") == 1
        and previous_id == DEVICE_ID
        and marker.get("discovery_prefix") == MQTT_DISCOVERY_PREFIX
    )
    if already_done:
        _state.DISCOVERY_CLEANED = True
        return 0

    topics = stale_discovery_topics()
    if previous_id and previous_id != DEVICE_ID:
        topics |= stale_discovery_topics(previous_id)

    # Availability topics from before every entity shared one.
    legacy_availability = {
        availability_topic_for_group(group) for group in SENSOR_GROUP_TITLES
    } - {AVAILABILITY_TOPIC}

    for topic in sorted(topics) + sorted(legacy_availability):
        client.publish(topic, "", retain=True)

    try:
        _state.atomic_write_json(
            DISCOVERY_MARKER_FILE,
            {
                "schema": 1,
                "device_id": DEVICE_ID,
                "discovery_prefix": MQTT_DISCOVERY_PREFIX,
            },
        )
    except Exception as exc:
        log("[HA MQTT] Could not record discovery cleanup marker: %s" % exc, level="warning")

    _state.DISCOVERY_CLEANED = True
    cleared = len(topics) + len(legacy_availability)
    log("[HA MQTT] Cleared %d stale discovery topics" % cleared, level="info")
    return cleared


#: What was last accepted by the broker on each group topic: (JSON text, monotonic time).
#: Lets publish_grouped_state skip a group whose payload has not changed.
_LAST_GROUP_PUBLISH: Dict[str, tuple] = {}


def reset_group_publish_cache() -> None:
    """Forget what was published, so the next publish sends every group. Called when
    the broker connection is (re)established."""
    _LAST_GROUP_PUBLISH.clear()


def _group_refresh_interval() -> float:
    """How long an unchanged group may go unsent. Home Assistant restarts a sensor's
    expire_after timer only when a message arrives on its state topic, so a group
    that never changes (settings, identity) must still be sent often enough to stay
    inside it: a third of EXPIRE_AFTER_SEC, the same cadence as the heartbeat."""
    if EXPIRE_AFTER_SEC:
        return float(max(UPDATE_INTERVAL_SEC, EXPIRE_AFTER_SEC // 3))
    return 600.0


def publish_grouped_state(state_payload: Dict[str, object], force: bool = False) -> bool:
    """Publish one state topic per device group. True when every publish was accepted.

    The return value exists because paho does not raise when the broker is gone: a
    QoS 0 publish on a disconnected client returns MQTT_ERR_NO_CONN and the message is
    dropped. Every call site used to discard that, so the bridge reported "Published to
    HA" for payloads that reached nothing.

    A group whose JSON is identical to the one the broker last accepted is not sent
    again (with the live reads, every publish used to resend all seven groups, about
    three messages a second, to say nothing had changed in most of them). Not skipped
    with `force`, without MQTT_RETAIN (a Home Assistant restart would then wait for the
    next change to see the value), or when the last send is older than
    _group_refresh_interval().
    """
    grouped_state: Dict[str, Dict[str, object]] = {}
    for key, value in list(state_payload.items()):
        group = get_sensor_group(key)
        grouped_state.setdefault(group, {})[key] = value

    now = time.monotonic()
    refresh = _group_refresh_interval()
    dedupe = bool(MQTT_RETAIN) and not force
    delivered = True
    for group, payload in grouped_state.items():
        topic = state_topic_for_group(group)
        body = json.dumps(payload)
        if dedupe:
            last = _LAST_GROUP_PUBLISH.get(topic)
            if last is not None and last[0] == body and now - last[1] < refresh:
                continue
        try:
            result = client.publish(topic, body, retain=MQTT_RETAIN)
        except Exception as exc:
            # Without this the exception unwinds into parse_payload's handler and is
            # printed as [PARSER ERROR] -- a broker fault attributed to the decoder.
            log_error_always(f"[HA MQTT ERROR] Publish to {group} failed: {exc}")
            return False
        if getattr(result, "rc", 0) != 0:
            delivered = False
        else:
            _LAST_GROUP_PUBLISH[topic] = (body, now)
    return delivered


def broker_is_connected() -> bool:
    """Whether the client currently has a live connection to the broker."""
    try:
        return bool(client.is_connected())
    except Exception:
        return False


#: The KIND of connection failure currently being reported, or None while connected.
#: A kind rather than a bool, so a repeat stays silent while a CHANGE of kind still gets
#: its line -- an unreachable broker that later starts refusing credentials is new
#: information, and a bool cannot express that. Written only from the paho network
#: thread, which is the single caller of all three callbacks below.
LAST_CONNECT_FAILURE = None


def on_connect(_client, _userdata, _flags, rc, _properties=None):
    global LAST_CONNECT_FAILURE
    # paho does not suppress callback exceptions: anything escaping here propagates
    # out of loop_forever and kills the network thread, after which publish() queues
    # into a dead loop and the bridge goes silent with nothing in the log.
    try:
        code = int(rc) if rc is not None else -1
        if code == 0:
            # Re-armed only on success, and only here. 2.6.21 cleared it in the
            # preamble before rc was read, so a CONNACK that REFUSED the connection
            # re-armed the unreachable message and the two failures cross-contaminated.
            LAST_CONNECT_FAILURE = None
            log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}", level="info")
            # A new session: whatever was published before may be gone (a broker
            # without persistence), so the replay below must send everything.
            reset_group_publish_cache()
            cleanup_stale_discovery()
            publish_discovery()
            if LOCAL_CLOUD_IP:
                # Only meaningful with a local-cloud connection to send a real
                # dev_rpc command through -- see publish_control_discovery's
                # docstring. Re-subscribing every reconnect because a clean-session
                # broker connection does not remember prior subscriptions.
                publish_control_discovery()
                subscribe_control_topics()
            # The snapshot is taken under the lock the capture and health threads
            # publish under, so this replay can never land after, and so overwrite, a
            # newer state they already sent.
            with _state.PUBLISH_LOCK:
                snapshot = _state.snapshot_state()
                if any(v is not None for v in snapshot.values()):
                    publish_grouped_state(snapshot)
        else:
            # Gated: a refusing broker sends a CONNACK on every retry, so an ungated
            # line here is one error every reconnect delay for as long as the
            # credentials stay wrong -- thousands a day.
            if LAST_CONNECT_FAILURE != f"rc={code}":
                LAST_CONNECT_FAILURE = f"rc={code}"
                log(
                    f"[HA MQTT ERROR] Broker refused the connection with rc={code}"
                    f" ({'bad credentials' if code in (4, 5) else 'see the MQTT spec'})",
                    level="error",
                )
    except Exception as exc:
        log_error_always(f"[HA MQTT ERROR] on_connect failed: {exc}")


def on_disconnect(_client, _userdata, rc, _properties=None):
    try:
        code = int(rc) if rc is not None else -1
        if code != 0 and _state.RUNNING:
            log(f"[HA MQTT] Disconnected (rc={code}), retrying...", level="warning")
    except Exception as exc:
        log_error_always(f"[HA MQTT ERROR] on_disconnect failed: {exc}")


def on_connect_fail(_client=None, _userdata=None):
    """paho reports an unreachable broker only here.

    connect_async plus loop_start retries forever in the network thread, and on_connect
    fires only on a CONNACK -- so its rc != 0 branch covers a broker that answers and
    refuses, never one that is not there. Without this callback a wrong MQTT_HOST, a
    stopped broker or a blocked port produced no output at all, while the parser went
    on logging "Published to HA" for every payload.

    One line per outage, not per retry: paho retries every few seconds and this would
    otherwise fill the log. on_connect resets it, so a later outage is reported again.
    """
    global LAST_CONNECT_FAILURE
    if LAST_CONNECT_FAILURE == "unreachable":
        return
    LAST_CONNECT_FAILURE = "unreachable"
    log_error_always(
        f"[HA MQTT] Cannot reach the broker at {MQTT_HOST}:{MQTT_PORT}. Retrying in the "
        "background; nothing will be published to Home Assistant until it answers. "
        "Check the host, the port, and that the broker is running."
    )


client.on_connect = on_connect
client.on_connect_fail = on_connect_fail
client.on_disconnect = on_disconnect
client.on_message = on_message


def start_mqtt() -> None:
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as exc:
        log(f"[HA MQTT ERROR] {exc}", level="error")



