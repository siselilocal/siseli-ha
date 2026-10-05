"""Regression tests for the rule that a value is published only when this payload
contains evidence for it.

Each class here pins the absence of something the parser used to invent: hardcoded
presets keyed on one inverter's settings, a second writer overwriting a key with a
different quantity, a range guard that discarded the alarm condition it was meant to
catch, or a calculated value derived from a payload that carried no inputs.

Fixtures are real captures from two devices -- see tests/captures.py.
"""

import inspect
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from src.siseli_local_bridge import parsers as parser_module
from src.siseli_local_bridge import state as shared_state
from src.siseli_local_bridge.parsers import SolarParser
from tests import captures
from tests.helpers import envelope, isolated_state, patch_consts


class _ParserTestCase(unittest.TestCase):
    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        shared_state.LAST_STATE.clear()
        parser_module.LAST_ENERGY_TS.clear()


class TestNoFabricatedValues(_ParserTestCase):
    def test_yavb_does_not_fabricate_bms_fault_flags(self):
        """Twelve BMS alarm flags appeared whenever the 16-bit flag word equalled one
        exact all-clear pattern, and nothing else ever wrote them -- so they were
        structurally incapable of reporting a fault."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})

        for key in (
            "bms_allow_charging_flag", "bms_allow_discharge_flag",
            "bms_communication_control_function", "bms_charging_overcurrent_sign",
            "bms_discharge_overcurrent_flag", "bms_low_battery_alarm_flag",
            "bms_low_power_fault_flag", "bms_low_temperature_flag",
            "bms_temperature_too_high_flag", "battery_not_connected", "battery_voltage_higher",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, state)

        # The raw bit word is the honest artefact and is still published.
        self.assertEqual(state["yavb_flags_raw"], "1001100000000000")
        # Its first bit alone is decoded: BMS communication, proven by captures.
        self.assertEqual(state["bms_communication_normal"], "Yes")
        self.assertEqual(state["bms_current_soc"], 58)
        self.assertEqual(state["bms_charging_current_a"], 29.1)

    def test_battery_type_is_not_guessed_from_a_block_name(self):
        """battery_type was defaulted to "LIA" whenever a Yavb block existed."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertNotIn("battery_type", state)

    def test_battery_type_is_not_guessed_from_a_non_numeric_token(self):
        """After the preset was removed the key kept a narrower guess: publish 2ONL
        token 6 as the pack chemistry if it happens not to be a number. On the one
        device with byte-faithful captures that token is 110007200000, a twelve-digit
        status field, so the guard never fired and nothing supported the reading it
        would have produced. The official app does report LIA for this device, so the
        old constant was right here -- and would have claimed LIA for every inverter."""
        state = SolarParser._try_ascii_schema({"2ONL": captures.BLOCK_2ONL_CHARGING})
        self.assertNotIn("battery_type", state)
        # The neighbouring token keeps its real meaning.
        self.assertEqual(state["bus_voltage"], 420.0)

    def test_battery_status_is_not_guessed_from_the_bus_voltage_token(self):
        """2ONL token 5 had the same shape -- publish it as the battery status if it
        is not numeric -- for a token that is the bus voltage."""
        state = SolarParser._try_ascii_schema({"2ONL": captures.BLOCK_2ONL_CHARGING})
        self.assertEqual(state["battery_status"], "Charge")  # from the calculated power
        self.assertEqual(state["bus_voltage"], 420.0)

    def test_battery_status_needs_battery_data(self):
        """A payload carrying only the grid block reported "Charge"."""
        state = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertNotIn("battery_status", state)

    def test_battery_status_reports_idle_from_arithmetic(self):
        state = SolarParser._try_ascii_schema({"2ONL": captures.SYNTH_2ONL_IDLE})
        self.assertEqual(state["battery_status"], "Idle")

    def test_unknown_blocks_decode_to_nothing(self):
        """_try_ascii_schema always returned six calculated keys, so every payload
        reported success and the unparsed diagnostics were unreachable."""
        self.assertEqual(SolarParser._try_ascii_schema({}), {})
        self.assertEqual(SolarParser._try_ascii_schema({"ZZZZ": captures.SYNTH_UNKNOWN_BLOCK}), {})


class TestSingleWriterPerKey(_ParserTestCase):
    def test_dc_rectification_temperature_comes_from_v4w3_only(self):
        """2l0E decoded it with a `>100 -> /10` rescale that turned the live token
        01175 into 117.5 C; V4W3 carries the same reading unscaled at 51.0 C."""
        only_2l0e = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertNotIn("dc_rectification_temperature_c", only_2l0e)

        with_v4w3 = SolarParser._try_ascii_schema({
            "2l0E": captures.BLOCK_2L0E_LOADED,
            "V4W3": captures.BLOCK_V4W3_TEMPS,
        })
        self.assertEqual(with_v4w3["dc_rectification_temperature_c"], 51.0)

    def test_dhrk_does_not_write_grid_connected_current(self):
        """dHrK token 2 is a state-of-charge percentage; it was written into a sensor
        declared with unit A and device_class current."""
        state = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertEqual(state["parallel_mode_turn_off_soc"], 20)
        self.assertNotIn("grid_connected_current_a", state)

    def test_dhrk_does_not_write_the_mains_charging_times(self):
        """One token was written to both the start and the end time."""
        state = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertNotIn("mains_charging_starting_time", state)
        self.assertNotIn("mains_charging_ending_time", state)

    def test_dhrk_token_11_is_the_ac_charger_window(self):
        """Front panel 2026-09-29: Programme 46 = 12:00 moved dHrK[11] 0000 -> 1200,
        then Programme 47 = 13:00 -> 1213. Token 12 did not move: not the stop hour."""
        base = b"(0 044.0 020 044.0 044.0 048.0 0 056.0 060 120 030 %s 0000 05 0000 52.0 50000\r"
        for token, start, stop in ((b"0000", "00:00", "00:00"), (b"1200", "12:00", "00:00"), (b"1213", "12:00", "13:00")):
            with self.subTest(token=token):
                state = SolarParser._try_ascii_schema({"dHrK": base % token})
                self.assertEqual(state["ac_charging_start_time"], start)
                self.assertEqual(state["ac_charging_stop_time"], stop)
                self.assertNotIn("output_starting_time", state)
                self.assertNotIn("output_ending_time", state)
        state = SolarParser._try_ascii_schema({"dHrK": base % b"2599"})
        self.assertNotIn("ac_charging_start_time", state)

    def test_93vq_token_18_is_programme_44_solar_feed_to_grid(self):
        """2026-09-27: GtE on the front panel moved 93VQ[18] 0 -> 1, GtD back."""
        gtd = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 004 0 0 \r"
        gte = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 004 1 0 \r"
        self.assertEqual(SolarParser._try_ascii_schema({"93VQ": gtd})["solar_feed_to_grid"], "Disabled")
        on = SolarParser._try_ascii_schema({"93VQ": gte})
        self.assertEqual(on["solar_feed_to_grid"], "Enabled")
        self.assertEqual(on["grid_connection_function"], "Off")  # config pack[7] did not move
        for gone in ("mains_charging_starting_time", "mains_charging_ending_time"):
            self.assertNotIn(gone, on)

    def test_bat_series_count_comes_from_2onl_only(self):
        from_2onl = SolarParser._try_ascii_schema({"2ONL": captures.BLOCK_2ONL_CHARGING})
        self.assertEqual(from_2onl["bat_series_count"], 4)
        from_yavb = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertNotIn("bat_series_count", from_yavb)

    def test_low_electric_lock_voltage_comes_from_93vq_only(self):
        from_yavb = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertNotIn("low_electric_lock_voltage_v", from_yavb)
        self.assertEqual(from_yavb["bms_discharge_voltage_limit_v"], 42.0)

    def test_float_and_bulk_voltage_come_from_93vq_only(self):
        """Both fell back to dHrK settings that mean something else entirely, so a
        payload carrying dHrK without 93VQ published the parallel-mode turn-off
        voltage as float charging voltage -- 44.0 V against a true 56.4 V."""
        dhrk_only = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertNotIn("float_v", dhrk_only)
        self.assertNotIn("bulk_v", dhrk_only)
        # The genuine dHrK readings are still published under their own names.
        self.assertEqual(dhrk_only["parallel_mode_turn_off_voltage_v"], 44.0)
        self.assertEqual(dhrk_only["return_to_mains_mode_voltage_v"], 46.0)

        with_93vq = SolarParser._try_ascii_schema({
            "dHrK": captures.BLOCK_DHRK_SETTINGS,
            "93VQ": captures.BLOCK_93VQ_SETTINGS,
        })
        self.assertEqual(with_93vq["float_v"], 56.4)
        self.assertEqual(with_93vq["bulk_v"], 56.4)

    def test_max_chg_carries_the_charge_limit_not_the_grid_current(self):
        """The alias fell back to grid_connected_current_a, a different quantity, when
        a short 93VQ token list omitted the charge limit. Both live in 93VQ and read
        50 A and 20 A respectively, so the value proves which source is in use."""
        state = SolarParser._try_ascii_schema({"93VQ": captures.BLOCK_93VQ_SETTINGS})
        self.assertEqual(state["maximum_total_charging_current_a"], 50)
        self.assertEqual(state["grid_connected_current_a"], 20)
        self.assertEqual(state["max_chg"], 50)

        # Absent source, absent alias -- no borrowing from another block.
        without_93vq = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertNotIn("max_chg", without_93vq)

    def test_cell_summary_comes_from_uxjp_only(self):
        """v09K carries at most 16 cells. This bank has 32 -- the BMS reports its
        minimum at position 32 -- so any summary derived from that list describes a
        subset of the pack."""
        cells = SolarParser._try_ascii_schema({"v09K": captures.BLOCK_V09K_CELLS_16})
        self.assertEqual(cells["bms_cell_count"], 16)
        self.assertEqual(cells["cell_1_mv"], 3321)
        for key in ("bms_min_cell_mv", "bms_max_cell_mv", "bms_min_cell_pos",
                    "bms_max_cell_pos", "bms_cell_delta_mv"):
            with self.subTest(key=key):
                self.assertNotIn(key, cells)

        summary = SolarParser._try_ascii_schema({"uxJp": captures.BLOCK_UXJP_BMS_CAPACITY})
        self.assertEqual(summary["bms_min_cell_pos"], 32)
        self.assertEqual(summary["bms_cell_delta_mv"], 7)


SUMMARY_KEYS = ("bms_max_cell_mv", "bms_max_cell_pos", "bms_min_cell_mv",
                "bms_min_cell_pos", "bms_cell_delta_mv")


class TestCellSummaryFallback(_ParserTestCase):
    """With one battery type the inverter's uxJp carries the cell summary and v09K is a
    frozen copy; with the other v09K is live and the four summary tokens read 0000. The
    zeros used to be published as 0 mV, position 0 and delta 0."""

    def _state(self, uxjp, v09k=None):
        blocks = {"uxJp": uxjp}
        if v09k is not None:
            blocks["v09K"] = v09k
        return SolarParser._try_ascii_schema(blocks)

    def test_a_zero_summary_falls_back_to_the_cell_list_of_the_same_payload(self):
        state = self._state(captures.SYNTH_UXJP_ZERO_SUMMARY, captures.SYNTH_V09K_CELLS_LIVE)
        self.assertEqual(state["bms_max_cell_mv"], 3342)
        self.assertEqual(state["bms_max_cell_pos"], 2)
        self.assertEqual(state["bms_min_cell_mv"], 3339)
        self.assertEqual(state["bms_min_cell_pos"], 12)
        self.assertEqual(state["bms_cell_delta_mv"], 3)
        # The rest of uxJp is unaffected.
        self.assertEqual(state["bms_remaining_ah"], 99.2)
        self.assertEqual(state["bms_nominal_ah"], 100.0)

    def test_a_zero_summary_without_cells_publishes_nothing_rather_than_zeros(self):
        state = self._state(captures.SYNTH_UXJP_ZERO_SUMMARY)
        for key in SUMMARY_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, state)
        self.assertEqual(state["bms_remaining_ah"], 99.2)

    def test_a_usable_bms_summary_wins_over_the_cell_list(self):
        state = self._state(captures.BLOCK_UXJP_BMS_CAPACITY, captures.BLOCK_V09K_CELLS_16)
        self.assertEqual(state["bms_max_cell_mv"], 3327)
        self.assertEqual(state["bms_max_cell_pos"], 9)
        self.assertEqual(state["bms_min_cell_mv"], 3320)
        self.assertEqual(state["bms_min_cell_pos"], 32)
        self.assertEqual(state["bms_cell_delta_mv"], 7)

    def test_half_a_summary_is_unusable_and_the_halves_are_not_mixed(self):
        state = self._state(captures.SYNTH_UXJP_HALF_SUMMARY, captures.SYNTH_V09K_CELLS_LIVE)
        self.assertEqual(state["bms_max_cell_mv"], 3342)  # from the cells, not the 3342 token
        self.assertEqual(state["bms_min_cell_mv"], 3339)
        self.assertEqual(state["bms_min_cell_pos"], 12)
        self.assertEqual(state["bms_cell_delta_mv"], 3)

    def test_a_single_cell_is_not_a_summary(self):
        state = self._state(captures.SYNTH_UXJP_ZERO_SUMMARY, b"(3321 00000000\r")
        for key in SUMMARY_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, state)

    def test_on_a_tie_the_first_cell_is_reported(self):
        state = self._state(captures.SYNTH_UXJP_ZERO_SUMMARY, b"(3341 3341 3341 00000000\r")
        self.assertEqual(state["bms_max_cell_pos"], 1)
        self.assertEqual(state["bms_min_cell_pos"], 1)
        self.assertEqual(state["bms_cell_delta_mv"], 0)

    def test_only_the_leading_run_of_valid_cells_counts(self):
        """The list stops at the collapsed third cell, so the two cells after it must not
        enter the summary either."""
        state = self._state(captures.SYNTH_UXJP_ZERO_SUMMARY, captures.SYNTH_V09K_CELL_3_COLLAPSED)
        self.assertEqual(state["bms_cell_count"], 2)
        self.assertEqual(state["bms_max_cell_mv"], 3321)
        self.assertEqual(state["bms_min_cell_mv"], 3321)
        self.assertEqual(state["bms_cell_delta_mv"], 0)

    def test_the_cell_list_alone_still_writes_no_summary(self):
        state = SolarParser._try_ascii_schema({"v09K": captures.SYNTH_V09K_CELLS_LIVE})
        for key in SUMMARY_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, state)


class TestNoQuantityIsRelabelledAsAnother(_ParserTestCase):
    """Each of these published one quantity under the name of a different one. They
    are not missing decodes -- they were wrong ones, which is worse, because a wrong
    value looks like a working sensor."""

    def test_output_set_frequency_is_not_the_measured_frequency(self):
        """It was out_hz rounded. The portal carries Output Frequency 49.9 Hz and
        Output Set Frequency 50 Hz as two fields at one instant, and a sagging output
        at 49.4 Hz would have published the user's setting as 49."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertIn("out_hz", state)
        self.assertNotIn("output_set_frequency", state)

    def test_solar_charging_switch_is_not_derived_from_pv_power(self):
        """It read "Open" if PV power was above zero, so it reported the switch closed
        every night."""
        state = SolarParser._try_ascii_schema({"Mpod": captures.BLOCK_MPOD_PV1_IDLE})
        self.assertEqual(state["pv_w"], 0)
        self.assertNotIn("solar_charging_switch", state)

    def test_fan_status_is_not_derived_from_fan_speed(self):
        """The portal carries Fan Speed, Fan Status and Abnormal Fan Speed as three
        separate fields."""
        state = SolarParser._try_ascii_schema({"V4W3": captures.BLOCK_V4W3_TEMPS})
        self.assertIn("fan_1_speed", state)
        self.assertNotIn("fan_1_status", state)
        self.assertNotIn("fan_2_status", state)

    def test_grid_connection_count_is_not_the_pv_channel_count(self):
        """noeP token 3 reads 2 in every capture, and so do the inverter count and
        uxJp token 2 on this device -- three unrelated quantities equalling two."""
        state = SolarParser._try_ascii_schema({"noeP": captures.BLOCK_NOEP_PV2_ACTIVE})
        self.assertNotIn("total_number_of_grid_connection", state)


class TestNoDecodeIsPinnedToOneDeviceConfiguration(_ParserTestCase):
    """A guard that only passes on the reference device is a memorised constant."""

    SETTINGS = (
        "mains_input_range", "grid_regulation_mode", "battery_type", "overload_restart_function",
        "over_temperature_restart_function", "grid_connection_function",
        "solar_supply_priority", "output_set_voltage",
    )

    def _decode(self, tail):
        base = captures.BLOCK_93VQ_SETTINGS.decode("ascii")
        block = base.replace("13310110230", "13310110" + tail, 1).encode("ascii")
        return SolarParser._try_ascii_schema({"93VQ": block})

    def test_the_settings_word_decodes_at_any_mains_voltage(self):
        """The whole nine-field decode was gated on the word ending in "230", the
        reference device's output setting. On a 120 V or 240 V inverter every one of
        these vanished silently, with no log line."""
        for tail in ("230", "220", "240", "120", "100"):
            with self.subTest(output_voltage=tail):
                state = self._decode(tail)
                self.assertEqual(state["output_set_voltage"], int(tail))
                for key in self.SETTINGS:
                    self.assertIn(key, state)

    def test_a_tail_that_is_not_a_voltage_decodes_nothing(self):
        """The tail still gates the decode -- it is what confirms this is the word we
        think it is. It just must not be compared against one device's setting."""
        state = self._decode("999")
        for key in self.SETTINGS:
            with self.subTest(key=key):
                self.assertNotIn(key, state)

    def test_the_reference_values_are_unchanged(self):
        state = self._decode("230")
        self.assertEqual(state["battery_type"], "LIA protocol (LIA)")
        self.assertEqual(state["grid_regulation_mode"], "Mode 4")
        self.assertEqual(state["mains_input_range"], "UPS")
        self.assertEqual(state["grid_connection_function"], "Off")


