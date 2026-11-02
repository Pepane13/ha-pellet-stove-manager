# Pellet Stove Manager for Home Assistant

Port of the Homey app **Pellet Stove Manager** (`com.pepane13.pelletstove` 0.9.4) to Home Assistant,
for pellet stoves driven by a **Duepi EVO** board (Interstoves and other brands using myDPRemote).

## Status: 0.1.0 (driver)

- Duepi EVO protocol ported to Python: frame codec (plain and AES cloud frames), 12 reads, write
  commands (power, setpoint, alarm reset) **not exposed** (off by default, never tested on site).
- Connection modes:
  - **Mirror**: reads the Homey app through its homeyHASS entities, no connection to the stove
    (comparison phase while Homey is still master);
  - **Duepi cloud relay** (`1.duepiwebserver1.com:3000`, as myDPRemote), one session, gentle polling,
    exponential back-off;
  - **Local serial bridge** (ESP-Link / ser2net);
  - **Simulator**.
- Entities: status, alarm message, power level, flue gas / board / ambient temperatures, auger, exhaust,
  burn hours, hours since service; binary sensors heating, alarm, connection.
- Events `pellet_stove_manager_event` (`stove_status_changed`, `stove_alarm_raised`, `stove_alarm_cleared`,
  `stove_connection_lost`), same as the Homey Flow triggers. An alarm is only raised or cleared after two
  consistent readings; a snapshot whose replies arrive out of order is discarded.

Only **one client** should talk to the stove at a time: stop the Homey app before using the cloud mode.
The device code is a credential: it is stored in the config entry only and never logged.

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/Pepane13/ha-pellet-stove-manager`
(category Integration), install, restart Home Assistant, then add **Pellet Stove Manager**.

## Development

```bash
pip install pytest cryptography
pytest -q
```

## Roadmap

0.2 temperature sensors, Météo-France forecast, house learning and start-time anticipation ·
0.3 pellets, cleaning, maintenance · 0.4 regulation (Netatmo compensated / direct) and opt-in commands.

## Credits

Protocol documentation: aceindy/Duepi_EVO, P. Bornat ("InterstoveRemoteCmd"), motionbug/testcodestove.
Licence GPL-3.0-or-later.
