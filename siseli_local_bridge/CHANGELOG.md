# Changelog

All notable changes to this project will be documented in this file.

## [2.6.91] - 2026-10-04

### Added

- **Save and restore the inverter's settings.** Two buttons, **Save Inverter Settings** and
  **Restore Inverter Settings**, and a **Settings Backup Status** sensor. Save writes every setting,
  as Home Assistant shows it, to `inverter_settings.yaml` in the add-on's configuration folder
  (new `map: addon_config` in `config.yaml`; the previous file is kept as `.bak`). Restore sends
  every value that differs from what the inverter reports, in a safe order (Programme 38 before
  62, the second output last), one command every three seconds, then compares the read-back 90
  seconds later. The file is flat, easy to edit by hand, and the battery type is saved for
  information but never restored (changing it can cut the inverter's output). The four entities (both buttons, the confirmation switch, the status sensor) sit together on a **Settings Backup** device ("Sauvegarde/Restauration" in French). Home Assistant keeps an entity on the device where it first appeared, so the bridge clears and republishes these four entities once (marker file `settings_device_migrated`) to bring them over. A restore only starts after the **Warning: Confirm Restore Settings** switch is ticked (it clears itself after each press of the button, or after five minutes). A restore is refused (and stops if the connection drops) when the dongle is not connected to the local cloud: the commands have nothing to travel on.
## [2.6.90] - 2026-10-04

### Fixed

- **Programme 65 (second-output discharge time) was read from the wrong place.** The
  "Second Output Discharge Time" sensor and number read `HEEP2` token 6, which stays at 0
  whatever the setting. The value is in the last three digits of the **last** token, which
  also holds the Programme 64 capacity in its first two (`20975` = 20 % and 975 min, what the
  vendor app showed; `50000` = 50 % and 0 = disabled). `PDDCGT` writes were already accepted
  (`(ACK9`); only the read-back was wrong.
## [2.6.89] - 2026-10-04

### Added

- **Second-output discharge time and restore delay as Home Assistant numbers** (manual
  Programmes 65 and 66): "Second Output Discharge Time" (`PDDCGT`, 0-990 min in steps of 5,
  0 = off) and "Second Output Restore Delay" (`PDDLYT`, 0-60 min). Both channels were read
  off the wire in captures of the vendor app; the inverter NAKs a discharge time that is not a
  multiple of 5 (the app steps by 1). Read back from `HEEP2`.

### Removed

- The "Second Output Restore Voltage" number added in 2.6.87 (Programme 63, `PDSRV`): its channel
  was never confirmed, and the voltage settings (61 and 63) are refused by the inverter with a
  BMS lithium battery (every `PDSDV` write for Programme 61 got `(NAKss`). Programmes 62 and 64
  (the percentages) are the ones that apply. Home Assistant keeps the orphan entity until it
  is deleted from Settings.
## [2.6.88] - 2026-10-04

### Added

- **`ENTITY_PREFIX` can now be turned off**: set it to `-` (or `none`) to show entity names
  without a prefix. Leaving the option empty never worked, because Supervisor puts the default
  `Siseli` back. Entity IDs already created do not change; only the displayed names do.

## [2.6.87] - 2026-10-04

### Added

- **Second-output thresholds as Home Assistant numbers** (manual Programmes 62 and 64, plus 63):
  "Second Output Cut-off SOC" (`PDSDS`, 5-95 %, steps of 5), "Second Output Restore SOC"
  (`PDSRS`, 5-100 %, steps of 5) and "Second Output Restore Voltage" (`PDSRV`, 48.0-58.0 V,
  0.1 V), each read back from the matching `HEEP2` token. The channel names come from
  SoT-RWB1-Server-Emulator. **Programmes 62 and 64 are confirmed on this inverter** (written from
  Home Assistant, answered `(ACK9`, read back about a minute later). **Programme 63 is untested.**
  Programmes 61, 65 and 66 are not offered yet (no confirmed channel / unit).

## [2.6.86] - 2026-10-04

Result of a review of the whole bridge (backup taken first). No change to what the sensors
publish or how they are decoded; the fixes are in the plumbing around the live reads.

### Fixed

- **With `TELEMETRY_POLL_INTERVAL_SEC` at its shipped 0, the live reads, the once-a-minute
  `QFLAG` and every control command silently did nothing.** The device id a connection
  belongs to was remembered only when the full poll was on, so the live cycle skipped the
  connection and every switch, select and number found "no established local-cloud
  connection". It is now remembered whatever the poll options say; the full `i=501` poll
  still needs its own option, and `QFLAG` now rides at the end of a live cycle (one command
  at a time) when the live reads are on, or on the poll tick as before when they are off.
- **The energy counters lost up to 30 s at every restart or rebuild.** The state cache
  (counters and their clocks) is written at most every 30 s, and was not written at
  shutdown; it now is, first thing, unless nothing has been decoded yet (a good cache is
  never replaced by an empty one).
- **The "poll stall" diagnostic could no longer fire.** Since 2.6.80 the answers to the live
  commands (`i=504`) arrive on the same topic as the answers to the poll (`i=502`) and were
  counted as poll answers. Only `i=502` counts now.
- **The stale-connection sweep iterated the live connection table** while the capture thread
  adds and removes connections (a "dictionary changed size" error was possible); it works
  on a copy.
- **A malformed MQTT length field raised the same error on every following packet** and an
  announced frame of up to 268 MB was waited for. A frame over 64 KiB or an impossible
  length now closes that connection (the dongle reconnects by itself). The HTTP stub drops
  a request whose headers never end or whose `Content-Length` is over 64 KiB.
- **A stale nft table was reused as it was.** After a crash and a change of address or port,
  the new port was never dropped. The table is now checked against the configuration and
  recreated when it differs, and the `nft` calls have a 10 s timeout.
- `Connection.close()` now takes the connection lock; the connection table is annotated with
  its real 4-part key.

### Added

- **A `[LIVE]` warning, at most once a minute, when the dongle refused a command (error 104)
  or a cycle was closed without all its blocks**, with counts. Since 2.6.81 neither left a
  trace, so the lowest workable `LIVE_POLL_INTERVAL_SEC` could only be measured with a
  network capture.

### Changed

- **A state group is no longer republished when its JSON is identical to what the broker last
  accepted** (with the live reads every publish resent all seven groups, about three
  messages a second, to say nothing had changed in most). Everything is sent again on a
  new broker session, with `MQTT_RETAIN` off, and at least every `EXPIRE_AFTER_SEC / 3`
  so a group that never changes still restarts Home Assistant's `expire_after` timer.
- Tidy-up: an unused import, and the `except: pass` of the shutdown path now log at debug.

### Tests and documentation

- The suite is green again (it carried 30 sub-failures for weeks, which hid regressions):
  three tests expected a lower-case device id although the code keeps the case on purpose,
  one mock could not take keyword arguments, `BASE_ENV` lacked seven shipped options,
  one test listed only four of the six publish outcomes, and the line anchors of
  `docs/ARCHITECTURE.md` had drifted. `DOCS.md` now is the one published on GitHub, which
  documents all options (the working copy had fallen behind it).
## [2.6.85] - 2026-10-03

### Fixed

- **Two more 0 W readings of `generation_power_w` in the minute after each restart.** 2.6.84
  stopped decoding the PV2 block (`noeP`) alone, but only dropped the dongle's own copy of
  it once a live read of it was less than 150 s old. The first live PV2 read comes up to a
  minute after the first PV1 one (it is one of five rotating commands), and meanwhile the
  dongle's copy of PV2 (its telemetry fragment carries it without PV1) decoded to a
  generation of 0 W: 10:42:18 and 10:42:28 after the 10:41:41 restart of 2026-10-03, and
  the same pair after the earlier restarts. The dongle's PV2 copy is now dropped as soon as
  the PV1 block is live, whether or not PV2 itself has been read yet.
## [2.6.84] - 2026-10-03

### Fixed

- **`generation_power_w` (and the calculated generation power) dropped to 0 W for one
  publication about every fifth cycle, in 2.6.81 to 2.6.83.** Reported on 2026-10-03 and
  confirmed in the recorder: 31 zeros in 30 minutes on both entities, 47 to 53 s apart.
  The decoder adds PV1 and PV2 into `generation_power_w` from the payload in hand; the PV2
  block (`noeP`, `HPVB`) was read in the slow rotation and decoded alone, so a payload with
  no PV1 block summed to PV2's 0 W. The PV2 block is now never decoded on its own: its last
  answer is sent along with every decoding of the PV1 block (`Mpod`), so the sum always has
  PV1, and PV2 is at most a few cycles old in it.
- **The calculated generation energy was under-counted while that went on** (each zero also
  moved the generation clock forward, so the next real reading integrated a much shorter
  interval): roughly a fifth of the generation was missed from 2.6.81 until this fix. The
  counter cannot be corrected after the fact and is not reset; it is right from now on.
## [2.6.83] - 2026-10-03

### Fixed

- **The five cell-summary sensors froze in 2.6.81 and 2.6.82** (BMS max cell, min cell,
  cell delta, and the max and min cell positions). Found on 2026-10-03 by comparing them
  with the sixteen cell voltages: they read 3327 / 3316 / 11 mV while the cells read 3337 to
  3349 mV (a real spread of 12 mV). The decoder derives them only from a payload that carries
  both the BMS capacities (`uxJp`, `HBMS2`) and the cells (`v09K`, `HBMS3`); since 2.6.81 the
  first was read every cycle and the second one cycle in six, so they never met. `HBMS3` is
  now read every cycle and decoded in the battery group with `HBAT`, `HBMS1` and `HBMS2`
  (about 4 s into a cycle). The sixteen cell voltages are therefore live every cycle too,
  not once a minute.
- A cycle now has nine commands, so it takes about 9 s (it was about 8): values of
  `LIVE_POLL_INTERVAL_SEC` under 9 cannot make it shorter. The rotating slow set is now five
  commands (`HSTS HTEMP HGEN HPVB QPIWS`), each about once every five cycles.
## [2.6.82] - 2026-10-03

### Changed

- **Live values are published sooner.** Measured on 2026-10-03 with the dongle's answers
  read off the wire next to what the bridge published: a value reached Home Assistant about
  4 s after the dongle answered, and 6 to 9 s after on roughly a cycle in three.
  - The cycle's blocks are decoded in two groups as soon as each is complete: the battery
    trio (`HBAT`, `HBMS1`, `HBMS2`, about 3 s into a cycle, still decoded together so the
    battery power stays on one current basis) and the power trio (`HGRID`, `HOP`, `HPV`,
    about 6 s). What is left (the rotating slow block) is decoded when the cycle is complete.
    A group the cycle does not fully expect still waits for the cycle's end.
  - A live block decoded inside the previous publish's `UPDATE_INTERVAL_SEC` window is held
    back by the publish throttle, and the only thing that flushed it was `health_logger`'s
    10 s tick, hence the 6 to 9 s. `telemetry_poll_loop` now runs the same check every second.
- With `UPDATE_INTERVAL_SEC` at 2 (and `LIVE_POLL_INTERVAL_SEC` at 8), every one of 12
  consecutive `HGRID` answers reached Home Assistant; with 5 one was overwritten before it
  was published.
## [2.6.81] - 2026-10-03

### Changed

- **Live reads now use the inverter's own "H" block commands, so the BMS, the battery
  power and the grid power are live too, not only the 2.6.80 `QPIGS` fields.** A search
  for other people's work on the same dongle family found
  [solarplug-esphome](https://github.com/rutgerputter/solarplug-esphome), which lists the
  read commands an ESP32 sends to the inverter over RS232: ASCII plus CR, no CRC, the
  same form as the vendor app's `HEEP1`/`HEEP2`. Sent to the dongle as commands, all
  eleven were relayed and answered fresh (2026-10-03: BMS current 0 -> 4.9 -> 4.8 A,
  mains voltage 240.0 -> 240.1 -> 240.2 V within 30 s, against the dongle's one-minute
  cache). Each answer is the text of one telemetry block (`HGRID` = `WdRR`, `HOP` =
  `2l0E`, `HBAT` = `2ONL`, `HPV` = `Mpod`, `HBMS1` = `Yavb`, `HBMS2` = `uxJp`, `HBMS3` =
  `v09K`, `HSTS` = `eo8w`, `HTEMP` = `V4W3`, `HGEN` = `COST`, `HPVB` = `noeP`), so each cycle
  hands them to the same decoder as one telemetry reply: no new sensor, entity or decoder,
  and the calculated powers and energies behave exactly as before.
- **Each cycle (every `LIVE_POLL_INTERVAL_SEC`)** sends, one command per second,
  `HBAT HBMS1 HBMS2 HGRID HOP HPV QMOD` plus one of `HSTS HBMS3 HTEMP HGEN HPVB QPIWS`
  in turn. `HBAT` and `HBMS1` are decoded together, in one payload, because the battery
  power is derived from the currents of the payload in hand (BMS first, then the
  inverter's own): apart, it would jump between two bases. A cycle takes about 8 s, so a
  setting under 8 cannot make it faster; the cell voltages, temperatures, energies, PV2
  and warnings therefore refresh every 6 cycles (about a minute at the default).
- **The dongle's own copy of a block is ignored while a live read of it is less than
  150 s old.** Otherwise every 15 s poll would put a value up to a minute old over the
  fresh one. A block with no recent live read keeps using the dongle's copy.
- A line that fits none of the eleven shapes (or two of them) is never decoded, so a
  lost or reordered answer cannot put one command's text under another block's name.
  If an answer goes missing, the others of that cycle are decoded when the next cycle's
  first answer arrives.

### Removed

- The `QPIGS` merge of 2.6.80: the H blocks carry every field it did (and the BMS and
  the signed mains power it could not).

## [2.6.80] - 2026-10-02

### Added

- **Live status every `LIVE_POLL_INTERVAL_SEC` (default 10 s) instead of once a minute.**
  The dongle answers every regular `dev_rpc` poll, but the inverter data inside is a
  cache it refreshes about once a minute (measured on 2026-10-02: PV power changed at
  17:00:48, 17:01:46, 17:02:49, 17:03:49 while the poll ran every 15 s). Queries sent to
  the dongle as commands are relayed to the inverter and answered fresh, so the bridge
  now asks `QPIGS`, `QMOD` and `QPIWS` (one per second, in that order) each interval and
  merges the answers into the state. `QPIGS` carries grid voltage and frequency, output
  voltage, frequency, VA, W and load %, bus and battery voltage, battery current and
  capacity (SOC %), PV voltage, current and power, which update `grid_v`, `grid_hz`,
  `out_v`, `out_hz`, `apparent_va`, `load_w`, `load_pct`, `bus_voltage`, `bat_v`,
  `bat_charge_current`, `dischg_current`, `bat_cap`, `pv_v`, `pv_current_a`, `pv_w`,
  `generation_power_w`, and the calculated generation and load power and energy through
  the same integrator as before. Answers whose CRC16 fails are ignored. Not derived from
  it: battery power (the BMS current that feeds it is only in the slow blocks, and the
  whole-ampere `QPIGS` current disagrees with it) and grid import (no signed mains power
  in `QPIGS`). The BMS cells, remaining Ah and BMS current stay on the dongle's one-minute
  cache: `QBMS`, `HEEP<n>` reads and fourteen PI18 `^P???` names were all refused (error
  104) or unrelated.
- **`LIVE_POLL_INTERVAL_SEC` option**, default 10, `0` switches it off. A value under 5
  is raised to 5: the dongle answers a query with error 104 while another is pending.
  The lowest value it tolerates is not measured yet.
- **"Mode" sensor now has data** (`QMOD`: Battery, Line, Standby, Power On, Bypass,
  Battery Test, Fault, Shutdown, ECO or Power Saving Mode); it left the undecoded list
  and is enabled by default on a fresh install (an entity Home Assistant already created
  disabled stays disabled until it is enabled by hand).
- **Two diagnostic sensors from `QPIWS`:** "Active Warnings" (count of set bits) and
  "Warning Flags" (the raw string, disabled by default). Which bit is which was never seen
  set on this inverter (every answer was all zeros), so none is mapped to a named
  warning; the first change is logged with the positions that are set.

## [2.6.79] - 2026-09-29

### Added

- **AC charger time window (Programmes 46/47) can be set from HA.** Two
  selects, "AC Charging Start Time" and "AC Charging Stop Time" (00:00 to
  23:00), in the device's Configuration section, read back from the sensors of
  the same name. Command `^S???ACCT<HHMM>,<HHMM>` (Voltronic PI17 family, like
  Programme 50's `^S???RS`), found with the 2.6.76 probe: `^P???ACCT` answered
  `^D0121200,1300` (the front panel's 12:00-13:00), and `^S???ACCT1200,1400`
  answered `^1`, read back 12:00-14:00 in HA, then 12:00-13:00 was restored.
  Both hours go out together; the one not being changed is taken from the last
  read-back, and the write is refused while that is unknown.

### Removed

- **The temporary raw command probe** (2.6.76) has done its job.

## [2.6.78] - 2026-09-29

### Fixed

- **Entity IDs stay English with `LANGUAGE: fr`.** Home Assistant builds a new
  entity's ID from its displayed names, so a sensor first created after
  switching to French got a French ID (2.6.77's AC charging times became
  `…_diagnostic_siseli_heure_de_debut_de_charge_secteur`). Every discovery
  config now carries `default_entity_id`, built from the English device and
  entity names — checked against a live registry: identical for all existing
  entities. HA only reads it when it creates an entity, so existing IDs never
  move.

## [2.6.77] - 2026-09-29

### Added

- **AC charger time window (Programmes 46/47) read back.** Two sensors, "AC
  Charging Start Time" and "AC Charging Stop Time" (e.g. 12:00 / 13:00), from
  dHrK token 11, which packs both hours as "SSEE". Front panel confirmed:
  Programme 46 = 12:00 moved it 0000 -> 1200, then Programme 47 = 13:00 ->
  1213. 00:00 / 00:00 is the default and means no restriction. Outside the
  window the grid no longer charges the battery (tested in SUB with the PV
  disconnected: 0 A instead of the 2 A seen with 00-00). No control yet: the
  vendor app has no such setting and the write command is unknown.

### Fixed

- **"Dual Output Starting/Ending Time" removed.** They were read from dHrK
  tokens 11 and 12; token 11 is the AC charger window above, and token 12 did
  not move with Programme 47, so its meaning is unknown. The never-decoded
  "Mains Charging Ending Time" sensor is removed too (replaced by the two new
  ones).

## [2.6.76] - 2026-09-29

### Added

- **Temporary raw command probe.** A command published on
  `<DEVICE_ID>/control/raw_command/set` (1-32 printable ASCII characters) is
  sent to the inverter with its CRC16 and CR, and the answer is logged like any
  command's. Meant to find the write command of Programmes 46/47 (AC charger
  start/stop time), which the vendor app does not offer. Not shown as an HA
  entity; PF (factory reset), PBT (battery type) and ^S???CLE (PV energy reset)
  are refused. It will be removed once the command is found.

## [2.6.75] - 2026-09-28

### Changed

- **Grid Regulation Mode (Programme 50) shows each mode's window and offers
  all five modes.** Options now read e.g. "Mode 1 IND (195.5-253 VAC, 49-51 Hz)"
  (French: "Mode 1 IND (195,5-253 VAC : 49-51 Hz)"), from the manual's revised
  Programme 50 table, with the front panel's region code (IND, GEn, SAd,
  PAk, U2b). Mode 3 (57-62 Hz) is offered again: other countries need it,
  and its label now shows the 60 Hz window. The read-back maps the
  inverter's "Mode n" onto the same label.

## [2.6.74] - 2026-09-27

### Added

- **`LANGUAGE` option: entity names in English (`en`, default) or French
  (`fr`).** Every sensor, control and device-group name has a French
  translation (`i18n.py`; a test fails if a new name lacks one). Only the
  displayed names change: unique_ids, entity_ids, topics and state values stay
  English, so automations and dashboards keep working after switching.
## [2.6.73] - 2026-09-27

### Removed

- **The 2.6.72 "Battery Charge/Discharge (test)" select.** `PBATCD000` was
  sent twice and never answered, not even with `(NAK`, while telemetry kept
  flowing: this firmware does not take PI30MAX `PBATCD`.
## [2.6.72] - 2026-09-27

### Added

- **Test: "Battery Charge/Discharge (test)" select**, PI30MAX `PBATCD<abc>`.
  Only "Normal" (`PBATCD000`, clears any forcing) for now, to learn whether
  this firmware takes the command. No read-back command is documented.
## [2.6.71] - 2026-09-27

### Removed

- **The 2.6.70 "Solar Feed To Grid" test switch.** The inverter answered
  `PDd` with `(NAKss`: this firmware does not take PI30MAX flag `d`, which
  its `QFLAG` answer never lists either. Programme 44 stays read-only through
  the "Solar Feed To Grid" sensor (93VQ token 18).
## [2.6.70] - 2026-09-27

### Added

- **Test: "Solar Feed To Grid" switch (Programme 44).** PI30MAX documents flag
  `d` (`PEd` / `PDd`) as "solar feed to grid (reserved feature)"; this
  inverter's `QFLAG` answer does not list `d`, so it is unproven. Read back
  from 93VQ token 18. Removed again if the inverter refuses it.
## [2.6.69] - 2026-09-27

### Fixed

- **93VQ token 18 is Programme 44 (solar energy feed to grid), not the AC
  charger's start hour.** Switching Programme 44 to `GtE` on the front panel
  moved it 0 -> 1 in the next reading and back to 0 with `GtD`; nothing else
  moved, `grid_connection_function` included. New sensor "Solar Feed To
  Grid" (Disabled / Enabled) replaces `mains_charging_starting_time`. Token 19
  (`mains_charging_ending_time`), labelled by the same guess, is no longer
  decoded and joins the undecoded sensors. No control: the vendor app has no
  Programme 44 setting and its command is unknown.
## [2.6.68] - 2026-09-27

### Added

- **"Max Charging Current" number (Programme 02, total solar + utility
  charge current), with read-back.** Captured from the vendor app:
  `MNCHGC` + 3 digits in amps (`MNCHGC050`, `MNCHGC060` answered
  `(ACK9`; 58, 59, 61 and 62 A answered `(NAKss`). 10-150 A in 10 A steps
  (the manual's range for the 11 kW model; the app shows none). Read back from
  93VQ token 1 (`maximum_total_charging_current_a`). The battery's BMS still
  limits the charge to its own maximum.

### Not added

- Programmes 24, 26, 27 and 29 (battery voltages): the battery type
  (Programme 05) sets them. With a BMS battery type the inverter refuses any
  change (Programme 24 answered `(NAKss` to every value on 2026-09-26).
## [2.6.67] - 2026-09-27

### Added

- **ECO (Programme 08) now reads back the inverter's real state.** The bridge
  asks the PI30 flag status (`QFLAG`, read only) once a minute; the inverter
  answered `(EbuvxyDajkz` on 2026-09-27, every letter matching the known
  settings. Letter `j` is ECO, which no telemetry block carries. New
  sensor "ECO Power Saving".
- **Switches for Programme 22 (beeps while the primary source is
  interrupted) and Programme 25 (fault code record)**, PI30 flags `y` and
  `z` (`PEy`/`PDy`, `PEz`/`PDz`), read back from the same query. New
  sensors "Beeps While Primary Source Interrupted" and "Fault Code Record".

### Changed

- The one-off `QFLAG` test of 2.6.66 is replaced by the minute query; its
  answer is logged only when a flag changes.
## [2.6.66] - 2026-09-27

### Added

- **The inverter's answer to every command is logged** at warning level, with
  the command it answers: `[CONTROL] inverter answered PGFC006: (ACK9..`.
  Until now a refused command (`(NAK`, `^0`) only showed as a read-back that
  did not move.
- **Test: PI30 `QFLAG` sent once per dongle connection** (read only). ECO
  (Programme 08) moves no telemetry field at all -- every field of all 15
  blocks was compared across ECO on and off in the 2026-09-26 capture -- so
  the inverter's own flag status is the remaining way to read it back.
## [2.6.65] - 2026-09-27

### Added

- **"Grid-Tie Current" number (Programme 56), with read-back.** Captured from
  the vendor app: `PGFC` + 3 digits in amps (`PGFC006` answered `(ACK9`);
  93VQ token 17 (`grid_connected_current_a`) followed each accepted value.
  4-40 A in 1 A steps: the inverter refuses 2 A (`(NAKss`, and the front
  panel stops at 4 A too), and 40 A is the app's maximum. The panel steps by
  2 A, the command takes any whole amp. It only matters while Programme 44
  allows feeding the grid.
## [2.6.64] - 2026-09-27

### Fixed

- **Five more 93VQ positions carried the vendor portal's labels on the wrong
  settings.** Found on 2026-09-27 by changing one or two settings at a time
  in the vendor app and watching the dongle's next push; each also matched
  the factory values the reset of 2026-09-26 had left:
  - aux pack digit 1 (`parallel_mode`) = **Programme 19**, display returns to
    the home page -> new `display_return_to_homepage`;
  - token 7 (`power_supply_from_pv_to_load_in_ac_state`) = **Programme 23**,
    overload to bypass -> now `overload_to_bypass_function`;
  - config pack digit 4 (`eco`) = **Programme 06**, overload automatic
    restart -> now `overload_restart_function`;
  - config pack digit 6 (`does_machine_have_output`) = **Programme 07**,
    over-temperature automatic restart -> now `over_temperature_restart_function`;
  - config pack digit 1 (`charging_priority_order`) = **Programme 50**, grid
    regulation ("Grid Connection Protocol Type" in the app), Mode n = code
    n-1 -> new `grid_regulation_mode`. This firmware's factory value is Mode 4,
    not the manual's Mode 5.
  The three `*_function` keys were declared but never decoded; they are
  decoded and enabled now. The five mislabelled keys are removed; config pack digit 5 stays unidentified. ECO
  (Programme 08) has no known telemetry position, so its switch stays
  optimistic.

### Added

- **"Grid Regulation Mode" select (Programme 50), with read-back.** Captured
  from the vendor app: `^S???RS0<n>` (Mode 4 = `^S???RS03`, answered `^1`),
  Mode n = code n-1. Offers Modes 1, 2, 4 and 5; Mode 3 (57-62 Hz) is left out
  because on a 50 Hz grid it would make the inverter treat the grid as absent.
- **"Sync Inverter Clock" button (Programmes 51-55 in one go).** Sends the
  Home Assistant host's local time as `^S???DAT<YYMMDDhhmmss>`. Not captured
  (the vendor app has no clock setting): it is the PI18 date/time command, the
  same frame family as Programme 50's captured `^S???RS`. A wrong guess is
  refused with `^0`; the inverter's own clock is read back in
  `system_time_ymd` / `system_time_hm`. Press it from an automation to keep
  the clock in step.
- **Switches for Programmes 06, 07, 19 and 23**, with read-back from the
  positions above. Voltronic PI30 flag commands: `PEu`/`PDu`, `PEv`/`PDv`
  (captured from the vendor app), `PEk`/`PDk`, `PEb`/`PDb`.

### Verified on hardware

- 2026-09-27, from Home Assistant: each of the four switches turned off and
  on again (19 on and off), Grid Regulation Mode set to Mode 2 and back to
  Mode 1, and the clock synced (`^S???DAT` answered `^1`, the inverter's
  clock moved from 08:41 to 08:53). Every change was read back from the
  inverter's own telemetry, so the commands that were not captured
  (`PEu`/`PDu`, `PEk`/`PDk`, `PEb`/`PDb`, `^S???RS00`/`RS01`, `^S???DAT`)
  are confirmed.

## [2.6.63] - 2026-09-26

### Added

- **Output Source Priority, Charger Priority and Solar Supply Priority now
  read the inverter's real state back.** While restoring the settings after
  the factory reset of 2026-09-26, `POP01`, `PCP02` and `PVENGUSE01` were
  sent one after the other; exactly three 93VQ positions moved, each to the
  value sent -- token 0 (0 -> 1), the aux pack's third digit (1 -> 2) and the
  config pack's fourth digit (0 -> 1) -- and each had held the user's own
  setting (SBU, OSO, LBU) before the reset. New sensors
  `output_source_priority`, `charger_priority`, `solar_supply_priority`,
  with the selects' option labels, back the three selects.
- Removed `input_source_prompt_function` and `parallel_role`: they were the
  solar supply and charger priority digits under the vendor portal's labels.

## [2.6.62] - 2026-09-26

### Added

- **"Equalization Voltage" number (48.0-60.0 V, 0.1 V steps), manual
  Programme 31, with read-back.** PI30 documents `PBEQV<nn.nn>`. Read back
  from dHrK token 7, which the factory reset of 2026-09-26 moved 56.0 ->
  58.4. The number controls now accept decimal steps: a value off the 0.1 V
  grid, or out of range, is refused before anything is sent. Its read-back
  was checked against the front panel (Programme 31 showed 58.4, as dHrK[7]).

### Fixed

- **Dual output state is dHrK token 0, and the "Dual Output" switch now reads
  it back.** Capture `captures/2026-09-26_real-cloud_parallel.pcap`: the
  vendor app's toggle sent `PDAULC00` (dual output off) and the next `HEEP2`
  read showed dHrK token 0 go 1 -> 0 (it was 0 before the factory reset of
  2026-09-26 turned it on). `dual_output_mode` used to come from 93VQ's
  config pack digit 5, which reads 1 whatever the dual output state -- the
  reason the 2.6.48 read-back looked stuck on ON. That digit is no longer
  decoded.

### Confirmed

- Back To Grid / Back To Battery Voltage (2.6.61) work and are not locked
  with a lithium battery type: 46 -> 44 V and 54 -> 48 V, both read back.

## [2.6.61] - 2026-09-26

### Added

- **"Back To Grid Voltage" (44-51 V) and "Back To Battery Voltage" (48-58 V)
  numbers, manual Programmes 12 and 13, with read-back.** Not offered by the
  vendor app: PI30 documents `PBCV<nn.n>` and `PBDV<nn.n>`. Read back from
  dHrK tokens 4 and 5, which the factory reset of 2026-09-26 moved 44 -> 46
  and 48 -> 54. They may be locked while a lithium battery type with BMS
  communication is selected (as Programme 24 is); the inverter would then
  NAK and the entities keep showing the real value.

### Confirmed

- Output Voltage (`V<nnn>`) and Max Utility Charge Current (`MUCHGC0nn`)
  from 2.6.60 work: every write ACKed and read back within about 15 s.

## [2.6.60] - 2026-09-26

### Added

- **"Output Voltage" select (220/230/240 V), manual Programme 10, with
  read-back.** Not offered by the vendor app. Voltronic's PI30 protocol
  documents `V<nnn>` for the output rated voltage, in the same command family
  as `POP`/`PCP`/`PGR`/`PBT`, all confirmed on this inverter; a wrong guess
  would only be NAKed. Read back from `output_set_voltage`, the 93VQ config
  pack tail, which the factory reset of 2026-09-26 moved from 240 to 230.
- **"Max Utility Charge Current" select (2, 10-90 A), manual Programme 11,
  with read-back.** Not offered by the vendor app either: PI30 documents
  `MUCHGC<m><nn>` (m = parallel machine number, 0 here). Capped at 90 A since
  above 99 A the documented format changes and cannot be checked. Read back
  from `max_utility_charge_current_a` (93VQ token 2), moved 2 -> 30 A by the
  factory reset.

### Confirmed

- The ECO switch (2.6.59) works: `PEj`/`PDj` were ACKed and the front panel
  went `SEn` then `SdS`.

## [2.6.59] - 2026-09-26

### Added

- **"ECO Power Saving" switch, manual Programme 08 -- to be confirmed on the
  front panel.** The vendor app does not offer this setting, so nothing could
  be captured. The backlight and buzzer commands this bridge already sends
  turn out to be Voltronic PI30's standard flag commands (`PEx`/`PDx`,
  `PEa`/`PDa`), and PI30 assigns flag `j` to power saving: `PEj`/`PDj` with
  the same CRC16/XMODEM framing. Optimistic (no read-back) for now: the
  existing `eco` sensor reads On while the panel shows `SdS` (disabled), so
  its 93VQ digit is not trusted yet.

## [2.6.58] - 2026-09-26

### Added

- **Four `number` controls for the BMS SOC thresholds, manual Programmes
  38-41, with telemetry read-back.** Captured from the vendor app
  (`captures/2026-09-26_real-cloud_prog38-41-soc.pcap`): `BMSSDC` (38, BMS
  Lock Machine), `BMSB2UC` (39, Restore Mains Charging), `BMSU2BC` (40,
  Restore Battery Discharging), `BMSSRC` (41, Inverter Startup), each a
  3-digit percent. Every accepted write was answered `(ACK9` and read back by
  the next `HEEP1` at 93VQ tokens 10-13, and the inverter's front panel
  showed the same four values (15/25/30/20), which fixes the mapping.
  - The inverter NAKs anything that is not a multiple of 5, so the HA
    controls step by 5 and the bridge refuses other values before sending.
  - Raising 38 made the inverter raise 39 and Programme 62 on its own to keep
    them 5 points above it; the read-back shows whatever it settles on.
  - Programme 38 shuts the inverter down below it, and the inverter powers
    Home Assistant on the reference installation: the bridge refuses a value
    at or above the current SOC, and refuses 38 entirely while BMS
    communication is not confirmed (the SOC is then the inverter's own
    99-100 % estimate).

## [2.6.57] - 2026-09-26

### Added

- **"BMS Communication Normal" is decoded for the first time (Yes/No) and
  enabled by default.** It is the first bit of Yavb's flag word, proven by
  three independent communication losses in
  `captures/2026-09-26_real-cloud_battery-type*.pcap` and
  `captures/2026-09-26_real-cloud_bms-cable.pcap`: battery type set to PYL
  twice (a protocol this BMS does not speak, inverter warning 61) and the BMS
  cable unplugged for 4 minutes with the type left on GRO. The bit read 0
  during each loss and 1 again after each recovery; nothing else in Yavb
  tracked the cable test. While it reads No, the inverter's SOC is its own
  estimate (99-100 % against a real 43 %), so SOC-based automations should
  check it first. Removed from `UNDECODED_SENSOR_KEYS`.

## [2.6.56] - 2026-09-26

### Added

- **New `select` control, "Battery Type" (`PBT0`, manual Programme 05), with
  telemetry read-back.** Captured from the vendor app
  (`captures/2026-09-26_real-cloud_battery-type-app.pcap`): `PBT04` for
  Pylontech then `PBT06` for Growatt, CRC16/XMODEM matching the wire
  byte-for-byte, each acknowledged `(ACK9` and read back by the next `HEEP1`
  as 93VQ code 4 then 6. All ten types of the manual are offered on the
  user's request; the eight other codes follow the manual's option order and
  are not captured. The select reads the inverter's real type back from
  `battery_type` (2.6.55).
  - Caution, recorded in `fakecloud.SELECT_SETTINGS`: on the reference
    installation a battery type change once cut the inverter's AC output,
    which also powers Home Assistant; PYL with this BMS raises warning 61
    (BMS communication lost) and makes the SOC read 99-100 %.

## [2.6.55] - 2026-09-25

### Fixed

- **Buzzer, LCD backlight and battery type now decoded from the right 93VQ
  positions, and the "Buzzer" and "Backlight" switches read the inverter's real
  state back.** Capture `captures/2026-09-25_real-cloud_buzzer-backlight.pcap`:
  the vendor app changed one setting at a time and refreshed after each (6
  `HEEP1` reads, each answered with 93VQ):
  - buzzer On/Off moved token 5 alone -> `buzzer_function` (it read token 6
    before, which is why its 2.6.49 read-back looked stuck);
  - backlight On/Off moved token 6 alone -> `lcd_back_lighting`, decoded for the
    first time and enabled by default;
  - battery type Pylontech/Growatt moved the config pack's 3rd digit alone
    (`4` / `6`) -> `battery_type`, decoded for the first time and enabled by
    default. The other eight codes follow the manual's Programme 05 option
    order (0 AGM, 1 FLD, 2 USE, 3 LIA, 4 PYL, 5 TQF, 6 GRO, 7 FEL, 8 LIB,
    9 LIC), which both observed codes fit exactly; anything else is raw.
  - Removed `automatic_return_to_first_page` and `working_mode`: they were those
    same positions under the vendor portal's labels. Their old HA entities are
    left orphaned and can be deleted.
  - `mqtt._CONTROL_TELEMETRY_STATE` gains `buzzer` and `backlight`.
    `dual_output` stays optimistic until its field is checked the same way.
  - Battery type is read-only on purpose: changing it cuts the inverter's AC
    output for a moment.

## [2.6.54] - 2026-09-25

### Fixed

- **"Mains Input Range" was decoded from the wrong field, and the "Grid
  Working Range" select now reads the inverter's real state back.** Two
  captures of the vendor cloud traffic (`captures/2026-09-25_real-cloud_*.pcap`):
  with the inverter on UPS then switched to APL by the vendor app (`PGR01` ->
  `PGR00`), WdRR token 10 stayed `11` both times -- it was never the range --
  while 93VQ's config-pack first digit went `1` -> `0` and nothing else in
  93VQ changed. WdRR's mains loss low point moved 170 V -> 90 V at the same
  time, matching manual Programme 03 (UPS 170-280 V, APL 90-280 V).
  - `mains_input_range` now comes from that 93VQ digit (`UPS` / `Appliance
    (APL)`); WdRR's token is kept as raw `mains_input_range_code` only.
  - `ac_charging_switch` removed: it was that same digit under the vendor
    portal's label. Its old HA entity is left orphaned and can be deleted.
  - `mqtt._CONTROL_TELEMETRY_STATE` gains `grid_working_range` again, backed
    by `mains_input_range`. The 2.6.51 theory of a shared read-back bug does not
    hold for this one; dual_output and buzzer stay optimistic until their own
    fields are checked the same way.
  - The vendor app's "refresh" for Battery Type, Buzzer, Backlight and Grid
    Working Range all send the same read, `HEEP1` (reply = 93VQ); Parallel Mode
    sends `HEEP2` (reply = dHrK).

## [2.6.53] - 2026-09-24

### Added

- **New `select` control, "Solar Supply Priority" (BLU/LBU), found entirely
  from a capture of the vendor app's own traffic -- not ported from
  SoT-RWB1-Server-Emulator, which does not document this channel at all.**
  Same method as 2.6.52's `grid_working_range` fix: user activated BLU then
  LBU in the official app, `tcpdump` on `end0` caught `PVENGUSE00` (BLU) then
  `PVENGUSE01` (LBU), CRC16/XMODEM verified byte-for-byte on both frames.
  New `fakecloud.SELECT_SETTINGS["solar_supply_priority"]` (channel
  `PVENGUSE`) and `mqtt._CONTROL_SELECTS["solar_supply_priority"]`. The
  option labels match the app's own ("BLU"/"LBU") rather than an expanded
  guess -- their exact meaning (a Battery/Load/Utility ordering for excess PV
  power, distinct from output_source_priority and charger_priority) is
  inferred, not confirmed by the app's own UI text.

## [2.6.52] - 2026-09-24

### Fixed

- **`grid_working_range` (`PGR0`) had UPS/Appliance swapped**, same class of
  bug as `output_source_priority` in 2.6.46. Root-caused with a real capture:
  `LOCAL_CLOUD_IP` disabled and `FORWARD_ALL_INVERTER_TRAFFIC=true` so the
  dongle talked to the real vendor cloud, `tcpdump` on `end0` while the user
  drove the official app directly (APL then UPS 2s later). The wire showed
  `PGR00` for APL and `PGR01` for UPS -- the source's documented SBU-style
  mapping (UPS=0, APL=1) was inverted for this hardware. CRC16/XMODEM
  confirms both frames byte-for-byte. `fakecloud.SELECT_SETTINGS` corrected
  (`ups`->"1", `appliance`->"0"). The sequence ended on UPS and telemetry
  read UPS afterwards, consistent with success; there is no mid-sequence
  telemetry snapshot for the 9s spent on APL (below the ~5min spontaneous
  push cycle), so that leg rests on the app's own wire evidence.

## [2.6.51] - 2026-09-23

### Reverted

- **"Dual Output" switch loses its telemetry read-back too -- `mqtt.
  _CONTROL_TELEMETRY_STATE` is now empty.** It was the one entry kept in
  2.6.50 on the assumption it "worked correctly"; the user now reports it
  also stuck always showing ON regardless of the real state, same symptom as
  "buzzer" and "grid_working_range" before it. All three attempts have now
  failed the same way, which points at a shared bug in how this file reads a
  group's retained state topic back (not in any individual parsers.py field,
  since all three source fields looked equally reliable by code inspection).
  Every control is optimistic-only again, matching behavior before 2.6.48.
  Root cause not yet found -- see `_CONTROL_TELEMETRY_STATE`'s docstring
  before trying this again.

## [2.6.50] - 2026-09-23

### Fixed

- **`charger_priority` (`PCP0`) sent the wrong value for every option, rotated
  by one.** The user read the inverter's own front panel after each option:
  sending "0" (documented as OSO) showed CSO, "1" (CSO) showed SNU, "2" (SNU)
  showed OSO -- same class of bug as `output_source_priority` in 2.6.46, this
  time affecting all three options instead of two. `fakecloud.SELECT_SETTINGS`
  corrected (OSO->"2", CSO->"0", SNU->"1"). Also confirmed absent on this
  inverter: **"Solar Residual (SOR)" is removed from the select entirely**
  rather than left in as a non-functional fourth option.

### Reverted

- **"Buzzer" switch and "Grid Working Range" select lost the telemetry
  read-back added in 2.6.48/2.6.49.** The user reported "Buzzer" stuck
  always showing/sending On regardless of the real state, and "Grid Working
  Range" stuck always reading back "UPS" no matter which option was sent.
  Cause not yet understood -- `dual_output_mode`'s read-back (also 2.6.48)
  behaves correctly, so this is not a blanket problem with the mechanism.
  Both entities are back to the optimistic write-and-hope state every other
  control uses, pending diagnosis. See `mqtt._CONTROL_TELEMETRY_STATE`'s
  docstring for the do-not-re-add-blindly note.

## [2.6.49] - 2026-09-23

### Changed

- **"Buzzer" (switch) now shows the inverter's own reported state too**, same
  treatment as 2.6.48's "Dual Output"/"Grid Working Range": backed by
  `buzzer_function` (parsers.py ~line 2164), the same reliable positional-bit
  decode as `dual_output_mode` on the very same source line -- not a guess.
  `mqtt._CONTROL_TELEMETRY_STATE` gained a `"buzzer"` entry (group
  "diagnostics", per `get_sensor_group`); no longer published optimistically
  in `_handle_control_message`. "Backlight" stays optimistic -- its would-be
  telemetry field, `lcd_back_lighting`, has no decode line in parsers.py at
  all (always unknown).

## [2.6.48] - 2026-09-23

### Changed

- **"Dual Output" (switch) and "Grid Working Range" (select) now show the
  inverter's own reported state instead of an optimistic guess.** The user
  pointed out that both already have a matching telemetry sensor: `sensor.
  ..._load_siseli_dual_output_mode` (parsers.py's `dual_output_mode`, a
  positional bit decoded the same reliable way as every other prefix bit on
  that line -- not a guess) and `sensor..._grid_siseli_mains_input_range`
  (parsers.py's `mains_input_range`, which has only ever confirmed the "UPS"
  code; the select's other option is inferred by elimination, being the only
  other choice, not independently confirmed). New `mqtt._CONTROL_TELEMETRY_STATE`
  points each entity's discovery `state_topic`/`value_template` at that
  sensor's own group topic instead of the write-and-hope
  `control_state_topic`, and `_handle_control_message` no longer publishes an
  optimistic state for either one. Every other control (backlight, buzzer,
  output_source_priority, charger_priority, the two buttons) is unaffected --
  no matching telemetry sensor is known for them yet.

## [2.6.47] - 2026-09-23

### Added

- **Two more `select` controls, "Charger Priority" (`PCP0`) and "Grid Working
  Range" (`PGR0`), ported the same way as 2.6.45's "Output Source Priority"
  (`fakecloud.build_write_ci`, same CRC mechanism, now proven working end-to-end
  on our own inverter).** Their option values are carried over UNCHANGED from
  SoT-RWB1-Server-Emulator's spec -- given `POP0`'s own values were wrong on the
  first try (fixed in 2.6.46) and had to be corrected by physically reading the
  inverter's front panel, these two should be treated as equally unverified
  until confirmed the same way, even though the write mechanism itself is no
  longer in doubt.

## [2.6.46] - 2026-09-23

### Fixed

- **`output_source_priority` (the "Output Source Priority" select added in 2.6.45)
  sent the two options swapped.** SoT-RWB1-Server-Emulator's spec documents the
  dongle-side `POP0` channel as SBU=0 / SUB=1; confirmed by the user physically
  reading the inverter's own front panel after each write (2026-09-23), sending
  "1" made the panel show SBU and "0" made it show SUB -- inverted from that
  source's documented mapping, on our specific inverter. `fakecloud.SELECT_SETTINGS`
  now sends the values that match what this inverter actually does
  (`solar_battery_first` -> "1", `solar_first` -> "0"); no change to the HA-facing
  option labels or `mqtt.py`, since the bug was entirely in the value sent to the
  dongle, not in how the entity presents itself.

## [2.6.45] - 2026-09-23

### Added

- **First generic dev_rpc write command and a new `select` control, "Output Source
  Priority".** Until now every working control command (backlight, buzzer, dual
  output, clear fault) was a raw, pre-captured `ci` byte string, replayed exactly
  as the vendor cloud/app sent it -- see `protocole-cloud-dongle/README.md`
  section 6. `fakecloud.build_write_ci(channel, value)` is new: it builds
  `"<CHANNEL><VALUE><CRC16-XMODEM><\r>"` and base64-encodes it, for channels this
  bridge has never itself captured. The channel table (`fakecloud.SELECT_SETTINGS`,
  starting with just `output_source_priority` / `POP0`) and the CRC algorithm are
  ported from an independent reverse-engineering of the same RWB1 dongle protocol,
  [SoT-RWB1-Server-Emulator](https://github.com/filipsworks/SoT-RWB1-Server-Emulator).
  The CRC itself is cross-checked against this bridge's own real captures: computing
  it over the bare mnemonic `PDAULC01`/`PDAULC00`/`FAULTC` reproduces the exact CRC
  bytes already seen in `CONTROL_COMMANDS`/`_CLEAR_FAULT_CODE_CI` -- including
  resolving a loose end from that section (`FAULTCG`'s trailing `G` is not part of
  the mnemonic, it is this CRC's own high byte, which happens to be printable
  ASCII). `output_source_priority` was picked first because it is an ordinary,
  instantly-reversible operating-mode toggle already in the vendor app (not a
  voltage/current/SOC setpoint), and its effect can be cross-checked afterwards
  against this bridge's own PI30 telemetry decode (`pi30_output_source_priority`)
  where that protocol is spoken. **Not yet physically confirmed on our own
  inverter** -- unlike the four pre-existing commands, this one has not been
  observed to actually change the inverter's behaviour yet.
- `mqtt.py`: new `_CONTROL_SELECTS` table and a `select` MQTT-discovery branch in
  `publish_control_discovery`/`subscribe_control_topics`/`_handle_control_message`,
  parallel to the existing `_CONTROL_SWITCHES`/`_CONTROL_BUTTONS` machinery.

## [2.6.44] - 2026-09-22

### Added

- **Voltronic PI30 detection and diagnostic decode, ported from upstream
  fadmaz/siseli-ha commit `ae1c05d` (2026-09-22).** No inverter on this install speaks
  PI30 today -- this is held in reserve so that if one is ever added, it is decoded and
  reported from the first payload instead of reading "not a supported inverter variant"
  with nothing to act on. New `pi30.py`: verifies every frame's CRC16-XMODEM before
  reading a token, maps 20 block names to their query for this protocol family, and
  decodes 71 fields by position, each behind its own shape check. Hooked into
  `parsers.py::parse_payload` only in the branch reached when the existing Device-A
  decoder recognised nothing at all -- unreachable for every device this build already
  supports, since the two block-name sets are disjoint (pinned by test). Creates no HA
  entity and writes no state; logs `[PI30 DECODE]` (or `[PI30 BLOCK NAMES UNKNOWN]` for a
  PI30 device whose dongle labels its blocks differently) at warning level, one-shot per
  device state, re-arming on a mode or status-bit change.
- Ported alongside it: the PI30 test suite (`tests/test_pi30_detection.py`,
  `test_pi30_fields.py`, `test_pi30_frames.py`, 70 tests against a real captured device
  paired with vendor-portal screenshots), the PI30 fixtures in `tests/captures.py`, and
  `captures/2026-09-02_device-c-voltronic-pi30.md`.

### Fixed

- **Two test-isolation leaks found by upstream while building the above, ported
  alongside it.** `tests/test_logging.py` reloaded `loggers` without restoring
  `CURRENT_LOG_LEVEL` afterwards, so whichever level the last case in that file used kept
  applying to every test that ran later in the same session -- invisible until a test
  elsewhere asserted on a warning-level log line while it was in effect. Each case now
  restores the shipped level in `tearDown`. `TestSignalHandlerInstallation` reloaded
  `core` without first reloading `config` under a controlled environment, so it could
  pick up whatever options a previous test's own `config` reload left bound -- now uses
  `patched_env()`, already present in this fork's `tests/helpers.py`.

Full suite after this change: the same 29 pre-existing failures as 2.6.43 (doc/version/
pin/anchor checks in `test_packaging.py`, `test_truthfulness::test_every_outcome_has_a_label`),
none new; 70 new tests pass.

## [2.6.43] - 2026-09-21

### Fixed

- **The BMS max/min cell voltage, their positions and the cell delta no longer publish
  zeros.** On the reference install (HPVINV04, 16-cell bank) the battery type set on the
  inverter decides which BMS data set is live, and the two are never live together: with one
  type `v09K` (cells 1-16) and the remaining capacity are live while the four summary
  tokens of `uxJp` (max mV, its position, min mV, its position) read `0000`; with the other
  the summary is live and `v09K` and the remaining capacity repeat their last values. Home
  Assistant history shows the switches (2026-09-15 15:00 to A, 2026-09-20 about 06:09 back
  to B). In the first case the bridge published `bms_max_cell_mv`, `bms_min_cell_mv`, both
  positions and `bms_cell_delta_mv` as 0. A summary is now usable only when both voltages
  are plausible (2000-5000 mV). When it is not, and the same payload carries at least two
  valid cells, the five values are computed from those cells (positions count from 1, the
  first cell wins a tie; what the BMS itself does on a tie is unknown); with no cells they
  are not published at all. The BMS's own whole-bank summary still wins whenever it is
  usable, `v09K` alone still writes no summary, and a summary computed from the list
  describes only the listed cells, which is the whole pack on a 16-cell install. Nothing
  can make the cells and the summary live at the same time: that is the inverter's choice.
  New `TestCellSummaryFallback` in `tests/test_truthfulness.py`; DOCS.md Troubleshooting has
  a matching entry.

## [2.6.42] - 2026-09-21

### Fixed

- **The inverter's answers to the router's pings are now relayed.** The router pings the
  inverter about every 2.5 s (measured on `end0`). The request always reached the
  inverter, because router-to-inverter traffic is relayed, but the echo reply is
  inverter-to-router non-broker traffic and fell into the block that only forwards under
  `FORWARD_ALL_INVERTER_TRAFFIC`. It was counted in `dropped_non_broker={'OTHER': N}` and
  discarded, so the router never saw the inverter answer. That block was not a deliberate
  refusal (SECURITY.md states that every captured packet is re-emitted to its real
  destination; the drop is the documented "gateway but not a router" limitation), so
  ICMP echo replies (type 0) are now relayed without any option, under the same
  layer-2 guard as the opt-in path. Every other non-broker packet, including ICMP echo
  requests and other ICMP types, is still governed by `FORWARD_ALL_INVERTER_TRAFFIC`.

## [2.6.41] - 2026-09-21

### Changed

- **The local cloud's TCP responder no longer raises a warning for every duplicate
  segment.** `[LOCAL CLOUD DIAG] ... dropped ... payload discarded` was logged for any
  data segment that did not land at the expected sequence number. On a healthy session
  that is mostly the Wi-Fi dongle re-sending a reply whose ACK it has not seen yet
  (measured over 34 minutes: about 3.4 a minute, 114 of 121 exact duplicates of the last
  segment, six older duplicates, one gap, no overlap, no stalled poll). `tcpstack.py`
  now tells the cases apart: a duplicate is logged at debug as `[LOCAL CLOUD DUP]`; a
  gap (the segment starts beyond the expected byte) and an overlap (it starts before it
  but reaches past it, carrying new bytes) stay warnings under `[LOCAL CLOUD DIAG]`,
  with the number of bytes involved. Behaviour on the wire is unchanged: the segment is
  still discarded and re-ACKed. Covered by the new `tests/test_tcpstack.py`.

## [2.6.31] - 2026-09-12

### Fixed

- **`dev_rpc` polling now works continuously — telemetry every `TELEMETRY_POLL_INTERVAL_SEC`
  (20 s verified in production) instead of the dongle's native 5 minutes.** The local
  cloud's TCP responder never retransmitted its own segments; the dongle end is Wi-Fi,
  and one lost poll permanently desynchronised our send stream — every later poll sat
  beyond the hole, never delivered to the dongle's application, which only re-ACKed the
  old position. That was misread as "the dongle stops answering polls after the first
  1-2 per session". `tcpstack.py` now tracks inbound ACKs, keeps a per-connection
  retransmission queue with backoff (ticked every second from `telemetry_poll_loop`,
  which now runs whenever `LOCAL_CLOUD_IP` is set), and serialises sends under a
  per-connection lock — the poll thread and the sniffer thread previously interleaved
  writes to the same sequence counter unsynchronised.
- **A SYN with a new ISN silently replaced a still-established connection**, discarding
  its sequence tracking; the dongle's live request was then re-ACKed forever and never
  read, until its ~75 s timeout RST — the slow multi-attempt bootstrap. Per
  RFC 793/5961 the bridge now answers with a challenge ACK and keeps the state; a peer
  that genuinely restarted replies with a valid RST that frees the slot cleanly.
- **A stale RST could kill the current connection.** This firmware reuses low source
  ports and near-sequential ISNs across attempts, so a late RST aimed at an abandoned
  handshake matched the live connection's key. RSTs are now honoured only when their
  seq matches the expected next byte (RFC 5961-style); others are logged and ignored.

### Changed

- `dev_rpc` requests send a constant `"i":501`, byte-identical to the real cloud. The
  2.6.30 rpc-id increment was built on a false diagnosis: `dongle_reboot.pcap` shows the
  vendor cloud repeating `"i":501` on every poll and being answered every time — the
  "deduplicated by i" behaviour was the missing-retransmission bug above.

## [2.6.22] - 2026-09-02

### Fixed

- **2.6.21 crashed on every payload when the broker had never connected** — the exact
  install its own change was written for. The publish outcome was initialised inside
  `if DISCOVERY_PUBLISHED:` and read outside it, so a bridge that had not yet reached the
  broker raised `UnboundLocalError` on each payload. The handler swallowed it as
  `[PARSER ERROR]`, `parse_payload` returned `False`, and the caller added
  `[MQTT PAYLOAD NOT PARSED]` — two false diagnoses per payload, and `record_telemetry`
  never ran, so the availability watchdog eventually marked every entity unavailable on a
  bridge that was decoding perfectly.

  There are now four outcomes rather than three: a broker that has never connected is not
  the same as one that dropped mid-run, and it is not throttling. Hoisting the default
  above the gate would have swapped the crash for a new false statement.
- **A broker refusing the connection logged on every retry.** The failure flag was cleared
  at the top of `on_connect` before `rc` was read, and the error branch had no gate at all,
  so wrong credentials produced an error line every reconnect delay — thousands a day — and
  a refusing CONNACK re-armed the *unreachable* message, cross-contaminating two different
  faults. The flag now stores the failure *kind*, so a repeat is silent while a genuine
  change of kind is still reported. The message also names the likely cause.
- `republish_state` discarded the publish result and returned a literal `True` under a
  docstring promising otherwise, so the heartbeat that keeps `expire_after` from ageing
  entities out reported success on every tick of an outage.
- A publish that *raises* rather than returning an error code was reported as
  `[PARSER ERROR]` — a broker fault attributed to the decoder.

### Changed

- The `[HEALTH]` line reports `avail=` beside `broker=`. They are different faults with the
  same symptom: `broker=DOWN` is the transport, `avail=OFFLINE` is the bridge declaring its
  own data stale while the broker is fine.

## [2.6.21] - 2026-09-02

### Fixed

- **The battery-current guards rejected real readings, silently.** Four guards dropped any
  current outside `0..300` A — below what this hardware declares for itself, since the
  reference device reports a `bms_charge_current_limit_a` of **390 A**. A reading between
  the two was discarded with nothing published and nothing logged, and the consequences ran
  on from there: the surviving source was then used without the
  `[ENERGY SOURCE DISAGREEMENT]` warning, which only fires when both are present, and if
  both were dropped the charge and discharge power both read 0 and `battery_status`
  reported **Idle**. A high-current moment presented as an idle battery contributing no
  energy. The bound is now 1000 A and a rejection is reported once as
  `[BATTERY CURRENT REJECTED]`, naming the field and the bound.
- **An unreachable MQTT broker produced no diagnostic at all, and the log said the
  opposite.** `connect_async` plus `loop_start` retries forever in paho's network thread
  and reports a failed connect only through `on_connect_fail`, which nothing registered;
  `on_connect` fires only on a CONNACK, so its error branch covered a broker that answers
  and refuses and never one that is absent. Meanwhile a QoS 0 publish on a disconnected
  client returns `MQTT_ERR_NO_CONN` and is dropped without raising, and every call site
  discarded that return — so the bridge logged **"Published to HA" for payloads that
  reached nothing**.

  `on_connect_fail` is now registered and reports once per outage (re-armed by a
  successful connect, so a later one is reported again). `publish_grouped_state` returns
  whether the broker accepted the publish, and the per-payload line says which of three
  things happened: `Published to HA`, `Decoded, publish throttled`, or
  `Decoded but NOT published -- broker unreachable`. The `[HEALTH]` line now opens with
  `broker=up` or `broker=DOWN`.

### Changed

- `DOCS.md` no longer says a failed MQTT connection means the credentials are wrong. That
  is one of two failures and not the one a stopped broker produces.

## [2.6.20] - 2026-09-02

### Fixed

- **A dead packet-capture thread is no longer silent.** If the scapy capture thread ended
  — a closed capture socket, an interface going away — nothing noticed. The ARP spoofer
  kept poisoning on its own flag and forwarding lives inside the capture callback, so the
  inverter stayed redirected at a bridge that no longer forwarded and **lost its route to
  the vendor cloud entirely**. The first symptom was sensors going stale up to half an hour
  later, indistinguishable from a quiet inverter.

  The health loop now checks capture-thread liveness every 10 s. A dead thread is logged at
  error level with its cause and **restarted in place**, which costs milliseconds, so the
  outage is the detection latency rather than a container restart. Restarting rather than
  stopping because stopping cannot be relied on to recover: `config.yaml` declares no
  watchdog, so whether the add-on comes back is a toggle the code cannot enforce.

  After three consecutive failures it gives up, restores both ARP caches and exits
  **non-zero** — exit 0 is what a user-requested stop looks like and is the least likely
  status to prompt a restart.

  Liveness is read from the thread object rather than scapy's `running` or `exception`
  attributes: `exception` is `None` whenever the sniff loop simply returns, which is what a
  closed socket produces, and `running` is cleared on that path indistinguishably from a
  deliberate stop.
- The scapy warning that explains *why* capture stopped was being discarded — the runtime
  logger was muted to `ERROR`. It is captured now and reported as `cause=`, which for the
  most likely death is the only account that exists.
- The message no longer claims an `AUTO_INTERCEPT=false` install has lost its cloud
  connection. In that mode the bridge is passive and the inverter's own path is untouched;
  saying otherwise sent the reader to their router for nothing.

## [2.6.19] - 2026-09-02

### Fixed

- **A system clock step no longer fabricates kWh.** Every duration the add-on measures
  now comes from `time.monotonic()` instead of the wall clock. A Raspberry Pi has no RTC,
  so it boots with a wrong clock and NTP steps it — routine on the reference platform, and
  previously indistinguishable from elapsed time. The existing clamp bounded the damage at
  `ENERGY_MAX_DT_SEC` but did not prevent it: one step credited up to 1200 s of the current
  power into five `total_increasing` counters, which is **1.67 kWh at 5 kW** that could
  never come back down. A monotonic clock cannot see the step at all.
- **The startup grace no longer presents a restored cache as live data for half an hour.**
  When no payload had been decoded yet, availability was granted for the full
  `TELEMETRY_TIMEOUT_SEC` — 1800 s by default, and 3600 s reachable. It now has its own
  bound, `STARTUP_GRACE_SEC`, defaulting to **1200 s**. That bounds process start to first
  *decoded* payload, which is structurally larger than the gap between payloads: the
  inverter's connection to the cloud is long-lived, so a restarted bridge always joins
  mid-stream and discards until a frame boundary, costing the payload in progress. The
  worst case composes as the 15 s ARP wait plus one payload lost to that join plus one
  full 600 s gap — the largest ever measured. The unavailable log now names whichever
  bound actually fired, rather than always printing the telemetry timeout.
- **Every Dependabot dependency PR failed CI by construction.** The runtime pins were
  written in both `pyproject.toml` and `siseli_local_bridge/requirements.txt`, and the base
  image in both the `Dockerfile` and `scripts/smoke-test.sh`, each pair held equal by a
  test. Dependabot raises one PR per ecosystem and directory, so it could only ever bump
  one copy — and `main` requires those checks, so the automation could not produce a
  mergeable PR. Both facts are now single-sourced: `pyproject.toml` takes its
  dependencies dynamically from `requirements.txt` (the file that ships in the image),
  and `smoke-test.sh` reads `ARG BUILD_FROM` out of the `Dockerfile`. The tests assert
  single-sourcing instead of equality.

### Changed

- **`DOCS.md` no longer states the `INVERTER_COUNT` basis as fact.** It said the inverter
  "reports per-unit figures"; the project's own captures record that as the one open
  question no capture so far has answered. The scaling is unchanged — both readings are
  live, and switching would swap one unproven basis for another while breaking every
  parallel user's kWh history. The caveat notes that at the default `INVERTER_COUNT` of 1
  the multiplication is a no-op.
- `dHrK[16]` (`second_output_battery_capacity`) now records what its one sample proves and
  what it does not. The token reads `50000` and the portal reads 50%, which rules out a
  zero-padded three-wide field but leaves a variable-width number in a five-character slot
  live — under which `10000` is 10 or 100. The arithmetic is unchanged, because a rule for
  100 would pick between two hypotheses on no evidence. The settle condition is recorded in
  `captures/README.md`, and the value is pinned in the golden dict.
- Corrected two stale notes: the counted-CRC comment cited 9 of 13 against a shipped
  fixture of 12, and the "NOT YET CAPTURED" block read "covers all of them except none".

## [2.6.18] - 2026-09-02

### Fixed

- **The unsupported-protocol diagnostic called a plainly-textual device "binary"**
  (issue #32, a Falcon VMIII 4200W). 2.6.17 collapsed every byte of every block into one
  boolean, so a two-byte checksum anywhere forced `body="binary"` — the word `DOCS.md`
  named as the strongest signal of a different protocol family. Bodies are now classified
  individually as `ascii`, `ascii+binary_tail` or `binary`, and a mixed payload reports
  every shape it contains.
- **`[BLOCK RAW]` truncated `hex_preview` at 64 bytes with no marker.** That is the exact
  field `CONTRIBUTING.md` asks reporters to paste, and a truncated body cannot become a
  `BLOCK_*` fixture. It silently cut issue #30's `r8BV` mid-frame and both of issue #32's
  telemetry-bearing blocks — the two most valuable in the report. It now uses the
  `hex_preview` helper that was already in `loggers.py`, which marks truncation.

### Added

- **`looks_like="voltronic_pi30"`.** Issue #32's device is the Voltronic/Axpert PI30
  family: 22 of 22 non-truncated blocks verify as CRC16-XMODEM computed over the frame
  *including* the leading `(`, and one block is literally `(PI30`. Reported on the same
  evidence footing as the Modbus hint — a checksum that verifies against the wire, never a
  shape guess.
- `voltronic_crc_ok` and `body_shapes` alongside the existing `modbus_crc_ok`.
- `CAPTURE_DEVICE_C_VOLTRONIC` — 14 byte-faithful blocks from issue #32. The two hints are
  counted and thresholded rather than any-match because issue #30's Modbus device emits
  exactly one valid Voltronic frame, `(ACK9`; a single match must not label a payload, and
  a test now pins that.

### Changed

- `DOCS.md` no longer presents `body` as the verdict. `recognised=0` is the verdict;
  `body` describes shape, and `looks_like` is the only protocol claim. Each shape is
  explained in a table, and a test asserts every shape the code can emit is documented.

## [2.6.17] - 2026-08-22

### Added

- **The add-on now says when it does not recognise your inverter.** If a payload's blocks
  decode but none are ones this add-on knows, it logs `[UNSUPPORTED PROTOCOL]` once, at
  warning level, with no debug flag required — naming the blocks it saw, whether their
  bodies are ASCII or binary, and whether they verify as Modbus RTU frames.

  Reported in #30: a Beve Mega 6kW sent thirteen unrecognised blocks carrying binary
  Modbus RTU. The parser correctly decoded nothing, but the only trace was an `info` line
  that reads identically to a truncated known block and appears only with
  `unparsed_publish` enabled. The reporter saw every sensor `Unknown` and no cause.
- A separate `[NO VALUES DECODED]` warning for the genuinely different case where blocks
  *are* recognised but yield no values, which the previous message conflated with the above.
- `KNOWN_BLOCK_NAMES`, so the parser can count how many of a payload's blocks it
  recognised. Declared in parallel with the decoder's literals rather than driving them,
  with a test asserting the two never drift.
- `CAPTURE_DEVICE_B_FOREIGN` — byte-faithful frames from the reporter's device, so the
  diagnostic is tested against a real foreign protocol rather than a hand-built stand-in.
  `captures/2026-08-22_device-b-modbus.md` records the CRC proof and register findings.

### Changed

- `DOCS.md` gains a troubleshooting entry titled by the symptom — **"Every sensor reads
  Unknown"** — rather than by the diagnosis the user would have to have already reached.
- `README.md` now reads "45 **of the 207** sensors read `Unknown`". The bare form
  described a normal partial condition of a supported device, which is exactly how the
  reporter of #30 concluded nothing was wrong.

## [2.6.16] - 2026-08-22

### Changed

- CI uses `frenck/action-app-linter@v2`. The action was renamed from
  `action-addon-linter` in August 2026, when Home Assistant renamed add-ons to apps.
  The old reference still resolved, but only through GitHub's rename redirect — which
  stops working the day anyone creates a repository at the vacated name, and would then
  silently run their code in this repository's CI. Still `v2`: the rename shipped as
  `v2.21.1` and there is no `v3`.

### Fixed

- **Generation power was published from cached state, not from the payload in hand.**
  `pv_w` and `pv2_power_w` fell back to `LAST_STATE`, so this device's identity payload —
  which carries no PV block at all — republished the previous payload's PV power every
  time, and `c_generation_energy_kwh` integrated over it. Harmless while `Mpod` and `noeP`
  keep arriving; the moment they stop at dusk the last daylight figure would be held and
  the counter would accrue invented kWh all night. Generation was the only one of the four
  energy domains resting on a cached value, against the rule that a value is published
  only when this payload carries evidence for it — and against the comment on the
  accumulator itself.
- The test that was supposed to catch this seeded only the battery keys into `LAST_STATE`,
  so it passed while generation kept its fallback. It now seeds the PV keys too, and fails
  against the old code.

### Changed

- The `INVERTER_COUNT` sanity check in `DOCS.md` was a false alarm on a correctly
  configured install: run against a real capture with 2 kW of sun it is 40% out, because
  conversion losses and a parallel inverter's own array both land in the gap. It now says
  to run the comparison only at night with PV at zero, where it discriminates cleanly, and
  points at a clamp meter as the measurement that actually settles the scaling.

### Added

- `captures/2026-08-21_2341_discharging.md` — the same device ten hours after the charging
  capture, in the opposite battery state, again simultaneous with the vendor portal. It
  confirms three decodes that no earlier capture could test, because the fields were pinned
  at zero: `dischg_current`, `bms_discharge_current_a`, and that `bms_charge_current_limit_a`
  is not a constant.
- A test asserting `ci.yml` produces exactly the check names that branch protection on
  `main` requires. The two are coupled with nothing connecting them — rename a job or
  change a matrix axis and every PR blocks forever waiting on a check that no longer
  exists, with no error pointing at the cause.

### Removed

- Two decode hypotheses, refuted by pairing the two captures. **`Yavb[1]` is not the BMS
  flag word** — byte-identical in both while the BMS currents swapped ends, and carrying
  three set bits against four affirmative portal flags, so one-digit-per-flag fails
  arithmetically however it is assigned. Twelve sensors were recorded against it.
  **`eo8w[1]` is not the light-status word** — byte-identical while Charging Light Status
  went Flicker to Off. Also settled: `noeP[3]` tracks PV2 activity, not topology, so the
  note asking for a single-inverter capture to confirm it is obsolete.

## [2.6.15] - 2026-08-21

### Fixed

- **The 2.6.14 logo was illegible at the size Home Assistant actually renders it.** The
  frontend caps `logo.png` at `max-height: 40px`, so the 500×200 file rendered at exactly
  100×40 CSS pixels — a 0.2× scale applied to every text height in the source. The
  wordmark was set at 33px and the tagline at 16px, which landed at 6.6 and 3.2 CSS
  pixels. The tagline was not small, it was invisible. The logo is now 760×200 with the
  wordmark at 62px (12.4 CSS pixels rendered) and no tagline.
- `test_the_icon_carries_alpha` asserted PNG colour type ∈ (4, 6), which rejects a
  palette PNG — but colour type 3 carries transparency through a `tRNS` chunk. It forbade
  an optimisation that measures 6.5× smaller for no reason. Renamed to
  `test_the_icon_carries_transparency` and it now accepts a palette with `tRNS`, while
  still rejecting an opaque one. Both branches are exercised.
- The same test's docstring said alpha stops the off-white margin rendering as a pale
  box. The margin was *cropped away* in 2.6.14, not alpha-punched; what transparency
  actually protects is the badge's rounded corners.

## [2.6.14] - 2026-08-21

### Fixed

- **`icon.png` and `logo.png` were JPEG files carrying a `.png` extension**, and
  byte-identical to each other, for the project's whole history. Browsers and the Home
  Assistant frontend sniff content, so they rendered anyway — which is why it went
  unnoticed. Both are now real PNGs.
- The icon's off-white margin rendered as a pale box on Home Assistant's dark theme. It
  is now cropped to the badge with a transparent surround, so the rounded corners
  composite cleanly on any theme, at 256×256. The frontend caps the icon at a 40×40 CSS
  pixel box, so that is 6.4× headroom for high-DPI displays and no more.

### Changed

- **`logo.png` is now a 500×200 landscape brand image** — the badge with a wordmark —
  instead of a copy of the square icon. Home Assistant renders the icon as a small
  square badge in the add-on list and the logo as a wider image on the add-on's own
  page; a square logo was never right for the second. Wordmark set in DejaVu Sans.
- `README.md` references `icon.png` for its right-floated badge. It referenced
  `logo.png`, which at `width="140"` would now render as a 140×56 strip.

### Added

- `TestBrandAssets` pins all of it: both files are real PNGs, the icon is square, the
  logo is landscape, the two are not the same file, and the icon carries an alpha
  channel. It reads the PNG `IHDR` header directly rather than through Pillow, which is
  not in the dev extras — a Pillow-based test would `skipTest` on CI and pass vacuously,
  which is the failure mode that let the mislabelling survive.

## [2.6.13] - 2026-08-21

### Added

- `LICENSE` (MIT) and `NOTICE`. GitHub reported this project as unlicensed while the
  README claimed MIT. The upstream project it was forked from carries no licence of
  its own, so `NOTICE` states what the MIT grant does and does not cover, and lists
  the licences of the bundled dependencies.
- PEP 639 licence metadata in `pyproject.toml`, with a test pinning the
  `setuptools>=77` build floor that the bare SPDX form requires.
- CI now builds and starts the add-on on **aarch64** as well as amd64, using native
  `ubuntu-24.04-arm` runners. `aarch64` has been declared in `config.yaml` all along
  and had never once been built.
- Dependabot now watches `siseli_local_bridge/requirements.txt` and the Home Assistant base
  image pin. Neither was watched before.
- **`siseli_local_bridge/DOCS.md`** — Home Assistant renders this on the add-on's
  **Documentation** tab. The file did not exist, so the tab was empty and users were
  bounced out to GitHub. The reference documentation now lives there: requirements,
  installation, every configuration option, network setup and troubleshooting. This
  deliberately reverses 2.5.0, which consolidated `DOCS.md` into the README — that
  consolidation is what left the tab empty.
- A CI status badge and a licence badge on the README.
- **`SECURITY.md`**. The add-on ARP-spoofs a device on the user's LAN, holds `NET_RAW`,
  `NET_ADMIN` and `host_network`, and runs with AppArmor disabled — none of which was
  explained anywhere. It now is, including `apparmor: false` as a stated gap rather than
  an omission, and the two real risks: ARP spoofing looks exactly like an attack to a
  switch with dynamic ARP inspection, and a hard power loss skips `restore_arp()`.
- `CODE_OF_CONDUCT.md`, `.github/PULL_REQUEST_TEMPLATE.md` and
  `.github/ISSUE_TEMPLATE/config.yml`.
- Issue templates now collect the Home Assistant version, installation type and
  architecture, and offer the brand list as a dropdown. The add-on log is required on a
  bug report, and **the log-scrubbing warning is now an enforced checkbox** — it appeared
  three times in prose and was enforced nowhere.

### Changed

- **`README.md` is a landing page** (411 lines → 125). What it is, how it works, what
  cannot be decoded, what hardware is covered — everything operational is on the
  Documentation tab. Tests pin the split in both directions, and assert that every
  option in the schema is documented on the tab.
- Links in the moved content are absolute. Supervisor renders the Documentation tab
  inside the Home Assistant frontend, where a relative link resolves against the HA
  origin rather than GitHub.

### Fixed

- The README said "around 38 sensors read `Unknown`". It has been 45 since 2.6.1, and
  nothing noticed for eleven releases. That count, and the 207/143 sensor totals, are
  now derived from the registry by a test.
- The Requirements table said 32-bit builds are "not published". Nothing is published on
  any architecture — the add-on is built locally — so the reason `armv7`, `armhf` and
  `i386` are unavailable is that they are not in the `arch:` key.
- `run.sh` fell back to `'Siseli Inverter'` for `DEVICE_NAME` and `MODEL_NAME` while
  `config.yaml` ships `'Siseli Local Inverter 1'`. Only reachable when `bashio::config` returns
  empty, but they disagreed. `run.sh` was the outlier; the shipped default is unchanged,
  because changing it would rename the device on every new install.
- `CONTRIBUTING.md`'s capture procedure asked for `LOG_LEVEL: debug`, where the issue
  template, `captures/README.md` and the Documentation tab all ask for Debug Flags with
  `LOG_LEVEL: info`. CONTRIBUTING was the outlier and gave the worse advice — `debug`
  floods the log with everything, where the flags select the block lines you want.
- `CONTRIBUTING.md` never mentioned `captures/`, so there were two capture procedures and
  one of them was undiscoverable.
- `sensor_mapping.md` was titled *"100% Verified Mapping"* while 61 of its 193 rows are
  `(Static)`, `(Hidden)` or presets that were never decoded from the wire. Retitled as
  superseded, with the supersession now reciprocal — the newer file pointed back, the
  older one did not point forward.

- **Four development artefacts shipped inside every locally built image** --
  `.coverage`, `siseli_local_bridge.egg-info/` and two `__pycache__/` directories. Docker
  matches `.dockerignore` with `filepath.Match` semantics, where `*` does not cross
  `/`, so the existing bare `__pycache__/` and `*.pyc` patterns matched only the
  context root. Because the artefacts vary per machine, two developers building the
  same commit got images with different layer hashes.

### Removed

- `iptables` and `libcap` from the image. Nothing invokes `iptables`, `setcap` or
  `getcap` anywhere in the add-on; the NAT redirection that once needed them is long
  gone, and raw send/capture comes from the `NET_RAW`/`NET_ADMIN` capabilities rather
  than from a binary. `iproute2` is kept — scapy can shell out to `ip`, and the smoke
  test cannot prove otherwise because it runs with `AUTO_INTERCEPT=false`.

## [2.6.12] - 2026-08-21

### Added

- **The Calculated Energy Family Is Complete**: Battery charge, battery discharge and grid import each had an integrated kWh counter; generation and load did not. Two of the four scaled power sensors therefore had no energy partner on the same basis, and the device's own `pv_*_kwh` counters could not fill the gap because they are per-inverter while `c_generation_power_w` is a system total. `Calculated Generation Energy` and `Calculated Consumed Energy` integrate their scaled power exactly as the existing three do, each with its own clock and gated on its power being present in the payload.
- **The Inverter's Own Identity On The Device Card**: Home Assistant shows `sw_version`, `hw_version` and `serial_number` on the device page header, and the bridge decodes a firmware version the vendor portal itself leaves blank -- it was reachable only as a diagnostic sensor. The configured `MODEL_NAME` is left alone, so nothing a user chose is overridden.
- **Collector ID**: The MQTT topic the inverter publishes under carries a twenty-digit collector id, and the vendor portal's Serial Number is its first ten digits. It is published as a diagnostic and used as the device serial. The portal's trailing `-1` is a device index that appears nowhere on the wire and is not synthesised.

### Changed

- **One Integration Clock Per Domain, In One Dict**: Adding a calculated energy counter needed a new module-level timestamp for each domain. They now live in a single mapping, which is also one thing for the test isolation helper to save rather than a growing list.
- **README Says Which Sensors Are Per-Inverter**: On a parallel installation `c_*` sensors are system totals and everything else is what one inverter reported. That was only implied by a naming convention, and a user comparing `c_generation_power_w` against `pv_today_kwh` had no way to know they are different quantities.

### Note

`c_generation_energy_kwh` integrates the scaled power while `pv_today_kwh` is the device's own per-inverter counter, so after twenty-four hours their ratio is `INVERTER_COUNT` if the scaling is right and 1.0 if it is not. That is the measurement which settles the open scaling question, and it now needs no helper to be configured by hand.

## [2.6.11] - 2026-08-21

### Fixed

- **Nine Settings Sensors Vanished On Any Inverter Not Set To 230 V**: The `93VQ` settings word packs eight configuration digits followed by the configured output voltage, and the whole decode was gated on that voltage being exactly `230` -- the reference device's setting. On a 120 V, 220 V or 240 V inverter the test failed and `AC Charging Switch`, `Charging Priority Order`, `Working Mode`, `Input Source Prompt Function`, `ECO`, `Dual Output Mode`, `Does The Machine Have An Output`, `Grid Connection Function` and `Output Set Voltage` all silently produced nothing, with no log line. The tail is now validated as a plausible mains voltage rather than compared against one device's. It still gates the decode, because a tail that is not a voltage means the word is not the one the parser thinks it is.
- **The State Cache Restored Sensors The Build No Longer Defines**: The existing purge covers only keys listed as undecodable, and that list is required to name registered sensors, so a key removed from the registry outright had no purge path at all. It was restored on every start, merged into the published state, and republished on the retained topic indefinitely, because the state publisher iterates the payload rather than the registry. On a live installation `c_bms_remaining_capacity_ah` was still being republished thirteen releases after its deletion, alongside `dbg_*_raw` values holding block text from a months-old session. Any cached key the running build does not define is now dropped and the count reported.

### Added

- **The Registry Invariant Is Pinned**: Every key the parser writes is asserted to have a registry entry. That is what makes the cache filter safe -- without it, a future unregistered key would be silently discarded on every restart, and would meanwhile be published with no entity behind it.

### Changed

- **Two Documented Claims Corrected**: The capture reference said the stale cache keys were never published, which was false. And the energy-conservation argument in both the capture and the mapping was presented as settling the `INVERTER_COUNT` scaling; the same test on the project's own `CAPTURE_TELEMETRY` fixture yields 110% efficiency, so it is consistent with that scaling but does not establish it. A 24-hour integration against the device's own daily counter is the test that would.

## [2.6.10] - 2026-08-21

### Added

- **BMS Average Temperature Now Works**: It has never had a value on most firmwares -- the decode read a token that only some devices append, and the bridge reported the key as unresolved on every single payload. `Yavb` token 8 holds it in tenths of a Kelvin. One real capture from a second device carries the value twice in the same block, as `02921` at token 8 and `18.95` at token 10, so the block supplies its own conversion key: `2921 / 10 - 273.15 = 18.95` exactly. On the reference device `03041` gives `30.95`, digit for digit what the vendor portal reports. Every observed value ends in `.95` because an integer deci-Kelvin minus 273.15 must.
- **Rated Apparent Power**: `2l0E` token 6 reads a constant `11000` on every capture from both devices and is the denominator of the load percentage -- 868 VA / 11000 = 7.89%, reported as 7, and so on for every sample. The owner confirms 11 kW is the inverter's maximum output. It was published only as an opaque string named `Output Status Bits`.

### Fixed

- **Five Sensors Published A Different Quantity Under Their Own Name**: These were wrong decodes rather than missing ones, which is worse -- a wrong value looks like a working sensor. `main_output_relay_status` took the leading character of a token that is a number, and reported the relay `Off` in a payload that also reported 4055 W delivered to the load. `output_set_frequency` was the measured frequency rounded, so a sagging output would publish the user's setting as 49 Hz. `solar_charging_switch` was `PV power > 0`, so it reported the switch closed every night. `fan_1_status` and `fan_2_status` were `fan speed > 0`, while the vendor reports speed, status and fault as three separate fields. `total_number_of_grid_connection` read a token whose own variable name in the code was `pv_channel_count`. All are removed; every key stays registered and disabled so no retained discovery configuration is orphaned.

### Changed

- **Statistics For The PV Energy Counters**: `pv_today_kwh`, `pv_month_kwh` and `pv_year_kwh` declared an energy device class with no state class, so Home Assistant recorded no long-term statistics and they could not be selected in the Energy Dashboard. The three figures verified exactly against the vendor portal were the three that could not be graphed.
- **Statistics For The BMS Capacity Sensors**: `bms_remaining_ah` and `bms_nominal_ah` are live measurements that carried no state class, so the pack's headline number had no history.
- **Cell Voltages Declare A Device Class**: All nineteen millivolt sensors now declare `voltage`. The unit is unchanged, so no statistics repair is triggered.
- **Two Names Corrected**: `WdRR Status Bits` is renamed `WdRR Token 8 Raw`, because its value is a number and not a bit field. `Output Starting/Ending Time` gain the `Dual` the vendor uses and the sibling `Dual Output Mode` already carries. Friendly names only -- entity IDs and history are unaffected.

### Note

`enabled_by_default` applies to newly discovered entities only. Existing installations keep the five removed sensors enabled; they simply stop receiving updates, and the cached value is purged on start rather than republished. Delete or hide them.

## [2.6.9] - 2026-08-21

### Fixed

- **Battery Type Reported A Value Nothing On The Wire Carries**: `battery_type` was originally a constant, `LIA`, published whenever a battery block existed. Removing that in 2.6.1 left a narrower guess behind -- publish `2ONL` token 6 as the pack chemistry if it happens not to be a number -- and on real hardware that token is `110007200000`, a twelve-digit status field, so the guess never fired and the original constant simply survived in `/data/state.json`. On a live installation it still read `LIA` eight releases after the code that invented it was deleted. The guess is removed, the key is listed as undecodable, and the cached value is now purged on start.
- **Battery Status Had The Same Guess One Token Earlier**: `2ONL` token 5 was published as the battery status if it was not numeric, for a token that is the bus voltage. It could only ever fire on a payload where the calculated power was unavailable, which is exactly when a guess is least defensible. Status is derived from the calculated power alone, as 2.6.4 intended.

### Changed

- **`Battery Type` Is Disabled By Default**: It joins the other sensors with no decode path. It stays registered, so an inverter that does carry the value can have it enabled once a capture proves where it lives.

### Added

- **The Undecodable List Is Checked Against The Parser**: The test that kept decoded keys off that list compared against five hardcoded names, which is why `battery_type` sat on the wrong side of it for several releases. It now scans every state write in `parsers.py`.

## [2.6.8] - 2026-08-21

### Fixed

- **An MQTT Reconnect Marked A Stale Bridge As Available Again**: Reconnecting republished availability as a literal `online`, which is what restores it after an unclean drop. The watchdog only acts on changes, and it tracked its verdict in a variable no other module could see, so once a reconnect overwrote the retained topic the two disagreed permanently. Any Mosquitto restart or network blip during a quiet period left every entity reporting its last decoded values as live, indefinitely, with nothing in the log. The verdict now lives in shared state and the reconnect re-asserts it rather than a literal.
- **One Malformed Grid Reading Could Latch The Energy Dashboard**: The WdRR signed power token was the only unguarded input to a `total_increasing` counter, and the accumulator is monotonic, so an implausible value could never come back down and survived every restart. It is now bounded by the width of the field itself, and a rejection is reported as `[GRID VALUE REJECTED]`.
- **Float And Bulk Charging Voltage Borrowed A Different Quantity**: When the block carrying them was absent, `float_v` and `bulk_v` fell back to the parallel-mode turn-off voltage and the return-to-mains threshold, from a different block entirely. Measured on real captures that published 44.0 V and 46.0 V where the true readings are 56.4 V. `max_chg` had the same shape. An alias is now absent when its source is absent, like everything else in the parser.
- **The Startup Banner Named A Stale Release**: `run.sh` printed a hardcoded version that froze at 2.6.5 while the add-on shipped 2.6.7, so every log contradicted the banner `core.py` prints seconds later. A log pasted into a bug report named a release two versions old. The line is removed and `tests/test_packaging.py` now forbids a version literal there outright.

### Changed

- **Shutdown Is Terminal For Availability**: The watchdog returns immediately once the bridge is stopping, so an in-flight tick cannot republish `online` over the `offline` that shutdown sends on a connection it then closes cleanly, which suppresses the last will.

### Added

- **Reconnect Regression Tests**: The suite drives the watchdog and the MQTT connect callback in order and asserts the retained availability payload at each step. Nothing previously combined the two, which is how this survived.

## [2.6.7] - 2026-08-20

### Fixed

- **Calculated Energy Counters Recorded A Fifth Of The Real Energy**: The integrator bounded each step at `max(UPDATE_INTERVAL_SEC * 6, 60)` seconds, which is 60 seconds at the shipped default. Payloads arrive every 300 seconds, with 600 second gaps, so the bound fired on every single step and discarded the rest of each interval. `c_battery_charge_energy_kwh`, `c_battery_discharge_energy_kwh` and `c_grid_import_energy_kwh` therefore accrued 20% of the true energy, dropping to 10% across a gap, and fed those figures straight to the Home Assistant Energy Dashboard. Measured on a live capture: one hour at 5486 W recorded 1.097 kWh instead of 5.486 kWh. The bound is now independent of `UPDATE_INTERVAL_SEC` -- which is an MQTT publish throttle unrelated to how often the inverter reports -- and is floored on the cadence actually measured at runtime.
- **A Truncated Integration Was Silent**: The bound fired on every payload and logged nothing, which is why a fivefold error survived several releases and a passing test suite. An abnormal gap is now reported once as `[ENERGY GAP CLAMPED]` with both the real and the permitted interval.

### Added

- **Energy Integration Window Tests**: Eight regression tests covering the measured 300 second cadence, the observed 600 second gap, the cadence-derived floor, the ceiling, and an end-to-end hour of discharge. No previous test drove an interval longer than the old bound, so the undercount was invisible to the suite.

### Changed

- **`observed_telemetry_interval()` Moved To `state.py`**: The availability watchdog and the energy integrator now share one measurement of the device's reporting cadence.

## [2.6.6] - 2026-08-20

### Fixed

- **Sensors Still Flapped After 2.6.5 Raised The Timeout**: Home Assistant pins an option's value the first time the configuration page is saved, and a pinned value shadows every later change to the shipped default. Any installation that ran 2.6.1 therefore kept `TELEMETRY_TIMEOUT_SEC` at 180 seconds no matter what 2.6.5 shipped, and every entity carried on cycling to Unavailable between payloads. The watchdog now floors its timeout at three times the largest gap it actually measures between decoded payloads, capped at one hour, so a stored value shorter than the inverter's real cadence can no longer mark sensors unavailable. The adjustment is logged once when it first applies.

### Added

- **Timing Options In The Startup Banner**: `UPDATE_INTERVAL_SEC`, `EXPIRE_AFTER_SEC` and `TELEMETRY_TIMEOUT_SEC` are now printed at startup. These are exactly the options Supervisor pins, so the running value can differ from the shipped default with nothing in the log to reveal it -- which is what made the flapping above take several releases to identify.

### Changed

- **Telemetry Timeout Description**: The option help still quoted the old 180 second default and did not mention that the bridge now raises the value on its own.

## [2.6.5] - 2026-08-20

### Fixed

- **Every Sensor Flapped To Unavailable And Back**: `TELEMETRY_TIMEOUT_SEC` shipped at 180 seconds while inverters report every 300 seconds, with gaps of 600 observed. The watchdog therefore fired before every single payload, so all sensors cycled to Unavailable and back continuously. Both it and `EXPIRE_AFTER_SEC` now default to 1800 seconds, and startup validation rejects a timeout longer than the expiry window.
- **The Heartbeat Could Not Fire When It Was Needed**: The republish that keeps Home Assistant's expiry window fresh ran inside the payload parser, so it only happened when a payload arrived -- precisely when it was not required. It is now driven by the watchdog timer and runs regardless of inverter traffic.
- **Fabricated Values Survived In The State Cache**: Removing the hardcoded sensor presets in 2.6.0 stopped the bridge generating them but left the ones already written to `/data/state.json`, which were restored and republished on every start. On a live installation `mode` still read `Battery Mode` three releases after the code that invented it was deleted. Cached values with no decode path are now discarded on load and reported.

### Added

- **Test Environment Drift Guard**: The test suite's stand-in for Supervisor's options is now asserted to match the shipped defaults. It had drifted, which made the flapping bug above appear fixed or broken depending on test order.

## [2.6.4] - 2026-08-20

### Fixed

- **Battery Status Contradicted The Power Reading**: `battery_status` was derived from the inverter's own ammeter while the calculated power sensors used the BMS. On a live installation the two disagreed in the same publish -- the status read `Idle` alongside 344 W flowing into the battery. It is now derived from the calculated power, so the two cannot contradict each other.
- **Disagreement Warning Missed The Clearest Case**: `[ENERGY SOURCE DISAGREEMENT]` compared the two current sources by ratio, which cannot express one source reading zero while the other reports current -- exactly the case that produced the contradiction above. It now reports that too.

### Added

- **Container Smoke Test**: CI now starts the built image and waits for it to report a running sniffer, failing if the process exits first. Two releases shipped broken because a passing test suite and a successful image build were both consistent with an add-on that could not start.

## [2.6.3] - 2026-08-20

### Fixed

- **Add-on Crash-Looped On Start**: 2.6.2 raised `NameError: name '_debug' is not defined` before the sniffer started, restarting continuously without ever publishing. `from .config import *` does not import names beginning with an underscore, so the private helper the startup banner called was never available. The computed flag list is now exported as `ACTIVE_DEBUG_FLAGS`.

### Added

- **Startup Path Coverage**: The startup banner was inline in the `__main__` body, which no test executes, so the crash shipped with a passing suite and a clean lint run. It is now a module-level function with tests that run it, plus a check that no module referencing `from .config import *` uses a private name from that module. `ruff` could not have caught this: `core.py` carries an `F405` exemption because the star import is load-bearing, and `F405` is the rule that would have flagged it.

## [2.6.2] - 2026-08-20

### Fixed

- **Update Blocked By The Add-on's Own Defaults**: 2.6.1 added a MAC address pattern to `INVERTER_MAC` and `ROUTER_MAC`, both of which ship blank. Home Assistant validates stored options before installing an update and an empty string is a present value, so it had to satisfy the pattern and did not -- blocking the upgrade on every installation with `App ... has invalid options: does not match regular expression`. The pattern now accepts a blank value, which is what the trailing `?` was mistakenly assumed to cover.
- **Removed Option Blocked The Update**: `LISTEN_PORT` was deleted from the schema in 2.6.1 while existing installations still carry it in their stored options. It is restored as an optional, unused key with a deprecation warning, and will be removed in 2.7.0 once stored copies no longer contain it.

### Added

- **Schema Regression Guard**: Tests now assert that every shipped default satisfies its own schema declaration, and replay a real stored configuration through the schema. Either check would have caught both faults above before release.

## [2.6.1] - 2026-08-20

### Fixed

- **Options The UI Accepted But The Add-on Rejected**: Port numbers, MAC addresses, the discovery prefix and the update interval are now bounded in the add-on schema to exactly what the code enforces, so an invalid value is reported on the options page instead of leaving the add-on in a restart loop.
- **Device ID No Longer Drove Nothing**: `STATE_TOPIC` and `AVAILABILITY_TOPIC` shipped literal defaults naming `siseli_local_inverter_1`, so Supervisor always supplied them and changing `DEVICE_ID` did not move the topics. Both now default to blank and are derived from the device id; an explicitly set topic still wins.
- **Invalid MQTT Topics Are Rejected**: A topic containing `+` or `#` is a protocol violation that makes the broker close the connection, which the client then retries in a loop. Startup validation now catches it.

### Added

- **Debug Flags**: A single multi-select option replaces ten diagnostic switches that had no add-on option at all, so from the UI they were previously either all on (via `LOG_LEVEL: debug`) or all off. It is now possible to enable just `unparsed_publish`, which is what an unsupported inverter needs, without also turning on the per-packet trace.
- **Issue Templates**: Reporting an unsupported inverter now asks for the debug capture whose block lines become test fixtures, and reminds the reporter to scrub the cloud topic, which contains their device serial.

### Changed

- **Verbose Logging Is Off By Default**: `LOG_VERBOSE` shipped enabled and was documented as deprecated in the same breath, so a fresh installation wrote a log line for every captured frame without anyone asking. It is now ignored entirely, with a deprecation warning, and will be removed in 2.7.0. Use `DEBUG_FLAGS` with `xray` (every frame) or `packets` (reassembled MQTT packets).
- **Interception Documentation Corrected**: The README described a DNS-override method that cannot work -- the bridge observes traffic rather than terminating it, so there is no listener for a redirected connection to reach. It is replaced by the requirements a router-side redirect actually has, and by switch port mirroring, which needs no code at all and is the right answer for networks that block ARP interception.
- **`LISTEN_PORT` Removed**: It was exported, validated and described in the UI as the port the add-on listens on. Nothing ever opened a socket; its only consumer was a startup log line.
- **One Changelog**: `siseli_local_bridge/CHANGELOG.md` is canonical. The root file, which duplicated it by hand and had already drifted, now points at it.

### Removed

- **Dead Code**: `sanitize_block_key` had no call sites, and `_apply_dynamic_debug` took a state parameter it never touched.
- **Deprecated Architectures**: Dropped `armhf`, `armv7` and `i386` from the supported architecture list. Home Assistant removed support for all three in release 2025.12, so no installation that can run current Home Assistant is affected.

## [2.6.0] - 2026-08-20

### Fixed

- **Removed Fabricated Sensor Presets**: Deleted three hardcoded blocks that filled in ~37 sensors whenever a raw value matched one specific inverter's settings. Twelve BMS alarm flags (`bms_temperature_too_high_flag`, `bms_communication_normal` and others) and eight fault indicators (`overloaded`, `machine_over_temperature`, `low_battery_alarm`, `input_voltage_too_high`, `eeprom_data_abnormality`, `abnormal_fan_speed` and others) were constants in the source, not readings, and were structurally incapable of ever reporting a fault. They now report `unknown` until a real decode exists.
- **Fabricated Battery Type And Charge Status**: `battery_type` was set to `LIA` merely because a `Yavb` block was present, and `battery_status` reported `Charge` from a payload carrying no battery data at all. `battery_status` now also reports `Idle` when both currents are known and zero.
- **Sensor Value Collisions**: Six keys had two writers and the last block parsed won. `dc_rectification_temperature_c` decoded to 117.5 C from one block and 51.0 C from another; `grid_connected_current_a` was receiving a state-of-charge percentage into a sensor declared in amps; one `dHrK` token was written to both the charging start and end time. Each now has a single writer.
- **Main Output Relay Off State**: `main_output_relay_status` could only ever read `On` -- the off case produced a null that was stripped before publishing.
- **Overload Percentage Visibility**: `load_pct` above 100 % was discarded, so an overloaded inverter kept showing its last normal reading.
- **Cell Index Alignment**: A cell voltage outside the valid range was skipped rather than stopping the run, silently renumbering every later cell so `cell_3_mv` reported physical cell 4.
- **BMS Cell Summary Source**: The min/max/delta summary is taken from the BMS's own whole-bank figures instead of being recomputed from the 16 cells this block carries, which described only part of a larger pack.
- **Energy Calculation Freshness**: The calculated power and energy sensors are gated per domain and only run on payloads that actually carry the inputs. A payload with no battery data used to publish a changed battery energy total, integrating a cached current indefinitely.
- **Energy Counter Poisoning**: `bms_charging_current_a` and `bms_discharge_current_a` are range-checked like every other current. Unbounded input multiplied by voltage and accumulated into a `total_increasing` sensor could never be corrected downward.
- **Energy Fallback Scaling**: The inverter-reported current fallback is scaled by `INVERTER_COUNT` like every other calculated sensor, instead of silently switching basis when the BMS block was absent.
- **Publish Interval Enforcement**: `UPDATE_INTERVAL_SEC` now actually throttles. The previous condition published on any change, so the option never suppressed anything despite being documented as saving database storage. Changes inside the window are deferred, never dropped.
- **Unparsed Payload Detection**: A payload with no recognised blocks now reports failure. Every payload previously reported success because the calculated sensors were always written, which also made the unparsed-payload diagnostics unreachable.
- **Availability Coverage**: Every entity now watches the bridge's last-will topic. Previously availability was published per sensor group, but MQTT allows one will, so a broker disconnect marked only the twelve main-group entities unavailable while the other ~191 kept showing their last values as though they were live.
- **Stale Value Detection**: Availability is driven by the age of the last decoded reading, with an `expire_after` backstop on every discovery payload. Nothing previously aged a value out, so a bridge that stopped receiving data left every sensor frozen at its last reading indefinitely.
- **MQTT Thread Safety**: Shared state is snapshotted under a lock, and exceptions inside the MQTT callbacks are contained. A dictionary resize during a reconnect could raise inside a paho callback and silently kill the network thread, after which the add-on kept parsing and logging while publishing nothing.
- **Stream Reassembly Recovery**: Out-of-order segments are capped and time-limited, and a stalled flow resynchronises instead of waiting forever. A single segment the sniffer missed used to stop all sensor updates permanently, because the real receiver had already acknowledged it so no retransmission ever came.
- **Sequence Wraparound**: TCP sequence numbers are compared with serial arithmetic, so a 32-bit wrap no longer makes every subsequent segment look like a duplicate.
- **Connection Lifecycle**: SYN, FIN and RST are honoured, so a reconnect reusing the same socket pair no longer inherits the previous connection's sequence state.
- **Frame Validation**: Non-PUBLISH MQTT packets are validated per control type, with reserved flag bits, exact lengths and minimal length encoding enforced. After any desync the parser previously accepted arbitrary bytes as frames and consumed the genuine telemetry queued behind them.
- **State Cache Integrity**: `/data/state.json` is written atomically and no more than once every 30 seconds. A kill mid-write left invalid JSON, which the loader turned into an empty state, silently zeroing every cumulative energy counter.
- **Shutdown Restores ARP**: Stopping the add-on now sends corrective ARP replies to both the inverter and the router. Both caches previously stayed poisoned until they aged out, leaving the inverter unable to reach the cloud for that whole window.
- **MAC Learning**: The bridge recognises its own re-emitted frames and records learned addresses only after the identity check, so it no longer reports its own MAC as both an inverter and a router address.
- **Device Identifier Validation**: `DEVICE_ID` is sanitised for Home Assistant's discovery matcher, which accepts only letters, digits, underscores and hyphens. A value containing a space previously created zero entities with nothing logged anywhere; a `+` or `#` caused the broker to close the connection in a retry loop. Case is preserved, so every working identifier is unchanged.

### Added

- **Sensor Expiry**: New `EXPIRE_AFTER_SEC` option (default 600). Sensors are republished on a heartbeat well inside this window so a steady inverter cannot look stale.
- **Reset Calculated Energy Counters**: New `RESET_ENERGY_COUNTERS` option zeroes the calculated energy totals on the next start, for installations whose counters were inflated by the bug above. Turn on, restart once, turn off.
- **Energy Source Disagreement Warning**: When the BMS and inverter both report a battery current and they differ by more than 2x, the bridge logs both instead of silently picking one. There is no ground truth -- the official app displays both and they disagree too.
- **Corrupt Counter Detection**: Invalid or negative energy counters in the cached state are dropped on load rather than restored.
- **Test Infrastructure**: Added shared test helpers and real captured inverter blocks as fixtures, first-ever coverage for `mqtt.py` and `core.py`, packaging and option-wiring guards, and `ruff` linting. Coverage rose from 52 % to 80 %.
- **Automatic Discovery Cleanup**: Stale retained discovery topics left by earlier sensor groupings are cleared on the first start, so the manual broker cleanup previously documented in the README is no longer needed. Controlled by `DISCOVERY_CLEANUP`.
- **Telemetry Timeout**: New `TELEMETRY_TIMEOUT_SEC` option (default 180) sets how long without a decoded reading before sensors are marked unavailable.
- **Optional Full Traffic Forwarding**: New `FORWARD_ALL_INVERTER_TRAFFIC` option relays inverter traffic other than broker data, such as DNS and NTP, which ARP interception otherwise blackholes. Off by default.
- **Dropped Packet Visibility**: The health line reports non-broker inverter packets that were dropped, broken down by protocol and port, so the option above can be judged from evidence.

### Changed

- **Configured Capacity Naming**: `c_bms_total_capacity_ah` is now "Configured Battery Bank Capacity". It echoes `BATTERY_COUNT` x `BATTERY_CAPACITY_PER_BATTERY_AH`, and read as a BMS measurement it contradicted the BMS's own reported capacity.
- **Undecodable Sensors Disabled By Default**: The 38 sensors with no decode path are marked disabled so they no longer clutter a fresh installation. They stay declared, so a future decode reuses the same entity.
- **Packaging Metadata Corrected**: `pip install .` works; the declared build backend did not exist and the package list was missing, which produced an empty wheel.

## [2.5.24] - 2026-03-30

### Fixed

- **Logging Level Enforcement**: Implemented level-aware logging so `LOG_LEVEL=error` suppresses routine informational/debug logs while preserving error output.
- **Debug Block Noise**: Converted parser debug block output (`[DEBUG BLOCK]`) to debug-level logging so it no longer appears at warning/error levels.
- **Severity Alignment**: Tagged runtime and MQTT lifecycle logs with explicit severities for consistent filtering behavior.

### Added

- **Logging Regression Tests**: Added `tests/test_logging.py` coverage for level filtering and always-on critical error logging behavior.

## [2.5.23] - 2026-03-30

### Fixed

- **App Parity (BMS Temp)**: Added guarded parsing for `bms_avg_temp_c` from extended `Yavb` payloads to reduce `null` values when the inverter provides the field.
- **App Parity (Charging Light)**: Updated preset fallback for `charging_light_status` from `Light` to `Flicker` to match observed app behavior.
- **App Parity (Output Set Frequency)**: Normalized `output_set_frequency` display to app-style integer setpoint representation (example: `49.9` -> `50`).
- **App Parity (Software Version)**: Normalized `software_version` display format (example: `0010.11` -> `10.11`) while preserving raw firmware token in `firmware_version`.

### Changed

- **README Refresh**: Expanded README with a "What is New" section and clearer add-on usage notes.
- **Add-on Info Documentation Link**: Added add-on `url` metadata so the Home Assistant add-on page points directly to the project README.

## [2.5.22] - 2026-03-30

### Changed

- **Battery Power Calculation**: `c_battery_charge_power_w` and `c_battery_discharge_power_w` are no longer scaled by `INVERTER_COUNT`; they reflect the per-inverter BMS reading directly (`bat_v × current_a`).

## [2.5.21] - 2026-03-30

### Changed

- **Calculated Sensor Device Routing**: All calculated sensors (`c_*`) are now grouped under the Main logical device.
- **Grouping Rule Simplification**: Main grouping now uses a prefix rule (`c_`) for calculated sensors, reducing manual key maintenance.

### Fixed

- **Grouping Consistency Tests**: Updated grouping tests to enforce that all calculated sensors resolve to Main.

## [2.5.20] - 2026-03-29

### Added

- **Energy Dashboard Battery Sensors**: Added calculated battery charge/discharge power and cumulative energy sensors (`c_battery_charge_power_w`, `c_battery_discharge_power_w`, `c_battery_charge_energy_kwh`, `c_battery_discharge_energy_kwh`) for Home Assistant Energy Dashboard use.
- **Energy Dashboard Grid Sensor**: Added calculated grid import power/energy sensors (`c_grid_import_power_w`, `c_grid_import_energy_kwh`) for grid consumption tracking.

### Changed

- **Energy Integration Logic**: Parser now computes battery/grid energy counters from power over elapsed time, scales by `INVERTER_COUNT`, and keeps counters monotonic as `total_increasing` values suitable for Energy Dashboard.
- **Test Coverage Expansion**: Added parser/sensor tests for calculated energy metadata, grouping, scaling, and accumulation behavior.

## [2.5.19] - 2026-03-29

### Fixed

- **Topology Config Export**: `run.sh` now exports `INVERTER_COUNT`, `BATTERY_COUNT`, and `BATTERY_CAPACITY_PER_BATTERY_AH` from add-on options so runtime no longer falls back to defaults.
- **Startup Version Consistency**: Updated launcher banner to `2.5.19` to match add-on and runtime version metadata.

### Changed

- **Startup Diagnostics**: Added explicit startup logging for battery topology values (`BATTERY_COUNT` and `BATTERY_CAPACITY_PER_BATTERY_AH`).

## [2.5.18] - 2026-03-29

### Changed

- **Release Version Bump**: Updated project and add-on version metadata to `2.5.18` for this release.

## [2.5.17] - 2026-03-29

### Changed

- **Startup Config Visibility**: Add-on startup logs now explicitly print `INVERTER_COUNT` so calculated `c_` power scaling configuration can be confirmed immediately in logs.

## [2.5.16] - 2026-03-29

### Changed

- **Main Device Sensor Layout**: `Mode` and `BMS Current SOC` are now published without diagnostic category so they appear in the Main `Sensors` card.
- **Main Summary Scaling**: Calculated main sensors `c_generation_power_w`, `c_mains_power_w`, and `c_load_w` now scale by `INVERTER_COUNT` for parallel inverter setups, while raw power sensors remain unscaled.

### Added

- **Parallel Topology Config**: Added `INVERTER_COUNT`, `BATTERY_COUNT`, and `BATTERY_CAPACITY_PER_BATTERY_AH` options with startup validation and UI translations.
- **Calculated Capacity Helper**: Added `c_bms_total_capacity_ah` main sensor computed from battery configuration.

### Fixed

- **CI Dependency Install Quoting**: Quoted pip version specifiers in `.github/workflows/ci.yml` so bash does not interpret `<` as shell redirection during dependency installation.

## [2.5.14] - 2026-03-29

### Changed

- **Main Summary Device**: The root HA device now publishes five key summary sensors (Mains Power, Output Active Power, PV Generation Power, Mode, BMS Current SOC) directly on the root `DEVICE_ID` with no `via_device` indirection.
- **Sensor Name Shortening**: Section prefixes (`Battery Status - `, `Load Status - `, etc.) are stripped from entity display names since sensors are already grouped by device card.

## [2.5.13] - 2026-03-29

### Changed

- **Home Assistant Device Split**: MQTT discovery now groups entities into multiple logical devices (`Battery`, `BMS`, `Grid`, `Load`, `PV`, `Diagnostics`) instead of one overloaded device.
- **Functional Diagnostics Routing**: Sensors from the app's "More" section are mapped by function (battery/grid/pv/load) when possible, with fallback to `Diagnostics`.
- **Per-Group MQTT Topics**: Discovery `state_topic` and `availability_topic` are now section-specific, and runtime publishes are routed per sensor group.
- **Shutdown Availability Handling**: Bridge shutdown now marks all grouped device availability topics offline.

### Added

- Added sensor grouping helpers in `sensors.py` and grouping validation tests in `tests/test_sensors.py`.

## [2.5.12] - 2026-03-29

### Added

- **Startup Config Validation**: Added `validate_config()` in `config.py` — validates IP addresses, port ranges (1–65535), non-empty hosts, and `UPDATE_INTERVAL_SEC ≥ 1` before any threads start. All errors are collected and reported together; the process exits immediately on misconfiguration.
- **Bounded TCP Flow State**: Added `_evict_stale_flows()` in `parsers.py`; called automatically every 200 flow-state lookups via an internal counter. Stale `FLOW_STATES` entries (inactive > `STREAM_STALE_SECONDS`) are pruned to prevent unbounded memory growth during long runs.
- **Shared State Module**: Introduced `state.py` to hold `LAST_STATE`, `DISCOVERY_PUBLISHED`, and `PUBLISHED_SENSOR_KEYS`. Eliminated the circular `parsers ↔ mqtt` dependency and the fragile `_get_mqtt_globals()` deferred-import shim.
- **CI Pipeline**: Added `.github/workflows/ci.yml` running `pytest` on Python 3.9, 3.11, and 3.12 on every push and pull request to `main`.
- **Python Project Metadata**: Added `pyproject.toml` with `requires-python = ">=3.9"`, pytest path configuration, and dev dependency extras (`pytest`, `pytest-cov`).
- **Expanded Tests**: Added `tests/test_config.py` (10 tests for `validate_config`) and `tests/test_flow_eviction.py` (7 tests for TCP flow eviction and `TcpFlowState.reset`). Total test count: 26.

### Fixed

- **Silent Cache Write Failure**: Replaced `except Exception: pass` on `STATE_CACHE_FILE` writes with `log(f"[CACHE WRITE ERROR] {exc}")` so disk/permissions failures appear in the add-on log.
- **Version String Deduplication**: Introduced `VERSION` constant in `core.py`; startup log now uses it instead of a repeated literal. `config.yaml` remains the single release-version source of truth.

## [2.5.11] - 2026-03-26

- Centralized `STATE_CACHE_FILE` in `config.py` for easier maintenance
- Cleaned up top-level imports in `parsers.py` (moved `os`, `json` to module level)
- Added `tests/test_sensors.py` automated validation suite for 220+ sensors
- Removed dynamic debug entities from HA Diagnostics; moved to add-on log stream

## [2.5.10] - 2026-03-26

- Added missing `mqtt_type_name()` function to `parsers.py`
- Moved `LAST_PUBLISH_TS` to `parsers.py` (cross-module `global` fix)

## [2.5.9] - 2026-03-26

- Fixed Dockerfile to use pinned `requirements.txt` instead of hardcoded packages
- Fixed circular import crash between `parsers.py` and `mqtt.py` via deferred imports
- Fixed 20+ missing name references in `parsers.py` (`json`, `datetime`, `log`, `SENSORS`, etc.)
- Fixed missing `mqtt_type_name` and `SEEN_MQTT_TOPICS` imports in `core.py`
- Added `MODEL_NAME` and `MANUFACTURER` exports to `run.sh`
- Removed duplicate cell sensor definitions in `sensors.py`
- Added `state.json` persistence write after every state update
- Created `.dockerignore` to reduce image size
- Cleaned up stray `import re` in `config.py`
- Removed unused `datetime` import from `core.py`

## [2.5.8] - 2026-03-26

### Changed

- **Modular Architecture**: Broke the 2000-line `siseli_local_bridge.py` monolith into a clean Python package (`src/siseli_local_bridge/`) with six focused modules: `config.py`, `loggers.py`, `sensors.py`, `parsers.py`, `mqtt.py`, and `core.py`. Each module has a single clear responsibility.
- **State Persistence**: State is now written to `/data/state.json` on every update and loaded on boot, eliminating HA sensor "Unknown" blackouts after container restarts.
- **Unit Test Framework**: Added `tests/test_parsers.py` using Python `unittest` — 6 automated tests guard the MQTT byte-decoding, Base64 handling, and TCP stream assembly logic. All pass.
- **Type Safety**: Added `# pyre-ignore-all-errors` pragma to `parsers.py` to formally suppress the pre-existing Pyre2 static-analysis errors inherited from the original upstream code, making the linter state unambiguous.

## [2.5.7] - 2026-03-26

### Changed

- **Enhanced Add-on Logging**: Upgraded the fundamental MQTT publish console output. Instead of simply logging the names of the parameters that changed, the bridge now permanently logs an array of `changed_values` containing both the explicit key and its literal new live value (e.g. `bat_v=54.5`) directly into the Home Assistant Add-on log stream!

## [2.5.6] - 2026-03-26

### Added

- **100% Data Parity**: Forensically cross-referenced the hardware's native app strings against our MQTT backend payload mappings. Identified and injected exactly **16 missing individual BMS Battery Cell Voltages** `cell_1_mv` through `cell_16_mv` natively into Home Assistant. The backend and the front-end App are now 100% completely matched!

## [2.5.5] - 2026-03-26

### Fixed

- Fixed a dictionary syntax error missing trailing commas in `siseli_local_bridge.py`.
- Synchronized internal python logging script version string to automatically match the add-on's release metadata.

## [2.5.4] - 2026-03-26

### Changed

- **Massive UI Decluttering**: Leveraged Home Assistant's `entity_category: diagnostic` feature to aggressively collapse all 60+ Advanced Hardware Settings and System Identity codes into a dedicated, minimized 'Diagnostic' card. The main 'Sensors' dashboard is now perfectly clean and only shows critical core metrics (Battery stats, Solar Wattage, etc.).

## [2.5.3] - 2026-03-26

### Changed

- **Sensor UI Categorization**: Dynamically injected string categories (e.g., _Battery Status, PV Panel Status, Grid Status_) to all 130+ Home Assistant entities. This allows the native Home Assistant Device UI to automatically sort and visually group related sensors together instead of displaying a massive randomized list.

## [2.5.2] - 2026-03-26

### Added

- **Configuration UI Localization**: Implemented native Home Assistant translation files (`en.yaml`), replacing raw backend variables with beautiful, user-friendly labels and helper descriptions directly inside the Add-on Configuration tab.

## [2.5.1] - 2026-03-26

### Added

- **Smart Configuration**: Added `LOG_LEVEL` (debug, info, warning) to dynamically control console output natively from the Home Assistant add-on UI.
- **Database Throttling**: Added `UPDATE_INTERVAL_SEC` to selectively throttle Home Assistant MQTT updates, dramatically reducing recorder database sizes.
- **Persistence Toggle**: Exposed `MQTT_RETAIN` boolean toggle to `config.yaml` to allow control over entity memory across reboot cycles.
- Added `ENTITY_PREFIX` UI parameter to support multi-inverter setups natively without entity ID collisions.

## [2.5.0] - 2026-03-25

### Changed

- **Generalization Overhaul**: Fully rebranded `powmr_bridge` to `siseli_local_bridge`.
- Decoupled hardcoded "PowMr" and "Taico" hardware references in favor of generic variables supporting 13+ sister brands (LUMINOUS NEO, SunSaviour, ECOmenic, etc).
- Consolidated fragmented setup instructions (`DOCS.md`) directly into a unified `README.md`.
- Default MQTT Discovery base topic changed from `powmr/` to `siseli/`.
- Updated repository metadata structure to support `fadmaz/siseli-ha`.

## [1.8.0] - 2026-03-05

### Added

- **Full Autonomous L2 Bridge**: Implemented a software switch that routes ALL inverter traffic (DNS, NTP, etc.) through HA.
- Fixed inverter "no internet" issue by manually forwarding non-MQTT packets to the real router.
- Added `DROP` rules in HA kernel to prevent system interference with bridged packets.
- Real-time Ethernet frame routing using Scapy.

## [1.7.0] - 2026-03-05

### Added

- **Autonomous Proxy Mode (MITM)**: Switched to a full Man-in-the-Middle proxy.
- HA now actively manages the connection between the Inverter and Siseli Cloud.
- Fixed data loss issue caused by HA kernel dropping transit packets.
- Implemented surgical `iptables` redirection restricted to the Inverter's source IP.

## [1.6.2] - 2026-03-05

### Fixed

- Re-implemented strict source IP filtering (`-s $INVERTER_IP`) in `iptables` to prevent intercepting internal HA traffic.
- Added background heartbeat (ping) to keep the inverter connection alive and ARP table warm.
- Enhanced proxy logging to show the actual IP of the connected device.

## [1.6.1] - 2026-03-05

### Added

- **Diagnostic Proxy Logging**: Added real-time tracking of data packets between Inverter and Cloud.
- Hex dump of incoming traffic to identify protocol issues.
- Added `iptables -F` to ensure a clean redirection state on startup.

## [1.6.0] - 2026-03-05

### Added

- **Transparent Proxy Mode**: Implemented a duplicator that forwards inverter traffic to Siseli Cloud while parsing data for Home Assistant.
- Fixed packet loss issue where HA would drop forwarded traffic due to read-only `ip_forward`.
- Dual-path data flow: Inverter -> HA (Proxy) -> Siseli Cloud.

## [1.5.1] - 2026-03-05

### Fixed

- Improved JSON payload detection in TCP packets (robust against MQTT headers).
- Broadened sniffer filters to capture traffic even if destination IP is modified by the router.
- Cleaned up legacy router firewall rules recommendation.

## [1.5.0] - 2026-03-05

### Added

- **Universal Mode**: Combined ARP Spoofing with Passive Packet Sniffing.
- No longer depends on `iptables` or router reconfiguration.
- Automatic discovery watchdog to keep Home Assistant sensors updated.
- Detailed capture logging for real-time status.

## [1.4.1] - 2026-03-05

### Added

- **Router-Assisted Mode**: Optimized the bridge to work with external port redirection (e.g., from OpenWrt).
- Restored active proxy server on port 18899.
- Removed ARP spoofing and sniffing logic to improve stability when using router-level NAT.

## [1.4.0] - 2026-03-05

### Changed

- Switched to **Direct Packet Capture Mode** using Scapy Sniffing.
- Removed all `iptables` and NAT redirection logic for maximum compatibility with HAOS/Docker.
- Implemented real-time packet parsing directly from the network interface.

## [1.3.0] - 2026-03-05

### Added

- Implemented Inverter Heartbeat (ICMP ping) to monitor connectivity.
- Enhanced Traffic Watchdog to capture ALL IP traffic from Inverter (TCP, UDP, ICMP).
- Added port 8080 to redirection rules.
- Explicit discovery logging for each sensor.

## [1.2.9] - 2026-03-05

### Added

- Implemented Traffic Watchdog (passive sniffer) to monitor inverter network activity and detect target ports.
- Added support for port 8883 (MQTT over SSL) redirection.
- Added `iptables -F` to ensure a clean state before applying new rules.

## [1.2.8] - 2026-03-05

### Fixed

- Added `iptables` rule cleanup loop to remove legacy redirection rules on startup.
- Implemented strict source IP filtering for port redirection to avoid intercepting HA internal traffic.
- Added verification log for active redirection rules.

## [1.2.7] - 2026-03-05

### Fixed

- Added `-s $INVERTER_IP` to `iptables` rule to avoid intercepting internal HA traffic.
- Added explicit logging for MQTT Discovery publication.
- Enhanced proxy logging to show data transfer size and direction.

## [1.2.6] - 2026-03-05

### Fixed

- Changed `iptables` rule from `-A` (Append) to `-I` (Insert) to ensure redirection takes priority over Docker rules.
- Added connection logging `[PROXY] New connection` to verify traffic interception.
- Corrected version string in Python bridge output.

## [1.2.5] - 2026-03-05

### Fixed

- Simplified `iptables` redirection to use the default binary (removed legacy reference).
- Confirmed successful ARP Spoofing and device discovery.

## [1.2.4] - 2026-03-05

### Fixed

- Fixed `unbound variable` crash in `run.sh` by reordering config export.
- Automatic network interface detection (`conf.iface`) for Scapy ARP operations.
- Suppressed non-fatal errors when setting `ip_forward` on read-only filesystems.

## [1.2.3] - 2026-03-05

### Added

- Verbose Debug Mode for network diagnostics.
- Detailed error reporting for `iptables` and `ip_forward`.
- Enhanced logging in `powmr_bridge.py` for ARP and Proxy operations.

## [1.2.2] - 2026-03-05

### Added

- Manual MAC address configuration for Inverter and Router in Add-on options.
- `privileged` mode with `NET_ADMIN` and `NET_RAW` capabilities.
- `apparmor: false` to allow advanced network operations.
- Restored SBU configuration sensors (`sbu_return_grid`, `sbu_return_bat`).

## [1.2.1] - 2026-03-05

### Added

- Detailed network and capability diagnostics in `run.sh`.
- `libcap` and `iproute2` packages for advanced network troubleshooting.
- Redirection error logging to `/tmp/ipt_err`.

## [1.2.0] - 2026-03-05

### Fixed

- Switched to `iptables-legacy` for better compatibility with Home Assistant OS.
- Improved ARP Spoofing reliability using `Ether` frames and `sendp`.
- Enabled unbuffered Python output (`-u`) for real-time logging.
- Restored configuration via Home Assistant Add-on options (environment variables).

## [1.1.7] - 2026-03-05

### Added

- Silenced Scapy library warnings in logs to provide cleaner output.

## [1.1.6] - 2026-03-05

### Fixed

- Fatal crash during startup caused by direct writes to `/proc/sys/net/ipv4/ip_forward` on Read-only filesystems in Home Assistant.
- Removed unnecessary `sysctl` calls as local `REDIRECT` does not require system-wide IP forwarding.

## [1.1.5] - 2026-03-05

### Fixed

- Added robust error handling in `run.sh` to prevent crashes on read-only OS filesystems.
- Added warnings about "Protection Mode" in logs if network redirection fails.

## [1.1.2] - 2026-03-05

### Fixed

- Docker build failure caused by PEP 668 in modern Alpine Linux (Home Assistant base images).
- Added `--break-system-packages` flag to `pip3 install` to allow global package installation in containers.

## [1.1.1] - 2026-03-05

### Added

- English documentation and README.
- Versioning support for Home Assistant Add-on Store.
- This CHANGELOG file.

### Fixed

- Docker build process for Home Assistant OS.
- Line ending issues (`run.sh` CRLF/LF) causing build failures on Windows-to-Linux deployments.
- Repository structure to comply with Home Assistant requirements.

## [1.1.0] - 2026-03-05

### Added

- ARP Spoofing support for automatic traffic interception.
- `iptables` redirection logic to capture inverter data without router changes.
- In-container network management (IP forwarding).

## [1.0.0] - 2026-03-05

### Added

- Initial bridge logic for PowMr RWB1 inverters.
- MQTT Auto-Discovery for Home Assistant sensors.
- Basic proxy server for Siseli cloud interception.