class TestMainsInputRangeFollowsTheSetting(_ParserTestCase):
    """Real HEEP1 replies (93VQ) and WdRR blocks from 2026-09-25, same inverter,
    before and after the vendor app switched UPS -> APL (PGR01 -> PGR00)."""

    VQ_UPS = b"(1 060 002 10611110240 002 0 1 1 0 1 015 020 030 025 056.4 056.4 042.0 005 0 0 \r"
    VQ_APL = b"(1 060 002 00611110240 002 0 1 1 0 1 015 020 030 025 056.4 056.4 042.0 005 0 0 \r"
    WDRR_UPS = b"(000.0 00.0 280 170 65 40 +00000 0 12000 11+00000\r"
    WDRR_APL = b"(000.0 00.0 280 090 70 40 +00000 0 12000 11+00000\r"

    def test_config_pack_first_digit_is_the_range(self):
        self.assertEqual(SolarParser._try_ascii_schema({"93VQ": self.VQ_UPS})["mains_input_range"], "UPS")
        self.assertEqual(SolarParser._try_ascii_schema({"93VQ": self.VQ_APL})["mains_input_range"], "Appliance (APL)")

    def test_wdrr_code_is_raw_only(self):
        """WdRR's "11" is identical under UPS and APL, so it must not name a range."""
        for block in (self.WDRR_UPS, self.WDRR_APL):
            state = SolarParser._try_ascii_schema({"WdRR": block})
            self.assertEqual(state["mains_input_range_code"], "11")
            self.assertNotIn("mains_input_range", state)

    def test_settings_toggled_one_at_a_time(self):
        """Real HEEP1 replies from one capture, vendor app changing a single setting
        before each refresh: buzzer On/Off, backlight On/Off, battery PYL/GRO."""
        replies = {
            "start":        (b"00611110240", b"0 0", "Off", "Off", "Growatt (GRO)"),
            "buzzer on":    (b"00611110240", b"1 0", "On", "Off", "Growatt (GRO)"),
            "backlight on": (b"00611110240", b"0 1", "Off", "On", "Growatt (GRO)"),
            "pylontech":    (b"00411110240", b"0 0", "Off", "Off", "Pylontech (PYL)"),
        }
        for label, (pack, tokens, buzzer, backlight, battery) in replies.items():
            with self.subTest(label):
                block = b"(1 060 002 " + pack + b" 002 " + tokens + b" 1 0 1 015 020 030 025 056.4 056.4 042.0 005 0 0 \r"
                state = SolarParser._try_ascii_schema({"93VQ": block})
                self.assertEqual(state["buzzer_function"], buzzer)
                self.assertEqual(state["lcd_back_lighting"], backlight)
                self.assertEqual(state["battery_type"], battery)
                self.assertNotIn("automatic_return_to_first_page", state)
                self.assertNotIn("working_mode", state)

    def test_battery_type_writes_are_the_captured_bytes(self):
        """The vendor app's own frames, 2026-09-26."""
        import base64
        from src.siseli_local_bridge import fakecloud
        channel = fakecloud.SELECT_SETTINGS["battery_type"]["channel"]
        options = fakecloud.SELECT_SETTINGS["battery_type"]["options"]
        for key, wire in (("pylontech", b"PBT04g\x8a\r"), ("growatt", b"PBT06G\xc8\r")):
            with self.subTest(key):
                ci = fakecloud.build_write_ci(channel, options[key])
                self.assertEqual(base64.b64decode(ci), wire)

    def test_battery_type_select_labels_are_the_decoded_values(self):
        """Every option must be a value the parser can publish, or the read-back
        would never match; and every code 0-9 must map back to its own option."""
        from src.siseli_local_bridge import fakecloud, mqtt
        _, _, options = mqtt._CONTROL_SELECTS["battery_type"]
        codes = fakecloud.SELECT_SETTINGS["battery_type"]["options"]
        for label, key in options.items():
            with self.subTest(label):
                block = b"(1 060 002 10" + codes[key].encode() + b"11110240 002 0 0 1 0 1 015 020 030 025 056.4 056.4 042.0 005 0 0 \r"
                state = SolarParser._try_ascii_schema({"93VQ": block})
                self.assertEqual(state["battery_type"], label)
        self.assertEqual(sorted(codes.values()), [str(n) for n in range(10)])

    def test_bms_communication_follows_the_first_yavb_flag(self):
        """Yavb from 2026-09-26: communication up, BMS cable unplugged, battery
        type set to PYL (which this BMS does not speak)."""
        cases = {
            "up": (b"(04 1001100000000000 042.0 056.0 100.0 044 0020.2 0000.0 03021 000000\r", "Yes"),
            "cable unplugged": (b"(04 0001100000000000 042.0 056.0 100.0 048 0005.7 0000.0 03021 000000\r", "No"),
            "PYL": (b"(00 0001100000000000 040.0 056.8 200.0 100 0000.0 0000.0 02731 000000\r", "No"),
        }
        for label, (block, expected) in cases.items():
            with self.subTest(label):
                state = SolarParser._try_ascii_schema({"Yavb": block})
                self.assertEqual(state["bms_communication_normal"], expected)

    def test_soc_threshold_writes_are_the_captured_bytes(self):
        """Frames the vendor app sent and the inverter ACKed, 2026-09-26."""
        import base64
        from src.siseli_local_bridge import fakecloud
        captured = {
            "bms_lock_machine_soc": (15, b"BMSSDC015M\xb2\r"),
            "bms_restore_mains_charging_soc": (25, b"BMSB2UC025\xb4)\r"),
            "bms_restore_battery_discharging_soc": (30, b"BMSU2BC030\x9a\x0c\r"),
            "bms_inverter_startup_soc": (20, b"BMSSRC020\x81\x9b\r"),
        }
        for setting, (value, wire) in captured.items():
            with self.subTest(setting):
                channel = fakecloud.NUMBER_SETTINGS[setting]["channel"]
                self.assertEqual(base64.b64decode(fakecloud.build_write_ci(channel, f"{value:03d}")), wire)

    def test_soc_threshold_values_the_inverter_would_nak_are_not_sent(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        with mock.patch.object(fakecloud, "_send_control_ci", return_value=True) as send:
            for value in (14, 16, 21, 0, 100, 22.5):
                with self.subTest(value=value):
                    self.assertFalse(fakecloud.send_control_number("bms_restore_mains_charging_soc", value))
            self.assertTrue(fakecloud.send_control_number("bms_restore_mains_charging_soc", 20))
            self.assertTrue(fakecloud.send_control_number("bms_inverter_startup_soc", 100))
            self.assertEqual(send.call_count, 2)

    def test_ac_charging_window_sends_both_hours_in_one_frame(self):
        """2026-09-29: ^S???ACCT1200,1400 answered ^1 and read back 1200,1400. The
        hour not being changed comes from the last read-back."""
        import base64
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        snapshot = {"ac_charging_start_time": "12:00", "ac_charging_stop_time": "13:00"}
        with mock.patch.object(shared_state, "snapshot_state", return_value=snapshot), \
                mock.patch.object(fakecloud, "_send_control_ci", return_value=True) as send:
            self.assertTrue(fakecloud.send_ac_charging_window(stop_hour=14))
            frame = base64.b64decode(send.call_args[0][0])
            self.assertEqual(frame[:-3], b"^S???ACCT1200,1400")
            self.assertEqual(frame, fakecloud.base64.b64decode(fakecloud.build_write_ci("^S???ACCT", "1200,1400")))
            self.assertTrue(fakecloud.send_ac_charging_window(start_hour=0))
            self.assertEqual(base64.b64decode(send.call_args[0][0])[:-3], b"^S???ACCT0000,1300")

    def test_ac_charging_window_refuses_without_the_other_hour(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        snapshot = {"ac_charging_start_time": None, "ac_charging_stop_time": "13:00"}
        with mock.patch.object(shared_state, "snapshot_state", return_value=snapshot), \
                mock.patch.object(fakecloud, "_send_control_ci", return_value=True) as send:
            self.assertFalse(fakecloud.send_ac_charging_window(stop_hour=14))
            self.assertFalse(fakecloud.send_ac_charging_window(start_hour=24, stop_hour=1))
            send.assert_not_called()

    def test_ac_charging_selects_route_to_the_window_command(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt as mqtt_module
        mqtt_module._CONTROL_LAST_SENT.clear()
        with mock.patch.object(fakecloud, "send_ac_charging_window", return_value=True) as send, \
                mock.patch.object(mqtt_module, "client"):
            mqtt_module._handle_control_message(
                mqtt_module.control_command_topic("ac_charging_stop_time"), b"14:00")
            send.assert_called_once_with(stop_hour=14)
            mqtt_module._handle_control_message(
                mqtt_module.control_command_topic("ac_charging_start_time"), b"25:00")
            send.assert_called_once()

    def test_entity_ids_stay_english_whatever_the_language(self):
        """2.6.77 created its two new sensors with French ids under LANGUAGE=fr
        (…_diagnostic_siseli_heure_de_debut_de_charge_secteur): HA builds the id
        from the displayed names. default_entity_id pins the English one."""
        import json
        from unittest import mock
        from src.siseli_local_bridge import mqtt as mqtt_module
        with mock.patch.multiple(mqtt_module, LANGUAGE="fr", DEVICE_NAME="Siseli Local Inverter 1",
                                 ENTITY_PREFIX="Siseli"), \
                mock.patch.object(mqtt_module, "client") as client:
            mqtt_module.publish_sensor_discovery("ac_charging_start_time")
            mqtt_module.publish_control_discovery()
            payloads = {json.loads(c[0][1])["unique_id"]: json.loads(c[0][1])
                        for c in client.publish.call_args_list if c[0][0].endswith("/config") and c[0][1]}
            cleared = [c[0][0] for c in client.publish.call_args_list
                       if c[0][0].endswith("/config") and not c[0][1]]
        # a withdrawn control has its retained discovery config emptied, not republished
        self.assertEqual(cleared, [f"homeassistant/number/{mqtt_module.DEVICE_ID}/second_output_restore_voltage/config"])
        sensor = payloads[f"{mqtt_module.DEVICE_ID}_diagnostics_ac_charging_start_time"]
        self.assertEqual(sensor["name"], "Siseli Heure de début de charge secteur (prog 46)")
        self.assertEqual(sensor["default_entity_id"],
                         "sensor.siseli_local_inverter_1_diagnostics_siseli_ac_charging_start_time")
        hour_select = payloads[f"{mqtt_module.DEVICE_ID}_ac_charging_start_time"]
        self.assertEqual(hour_select["default_entity_id"],
                         "select.siseli_local_inverter_1_siseli_ac_charging_start_time")
        self.assertEqual(hour_select["entity_category"], "config")
        self.assertEqual(len(hour_select["options"]), 24)
        self.assertIn("12:00", hour_select["options"])
        select = payloads[f"{mqtt_module.DEVICE_ID}_output_source_priority"]
        self.assertEqual(select["default_entity_id"], "select.siseli_local_inverter_1_siseli_output_source_priority")
        for payload in payloads.values():
            self.assertRegex(payload["default_entity_id"], r"^[a-z]+\.[a-z0-9_]+$")

    def test_raw_probe_is_gone(self):
        """The 2.6.76 probe found the ACCT command and was removed in 2.6.79."""
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt as mqtt_module
        self.assertFalse(hasattr(fakecloud, "send_raw_command"))
        with mock.patch.object(mqtt_module, "client") as client:
            mqtt_module.subscribe_control_topics()
            self.assertNotIn("raw_command", str(client.subscribe.call_args_list))

    def test_lock_machine_soc_never_reaches_the_current_soc(self):
        """Programme 38 shuts the inverter down below it -- and the inverter powers
        Home Assistant. Refused at or above the SOC, or when the SOC is not trusted."""
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        cases = [
            ({"bms_communication_normal": "Yes", "bat_cap": 55}, 50, True),
            ({"bms_communication_normal": "Yes", "bat_cap": 55}, 55, False),
            ({"bms_communication_normal": "Yes", "bat_cap": 55}, 60, False),
            ({"bms_communication_normal": "No", "bat_cap": 100}, 15, False),
            ({"bat_cap": 55}, 15, False),
            ({"bms_communication_normal": "Yes"}, 15, False),
        ]
        for snapshot, value, expected in cases:
            with self.subTest(snapshot=snapshot, value=value), \
                    mock.patch.object(fakecloud, "_send_control_ci", return_value=True), \
                    mock.patch("src.siseli_local_bridge.state.snapshot_state", return_value=snapshot):
                self.assertIs(fakecloud.send_control_number("bms_lock_machine_soc", value), expected)

    def test_soc_numbers_read_back_the_front_panel_positions(self):
        """93VQ tokens 10-13 = Programmes 38-41, checked against the panel
        (15/25/30/20 on both, 2026-09-26)."""
        from src.siseli_local_bridge import mqtt
        from src.siseli_local_bridge.sensors import SENSORS
        block = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 005 0 0 \r"
        state = SolarParser._try_ascii_schema({"93VQ": block})
        expected = {
            "bms_lock_machine_soc": ("bms_low_power_soc", 15),
            "bms_restore_mains_charging_soc": ("bms_returns_to_mains_mode_soc", 25),
            "bms_restore_battery_discharging_soc": ("bms_returns_to_battery_mode_soc", 30),
            "bms_inverter_startup_soc": ("bms_auto_start_soc_after_low", 20),
        }
        for setting, (key, value) in expected.items():
            with self.subTest(setting):
                self.assertIn(setting, mqtt._CONTROL_NUMBERS)
                self.assertIn(key, SENSORS)
                self.assertEqual(state[key], value)
                self.assertIn(key, mqtt._CONTROL_TELEMETRY_STATE[setting]["value_template"])

    def test_eco_follows_the_pi30_flag_family_of_the_captured_commands(self):
        """backlight/buzzer are PI30's PEx/PDx and PEa/PDa; ECO uses flag j."""
        import base64
        import binascii
        from src.siseli_local_bridge import fakecloud, mqtt
        expect = {
            ("backlight", "on"): b"PEx", ("backlight", "off"): b"PDx",
            ("buzzer", "on"): b"PEa", ("buzzer", "off"): b"PDa",
            ("eco", "on"): b"PEj", ("eco", "off"): b"PDj",
        }
        for (setting, state), mnemonic in expect.items():
            with self.subTest(setting=setting, state=state):
                frame = base64.b64decode(fakecloud.CONTROL_COMMANDS[setting][state])
                self.assertEqual(frame, mnemonic + binascii.crc_hqx(mnemonic, 0).to_bytes(2, "big") + b"\r")
        self.assertIn("eco", mqtt._CONTROL_SWITCHES)
        # No telemetry block carries ECO; its read-back is the QFLAG letter j.
        self.assertIn("power_saving_function", mqtt._CONTROL_TELEMETRY_STATE["eco"]["value_template"])

    def test_output_voltage_is_the_pi30_v_command_with_read_back(self):
        import base64
        import binascii
        from src.siseli_local_bridge import fakecloud, mqtt
        d = fakecloud.SELECT_SETTINGS["output_voltage"]
        for volts in ("220", "230", "240"):
            with self.subTest(volts):
                frame = base64.b64decode(fakecloud.build_write_ci(d["channel"], d["options"][volts]))
                body = b"V" + volts.encode()
                self.assertEqual(frame, body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
        _, _, options = mqtt._CONTROL_SELECTS["output_voltage"]
        # value_template renders "<output_set_voltage> V"; every option must be reachable
        block = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 005 0 0 \r"
        state = SolarParser._try_ascii_schema({"93VQ": block})
        self.assertIn(f"{state['output_set_voltage']} V", options)
        self.assertEqual(set(options.values()), set(d["options"]))

    def test_max_utility_charge_current_is_muchgc_with_read_back(self):
        import base64
        import binascii
        from src.siseli_local_bridge import fakecloud, mqtt
        d = fakecloud.SELECT_SETTINGS["max_utility_charge_current"]
        for amps, body in (("2", b"MUCHGC002"), ("30", b"MUCHGC030"), ("90", b"MUCHGC090")):
            with self.subTest(amps):
                frame = base64.b64decode(fakecloud.build_write_ci(d["channel"], d["options"][amps]))
                self.assertEqual(frame, body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
        _, _, options = mqtt._CONTROL_SELECTS["max_utility_charge_current"]
        self.assertEqual(set(options.values()), set(d["options"]))
        for block, amps in ((b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 005 0 0 \r", 2),
                            (b"(1 060 030 13610110230 012 0 1 0 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r", 30)):
            state = SolarParser._try_ascii_schema({"93VQ": block})
            self.assertEqual(state["max_utility_charge_current_a"], amps)
            self.assertIn(f"{amps} A", options)
        self.assertIn("max_utility_charge_current_a", mqtt._CONTROL_TELEMETRY_STATE["max_utility_charge_current"]["value_template"])

    def test_back_to_grid_and_battery_voltages_are_pbcv_pbdv_with_read_back(self):
        import binascii
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt
        sent = []
        with mock.patch.object(fakecloud, "_send_control_ci", side_effect=lambda ci: sent.append(ci) or True):
            self.assertTrue(fakecloud.send_control_number("back_to_grid_voltage", 44))
            self.assertTrue(fakecloud.send_control_number("back_to_battery_voltage", 48.0))
            self.assertFalse(fakecloud.send_control_number("back_to_grid_voltage", 52))
            self.assertFalse(fakecloud.send_control_number("back_to_battery_voltage", 47))
        import base64
        for ci, body in zip(sent, (b"PBCV44.0", b"PBDV48.0")):
            self.assertEqual(base64.b64decode(ci), body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
        # dHrK before / after the 2026-09-26 factory reset
        for block, grid, batt in ((b"(0 044.0 020 044.0 044.0 048.0 0 056.0 060 120 030 0000 0000 05 0000 52.0 25975\r", 44.0, 48.0),
                                  (b"(1 044.0 020 044.0 046.0 054.0 0 058.4 060 120 030 0000 0000 05 0000 52.0 50000\r", 46.0, 54.0)):
            state = SolarParser._try_ascii_schema({"dHrK": block})
            self.assertEqual(state["return_to_mains_mode_voltage_v"], grid)
            self.assertEqual(state["return_to_battery_mode_voltage_v"], batt)
        self.assertIn("return_to_mains_mode_voltage_v", mqtt._CONTROL_TELEMETRY_STATE["back_to_grid_voltage"]["value_template"])
        self.assertIn("return_to_battery_mode_voltage_v", mqtt._CONTROL_TELEMETRY_STATE["back_to_battery_voltage"]["value_template"])

    def test_equalization_voltage_is_pbeqv_with_decimal_steps(self):
        import base64
        import binascii
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt
        sent = []
        with mock.patch.object(fakecloud, "_send_control_ci", side_effect=lambda ci: sent.append(ci) or True):
            self.assertTrue(fakecloud.send_control_number("equalization_voltage", 56.0))
            self.assertTrue(fakecloud.send_control_number("equalization_voltage", 58.4))
            self.assertFalse(fakecloud.send_control_number("equalization_voltage", 56.05))
            self.assertFalse(fakecloud.send_control_number("equalization_voltage", 47.9))
            self.assertFalse(fakecloud.send_control_number("equalization_voltage", 60.1))
        for ci, body in zip(sent, (b"PBEQV56.00", b"PBEQV58.40")):
            self.assertEqual(base64.b64decode(ci), body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
        for block, eq in ((b"(0 044.0 020 044.0 044.0 048.0 0 056.0 060 120 030 0000 0000 05 0000 52.0 25975\r", 56.0),
                          (b"(1 044.0 020 044.0 046.0 054.0 0 058.4 060 120 030 0000 0000 05 0000 52.0 50000\r", 58.4)):
            self.assertEqual(SolarParser._try_ascii_schema({"dHrK": block})["battery_equalization_voltage_v"], eq)
        self.assertIn("battery_equalization_voltage_v", mqtt._CONTROL_TELEMETRY_STATE["equalization_voltage"]["value_template"])

    def test_dual_output_is_dhrk_token_0(self):
        """App sent PDAULC00; HEEP2 read-back 1 -> 0 (2026-09-26)."""
        from src.siseli_local_bridge import mqtt
        for block, expected in ((b"(1 044.0 020 044.0 044.0 048.0 0 058.4 060 120 030 0000 0000 05 0000 52.0 50000\r", "On"),
                                (b"(0 044.0 020 044.0 044.0 048.0 0 058.4 060 120 030 0000 0000 05 0000 52.0 50000\r", "Off")):
            with self.subTest(expected):
                self.assertEqual(SolarParser._try_ascii_schema({"dHrK": block})["dual_output_mode"], expected)
        vq = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 005 0 0 \r"
        self.assertNotIn("dual_output_mode", SolarParser._try_ascii_schema({"93VQ": vq}))
        entry = mqtt._CONTROL_TELEMETRY_STATE["dual_output"]
        self.assertIn("dual_output_mode", entry["value_template"])
        self.assertEqual((entry["state_on"], entry["state_off"]), ("On", "Off"))

    def test_priorities_read_back_the_value_sent(self):
        """93VQ before the restore (factory values) and after POP01/PCP02/PVENGUSE01."""
        from src.siseli_local_bridge import fakecloud, mqtt
        before = b"(0 060 030 13600110230 011 1 1 0 0 1 010 020 095 050 056.4 056.4 042.0 020 0 0 \r"
        after = b"(1 060 030 13610110230 012 0 1 0 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r"
        s0 = SolarParser._try_ascii_schema({"93VQ": before})
        s1 = SolarParser._try_ascii_schema({"93VQ": after})
        self.assertEqual((s0["output_source_priority"], s1["output_source_priority"]),
                         ("Solar First (SUB)", "Solar+Battery First (SBU)"))
        self.assertEqual((s0["charger_priority"], s1["charger_priority"]),
                         ("Solar First (SNU)", "Solar Only (OSO)"))
        self.assertEqual((s0["solar_supply_priority"], s1["solar_supply_priority"]), ("BLU", "LBU"))
        for setting in ("output_source_priority", "charger_priority", "solar_supply_priority"):
            with self.subTest(setting):
                _, _, options = mqtt._CONTROL_SELECTS[setting]
                self.assertIn(setting, mqtt._CONTROL_TELEMETRY_STATE)
                # every label the parser can produce for a sent code is a select option
                for _label, key in options.items():
                    code = fakecloud.SELECT_SETTINGS[setting]["options"][key]
                    self.assertTrue(code in ("0", "1", "2", "00", "01"), code)
                self.assertIn(s1[setting], options)
                self.assertIn(s0[setting], options)
        self.assertNotIn("input_source_prompt_function", s1)
        self.assertNotIn("parallel_role", s1)

    def test_settings_found_after_the_factory_reset(self):
        """Real 93VQ blocks, 2026-09-27, one or two app changes between pushes."""
        b0811 = b"(1 060 002 13610110240 002 0 1 0 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r"  # before
        b0816 = b"(1 060 002 13611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r"  # 06+07 on
        b0822 = b"(1 060 002 13611100240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r"  # 07 off
        b0832 = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r"  # mode 1
        s = {k: SolarParser._try_ascii_schema({"93VQ": v}) for k, v in
             (("0811", b0811), ("0816", b0816), ("0822", b0822), ("0832", b0832))}
        self.assertEqual(s["0811"]["overload_restart_function"], "Off")
        self.assertEqual(s["0816"]["overload_restart_function"], "On")
        self.assertEqual(s["0816"]["over_temperature_restart_function"], "On")
        self.assertEqual(s["0822"]["over_temperature_restart_function"], "Off")
        self.assertEqual(s["0822"]["overload_restart_function"], "On")
        self.assertEqual(s["0816"]["grid_regulation_mode"], "Mode 4")
        self.assertEqual(s["0832"]["grid_regulation_mode"], "Mode 1")
        self.assertEqual(s["0832"]["display_return_to_homepage"], "Off")
        self.assertEqual(s["0816"]["overload_to_bypass_function"], "On")
        for gone in ("eco", "parallel_mode", "charging_priority_order", "power_supply_from_pv_to_load_in_ac_state", "does_machine_have_output"):
            self.assertNotIn(gone, s["0832"])

    def test_pi30_flag_switches_have_read_back(self):
        import base64
        import binascii
        from src.siseli_local_bridge import fakecloud, mqtt
        flags = {"overload_restart": b"u", "over_temperature_restart": b"v",
                 "display_return_to_homepage": b"k", "overload_bypass": b"b"}
        for setting, flag in flags.items():
            with self.subTest(setting):
                for state, prefix in (("on", b"PE"), ("off", b"PD")):
                    body = prefix + flag
                    frame = base64.b64decode(fakecloud.CONTROL_COMMANDS[setting][state])
                    self.assertEqual(frame, body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
                self.assertIn(setting, mqtt._CONTROL_SWITCHES)
                entry = mqtt._CONTROL_TELEMETRY_STATE[setting]
                self.assertEqual((entry["state_on"], entry["state_off"]), ("On", "Off"))
        # The over-temperature frames are the ones the vendor app sent.
        self.assertEqual(base64.b64decode(fakecloud.CONTROL_COMMANDS["over_temperature_restart"]["on"]), b"PEv\xb2\xa6\r")
        self.assertEqual(base64.b64decode(fakecloud.CONTROL_COMMANDS["over_temperature_restart"]["off"]), b"PDv\x81\x97\r")

    def test_grid_regulation_mode_select_sends_the_captured_frame(self):
        import base64
        import json
        from src.siseli_local_bridge import fakecloud, mqtt
        d = fakecloud.SELECT_SETTINGS["grid_regulation_mode"]
        frame = base64.b64decode(fakecloud.build_write_ci(d["channel"], d["options"]["mode_4"]))
        self.assertEqual(frame, b"^S???RS03\xb0\xd5\r")  # the vendor app's own frame
        _, _, options = mqtt._CONTROL_SELECTS["grid_regulation_mode"]
        self.assertEqual(len(options), 5)  # Modes 1-5, Mode 3 included (2026-09-28)
        self.assertIn("Mode 1 IND (195.5-253 VAC, 49-51 Hz)", options)
        self.assertIn("Mode 3 SAd (184-264.5 VAC, 57-62 Hz)", options)
        # The read-back template maps the parser's "Mode n" onto the option label.
        template = mqtt._CONTROL_TELEMETRY_STATE["grid_regulation_mode"]["value_template"]
        mapping = json.loads(template[3:template.index(".get(")])
        for label, key in options.items():
            with self.subTest(label):
                code = d["options"][key]
                vq = b"(1 060 002 1" + code[1:].encode() + b"611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 020 0 0 \r"
                parsed = SolarParser._try_ascii_schema({"93VQ": vq})["grid_regulation_mode"]
                self.assertEqual(parsed, f"Mode {int(code) + 1}")
                self.assertEqual(mapping[parsed], label)

    def test_grid_regulation_mode_labels_in_french(self):
        from src.siseli_local_bridge import i18n
        self.assertEqual(i18n.grid_mode_label(1, "fr"), "Mode 1 IND (195,5-253 VAC : 49-51 Hz)")
        self.assertEqual(i18n.grid_mode_label(4, "fr"), "Mode 4 PAk (170-264,5 VAC : 47,5-53,5 Hz)")
        self.assertEqual(i18n.grid_mode_label(5, "en"), "Mode 5 U2b (100-280 VAC, 47.5-53.5 Hz)")

    def test_clock_sync_sends_local_time_as_pi18_dat(self):
        import base64
        import binascii
        import time as _time
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt
        fixed = _time.struct_time((2026, 9, 27, 8, 45, 30, 6, 270, 0))
        sent = []
        with mock.patch.object(fakecloud.time, "localtime", return_value=fixed), \
                mock.patch.object(fakecloud, "_send_control_ci", side_effect=lambda ci: sent.append(ci) or True):
            self.assertTrue(fakecloud.send_clock_sync())
        body = b"^S???DAT260927084530"
        self.assertEqual(base64.b64decode(sent[0]), body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
        self.assertEqual(mqtt._CONTROL_BUTTONS["sync_inverter_clock"][2], "send_clock_sync")

    def test_command_replies_are_logged_and_qflag_read_back(self):
        """2026-09-27: the inverter's answer to a command is logged with the
        command it answers; telemetry replies are not; a QFLAG answer (the
        real one, "(EbuvxyDajkz") sets ECO / Programmes 22 and 25 instead."""
        import base64
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, parsers
        from src.siseli_local_bridge import state as shared_state
        conn = SimpleNamespace(reply=mock.Mock(), dtu_id="1", last_command="PGFC006")

        def publish(b):
            payload = b"\x00" + json.dumps({"c": 5, "t": "x", "s": "y", "i": 504, "e": 0, "b": b}).encode()
            topic = b"dtu/1/pub/service/dev_rpc_reply"
            return len(topic).to_bytes(2, "big") + topic + payload

        def co(raw):
            return {"sa": "", "co": base64.b64encode(raw).decode()}

        with mock.patch.object(fakecloud, "log") as log, \
                mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True), \
                mock.patch.dict(shared_state.LAST_STATE, {}, clear=False), \
                mock.patch.object(parsers, "PENDING_PUBLISH", False):
            fakecloud._handle_publish(0x30, publish({"sa": "", "ct": [{"cn": "93VQ", "co": "KDEgMDYwDQ=="}]}), conn)
            fakecloud._handle_publish(0x30, publish(co(b"(ACK9 \r")), conn)
            fakecloud._handle_publish(0x30, publish(co(b"(EbuvxyDajkz8\x12\r")), conn)
            snapshot = shared_state.snapshot_state()
            pending = parsers.PENDING_PUBLISH
        answered = [str(c) for c in log.call_args_list if "inverter answered" in str(c)]
        self.assertEqual(len(answered), 1)
        self.assertIn("PGFC006: (ACK9", answered[0])
        self.assertEqual(snapshot["power_saving_function"], "Off")
        self.assertEqual(snapshot["primary_source_interrupt_alarm"], "On")
        self.assertEqual(snapshot["fault_code_record"], "Off")
        self.assertTrue(pending)
        conn.reply.assert_not_called()  # replies never trigger a send

    def _reply_envelope(self, raw):
        import base64
        import json
        return b"\x00" + json.dumps({"c": 5, "i": 504, "e": 0, "b": {"sa": "", "co": base64.b64encode(raw).decode()}}).encode()

    def _pi30_frame(self, text):
        import binascii
        return text + binascii.crc_hqx(text, 0).to_bytes(2, "big") + b"\r"

    def test_qmod_answer_sets_the_mode_sensor(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, parsers
        from src.siseli_local_bridge import state as shared_state
        conn = SimpleNamespace(last_command="QMOD")
        for letter, label in ((b"B", "Battery Mode"), (b"L", "Line Mode"), (b"S", "Standby Mode")):
            with mock.patch.dict(shared_state.LAST_STATE, {}, clear=True), \
                    mock.patch.object(fakecloud, "log") as log, \
                    mock.patch.object(parsers, "PENDING_PUBLISH", False):
                fakecloud._log_command_reply(self._reply_envelope(self._pi30_frame(b"(" + letter)), conn)
                self.assertEqual(shared_state.snapshot_state()["mode"], label)
                self.assertTrue(parsers.PENDING_PUBLISH)
            log.assert_not_called()

    def test_a_command_ack_is_not_mistaken_for_a_mode(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        from src.siseli_local_bridge import state as shared_state
        conn = SimpleNamespace(last_command="PGFC006")
        with mock.patch.dict(shared_state.LAST_STATE, {}, clear=True), mock.patch.object(fakecloud, "log") as log:
            fakecloud._log_command_reply(self._reply_envelope(self._pi30_frame(b"(ACK")), conn)
            self.assertNotIn("mode", shared_state.snapshot_state())
        self.assertEqual(log.call_count, 1)

    def test_qpiws_answer_publishes_the_count_and_logs_only_a_change(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        from src.siseli_local_bridge import state as shared_state
        conn = SimpleNamespace(last_command="QPIWS")
        clear = b"0" * 32
        raised = b"0" * 4 + b"1" + b"0" * 10 + b"1" + b"0" * 16
        with mock.patch.dict(shared_state.LAST_STATE, {}, clear=True), mock.patch.object(fakecloud, "log") as log:
            for flags in (clear, clear, raised, raised):
                fakecloud._log_command_reply(self._reply_envelope(self._pi30_frame(b"(" + flags)), conn)
            snapshot = shared_state.snapshot_state()
        self.assertEqual(snapshot["warning_count"], 2)
        self.assertEqual(snapshot["warning_flags"], raised.decode())
        self.assertEqual(log.call_count, 1)  # only the clear -> raised change
        self.assertIn("[5, 16]", str(log.call_args))

    def test_live_interval_has_a_floor_and_an_off_switch(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        for configured, expected in ((0, 0), (-3, 0), (1, 5), (4, 5), (5, 5), (10, 10), (30, 30)):
            with mock.patch.object(fakecloud, "LIVE_POLL_INTERVAL_SEC", configured):
                self.assertEqual(fakecloud._live_interval(), expected, configured)

    # The eleven real answers of the H read commands (2026-10-03), no CRC.
    _H_ANSWERS = {
        "HBMS1": "(04 1001100000000000 042.0 056.0 100.0 041 0004.9 0000.0 02991 000000",
        "HBMS2": "(0041.0 0100.0 2 0000 0000 0000 0000 0000000000000000000000",
        "HBMS3": "(3227 3227 3228 3232 3227 3230 3233 3232 3231 3223 3227 3233 3230 3233 3229 3218 00000000",
        "HGRID": "(240.1 49.9 280 170 65 40 +00000 0 12000 11+00000",
        "HOP": "(242.5 49.8 00679 00235 006 037 11000 006.1 00126",
        "HBAT": "(04 052.4 041 002 00000 377 110007200000 00000000",
        "HPV": "(195.4 03.7 00728 00000.0 00000 2 195.2 027 15000",
        "HTEMP": "(040 044 029 054 054 030 030 11 038 045 000000000",
        "HGEN": "(261003 09:03 01.263 0028.4 0107.7 000000107.7 000000000000",
        "HSTS": "(00 B010000000000 10211002120B117200000",
        "HPVB": "(000.0 00.0 00000 0 380.0 00000000000000000000000",
    }

    def _h_raw(self, command):
        return self._H_ANSWERS[command].encode("ascii") + b"\r"

    def test_every_h_answer_is_identified_as_its_own_block(self):
        from src.siseli_local_bridge import fakecloud
        for command in self._H_ANSWERS:
            with self.subTest(command=command):
                self.assertEqual(fakecloud._h_block_of(self._h_raw(command)), fakecloud._H_BLOCKS[command])
        self.assertEqual(len(set(fakecloud._H_BLOCKS.values())), len(fakecloud._H_BLOCKS))

    def test_a_line_that_is_not_one_of_the_blocks_is_never_decoded(self):
        from src.siseli_local_bridge import fakecloud
        for raw in (
            b"(ACK9\r", b"(NAKss\r", b"(EbuvxyDajkz\x8b\x12\r", b"hello\r", b"\xff\xfe\r", b"(\r",
            self._h_raw("HGRID").replace(b" +00000", b""),          # a token short
            self._h_raw("HOP").replace(b"006.1", b"0061"),          # telltale token wrong
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(fakecloud._h_block_of(raw))

    def _h_conn(self, expected, cycle=1):
        from types import SimpleNamespace
        return SimpleNamespace(dtu_id="1", live_cycle=cycle, h_expected=frozenset(expected))

    def _feed(self, conn, command):
        from src.siseli_local_bridge import fakecloud
        fakecloud._log_command_reply(self._reply_envelope(self._h_raw(command)), conn)

    def _blocks_of(self, parse_call):
        import base64
        import json
        payload = parse_call[0][0]
        envelope = json.loads(payload[payload.index(b"{"):])
        return {b["cn"]: base64.b64decode(b["co"]) for b in envelope["b"]["ct"]}

    def test_the_battery_and_power_groups_are_decoded_as_soon_as_each_is_complete(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        cycle = ["HBAT", "HBMS1", "HBMS2", "HBMS3", "HGRID", "HOP", "HPV", "HSTS"]
        conn = self._h_conn(fakecloud._H_BLOCKS[c] for c in cycle)
        with mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True) as parse, \
                mock.patch.object(fakecloud, "log") as log:
            for command in cycle[:3]:
                self._feed(conn, command)
            parse.assert_not_called()  # the battery and BMS blocks wait for the cells: never decoded apart
            self._feed(conn, "HBMS3")
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(set(self._blocks_of(parse.call_args)), {"2ONL", "Yavb", "uxJp", "v09K"})
            for command in cycle[4:6]:
                self._feed(conn, command)
            self.assertEqual(parse.call_count, 1)
            self._feed(conn, "HPV")
            self.assertEqual(parse.call_count, 2)
            self.assertEqual(set(self._blocks_of(parse.call_args)), {"WdRR", "2l0E", "Mpod"})
            self._feed(conn, "HSTS")  # the rotating slow block goes alone, with nothing re-sent
            self.assertEqual(parse.call_count, 3)
            self.assertEqual(set(self._blocks_of(parse.call_args)), {"eo8w"})
        log.assert_not_called()
        self.assertEqual(conn.h_pending, {})

    def test_a_group_the_cycle_does_not_fully_expect_waits_for_the_cycle_end(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        conn = self._h_conn(["2ONL", "Yavb", "WdRR"])  # no uxJp, so the battery group is not whole
        with mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True) as parse:
            self._feed(conn, "HBAT")
            self._feed(conn, "HBMS1")
            parse.assert_not_called()
            self._feed(conn, "HGRID")
        parse.assert_called_once()
        self.assertEqual(set(self._blocks_of(parse.call_args)), {"2ONL", "Yavb", "WdRR"})

    def test_a_throttled_publish_is_flushed_every_second_not_every_ten(self):
        from unittest import mock
        from src.siseli_local_bridge import core, fakecloud, state as shared_state, tcpstack

        def stop(_seconds):
            shared_state.RUNNING = False

        with mock.patch.object(shared_state, "RUNNING", True), \
                mock.patch.object(core.time, "sleep", side_effect=stop), \
                mock.patch.object(tcpstack, "retransmit_tick"), \
                mock.patch.object(fakecloud, "poll_due_connections") as poll, \
                mock.patch.object(core, "publish_tick") as tick:
            core.telemetry_poll_loop()
        poll.assert_called_once()
        tick.assert_called_once()
    def test_a_lost_reply_is_made_up_for_when_the_next_cycle_starts(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        conn = self._h_conn(["2ONL", "Yavb", "WdRR"], cycle=1)
        with mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True) as parse:
            self._feed(conn, "HBAT")
            self._feed(conn, "HBMS1")  # HGRID never answers
            parse.assert_not_called()
            conn.live_cycle = 2
            self._feed(conn, "HBAT")
        parse.assert_called_once()
        self.assertEqual(set(self._blocks_of(parse.call_args)), {"2ONL", "Yavb"})
        self.assertEqual(set(conn.h_pending), {"2ONL"})  # the new cycle's block waits for its own

    def test_the_dongle_cache_never_overwrites_a_block_read_live_moments_ago(self):
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud

        def telemetry(*names):
            body = {"sa": "", "ct": [{"cn": n, "co": "KDAp"} for n in names]}
            return b"\x00" + json.dumps({"c": 5, "i": 502, "e": 0, "b": body}).encode()

        def names(payload):
            return [b["cn"] for b in json.loads(payload[payload.index(b"{"):])["b"]["ct"]]

        conn = SimpleNamespace(h_block_ts={"WdRR": 100.0, "2ONL": 100.0})
        with mock.patch.object(fakecloud.time, "monotonic", return_value=130.0):
            out = fakecloud._without_live_blocks(telemetry("WdRR", "93VQ", "2ONL", "dHrK"), conn)
            self.assertEqual(names(out), ["93VQ", "dHrK"])  # only the settings blocks remain
            self.assertEqual(fakecloud._without_live_blocks(telemetry("WdRR", "2ONL"), conn), b"")
            self.assertEqual(fakecloud._without_live_blocks(telemetry("93VQ"), conn), telemetry("93VQ"))
        with mock.patch.object(fakecloud.time, "monotonic", return_value=400.0):  # live reads stopped
            payload = telemetry("WdRR", "2ONL")
            self.assertEqual(fakecloud._without_live_blocks(payload, conn), payload)
        self.assertEqual(fakecloud._without_live_blocks(telemetry("WdRR"), SimpleNamespace()), telemetry("WdRR"))

    def test_a_telemetry_reply_made_only_of_live_blocks_is_not_decoded(self):
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        conn = SimpleNamespace(h_block_ts={"WdRR": 100.0}, probe_last=None)
        topic = b"dtu/1/pub/service/dev_rpc_reply"
        envelope = {"c": 5, "i": 502, "e": 0, "b": {"sa": "", "ct": [{"cn": "WdRR", "co": "KDAp"}]}}
        body = len(topic).to_bytes(2, "big") + topic + b"\x00" + json.dumps(envelope).encode()
        with mock.patch.object(fakecloud.time, "monotonic", return_value=120.0), \
                mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True) as parse:
            fakecloud._handle_publish(0x30, body, conn)
        parse.assert_not_called()

    def test_live_cycle_sends_the_fast_set_and_one_rotating_slow_command(self):
        import base64
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, tcpstack
        conn = SimpleNamespace(reply=mock.Mock(), dtu_id="1", next_poll_ts=1e9, closed=False, peer_ip="x", peer_port=1)

        def command_of(call):
            frame = call[0][0]
            raw = base64.b64decode(json.loads(frame[frame.index(b"{"):])["b"]["ci"])
            for known in ("QMOD", "QPIWS", "QFLAG"):
                if raw.startswith(known.encode()):
                    return known
            return raw.decode().rstrip("\r")

        # The first cycle also carries the once-a-minute QFLAG, so it has ten commands.
        ticks = [100.0 + i for i in range(10)] + [110.0 + i for i in range(9)]
        with mock.patch.object(fakecloud, "TELEMETRY_POLL_INTERVAL_SEC", 15), \
                mock.patch.object(fakecloud, "LIVE_POLL_INTERVAL_SEC", 10), \
                mock.patch.dict(tcpstack.CONNECTIONS, {("a", 1, "b", 2): conn}, clear=True), \
                mock.patch.object(fakecloud.time, "monotonic", side_effect=ticks):
            for _ in ticks:
                fakecloud.poll_due_connections()
        sent = [command_of(c) for c in conn.reply.call_args_list]
        fast = ["HBAT", "HBMS1", "HBMS2", "HBMS3", "HGRID", "HOP", "HPV", "QMOD"]
        # One command per second, a cycle every 10 s, the slow set taking one turn each,
        # QFLAG once a minute (so not in the second cycle).
        self.assertEqual(sent, fast + ["HTEMP", "QFLAG"] + fast + ["HGEN"])
        # The second cycle expects the fast blocks plus COST (HGEN), nothing else.
        self.assertEqual(conn.h_expected, frozenset({"2ONL", "Yavb", "uxJp", "v09K", "WdRR", "2l0E", "Mpod", "COST"}))

    def _connect_body(self, username=b"12345678901234567890"):
        proto = b"\x00\x04MQTT\x04"
        flags = b"\xc2"  # username + password + clean session
        keep_alive = b"\x00\x3c"
        client_id = b"dtu_" + username
        parts = [client_id, username, b"secret"]
        return proto + flags + keep_alive + b"".join(len(p).to_bytes(2, "big") + p for p in parts)

    def test_commands_and_live_reads_work_with_the_full_poll_switched_off(self):
        """TELEMETRY_POLL_INTERVAL_SEC ships at 0, and with it nothing remembered which
        device a connection belonged to: the live reads, QFLAG and every control
        command (no connection found) all silently did nothing."""
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, tcpstack
        conn = SimpleNamespace(reply=mock.Mock(), closed=False, peer_ip="x", peer_port=1)
        with mock.patch.object(fakecloud, "TELEMETRY_POLL_INTERVAL_SEC", 0), \
                mock.patch.object(fakecloud, "LIVE_POLL_INTERVAL_SEC", 10), \
                mock.patch.object(fakecloud, "log"):
            fakecloud._handle_frame(0x10, self._connect_body(), conn)
            self.assertEqual(conn.dtu_id, "12345678901234567890")
            self.assertFalse(hasattr(conn, "next_poll_ts"))  # no full poll was asked for
            with mock.patch.dict(tcpstack.CONNECTIONS, {("a", 1, "b", 2): conn}, clear=True):
                self.assertIs(fakecloud._first_established_connection(), conn)
                ticks = [100.0 + i for i in range(10)]
                with mock.patch.object(fakecloud.time, "monotonic", side_effect=ticks):
                    for _ in ticks:
                        fakecloud.poll_due_connections()
        ids = []
        for call in conn.reply.call_args_list:
            frame = call[0][0]
            ids.append(json.loads(frame[frame.index(b"{"):])["i"])
        self.assertEqual(len(ids), 10)       # the whole first cycle went out
        self.assertEqual(set(ids), {503})    # commands only: no i=501 poll

    def test_nothing_is_sent_when_both_the_poll_and_the_live_reads_are_off(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, tcpstack
        conn = SimpleNamespace(reply=mock.Mock(), dtu_id="1", closed=False, peer_ip="x", peer_port=1)
        with mock.patch.object(fakecloud, "TELEMETRY_POLL_INTERVAL_SEC", 0), \
                mock.patch.object(fakecloud, "LIVE_POLL_INTERVAL_SEC", 0), \
                mock.patch.dict(tcpstack.CONNECTIONS, {("a", 1, "b", 2): conn}, clear=True):
            for _ in range(5):
                fakecloud.poll_due_connections()
        conn.reply.assert_not_called()

    def _publish_body(self, envelope):
        import json
        topic = b"dtu/1/pub/service/dev_rpc_reply"
        return len(topic).to_bytes(2, "big") + topic + b"\x00" + json.dumps(envelope).encode()

    def test_only_the_answers_to_the_poll_count_for_the_stall_check(self):
        """Since 2.6.80 the answers to the live commands (i=504) arrive on the same
        topic; counting them made 'sent > replied' impossible, so a stalled poll could
        never be reported."""
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        conn = SimpleNamespace(poll_reply_count=0, last_reply_ts=0.0, poll_stall_logged=True)
        command_answer = {"c": 5, "i": 504, "e": 0, "b": {"sa": "", "co": "KEFDSzkN"}}
        poll_answer = {"c": 5, "i": 502, "e": 0, "b": {"sa": "", "ct": [{"cn": "WdRR", "co": "KDAp"}]}}
        with mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True), \
                mock.patch.object(fakecloud, "log"):
            fakecloud._handle_publish(0x30, self._publish_body(command_answer), conn)
            self.assertEqual(conn.poll_reply_count, 0)
            self.assertTrue(conn.poll_stall_logged)
            fakecloud._handle_publish(0x30, self._publish_body(poll_answer), conn)
        self.assertEqual(conn.poll_reply_count, 1)
        self.assertFalse(conn.poll_stall_logged)

    def test_refused_commands_and_incomplete_cycles_are_reported_once_a_minute(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        conn = SimpleNamespace(probe_last=None)
        refused = {"c": 5, "i": 504, "e": 104}
        with mock.patch.object(fakecloud, "log") as log:
            for _ in range(3):
                fakecloud._log_command_reply(self._publish_body(refused)[len(b"\x00\x1fdtu/1/pub/service/dev_rpc_reply"):], conn)
            fakecloud._live_note(conn, "cycle closed without all its blocks")
            fakecloud._live_report(conn, 100.0)
            self.assertEqual(log.call_count, 1)
            text = str(log.call_args)
            self.assertIn("command refused (e=104) x3", text)
            self.assertIn("cycle closed without all its blocks x1", text)
            fakecloud._live_note(conn, "command refused (e=104)")
            fakecloud._live_report(conn, 130.0)   # inside the minute: kept for later
            self.assertEqual(log.call_count, 1)
            fakecloud._live_report(conn, 161.0)
            self.assertEqual(log.call_count, 2)
            fakecloud._live_report(conn, 300.0)   # nothing new: silent
            self.assertEqual(log.call_count, 2)

    def test_a_malformed_mqtt_length_closes_the_connection_instead_of_failing_forever(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, tcpstack
        key = ("l", 1883, "p", 5555)
        conn = SimpleNamespace(recv_buffer=b"\x30\xff\xff\xff\xff\x7f", peer_ip="p", peer_port=5555,
                               local_ip="l", local_port=1883, closed=False, close=mock.Mock())
        with mock.patch.dict(tcpstack.CONNECTIONS, {key: conn}, clear=True), mock.patch.object(fakecloud, "log"):
            fakecloud._on_data(conn)
            self.assertNotIn(key, tcpstack.CONNECTIONS)
        self.assertEqual(conn.recv_buffer, b"")
        conn.close.assert_called_once()

    def test_a_frame_announced_larger_than_any_real_one_is_refused(self):
        from src.siseli_local_bridge import fakecloud
        from src.siseli_local_bridge.config import MAX_MQTT_PACKET
        frame = b"\x30" + fakecloud._encode_remaining_length(MAX_MQTT_PACKET + 1)
        with self.assertRaises(ValueError):
            fakecloud.extract_mqtt_frames(frame)
        ok = b"\x30" + fakecloud._encode_remaining_length(2) + b"ab"
        frames, rest = fakecloud.extract_mqtt_frames(ok)
        self.assertEqual((frames, rest), ([(0x30, b"ab")], b""))

    def test_the_stale_sweep_works_on_a_copy_of_the_connection_table(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import tcpstack
        old = SimpleNamespace(last_activity=0.0)
        new = SimpleNamespace(last_activity=990.0)
        with mock.patch.dict(tcpstack.CONNECTIONS, {("a", 1, "b", 1): old, ("a", 1, "b", 2): new}, clear=True), \
                mock.patch.object(tcpstack.time, "monotonic", return_value=1000.0):
            self.assertEqual(tcpstack.sweep_stale(300), 1)
            self.assertEqual(list(tcpstack.CONNECTIONS), [("a", 1, "b", 2)])

    def test_the_http_stub_drops_a_request_it_would_otherwise_wait_for_forever(self):
        from unittest import mock
        from src.siseli_local_bridge import httpstub
        with mock.patch.object(httpstub, "log"):
            self.assertEqual(httpstub._extract_request(b"A" * 70000), (None, b""))
            huge = b"POST /x HTTP/1.1\r\nContent-Length: 100000\r\n\r\n"
            self.assertEqual(httpstub._extract_request(huge), (None, b""))
        request, rest = httpstub._extract_request(b"GET /dtu/servers/mqtt HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual((request["method"], request["path"], rest), ("GET", "/dtu/servers/mqtt", b""))

    def test_the_firewall_recreates_a_table_left_for_other_ports(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import firewall

        calls = []
        stale = "table inet siseli_local_cloud {\n chain prerouting {\n  ip daddr 192.168.1.60 tcp dport 1883 drop\n }\n}\n"

        def fake_run(*args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=stale if args[0] == "list" else "", stderr="")

        with mock.patch.object(firewall, "LOCAL_CLOUD_IP", "192.168.1.60"), \
                mock.patch.object(firewall, "_PORTS", (1883, 80)), \
                mock.patch.object(firewall, "_run", side_effect=fake_run), \
                mock.patch.object(firewall, "log"):
            self.assertTrue(firewall.install_local_cloud_block())
        verbs = [c[0] for c in calls]
        self.assertIn("delete", verbs)                 # the stale table went away
        self.assertEqual(verbs.count("add"), 4)        # table, chain, two rules

    def test_the_firewall_keeps_a_table_that_already_matches(self):
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import firewall
        good = ("table inet siseli_local_cloud {\n\tchain prerouting {\n\t\tip daddr 192.168.1.60 tcp dport 1883 drop\n"
                "\t\tip daddr 192.168.1.60 tcp dport 80 drop\n\t}\n}\n")
        calls = []

        def fake_run(*args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=good, stderr="")

        with mock.patch.object(firewall, "LOCAL_CLOUD_IP", "192.168.1.60"), \
                mock.patch.object(firewall, "_PORTS", (1883, 80)), \
                mock.patch.object(firewall, "_run", side_effect=fake_run), \
                mock.patch.object(firewall, "log"):
            self.assertTrue(firewall.install_local_cloud_block())
        self.assertNotIn("delete", [c[0] for c in calls])
        self.assertNotIn("add", [c[0] for c in calls])

    def test_the_state_cache_is_written_at_shutdown_whatever_the_throttle_says(self):
        import json
        import os
        import tempfile
        from unittest import mock
        from src.siseli_local_bridge import parsers, state as shared_state
        from tests.helpers import isolated_state
        with isolated_state(), tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            shared_state.LAST_STATE.update({"c_generation_energy_kwh": 12.5})
            with mock.patch.object(parsers, "STATE_CACHE_FILE", path), \
                    mock.patch.object(parsers, "LAST_CACHE_WRITE_TS", parsers.time.monotonic()):
                self.assertFalse(parsers._write_state_cache(shared_state.snapshot_state()))  # throttled
                self.assertFalse(os.path.exists(path))
                self.assertTrue(parsers.flush_state_cache())                               # not at shutdown
            with open(path) as handle:
                self.assertEqual(json.load(handle)["c_generation_energy_kwh"], 12.5)
            shared_state.LAST_STATE.clear()
            with mock.patch.object(parsers, "STATE_CACHE_FILE", path):
                self.assertFalse(parsers.flush_state_cache())                              # nothing decoded yet
            with open(path) as handle:
                self.assertEqual(json.load(handle)["c_generation_energy_kwh"], 12.5)       # good cache kept

    def test_shutdown_saves_the_state_before_the_slow_steps(self):
        from unittest import mock
        from src.siseli_local_bridge import core, state as shared_state
        order = []
        with mock.patch.object(shared_state, "RUNNING", True), \
                mock.patch.object(core, "sniffer", None), \
                mock.patch.object(core, "flush_state_cache", side_effect=lambda: order.append("cache") or True), \
                mock.patch.object(core, "restore_arp", side_effect=lambda: order.append("arp")), \
                mock.patch.object(core, "teardown_local_cloud_block"), \
                mock.patch.object(core, "publish_availability"), \
                mock.patch.object(core, "client"), \
                mock.patch.object(core, "log"):
            core.shutdown()
        self.assertEqual(order, ["cache", "arp"])

    def test_the_cell_summary_is_computed_because_cells_and_capacities_share_a_payload(self):
        """2.6.81-82 read HBMS2 (capacities) every cycle and HBMS3 (cells) one cycle in six,
        never in the same payload, so bms_max_cell_mv, bms_min_cell_mv, bms_cell_delta_mv and
        the two positions froze at their last value while the cells moved. The real decoder,
        fed the real answers of 2026-10-03 as the battery group, now derives them."""
        from src.siseli_local_bridge import fakecloud, state as shared_state
        from tests.helpers import isolated_state
        cycle = ["HBAT", "HBMS1", "HBMS2", "HBMS3"]
        with isolated_state():
            conn = self._h_conn(fakecloud._H_BLOCKS[c] for c in cycle)
            for command in cycle:
                self._feed(conn, command)
            snapshot = shared_state.snapshot_state()
        self.assertEqual(snapshot["cell_1_mv"], 3227)
        self.assertEqual(snapshot["bms_max_cell_mv"], 3233)
        self.assertEqual(snapshot["bms_min_cell_mv"], 3218)
        self.assertEqual(snapshot["bms_cell_delta_mv"], 15)
        self.assertEqual(snapshot["bms_min_cell_pos"], 16)
        self.assertEqual(snapshot["bms_remaining_ah"], 41.0)
        self.assertEqual(snapshot["c_battery_charge_power_w"], 257)  # one payload, BMS current basis

    def test_the_pv2_block_never_zeroes_the_generation_power(self):
        """2.6.81-83 decoded noeP (HPVB, PV2) alone every fifth cycle; with no PV1 block
        in the payload the decoder took PV2's 0 W for the whole generation, so
        generation_power_w and the calculated generation power and energy dropped to
        zero for one publication (31 times in 30 minutes, 2026-10-03). PV2 now only
        rides along with the PV1 block."""
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, state as shared_state
        from tests.helpers import isolated_state
        power = ["HGRID", "HOP", "HPV"]
        with isolated_state():
            # A cycle whose slow command is HPVB, read first: nothing is decoded for it alone.
            conn = self._h_conn(["WdRR", "2l0E", "Mpod", "noeP"], cycle=1)
            with mock.patch.object(fakecloud.SolarParser, "parse_payload", wraps=fakecloud.SolarParser.parse_payload) as parse:
                self._feed(conn, "HPVB")
                parse.assert_not_called()
                self.assertNotIn("generation_power_w", shared_state.snapshot_state())
                for command in power:
                    self._feed(conn, command)
                parse.assert_called_once()
                self.assertEqual(set(self._blocks_of(parse.call_args)), {"WdRR", "2l0E", "Mpod", "noeP"})
            snapshot = shared_state.snapshot_state()
            self.assertEqual(snapshot["generation_power_w"], 728)
            self.assertEqual(snapshot["pv2_v"], 0.0)
            # A cycle whose slow command is HPVB, read last: the figure must not move.
            conn.live_cycle = 2
            conn.h_expected = frozenset({"WdRR", "2l0E", "Mpod", "noeP"})
            for command in power:
                self._feed(conn, command)
            self._feed(conn, "HPVB")
            self.assertEqual(shared_state.snapshot_state()["generation_power_w"], 728)
            self.assertEqual(shared_state.snapshot_state()["c_generation_power_w"], 728)

    def test_the_dongles_pv2_copy_is_dropped_once_pv1_is_live_even_before_pv2_is_read(self):
        """After a restart the first live PV2 read comes up to a minute after the PV1 one;
        meanwhile the dongle's copy of PV2 (its telemetry fragments carry it without PV1)
        decoded to generation_power_w = 0 W: two zeros in the minute after the 2.6.84
        restart of 2026-10-03."""
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud

        def telemetry(*names):
            body = {"sa": "", "ct": [{"cn": n, "co": "KDAp"} for n in names]}
            return b"\x00" + json.dumps({"c": 5, "i": 502, "e": 0, "b": body}).encode()

        def names(payload):
            return [b["cn"] for b in json.loads(payload[payload.index(b"{"):])["b"]["ct"]]

        conn = SimpleNamespace(h_block_ts={"Mpod": 100.0, "WdRR": 100.0})  # no noeP read yet
        with mock.patch.object(fakecloud.time, "monotonic", return_value=130.0):
            out = fakecloud._without_live_blocks(telemetry("93VQ", "Yavb", "noeP", "dHrK"), conn)
            self.assertEqual(names(out), ["93VQ", "Yavb", "dHrK"])
        with mock.patch.object(fakecloud.time, "monotonic", return_value=400.0):  # PV1 reads stopped
            payload = telemetry("noeP")
            self.assertEqual(fakecloud._without_live_blocks(payload, conn), payload)

    def test_before_any_pv2_answer_the_pv1_block_is_decoded_alone(self):
        from unittest import mock
        from src.siseli_local_bridge import fakecloud
        conn = self._h_conn(["WdRR", "2l0E", "Mpod"])
        with mock.patch.object(fakecloud.SolarParser, "parse_payload", return_value=True) as parse:
            for command in ("HGRID", "HOP", "HPV"):
                self._feed(conn, command)
        self.assertEqual(set(self._blocks_of(parse.call_args)), {"WdRR", "2l0E", "Mpod"})

    def test_qflag_is_asked_once_a_minute_on_the_poll_tick(self):
        import base64
        import binascii
        import json
        from types import SimpleNamespace
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, tcpstack
        conn = SimpleNamespace(reply=mock.Mock(), dtu_id="1", next_poll_ts=0.0, closed=False,
                               peer_ip="x", peer_port=1)
        sent = []

        def ci_of(call):
            frame = call[0][0]
            return json.loads(frame[frame.index(b"{"):])["b"].get("ci")

        with mock.patch.object(fakecloud, "TELEMETRY_POLL_INTERVAL_SEC", 15), \
                mock.patch.object(fakecloud, "LIVE_POLL_INTERVAL_SEC", 0), \
                mock.patch.dict(tcpstack.CONNECTIONS, {("a", 1, "b", 2): conn}, clear=True), \
                mock.patch.object(fakecloud.time, "monotonic", side_effect=[100.0, 116.0, 161.0]):
            for _ in range(3):
                conn.next_poll_ts = 0.0
                fakecloud.poll_due_connections()
        sent =[base64.b64decode(ci) for ci in map(ci_of, conn.reply.call_args_list) if ci]
        qflag = b"QFLAG" + binascii.crc_hqx(b"QFLAG", 0).to_bytes(2, "big") + b"\r"
        self.assertEqual(sent, [qflag, qflag])  # t=100 and t=161, not t=116

    def test_programme_22_and_25_switches_are_pi30_flags_y_and_z(self):
        import base64
        import binascii
        from src.siseli_local_bridge import fakecloud, mqtt
        for setting, flag in (("primary_source_interrupt_alarm", b"y"), ("fault_code_record", b"z")):
            for state, prefix in (("on", b"PE"), ("off", b"PD")):
                body = prefix + flag
                self.assertEqual(base64.b64decode(fakecloud.CONTROL_COMMANDS[setting][state]),
                                 body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r")
            self.assertIn(setting, mqtt._CONTROL_SWITCHES)
            self.assertIn(setting, mqtt._CONTROL_TELEMETRY_STATE)
        self.assertIn("power_saving_function", mqtt._CONTROL_TELEMETRY_STATE["eco"]["value_template"])

    def test_every_entity_name_has_a_french_translation(self):
        """LANGUAGE=fr: every sensor and control name, and every device group
        title, has a French entry -- a new entity cannot silently stay English."""
        from src.siseli_local_bridge import i18n, mqtt
        from src.siseli_local_bridge.sensors import SENSOR_GROUP_TITLES, SENSORS
        names = {mqtt._trim_section_prefix(str(meta["name"])) for meta in SENSORS.values()}
        for table in (mqtt._CONTROL_SWITCHES, mqtt._CONTROL_BUTTONS, mqtt._CONTROL_SELECTS, mqtt._CONTROL_NUMBERS):
            names |= {entry[0] for entry in table.values()}
        self.assertEqual(sorted(n for n in names if n not in i18n.FR_NAMES), [])
        self.assertEqual(sorted(t for t in SENSOR_GROUP_TITLES.values() if t not in i18n.FR_GROUP_TITLES), [])
        self.assertEqual(i18n.translate_name("Battery Voltage", "fr"), "Tension batterie")
        self.assertEqual(i18n.translate_name("Battery Voltage", "en"), "Battery Voltage")
        self.assertEqual(i18n.translate_name("Not A Name", "fr"), "Not A Name")

    def test_language_changes_the_name_only(self):
        from unittest import mock
        from src.siseli_local_bridge import mqtt
        with mock.patch.object(mqtt, "LANGUAGE", "fr"), mock.patch.object(mqtt, "ENTITY_PREFIX", "Siseli"):
            self.assertEqual(mqtt.display_sensor_name("Battery Status - Battery Voltage"), "Siseli Tension batterie")
            self.assertEqual(mqtt.device_info("battery")["name"].split()[-1], "Batterie")
            self.assertEqual(mqtt.device_info("battery")["identifiers"], [mqtt.device_id_for_group("battery")])
        with mock.patch.object(mqtt, "LANGUAGE", "en"), mock.patch.object(mqtt, "ENTITY_PREFIX", "Siseli"):
            self.assertEqual(mqtt.display_sensor_name("Battery Status - Battery Voltage"), "Siseli Battery Voltage")

    def test_french_control_names_show_the_programme(self):
        """Every control with a front-panel programme shows it in French, once."""
        from unittest import mock
        from src.siseli_local_bridge import i18n, mqtt
        tables = (mqtt._CONTROL_SWITCHES, mqtt._CONTROL_BUTTONS, mqtt._CONTROL_SELECTS,
                  mqtt._CONTROL_NUMBERS, mqtt._CONTROL_HOUR_SELECTS)
        settings = {setting for table in tables for setting in table}
        self.assertEqual(sorted(set(i18n.CONTROL_PROGRAMMES) - settings), [])
        with mock.patch.object(mqtt, "LANGUAGE", "fr"), mock.patch.object(mqtt, "ENTITY_PREFIX", None):
            self.assertEqual(mqtt.display_control_name("buzzer", "Buzzer"), "Buzzer (prog 18)")
            self.assertEqual(mqtt.display_control_name("grid_regulation_mode", "Grid Regulation Mode"),
                             "Mode réseau (prog 50)")
            self.assertEqual(mqtt.display_control_name("refresh_telemetry", "Refresh Telemetry"),
                             "Actualiser les données")
        with mock.patch.object(mqtt, "LANGUAGE", "en"), mock.patch.object(mqtt, "ENTITY_PREFIX", None):
            self.assertEqual(mqtt.display_control_name("buzzer", "Buzzer"), "Buzzer")

    def test_french_sensor_names_show_the_programme(self):
        from unittest import mock
        from src.siseli_local_bridge import i18n, mqtt
        from src.siseli_local_bridge.sensors import SENSORS
        self.assertEqual(sorted(set(i18n.SENSOR_PROGRAMMES) - set(SENSORS)), [])
        with mock.patch.object(mqtt, "LANGUAGE", "fr"), mock.patch.object(mqtt, "ENTITY_PREFIX", None):
            self.assertEqual(mqtt.display_sensor_name("Settings - Buzzer Function", "18"),
                             "Fonction buzzer (prog 18)")
            self.assertEqual(mqtt.display_sensor_name("Settings - Grid Regulation Mode", "50"),
                             "Mode réseau (prog 50)")
            self.assertEqual(mqtt.display_sensor_name("Battery Status - Battery Voltage"), "Tension batterie")
        with mock.patch.object(mqtt, "LANGUAGE", "en"), mock.patch.object(mqtt, "ENTITY_PREFIX", None):
            self.assertEqual(mqtt.display_sensor_name("Settings - Buzzer Function", "18"), "Buzzer Function")

    def test_max_charging_current_sends_the_captured_frames(self):
        """Programme 02, 2026-09-27: the vendor app's MNCHGC frames; the
        inverter NAKed anything off the 10 A grid."""
        import base64
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt
        sent = []
        with mock.patch.object(fakecloud, "_send_control_ci", side_effect=lambda ci: sent.append(ci) or True):
            for amps in (50, 60):
                self.assertTrue(fakecloud.send_control_number("max_charging_current", amps))
            for amps in (58, 59, 61, 62, 0, 160):
                self.assertFalse(fakecloud.send_control_number("max_charging_current", amps))
        self.assertEqual([base64.b64decode(ci) for ci in sent],
                         [b"MNCHGC050\x81}\r", b"MNCHGC060\xd4.\r"])
        self.assertIn("max_charging_current", mqtt._CONTROL_NUMBERS)
        self.assertIn("maximum_total_charging_current_a",
                      mqtt._CONTROL_TELEMETRY_STATE["max_charging_current"]["value_template"])

    def test_grid_tie_current_sends_the_captured_frames(self):
        """Programme 56, 2026-09-27: the vendor app's PGFC frames, and the
        93VQ token 17 read-back they moved."""
        import base64
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt
        sent = []
        with mock.patch.object(fakecloud, "_send_control_ci", side_effect=lambda ci: sent.append(ci) or True):
            for amps in (6, 5, 4):
                self.assertTrue(fakecloud.send_control_number("grid_tie_current", amps))
            for amps in (2, 3, 41):  # 2 A NAKed by the inverter; 40 A is the app's max
                self.assertFalse(fakecloud.send_control_number("grid_tie_current", amps))
        self.assertEqual([base64.b64decode(ci) for ci in sent],
                         [b"PGFC006\xf7\xce\r", b"PGFC005\xc7\xad\r", b"PGFC004\xd7\x8c\r"])
        self.assertIn("grid_tie_current", mqtt._CONTROL_NUMBERS)
        vq = b"(1 060 002 10611110240 002 0 1 1 0 1 015 025 030 020 056.4 056.4 042.0 006 0 0 \r"
        self.assertEqual(SolarParser._try_ascii_schema({"93VQ": vq})["grid_connected_current_a"], 6)
        entry = mqtt._CONTROL_TELEMETRY_STATE["grid_tie_current"]
        self.assertIn("grid_connected_current_a", entry["value_template"])

    def test_second_output_thresholds_build_the_frames(self):
        """Programmes 62, 64 (proven by a write from Home Assistant, 2026-10-04),
        65 and 66 (channels seen in captures of the vendor app). Programmes 61
        and 63 (voltages) are deliberately not offered."""
        import base64
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt
        sent = []
        with mock.patch.object(fakecloud, "_send_control_ci", side_effect=lambda ci: sent.append(ci) or True):
            self.assertTrue(fakecloud.send_control_number("second_output_cutoff_soc", 20))
            self.assertTrue(fakecloud.send_control_number("second_output_restore_soc", 50))
            self.assertTrue(fakecloud.send_control_number("second_output_discharge_time", 975))
            self.assertTrue(fakecloud.send_control_number("second_output_discharge_time", 0))
            self.assertTrue(fakecloud.send_control_number("second_output_delay_time", 10))
            for name, bad in (("second_output_cutoff_soc", 22), ("second_output_cutoff_soc", 100),
                              ("second_output_restore_soc", 21), ("second_output_restore_soc", 105),
                              ("second_output_discharge_time", 974), ("second_output_discharge_time", 995),
                              ("second_output_delay_time", 61), ("second_output_delay_time", -1)):
                self.assertFalse(fakecloud.send_control_number(name, bad), (name, bad))
        frames = [base64.b64decode(ci) for ci in sent]
        self.assertEqual([f[:-3] for f in frames],
                         [b"PDSDS020", b"PDSRS050", b"PDDCGT0975", b"PDDCGT0000", b"PDDLYT010"])
        for name in ("second_output_cutoff_soc", "second_output_restore_soc",
                     "second_output_discharge_time", "second_output_delay_time"):
            self.assertIn(name, mqtt._CONTROL_NUMBERS)
            self.assertIn(name, mqtt._CONTROL_TELEMETRY_STATE)
        self.assertNotIn("second_output_restore_voltage", fakecloud.NUMBER_SETTINGS)
        self.assertIn("second_output_restore_voltage", mqtt._WITHDRAWN_CONTROL_NUMBERS)
        # the number entities read a bare figure from the "N min" sensors
        template = mqtt._CONTROL_TELEMETRY_STATE["second_output_delay_time"]["value_template"]
        self.assertIn("replace(' min', '') | int", template)
    def test_second_output_discharge_time_is_the_last_digits_of_the_last_dhrk_token(self):
        """Programme 65 lives in the last three digits of dHrK's last token:
        "20975" = capacity 20 % + 975 min (what the vendor app showed on
        2026-09-12), "50000" = 50 % + 0 (disabled). Token 6 never moved."""
        def block(last):
            return ("(0 044.0 015 044.0 044.0 048.0 0 056.0 060 120 030 0000 0000 05 0000 52.0 "
                    + last + "\r").encode()
        state = SolarParser._try_ascii_schema({"dHrK": block("20975")})
        self.assertEqual(state["second_output_discharge_time"], "975 min")
        self.assertEqual(state["second_output_battery_capacity"], 20)
        state = SolarParser._try_ascii_schema({"dHrK": block("50000")})
        self.assertEqual(state["second_output_discharge_time"], "0 min")
        self.assertEqual(state["second_output_battery_capacity"], 50)
        self.assertEqual(SolarParser._try_ascii_schema({"dHrK": block("55005")})["second_output_discharge_time"], "5 min")
    def test_labels_are_the_select_options(self):
        """grid_working_range's HA select reads this key back verbatim."""
        from src.siseli_local_bridge import mqtt
        _, _, options = mqtt._CONTROL_SELECTS["grid_working_range"]
        self.assertEqual(set(options), {"UPS", "Appliance (APL)"})
        self.assertIn("grid_working_range", mqtt._CONTROL_TELEMETRY_STATE)
        for switch in ("buzzer", "backlight"):
            with self.subTest(switch):
                entry = mqtt._CONTROL_TELEMETRY_STATE[switch]
                self.assertEqual((entry["state_on"], entry["state_off"]), ("On", "Off"))


class TestDecodedFromMeasuredEvidence(_ParserTestCase):
    """The other direction: values that were missing and are now genuinely decoded."""

    def test_bms_average_temperature_comes_from_deci_kelvin(self):
        """Token 8 is tenths of a Kelvin. 3041/10 - 273.15 = 30.95, digit for digit
        what the vendor portal reports for this device."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertEqual(state["bms_avg_temp_c"], 30.95)

    def test_rated_apparent_power_is_the_load_percentage_denominator(self):
        """Constant 11000 on both devices. The owner confirms 11 kW is the maximum
        output, and apparent_va / 11000 reproduces load_pct on every capture."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertEqual(state["rated_apparent_va"], 11000)
        self.assertEqual(
            int(state["apparent_va"] / state["rated_apparent_va"] * 100),
            state["load_pct"],
        )


class TestRangeGuards(_ParserTestCase):
    def test_relay_status_is_not_taken_from_a_numeric_token(self):
        """WdRR token 8 is a number -- it reads 11000, 08969 and 07969 across captures.
        Taking its leading character made the 07969 payload report the relay Off while
        the same payload reported 4055 W delivered to the load."""
        state = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertNotIn("main_output_relay_status", state)
        # The raw token is still published as the artefact to work from.
        self.assertEqual(state["wdrr_status_bits"], "11000")

    def test_overload_percentage_is_published(self):
        """Above 100% is exactly the reading a user needs, and it was discarded --
        leaving the last sub-100 value on screen."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.SYNTH_2L0E_OVERLOADED})
        self.assertEqual(state["load_pct"], 115)

    def test_parse_garbage_is_still_rejected(self):
        """Widening the guard must not let a mis-indexed token through."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertEqual(state["load_pct"], 7)

    def test_absurd_bms_current_is_rejected(self):
        """Unbounded current times voltage, accumulated into a total_increasing
        sensor, can never be corrected downward."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.SYNTH_YAVB_ABSURD_CURRENT})
        self.assertNotIn("bms_charging_current_a", state)
        self.assertEqual(state["bms_discharge_current_a"], 0.0)

    def test_absurd_grid_power_is_rejected(self):
        """The only unguarded input to a total_increasing counter. _accumulate_kwh is
        monotonic, so one malformed token latched the Energy Dashboard permanently."""
        good = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertEqual(good["mains_wdrr_value"], 0)
        self.assertEqual(good["mains_power_w"], 0)

        with mock.patch("src.siseli_local_bridge.parsers.log_kv"):
            bad = SolarParser._try_ascii_schema({"WdRR": captures.SYNTH_WDRR_ABSURD_POWER})
        for key in ("mains_wdrr_value", "mains_wdrr_abs", "mains_power_w", "c_mains_power_w"):
            with self.subTest(key=key):
                self.assertNotIn(key, bad)
        # The raw token survives as a diagnostic, exactly as bat_series_count does.
        self.assertEqual(bad["mains_wdrr_token"], "+999999999")

    def test_a_rejected_grid_value_never_reaches_the_energy_counter(self):
        """Dropping the key must skip the grid domain rather than integrate a zero."""
        shared_state.LAST_STATE["c_grid_import_energy_kwh"] = 46.732669
        with mock.patch("src.siseli_local_bridge.parsers.log_kv"):
            state = SolarParser._try_ascii_schema({"WdRR": captures.SYNTH_WDRR_ABSURD_POWER})
        self.assertNotIn("c_grid_import_energy_kwh", state)
        self.assertNotIn("c_grid_import_power_w", state)

    def test_the_rejection_is_reported(self):
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            SolarParser._try_ascii_schema({"WdRR": captures.SYNTH_WDRR_ABSURD_POWER})
        self.assertTrue(logged.called)
        self.assertEqual(logged.call_args[0][0], "[GRID VALUE REJECTED]")

    def test_cell_list_stops_at_the_first_out_of_range_cell(self):
        """Skipping it renumbered every later cell, so cell_3_mv reported physical
        cell 4's voltage."""
        state = SolarParser._try_ascii_schema({"v09K": captures.SYNTH_V09K_CELL_3_COLLAPSED})
        self.assertEqual(state["bms_cell_count"], 2)
        self.assertEqual(state["cell_1_mv"], 3321)
        self.assertEqual(state["cell_2_mv"], 3321)
        self.assertNotIn("cell_3_mv", state)


class TestCalculatedEnergyFamily(_ParserTestCase):
    """Battery and grid had integrated counters; generation and load did not, so two
    of the four scaled power sensors had no energy partner on the same basis. The
    device's own pv_*_kwh counters are per-inverter and cannot fill that role."""

    def _run_an_hour(self, **power):
        now = 1000.0
        with mock.patch("src.siseli_local_bridge.parsers.log_kv"):
            for _ in range(13):  # the first call only establishes the baseline
                state = dict(power)
                SolarParser._apply_energy_dashboard_calculations(state, now_ts=now)
                shared_state.LAST_STATE.update(state)
                now += 300.0
        return state

    def test_generation_energy_integrates_the_scaled_power(self):
        state = self._run_an_hour(c_generation_power_w=4110)
        self.assertAlmostEqual(state["c_generation_energy_kwh"], 4.110, places=3)

    def test_load_energy_integrates_the_scaled_power(self):
        state = self._run_an_hour(c_load_w=1796)
        self.assertAlmostEqual(state["c_load_energy_kwh"], 1.796, places=3)

    def test_each_domain_keeps_its_own_clock(self):
        """A shared clock plus per-domain gating loses energy: a payload carrying only
        one domain would consume the interval another was waiting for."""
        with mock.patch("src.siseli_local_bridge.parsers.log_kv"):
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 100}, now_ts=0.0)
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 100}, now_ts=60.0)
            self.assertEqual(parser_module.LAST_ENERGY_TS.get("load"), 60.0)
            self.assertIsNone(parser_module.LAST_ENERGY_TS.get("generation"))

            state = {"c_generation_power_w": 600}
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=60.0)
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=120.0)
        # 600 W across the full 60 s the generation clock waited, not a truncated slice.
        self.assertAlmostEqual(
            state["c_generation_energy_kwh"], 600 * 60 / 3_600_000, places=6
        )

    def test_a_payload_without_the_power_writes_no_energy(self):
        """Same gating rule as battery and grid: evidence in this payload or nothing."""
        with mock.patch("src.siseli_local_bridge.parsers.log_kv"):
            state = {"mains_wdrr_value": 100}
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=0.0)
        self.assertNotIn("c_generation_energy_kwh", state)
        self.assertNotIn("c_load_energy_kwh", state)


class TestEnergyIntegrationWindow(_ParserTestCase):
    """The integrator must credit the interval the inverter actually reported over.

    _energy_dt_seconds used to bound dt at max(UPDATE_INTERVAL_SEC * 6, 60), which is
    60 s at the shipped default. Payloads arrive every ~300 s with observed 600 s
    gaps, so the bound fired on every single step and all three kWh counters accrued a
    fifth of the real energy -- silently, and with the whole suite passing, because no
    test ever drove an interval longer than the bound.
    """

    #: The live capture: 52.6 V x 104.3 A, exactly as published.
    DISCHARGE_W = 5486.0

    def setUp(self):
        super().setUp()
        parser_module.ENERGY_DT_CLAMP_LOGGED = False

    def test_a_real_300s_interval_is_credited_in_full(self):
        parser_module.LAST_ENERGY_TS["battery"] = 1000.0
        self.assertEqual(SolarParser._energy_dt_seconds("battery", 1300.0), 300.0)

    def test_the_observed_600s_gap_is_credited_in_full(self):
        parser_module.LAST_ENERGY_TS["grid"] = 1000.0
        self.assertEqual(SolarParser._energy_dt_seconds("grid", 1600.0), 600.0)

    def test_an_abnormal_jump_is_still_bounded(self):
        """A clock jump or a suspended process must not credit a fabricated block."""
        parser_module.LAST_ENERGY_TS["battery"] = 1000.0
        with mock.patch("src.siseli_local_bridge.parsers.log_kv"):
            dt = SolarParser._energy_dt_seconds("battery", 1000.0 + 6 * 3600)
        self.assertEqual(dt, float(parser_module.ENERGY_MAX_DT_SEC))

    def test_the_bound_rises_with_a_slower_measured_cadence(self):
        """An inverter reporting every 15 min must not be truncated at 1200 s."""
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1000.0 + 900.0)
        self.assertEqual(SolarParser._energy_max_dt(), 1800.0)

    def test_the_bound_is_capped(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1000.0 + 8 * 3600)
        self.assertEqual(
            SolarParser._energy_max_dt(), float(parser_module.TELEMETRY_TIMEOUT_CEILING_SEC)
        )

    def test_an_hour_of_discharge_records_an_hour_of_energy(self):
        """The regression in the units a user reads.

        Twelve payloads at the measured 300 s cadence, at the discharge power from the
        live capture. The old 60 s bound produced 1.097 kWh for the same hour.
        """
        state = {}
        parser_module.LAST_ENERGY_TS.clear()
        now = 1000.0
        for _ in range(13):  # first call is the baseline, so 12 integration steps
            dt = SolarParser._energy_dt_seconds("battery", now)
            SolarParser._accumulate_kwh(state, "c_battery_discharge_energy_kwh", self.DISCHARGE_W, dt)
            shared_state.LAST_STATE.update(state)
            now += 300.0

        self.assertAlmostEqual(state["c_battery_discharge_energy_kwh"], 5.486, places=3)

    def test_an_abnormal_gap_is_reported_once(self):
        parser_module.LAST_ENERGY_TS["battery"] = 1000.0
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            SolarParser._energy_dt_seconds("battery", 1000.0 + 6 * 3600)
            parser_module.LAST_ENERGY_TS["battery"] = 1000.0
            SolarParser._energy_dt_seconds("battery", 1000.0 + 6 * 3600)
        self.assertEqual(logged.call_count, 1)

    def test_the_window_does_not_depend_on_the_publish_throttle(self):
        """UPDATE_INTERVAL_SEC is an MQTT throttle. Deriving the integration window
        from it is what caused the undercount, and it is Supervisor-pinned, so no
        default change could have fixed an existing install."""
        with patch_consts("src.siseli_local_bridge.parsers", UPDATE_INTERVAL_SEC=1):
            parser_module.LAST_ENERGY_TS["battery"] = 1000.0
            self.assertEqual(SolarParser._energy_dt_seconds("battery", 1300.0), 300.0)


class TestEnergyDomainGating(_ParserTestCase):
    def test_grid_only_payload_does_not_zero_battery_power(self):
        """One combined gate would let this through, find no battery current, and
        write 0 W -- flapping battery power to zero on every grid-only payload."""
        state = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertNotIn("c_battery_charge_power_w", state)
        self.assertNotIn("c_battery_discharge_power_w", state)
        self.assertEqual(state["c_grid_import_power_w"], 0)

    def test_identity_payload_writes_no_calculated_values(self):
        """The real second payload from a live install. It carries no battery voltage
        or current at all, yet it published a changed energy total.

        The PV keys are seeded too, and that is the point: this test seeded only the
        battery pair until 2.6.16, so it passed while generation quietly kept a
        LAST_STATE fallback and republished the previous payload's PV power on every
        identity payload. A gate is only worth what its test seeds.
        """
        shared_state.LAST_STATE.update(
            {"bat_v": 53.4, "bms_charging_current_a": 29.1, "pv_w": 0, "pv2_power_w": 1403}
        )
        parser_module.LAST_ENERGY_TS["battery"] = 100.0
        parser_module.LAST_ENERGY_TS["generation"] = 100.0

        state = SolarParser._try_ascii_schema(dict(captures.CAPTURE_IDENTITY))

        self.assertEqual([k for k in state if k.startswith("c_")], [])
        self.assertNotIn("generation_power_w", state)
        self.assertEqual(
            parser_module.LAST_ENERGY_TS.get("battery"), 100.0, "battery clock must not advance"
        )
        self.assertEqual(
            parser_module.LAST_ENERGY_TS.get("generation"), 100.0, "generation clock must not advance"
        )

    def test_the_two_clocks_are_independent(self):
        """A shared clock plus per-domain gating loses energy: a grid-only payload
        would consume the interval the battery integrator needed."""
        SolarParser._apply_energy_dashboard_calculations({"mains_wdrr_value": 100}, now_ts=0.0)
        SolarParser._apply_energy_dashboard_calculations({"mains_wdrr_value": 100}, now_ts=60.0)

        self.assertEqual(parser_module.LAST_ENERGY_TS.get("grid"), 60.0)
        self.assertIsNone(parser_module.LAST_ENERGY_TS.get("battery"))

        state = {"bat_v": 50.0, "bms_charging_current_a": 10.0}
        SolarParser._apply_energy_dashboard_calculations(state, now_ts=60.0)
        SolarParser._apply_energy_dashboard_calculations(state, now_ts=120.0)
        # 500 W across the full 60 s the battery clock waited, not a truncated slice.
        self.assertAlmostEqual(
            state["c_battery_charge_energy_kwh"], 500 * 60 / 3_600_000, places=6
        )

    def test_legacy_current_fallback_is_scaled_like_every_other_sensor(self):
        """BMS figures are whole-bank; 2ONL figures are per-inverter. Without scaling
        the fallback, reported power steps whenever the BMS block is absent."""
        state = {"bat_v": 48.0, "bat_charge_current": 5.0, "dischg_current": 4.0}
        with mock.patch("src.siseli_local_bridge.parsers.INVERTER_COUNT", 2):
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        self.assertEqual(state["c_battery_charge_power_w"], 480)
        self.assertEqual(state["c_battery_discharge_power_w"], 384)

    def test_cached_current_no_longer_overrides_a_fresh_reading(self):
        """A cached BMS current used to win over a fresh inverter current in the same
        payload, integrating a stale rate indefinitely."""
        shared_state.LAST_STATE.update({"bms_charging_current_a": 29.1})
        state = {"bat_v": 53.4, "bat_charge_current": 0.0, "dischg_current": 0.0}
        with mock.patch("src.siseli_local_bridge.parsers.INVERTER_COUNT", 1):
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        self.assertEqual(state["c_battery_charge_power_w"], 0)

    def test_disagreeing_current_sources_are_logged_not_silently_resolved(self):
        """No ground truth exists -- the official app displays both and they differ by
        about 2x on the reference unit and 4x on another."""
        state = {"bat_v": 53.4, "bms_charging_current_a": 29.1, "bat_charge_current": 7.0}
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        tags = [call.args[0] for call in logged.call_args_list if call.args]
        self.assertIn("[ENERGY SOURCE DISAGREEMENT]", tags)
        self.assertEqual(state["c_battery_charge_power_w"], round(53.4 * 29.1))


class TestLiveCaptureParity(_ParserTestCase):
    """The full telemetry payload from a live 2x-parallel install must still decode to
    exactly the values that installation published."""

    def test_telemetry_capture_decodes_to_the_recorded_values(self):
        with mock.patch("src.siseli_local_bridge.parsers.INVERTER_COUNT", 2):
            state = SolarParser._try_ascii_schema(dict(captures.CAPTURE_TELEMETRY))

        for key, expected in captures.EXPECTED_TELEMETRY.items():
            with self.subTest(key=key):
                self.assertEqual(state.get(key), expected)

        for key, expected in captures.EXPECTED_TELEMETRY_SCALED.items():
            with self.subTest(key=key):
                self.assertEqual(state.get(key), expected)

    def test_identity_capture_decodes_to_the_recorded_values(self):
        state = SolarParser._try_ascii_schema(dict(captures.CAPTURE_IDENTITY))
        for key, expected in captures.EXPECTED_IDENTITY.items():
            with self.subTest(key=key):
                self.assertEqual(state.get(key), expected)


class TestEnergyIsImmuneToAClockStep(unittest.TestCase):
    """A Raspberry Pi has no RTC. It boots with a wrong clock and NTP steps it, which
    is routine on the reference platform -- and the integrator used to measure its
    interval on the wall clock, so a step was indistinguishable from elapsed time.

    _energy_max_dt bounded the damage at ENERGY_MAX_DT_SEC but did not prevent it: a
    step credited up to 1200 s of the current power into five total_increasing counters,
    which round(max(previous, total)) makes permanent. At 5 kW that is 1.67 kWh that can
    never come back down. Durations are measured on time.monotonic() now, so the step
    cannot be seen at all.
    """

    def setUp(self):
        self._ctx = isolated_state()
        self._ctx.__enter__()
        self.addCleanup(lambda: self._ctx.__exit__(None, None, None))
        parser_module.LAST_ENERGY_TS.clear()

    def test_a_wall_clock_jump_credits_nothing(self):
        with mock.patch.object(parser_module.time, "monotonic", return_value=1000.0):
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 5000})

        # Wall clock leaps four hours; the monotonic clock advances a normal interval.
        state = {"c_load_w": 5000}
        with mock.patch.object(parser_module.time, "time", return_value=1e9), mock.patch.object(
            parser_module.time, "monotonic", return_value=1300.0
        ):
            SolarParser._apply_energy_dashboard_calculations(state)

        expected = 5000 * 300 / 3_600_000.0
        self.assertAlmostEqual(state["c_load_energy_kwh"], expected, places=5)
        self.assertLess(
            state["c_load_energy_kwh"], 0.5, "a clock step is being credited as elapsed time"
        )

    def test_the_integrator_reads_the_monotonic_clock(self):
        """Pins the clock source itself. Measuring durations on the wall clock is the
        defect, and it is invisible in behaviour until a step happens."""
        seen = []
        with mock.patch.object(
            parser_module.time, "monotonic", side_effect=lambda: seen.append(1) or 500.0
        ):
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 100})
        self.assertTrue(seen, "the energy path no longer consults the monotonic clock")


class TestNoModuleMeasuresADurationOnTheWallClock(unittest.TestCase):
    """A source pin, because a partial migration is invisible in behaviour until a
    clock steps -- and the whole suite would still pass. Moving record_telemetry to
    monotonic while telemetry_is_fresh stayed on the wall clock would make now - last
    about 1.7e9 and read every entity Unavailable forever."""

    SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "siseli_local_bridge"

    def test_no_runtime_module_calls_time_time(self):
        for name in ("core.py", "parsers.py", "state.py", "mqtt.py", "pi30.py"):
            with self.subTest(module=name):
                text = (self.SRC / name).read_text(encoding="utf-8")
                self.assertNotIn(
                    "time.time()",
                    text,
                    f"{name} measures a duration on a clock that can step; use time.monotonic()",
                )

    def test_the_human_readable_timestamp_is_still_a_wall_clock(self):
        """The one correct wall-clock use: the time a person reads in the log."""
        text = (self.SRC / "parsers.py").read_text(encoding="utf-8")
        self.assertIn("datetime.now().strftime", text)


class TestBatteryCurrentGuard(unittest.TestCase):
    """The guard used to reject anything over 300 A, silently.

    300 is below what this hardware declares for itself -- the reference device reports
    bms_charge_current_limit_a of 390 -- so a real reading between the two was thrown
    away. Silently is the worse half: the key simply went absent, _battery_current then
    picked whichever source survived without the [ENERGY SOURCE DISAGREEMENT] warning
    (which needs both present), and if both were dropped the powers read 0 and
    battery_status reported Idle. A high-current moment presented as an idle battery.
    """

    def setUp(self):
        self._ctx = isolated_state()
        self._ctx.__enter__()
        self.addCleanup(lambda: self._ctx.__exit__(None, None, None))
        parser_module.BATTERY_CURRENT_REJECTED_LOGGED = False

    def test_a_current_the_device_itself_allows_is_kept(self):
        """390 A is Yavb[4] on the reference device -- its own declared charge limit.
        Rejected by the old 300 A bound."""
        for amps in (301.0, 350.0, 390.0, 600.0):
            with self.subTest(amps=amps):
                self.assertEqual(
                    SolarParser._plausible_current(amps, "bms_charging_current_a"), amps
                )

    def test_an_impossible_current_is_still_rejected(self):
        for amps in (-1.0, 9999.0, 100000.0):
            with self.subTest(amps=amps):
                parser_module.BATTERY_CURRENT_REJECTED_LOGGED = False
                self.assertIsNone(SolarParser._plausible_current(amps, "field"))

    def test_the_synthetic_absurd_block_is_still_rejected(self):
        """The only existing test of these guards. 9999 A must not become a reading."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.SYNTH_YAVB_ABSURD_CURRENT})
        self.assertNotIn("bms_charging_current_a", state)

    def test_a_rejection_is_reported_once_not_per_payload(self):
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            for _ in range(4):
                SolarParser._plausible_current(9999.0, "bms_charging_current_a")
        tags = [c.args[0] for c in logged.call_args_list if c.args]
        self.assertEqual(tags.count("[BATTERY CURRENT REJECTED]"), 1)

    def test_the_rejection_names_the_field_and_the_bound(self):
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            SolarParser._plausible_current(9999.0, "bms_discharge_current_a")
        call = next(c for c in logged.call_args_list if c.args and c.args[0] == "[BATTERY CURRENT REJECTED]")
        self.assertEqual(call.kwargs["level"], "warning")
        self.assertEqual(call.kwargs["field"], "bms_discharge_current_a")
        self.assertEqual(call.kwargs["max_a"], parser_module.BATTERY_CURRENT_MAX_A)

    def test_the_bound_clears_the_limit_the_hardware_declares(self):
        """Pins the reason for the number rather than the number. 390 A is the highest
        limit any capture has shown; a bound at or below it rejects real readings."""
        self.assertGreater(parser_module.BATTERY_CURRENT_MAX_A, 390)


class TestPublishOutcomeIsReportedHonestly(unittest.TestCase):
    """2.6.21 initialised the publish outcome INSIDE `if DISCOVERY_PUBLISHED:` and read
    it outside. On a broker that had never connected -- the exact install the change was
    written for -- every payload raised UnboundLocalError, which the handler swallowed as
    [PARSER ERROR], so a perfectly good decode was reported as a failure and
    record_telemetry never ran.
    """

    def setUp(self):
        self._ctx = isolated_state()
        self._ctx.__enter__()
        self.addCleanup(lambda: self._ctx.__exit__(None, None, None))

    def _parse(self):
        return SolarParser.parse_payload(envelope(captures.CAPTURE_TELEMETRY))

    def test_a_payload_decodes_when_the_broker_has_never_connected(self):
        shared_state.DISCOVERY_PUBLISHED = False
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            self.assertTrue(self._parse(), "a good payload must not be reported as a failure")
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertIn("no broker connection yet", said)

    def test_the_never_connected_case_is_not_called_throttled(self):
        """Hoisting the default above the gate would have swapped the crash for a new
        false statement: nothing is being throttled when nothing can be published."""
        shared_state.DISCOVERY_PUBLISHED = False
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            self._parse()
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertNotIn("throttled", said)

    def test_every_outcome_has_a_label(self):
        outcomes = set(parser_module.PUBLISH_OUTCOMES)
        self.assertEqual(
            outcomes,
            {"sent", "throttled", "broker-unreachable", "no-broker-yet", "flushed", "flush-unreachable"},
        )
        self.assertEqual(parser_module.PUBLISH_OUTCOMES["sent"], "Published to HA")
        for label in parser_module.PUBLISH_OUTCOMES.values():
            self.assertTrue(label.strip(), "every outcome needs a readable label")


class TestPublishThrottle(unittest.TestCase):
    """UPDATE_INTERVAL_SEC never suppressed a publish: the gate was
    `changed or interval elapsed`, and something always changed."""

    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        shared_state.LAST_STATE.clear()
        shared_state.DISCOVERY_PUBLISHED = True
        parser_module.LAST_ENERGY_TS.clear()
        parser_module.PENDING_PUBLISH = False

        self.publish_state = mock.Mock()
        self.publish_discovery = mock.Mock()
        p = mock.patch.object(
            parser_module, "_get_mqtt_publish",
            return_value=(self.publish_discovery, self.publish_state),
        )
        p.start()
        self.addCleanup(p.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        c = mock.patch.object(parser_module, "STATE_CACHE_FILE", os.path.join(self.tmp.name, "state.json"))
        c.start()
        self.addCleanup(c.stop)

    def _payload(self, bat_cap):
        # Derived from the real capture so the framing stays byte-faithful.
        return envelope(
            {"2ONL": captures.BLOCK_2ONL_CHARGING.replace(b"058", b"0%d" % bat_cap)}
        )

    def test_a_change_inside_the_window_is_deferred_not_dropped(self):
        parser_module.LAST_PUBLISH_TS = 1000.0
        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1002.0):
            SolarParser.parse_payload(self._payload(58))
            SolarParser.parse_payload(self._payload(59))
        self.publish_state.assert_not_called()
        self.assertTrue(parser_module.PENDING_PUBLISH, "the change must be remembered")

        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1011.0):
            SolarParser.parse_payload(self._payload(60))
        self.publish_state.assert_called_once()
        self.assertFalse(parser_module.PENDING_PUBLISH)

    def test_heartbeat_republishes_when_nothing_changes(self):
        """Without this, a steady inverter publishes nothing and expire_after marks
        every entity unavailable."""
        parser_module.LAST_PUBLISH_TS = 1000.0
        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1000.0):
            SolarParser.parse_payload(self._payload(58))
        self.publish_state.reset_mock()
        parser_module.PENDING_PUBLISH = False
        parser_module.LAST_PUBLISH_TS = 1000.0

        # Same values again, one full heartbeat interval later (600 // 3 = 200 s).
        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1201.0):
            SolarParser.parse_payload(self._payload(58))
        self.publish_state.assert_called_once()

    def test_payload_with_no_recognised_blocks_reports_failure(self):
        self.assertFalse(SolarParser.parse_payload(envelope({"ZZZZ": b"(1 2 3)"})))
        self.publish_state.assert_not_called()


class TestForeignProtocolIsDiagnosed(_ParserTestCase):
    """Issue #30: a Beve Mega 6kW published fifteen block names this add-on has never
    seen, carrying binary Modbus RTU instead of ASCII tokens. The parser did the right
    thing and decoded nothing -- but said so only through an info-level line that reads
    identically to a known block with a truncated token list, and only when a debug
    flag was on. The reporter saw ~200 entities reading Unknown and no cause.

    Decoding this device is out of scope. Being able to tell its owner what happened
    is not.
    """

    def setUp(self):
        super().setUp()
        parser_module.UNSUPPORTED_PROTOCOL_LOGGED = False

    def _warnings(self, tag, payload):
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            result = SolarParser.parse_payload(payload)
        self.assertFalse(result)
        return [c for c in logged.call_args_list if c.args and c.args[0] == tag]

    def test_a_foreign_device_is_named_as_unsupported(self):
        calls = self._warnings(
            "[UNSUPPORTED PROTOCOL]", envelope(captures.CAPTURE_DEVICE_B_FOREIGN)
        )
        self.assertEqual(len(calls), 1)
        reported = calls[0].kwargs
        self.assertEqual(reported["level"], "warning", "a debug flag must not be needed to see this")
        self.assertEqual(reported["recognised"], 0)
        self.assertEqual(reported["block_count"], len(captures.CAPTURE_DEVICE_B_FOREIGN))
        self.assertEqual(reported["body"], "binary")
        self.assertEqual(reported["looks_like"], "modbus_rtu")

    def test_the_verdict_is_said_once_not_on_every_payload(self):
        """The device republishes every few seconds. A per-payload warning would bury
        the log it is meant to make readable."""
        payload = envelope(captures.CAPTURE_DEVICE_B_FOREIGN)
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            for _ in range(5):
                SolarParser.parse_payload(payload)
        tags = [c.args[0] for c in logged.call_args_list if c.args]
        self.assertEqual(tags.count("[UNSUPPORTED PROTOCOL]"), 1)

    def test_recognised_blocks_that_yield_nothing_take_the_other_branch(self):
        """A truncated known block is a different fault from a foreign device, and the
        old message could not tell them apart."""
        calls = self._warnings("[NO VALUES DECODED]", envelope({"2l0E": b"("}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["recognised"], 1)
        self.assertNotIn("looks_like", calls[0].kwargs)

    def test_a_supported_device_is_never_called_unsupported(self):
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            SolarParser.parse_payload(envelope(captures.CAPTURE_TELEMETRY))
        tags = [c.args[0] for c in logged.call_args_list if c.args]
        self.assertNotIn("[UNSUPPORTED PROTOCOL]", tags)
        self.assertNotIn("[NO VALUES DECODED]", tags)

    def test_the_modbus_hint_needs_more_than_a_foreign_name(self):
        """An ASCII foreign block must not be labelled Modbus -- the CRC is the whole
        basis for naming a protocol in a log line, and without it this is a guess."""
        described = SolarParser._describe_foreign_blocks({"ZZZZ": b"(1 2 3"})
        self.assertEqual(described["body"], "ascii")
        self.assertNotIn("looks_like", described)

    def test_the_crc16_xmodem_helper_matches_known_frames(self):
        """Hand-computed from real wire bytes. Pins the polynomial, the init value and
        -- the part that is easy to tidy away -- that the CRC covers the leading paren.
        Excluding it matches nothing at all."""
        self.assertEqual(SolarParser._crc16_xmodem(b"(PI30"), 0x9A0B)
        self.assertEqual(SolarParser._crc16_xmodem(b"(NAK"), 0x7373)
        self.assertEqual(SolarParser._crc16_xmodem(b"(VMIII-4000"), 0xDE93)
        self.assertNotEqual(SolarParser._crc16_xmodem(b"PI30"), 0x9A0B)

    def test_an_ascii_device_with_a_checksum_is_not_called_binary(self):
        """Issue #32, the defect this fixes. 2.6.17 called a plainly-textual device
        binary because of a two-byte CRC, and the docs name binary as the strongest
        signal of a different protocol family."""
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_DEVICE_C_VOLTRONIC)
        self.assertEqual(described["body"], "ascii+binary_tail")
        self.assertNotEqual(described["body"], "binary")
        self.assertEqual(described["looks_like"], "voltronic_pi30")
        self.assertEqual(described["recognised"], 0)

    def test_the_modbus_device_does_not_regress_to_voltronic(self):
        """Issue #30's payload contains exactly one valid Voltronic frame -- its DTU
        emits an ACK in that framing while the data blocks are Modbus. That single
        frame is why both hints are counted and thresholded rather than any-match."""
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_DEVICE_B_FOREIGN)
        self.assertEqual(described["body"], "binary")
        self.assertEqual(described["looks_like"], "modbus_rtu")
        self.assertEqual(described["voltronic_crc_ok"], "1/12")

    def test_the_ack_frame_alone_never_names_a_protocol(self):
        """The threshold is set by observed data, not picked: one valid frame out of
        twelve must not label a payload."""
        described = SolarParser._describe_foreign_blocks(
            {"aRv4": captures.DEVB_BLOCK_ARV4}
        )
        self.assertNotIn("looks_like", described)

    def test_a_supported_device_gets_no_protocol_hint(self):
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_TELEMETRY)
        self.assertEqual(described["body"], "ascii")
        self.assertNotIn("looks_like", described)
        self.assertEqual(described["recognised"], len(captures.CAPTURE_TELEMETRY))

    def test_mixed_payloads_report_every_shape_present(self):
        """A payload that is part text and part binary is itself the signal, and the
        headline takes the worst shape so nothing is understated."""
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_DEVICE_B_FOREIGN)
        self.assertIn("body_shapes", described)
        self.assertIn("ascii=1", described["body_shapes"])
        self.assertIn("binary=11", described["body_shapes"])

    def test_the_known_name_registry_matches_the_decoder(self):
        """KNOWN_BLOCK_NAMES is declared in parallel with the literals inside
        _try_ascii_schema rather than driving them, so nothing but this test stops the
        two drifting -- and a stale registry makes the diagnostic above lie about how
        many blocks were recognised."""
        source = inspect.getsource(SolarParser._try_ascii_schema)
        literals = set(re.findall(r'parsed\.get\("([^"]{4})"', source))
        literals |= set(re.findall(r'"([^"]{4})" in parsed', source))
        self.assertEqual(
            literals,
            set(parser_module.KNOWN_BLOCK_NAMES),
            "KNOWN_BLOCK_NAMES and the block names _try_ascii_schema decodes have drifted",
        )


class TestBatteryStatusMatchesReportedPower(_ParserTestCase):
    """battery_status used to come from the inverter's own ammeter while the power
    sensors came from the BMS. On a live installation the two disagreed: the status
    read "Idle" in the same publish that reported 344 W flowing into the battery.

    It is now derived from the calculated power, so the contradiction is impossible
    rather than merely unlikely.
    """

    def _resolve(self, state, count=2):
        with mock.patch("src.siseli_local_bridge.parsers.INVERTER_COUNT", count):
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
            SolarParser._derive_battery_status(state)
        return state

    def test_the_live_case_that_reported_idle_while_charging(self):
        """Values taken verbatim from a running installation."""
        state = self._resolve({
            "bat_v": 53.7,
            "bat_charge_current": 0.0,      # the inverter's ammeter
            "dischg_current": 0.0,
            "bms_charging_current_a": 6.4,  # the BMS
            "bms_discharge_current_a": 0.0,
        })
        self.assertEqual(state["c_battery_charge_power_w"], 344)
        self.assertEqual(state["battery_status"], "Charge")

    def test_status_and_power_can_never_contradict(self):
        for label, inputs, expected in (
            ("idle", {"bat_v": 53.7, "bat_charge_current": 0.0, "dischg_current": 0.0}, "Idle"),
            ("charging", {"bat_v": 53.7, "bat_charge_current": 5.0, "dischg_current": 0.0}, "Charge"),
            ("discharging", {"bat_v": 53.7, "bat_charge_current": 0.0, "dischg_current": 10.0}, "Discharge"),
        ):
            with self.subTest(case=label):
                state = self._resolve(dict(inputs))
                status = state["battery_status"]
                charge = state["c_battery_charge_power_w"]
                discharge = state["c_battery_discharge_power_w"]
                self.assertEqual(status, expected)
                if status == "Charge":
                    self.assertGreater(charge, 0)
                elif status == "Discharge":
                    self.assertGreater(discharge, 0)
                else:
                    self.assertEqual((charge, discharge), (0, 0))

    def test_no_battery_data_means_no_status(self):
        state = self._resolve({"mains_wdrr_value": 100})
        self.assertNotIn("battery_status", state)

    def test_one_source_reading_zero_is_reported(self):
        """A ratio test cannot express this, and it is the most informative
        disagreement there is -- it is what produced the Idle-while-charging case."""
        state = {"bat_v": 53.7, "bat_charge_current": 0.0, "bms_charging_current_a": 6.4}
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        tags = [call.args[0] for call in logged.call_args_list if call.args]
        self.assertIn("[ENERGY SOURCE DISAGREEMENT]", tags)

    def test_agreeing_sources_stay_quiet(self):
        state = {"bat_v": 53.7, "bat_charge_current": 3.0, "bms_charging_current_a": 6.0}
        with mock.patch("src.siseli_local_bridge.parsers.log_kv") as logged:
            with mock.patch("src.siseli_local_bridge.parsers.INVERTER_COUNT", 2):
                SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        tags = [call.args[0] for call in logged.call_args_list if call.args]
        self.assertNotIn("[ENERGY SOURCE DISAGREEMENT]", tags)


class TestSettingsBackup(unittest.TestCase):
    """The YAML file of settings and its Save / Restore buttons (settings_backup.py)."""

    STATE = {
        "maximum_total_charging_current_a": 60, "return_to_mains_mode_voltage_v": 44.0,
        "return_to_battery_mode_voltage_v": 48.0, "battery_equalization_voltage_v": 56.0,
        "bms_low_power_soc": 10, "bms_returns_to_mains_mode_soc": 20,
        "bms_returns_to_battery_mode_soc": 25, "bms_auto_start_soc_after_low": 25,
        "grid_connected_current_a": 4, "parallel_mode_turn_off_soc": 30,
        "second_output_battery_capacity": 50, "second_output_discharge_time": "0 min",
        "second_delay_time": "0 min", "output_source_priority": "Solar+Battery First (SBU)",
        "max_utility_charge_current_a": 2, "mains_input_range": "UPS", "output_set_voltage": 240,
        "grid_regulation_mode": "Mode 2", "charger_priority": "Solar Only (OSO)",
        "solar_supply_priority": "LBU", "ac_charging_start_time": "12:00", "ac_charging_stop_time": "13:00",
        "dual_output_mode": "On", "buzzer_function": "Off", "battery_type": "Growatt (GRO)",
    }

    def _state(self, **changes):
        return {**self.STATE, **changes}

    def test_displayed_values_are_what_home_assistant_shows(self):
        from src.siseli_local_bridge import settings_backup as sb
        snap = self._state()
        self.assertEqual(sb.displayed_value("max_charging_current", snap), "60")
        self.assertEqual(sb.displayed_value("output_voltage", snap), "240 V")
        self.assertEqual(sb.displayed_value("max_utility_charge_current", snap), "2 A")
        self.assertEqual(sb.displayed_value("second_output_delay_time", snap), "0")
        self.assertEqual(sb.displayed_value("dual_output", snap), "on")
        self.assertEqual(sb.displayed_value("ac_charging_start_time", snap), "12:00")
        self.assertEqual(sb.displayed_value("buzzer", snap), "off")
        self.assertTrue(sb.displayed_value("grid_regulation_mode", snap).startswith("Mode 2 GEn"))
        self.assertIsNone(sb.displayed_value("eco", snap))  # not reported yet

    def test_the_file_round_trips_and_keeps_the_battery_type_read_only(self):
        from src.siseli_local_bridge import settings_backup as sb
        values = sb._current_values(self._state())
        text = sb.render_file(values, "2026-10-04 11:00:00")
        self.assertIn('battery_type: "Growatt (GRO)"', text)
        parsed, saved_at = sb.parse_file(text)
        self.assertEqual(saved_at, "2026-10-04 11:00:00")
        self.assertNotIn("battery_type", parsed)
        self.assertEqual(parsed["max_charging_current"], "60")
        self.assertEqual(parsed["dual_output"], "on")
        self.assertEqual((parsed["ac_charging_start_time"], parsed["ac_charging_stop_time"]), ("12:00", "13:00"))
        self.assertEqual(parsed["output_source_priority"], "Solar+Battery First (SBU)")
        self.assertEqual({k: v for k, v in values.items() if k != "battery_type"}, parsed)

    def test_restore_sends_only_what_differs_in_a_safe_order(self):
        from src.siseli_local_bridge import settings_backup as sb
        saved = {"bms_lock_machine_soc": "10", "second_output_cutoff_soc": "35", "dual_output": "off",
                 "second_output_delay_time": "5", "grid_regulation_mode": "Mode 1 whatever",
                 "buzzer": "off", "battery_type": "AGM", "output_voltage": "999 V"}
        to_send, skipped = sb.plan_restore(saved, self._state())
        names = [s for s, _, _ in to_send]
        # unchanged ones are left alone, the second output goes last, battery_type is never offered
        self.assertEqual(names, ["second_output_cutoff_soc", "second_output_delay_time",
                                 "grid_regulation_mode", "dual_output"])
        self.assertEqual(dict((s, p) for s, _, p in to_send)["dual_output"], "OFF")
        self.assertTrue(dict((s, p) for s, _, p in to_send)["grid_regulation_mode"].startswith("Mode 1 IND"))
        self.assertIn("battery_type (not a restorable setting)", skipped)
        self.assertTrue(any(item.startswith("output_voltage") for item in skipped))

    def test_restore_is_refused_without_a_local_cloud_connection(self):
        import os
        import tempfile
        from unittest import mock
        from src.siseli_local_bridge import fakecloud, mqtt, settings_backup as sb
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "inverter_settings.yaml")
            open(path, "w", encoding="utf-8").write('numbers:\n  second_output_cutoff_soc: "35"\n')
            sent = []
            with mock.patch.object(fakecloud, "_first_established_connection", return_value=None), \
                    mock.patch.object(sb._state, "snapshot_state", return_value=self._state()), \
                    mock.patch.object(mqtt, "client"), \
                    mock.patch.object(mqtt, "_handle_control_message", side_effect=lambda t, p: sent.append(t)):
                sb.set_confirmation(True)
                self.assertFalse(sb.restore_settings(path, wait=True))
            self.assertEqual(sent, [])
            self.assertIn("local cloud", sb._status_text)
    def test_restore_needs_the_confirmation_box_and_clears_it(self):
        import os
        import tempfile
        from unittest import mock
        from src.siseli_local_bridge import mqtt, settings_backup as sb
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "inverter_settings.yaml")
            open(path, "w", encoding="utf-8").write('numbers:\n  second_output_cutoff_soc: "35"\n')
            sent = []
            with mock.patch.object(sb._state, "snapshot_state", return_value=self._state()), \
                    mock.patch.object(mqtt, "client") as client, \
                    mock.patch.object(mqtt, "_handle_control_message", side_effect=lambda t, p: sent.append(t)), \
                    mock.patch.object(sb, "_connected", return_value=True), \
                    mock.patch.object(sb, "RESTORE_SPACING_SEC", 0), mock.patch.object(sb, "VERIFY_DELAY_SEC", 0):
                sb._CONFIRMED_AT = None
                self.assertFalse(sb.restore_settings(path, wait=True))   # box not ticked: nothing sent
                self.assertEqual(sent, [])
                self.assertIn("tick", sb._status_text)
                sb.set_confirmation(True)
                self.assertTrue(sb.restore_settings(path, wait=True))    # ticked: goes through
                self.assertEqual(len(sent), 1)
                self.assertFalse(sb.restore_settings(path, wait=True))   # the box cleared itself
                self.assertEqual(len(sent), 1)
                sb.set_confirmation(True)
                with mock.patch.object(sb.time, "monotonic", return_value=sb._CONFIRMED_AT + sb.CONFIRM_WINDOW_SEC + 1):
                    self.assertFalse(sb.restore_settings(path, wait=True))   # ticked too long ago
                self.assertEqual(len(sent), 1)
                states = [c[0][1] for c in client.publish.call_args_list if c[0][0].endswith("confirm_restore_settings/state")]
                self.assertEqual(states[-1], "OFF")
        # the switch is wired to its topic and named as a warning in both languages
        self.assertTrue(mqtt.control_command_topic("confirm_restore_settings").endswith("/confirm_restore_settings/set"))
        from src.siseli_local_bridge.i18n import FR_NAMES
        self.assertIn("Warning: Confirm Restore Settings", FR_NAMES)
    def test_the_four_entities_share_one_device_of_their_own(self):
        """Save, Restore, the confirmation switch and the status sensor sit together on
        a "Settings Backup" device, as visible entities (no Configuration/Diagnostic)."""
        import json
        from unittest import mock
        from src.siseli_local_bridge import mqtt
        with mock.patch.object(mqtt, "client") as client:
            mqtt.publish_control_discovery()
            published = {c[0][0]: json.loads(c[0][1]) for c in client.publish.call_args_list
                         if c[0][0].endswith("/config") and c[0][1]}
        wanted = ("button/%s/save_inverter_settings", "button/%s/restore_inverter_settings",
                  "switch/%s/confirm_restore_settings", "sensor/%s/settings_backup_status")
        device_ids = set()
        for suffix in wanted:
            payload = published[f"homeassistant/{suffix % mqtt.DEVICE_ID}/config"]
            device_ids.add(tuple(payload["device"]["identifiers"]))
            self.assertNotIn("entity_category", payload, suffix)
        self.assertEqual(device_ids, {(f"{mqtt.DEVICE_ID}_settings_backup",)})
        with mock.patch.object(mqtt, "LANGUAGE", "fr"):
            self.assertTrue(mqtt.settings_device_info()["name"].endswith("Sauvegarde/Restauration"))
        # the other buttons stay on the main device, under Configuration
        sync = published[f"homeassistant/button/{mqtt.DEVICE_ID}/sync_inverter_clock/config"]
        self.assertEqual(sync["entity_category"], "config")
        self.assertEqual(sync["device"]["identifiers"], [mqtt.DEVICE_ID])
    def test_the_entities_are_moved_to_their_device_once(self):
        import os
        import tempfile
        from unittest import mock
        from src.siseli_local_bridge import settings_backup as sb
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "settings_device_migrated")
            client, again = mock.MagicMock(), mock.MagicMock()
            with mock.patch.object(sb, "SETTINGS_DEVICE_MARKER_FILE", marker), \
                    mock.patch.object(sb, "MIGRATION_ALLOWED_IN_TESTS", True), \
                    mock.patch.object(sb, "MIGRATION_DELAY_SEC", 0):
                self.assertTrue(sb.migrate_to_own_device_once(client, again))
                for thread in __import__("threading").enumerate():
                    if thread.name == "settings-device-move":
                        thread.join(5)
                cleared = [c[0][0] for c in client.publish.call_args_list if c[0][1] == ""]
                self.assertEqual(len(cleared), 4)
                self.assertTrue(any("save_inverter_settings" in t for t in cleared))
                again.assert_called_once()
                self.assertTrue(os.path.exists(marker))
                self.assertFalse(sb.migrate_to_own_device_once(client, again))   # once only
            # no /data (tests, Windows): nothing to do
            with mock.patch.object(sb, "SETTINGS_DEVICE_MARKER_FILE", os.path.join(folder, "nope", "marker")), \
                    mock.patch.object(sb, "MIGRATION_ALLOWED_IN_TESTS", True):
                self.assertFalse(sb.migrate_to_own_device_once(client, again))
    def test_save_and_restore_go_through_the_control_handlers(self):
        import os
        import tempfile
        from unittest import mock
        from src.siseli_local_bridge import mqtt, settings_backup as sb
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "inverter_settings.yaml")
            with mock.patch.object(sb._state, "snapshot_state", return_value=self._state()), \
                    mock.patch.object(mqtt, "client"):
                self.assertGreater(sb.save_settings(path), 20)
                self.assertTrue(sb.save_settings(path))
                self.assertTrue(os.path.exists(path + ".bak"))
            text = open(path, encoding="utf-8").read().replace('second_output_cutoff_soc: "30"',
                                                               'second_output_cutoff_soc: "35"')
            open(path, "w", encoding="utf-8").write(text)
            sent = []
            with mock.patch.object(sb._state, "snapshot_state", return_value=self._state()), \
                    mock.patch.object(mqtt, "client"), \
                    mock.patch.object(mqtt, "_handle_control_message", side_effect=lambda t, p: sent.append((t, p))), \
                    mock.patch.object(sb, "_connected", return_value=True), \
                    mock.patch.object(sb, "RESTORE_SPACING_SEC", 0), mock.patch.object(sb, "VERIFY_DELAY_SEC", 0):
                sb.set_confirmation(True)
                self.assertTrue(sb.restore_settings(path, wait=True))
            self.assertEqual(sent, [(mqtt.control_command_topic("second_output_cutoff_soc"), b"35")])
        self.assertIn("save_inverter_settings", mqtt._CONTROL_BUTTONS)
        self.assertIn("restore_inverter_settings", mqtt._CONTROL_BUTTONS)
if __name__ == "__main__":
    unittest.main()
