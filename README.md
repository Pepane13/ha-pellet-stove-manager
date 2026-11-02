# Pellet Stove Manager for Home Assistant

Port of the Homey app **Pellet Stove Manager** (`com.pepane13.pelletstove` 0.9.4) to Home Assistant,
for pellet stoves driven by a **Duepi EVO** board (Interstoves and other brands using myDPRemote).

## Status: 0.3.2 (pellets, cleaning, maintenance, alarms)

The external thermostat (Netatmo) drives the stove; the direct link brings the technical data, the
burn time, the alarms, the alarm reset and the power level.

- Duepi EVO protocol ported to Python: frame codec (plain and AES cloud frames), 10 reads, write
  commands (power / stop, alarm reset; setpoint supported by the library but not exposed).
- Connection modes: **mirror** (Homey app through homeyHASS, read-only), **Duepi cloud relay**
  (`1.duepiwebserver1.com:3000`, as myDPRemote), **local serial bridge**, **simulator**.
- Stove entities: status, alarm message (with advice), power level, flue gas / board temperatures,
  auger, exhaust, burn hours, hours since service, pressure switch; heating, alarm, connection.
- **Pellets** (port of the Homey app): tank level estimated from the power levels actually used
  (0.95 → 2.33 kg/h, Diego 9) × a factor, plus an extra load per ignition learnt from the real levels
  (empty tank, level seen before refilling): short Netatmo cycles make ignitions ≈ 40 % of the burn time; level, autonomy, empty time, kg / €
  this month, month forecast. Refill advice at 21:00 (tank must last until 08:00), morning check when
  the house opens, reminder 45 min later, « tank low » held overnight, alert at closing time.
- **Cleaning**: burning hours since the last cleaning; fixed threshold (20 h for the owner) or
  N × the mean burning hours of a tank; pre-alert at 80 %. Reminders only on weekday evenings (19:00)
  or weekend mornings (10:00), every such slot while due.
- **Maintenance**: journal (installation, services), due 12 months after the last service or at
  1,200 h, reminder 30 days / 100 h before.
- **Burn time**: minutes per stove state per day and month, ignitions per day. The stove's own hour
  counter only counts the heating phase (not ignitions): it stays the reference for the 1,200 h service
  rule, not for pellets or cleaning.
- **Alarms**: advice for each Duepi code (`alarms.py`, to check against the stove's manual). Reset
  from Home Assistant only for user-solvable alarms, with the stove off; never for severity 3.
  A second reset within 24 h suggests calling the technician.
- **Notifications**: persistent notification + notify services, with « Rechargé », « Nettoyé »,
  « Acquitter » actions; held while the house is closed (except severity 3 alarms).
- Buttons: tank refilled, cleaning done, maintenance done, reset alarm. Number: power (sent only
  while the stove burns: on a stove that is off, `F00n0` would start it). Switch **Commands allowed**
  guards the power command (off by default).
- Actions: `refilled`, `set_pellet_level`, `cleaned`, `serviced`, `add_burn`, `import_homey`
  (takes over the state learnt by the Homey app, script `tools/export_homey_state.homeyscript.js`).
- Events `pellet_stove_manager_event`: `stove_status_changed`, `stove_alarm_raised` /
  `_cleared`, `stove_connection_lost`, `stove_command`, `pellets_refill_advised`,
  `pellets_refill_not_needed`, `pellets_refill_missing`, `pellets_low`, `pellets_empty`,
  `pellets_refilled`, `cleaning_soon`, `cleaning_due`, `cleaning_done`, `maintenance_soon`,
  `maintenance_due`, `maintenance_done`.

Only **one client** should talk to the stove at a time: stop the Homey app before using the cloud mode.
The device code is a credential: it is stored in the config entry only and never logged.

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/Pepane13/ha-pellet-stove-manager`
(category Integration), install, restart Home Assistant, then add **Pellet Stove Manager**.

## Development

```bash
pip install pytest cryptography
pytest -q -p no:homeassistant --ignore tests/test_commands_ha.py   # driver + consumables
# Home Assistant entity tests (simulator)
pip install pytest-homeassistant-custom-component
pytest -q -o asyncio_mode=auto tests/test_commands_ha.py
```

## Roadmap

0.4 temperature sensors, Météo-France forecast, house learning and
start-time anticipation · 0.5 regulation (Netatmo compensated / direct).

## Credits

Protocol documentation: aceindy/Duepi_EVO, P. Bornat ("InterstoveRemoteCmd"), motionbug/testcodestove.
Licence GPL-3.0-or-later.
