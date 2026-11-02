import asyncio

import pytest

from duepi.client import DuepiClient
from duepi.protocol import DECODERS, WRITE, is_ack


def test_status_phases():
    s = DECODERS["status"]
    assert s("00000020") == "off"
    assert s("01010022") == "ignition"
    assert s("02010023") == "heating"
    assert s("0802002A") == "cooling"
    assert s("08010029") == "cooling"


def test_values_read_on_owner_stove_28_10_2026():
    assert DECODERS["power_level"]("00010021") == 1
    assert DECODERS["ambient"]("03E7003F") is None
    assert DECODERS["flue_gas"]("00140025") == 20
    assert DECODERS["board"]("00210023") == 33
    assert DECODERS["hours_total"]("0006C740") == 1735
    assert DECODERS["hours_service"]("00000C33") == 12
    assert DECODERS["pressure_switch"]("01000021") == "normal"
    assert DECODERS["setpoint"]("00000020") is None
    assert DECODERS["error"]("00000020").code == 0


def test_errors():
    e = DECODERS["error"]("00050025")
    assert e.id == "out_of_pellets" and e.fr == "Manque de pellets"
    assert DECODERS["error"]("00070027").id == "unknown_7"


def test_ambient_with_probe():
    assert DECODERS["ambient"]("00E9003E") == 23.3


def test_write_commands():
    assert WRITE["power"](0) == "F0000"
    assert WRITE["power"](3) == "F0030"
    with pytest.raises(ValueError):
        WRITE["power"](6)
    assert WRITE["setpoint"](20) == "F2140"
    assert WRITE["reset_alarm"]() == "D6000"
    assert is_ack("00000020")


def test_client_write_sends_frames():
    sent = []

    class T:
        strays = 0

        async def request(self, cmd):
            sent.append(cmd)
            return "00000020"

    async def run():
        c = DuepiClient(T())
        await c.write("power", 3)
        await c.write("power", 0)
        await c.write("reset_alarm")
        with pytest.raises(ValueError):
            await c.write("power", 7)
        with pytest.raises(ValueError):
            await c.write("format")

    asyncio.run(run())
    assert sent == ["F0030", "F0000", "D6000"]
