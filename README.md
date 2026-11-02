# Pellet Stove Manager for Home Assistant

Port of the Homey app **Pellet Stove Manager** (`com.pepane13.pelletstove` 0.9.4) to Home Assistant,
for pellet stoves driven by a **Duepi EVO** board (Interstoves and other brands using myDPRemote).

## Status: 0.2.1 (driver + commands)

- Duepi EVO protocol ported to Python: frame codec (plain and AES cloud frames), 12 reads, write
  commands (power / stop, alarm reset; setpoint supported by the library but not exposed).
- Connection modes:
  - **Mirror**: reads the Homey app through its homeyHASS entities, no connection to the stove
    (comparison phase while Homey is still master);
  - **Duepi cloud relay** (`1.duepiwebserver1.com:3000`, as myDPRemote), one session, gentle polling,
    exponential back-off;
  - **Local serial bridge** (ESP-Link / ser2net);
  - **Simulator**.
- Entities: status, alarm message, power level, flue gas / board temperatures, auger, exhaust,
  burn hours, hours since service, pressure switch; binary sensors heating, alarm, connection.
- **Commands** (cloud, local and simulator modes only, never in mirror mode):
  - switch **Commands allowed** (off by default, restored after restart): master guard, every command
    is refused while it is off;
  - buttons **Start** (only when the stove is off, at the requested power, else the last level, else 3),
    **Stop** (power 0), **Reset alarm**;
  - number **Power** 1–5 (sent while the stove burns, kept for the next start when it is off).
  - No setpoint command and no ambient probe reading: the room temperature is left to the external
    thermostat (Netatmo) and the stove's probe is not connected. The protocol library still supports both.
  - Each command is logged in the logbook and raises a `stove_command` event with the stove's reply
    (`ack`); the stove is read again 5 s later.
- Events `pellet_stove_manager_event` (`stove_status_changed`, `stove_alarm_raised`, `stove_alarm_cleared`,
  `stove_connection_lost`, `stove_command`, `stove_commands_allowed`), same as the Homey Flow triggers. An alarm is only raised or cleared after two
  consistent readings; a snapshot whose replies arrive out of order is discarded.

Only **one client** should talk to the stove at a time: stop the Homey app before using the cloud mode.
The device code is a credential: it is stored in the config entry only and never logged.

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/Pepane13/ha-pellet-stove-manager`
(category Integration), install, restart Home Assistant, then add **Pellet Stove Manager**.

## Development

```bash
pip install pytest cryptography
pytest -q -p no:homeassistant --ignore tests/test_commands_ha.py
# Home Assistant entity tests (simulator)
pip install pytest-homeassistant-custom-component
pytest -q -o asyncio_mode=auto tests/test_commands_ha.py
```

## Roadmap

0.3 pellets, cleaning, maintenance · 0.4 temperature sensors, Météo-France forecast, house learning and
start-time anticipation · 0.5 regulation (Netatmo compensated / direct).

## Credits

Protocol documentation: aceindy/Duepi_EVO, P. Bornat ("InterstoveRemoteCmd"), motionbug/testcodestove.
Licence GPL-3.0-or-later.
