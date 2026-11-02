"""Transport tests against a fake Duepi relay backed by the simulator."""

import asyncio
import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from duepi import codec
from duepi.client import DesyncError, DuepiClient
from duepi.simulator import SimulatedStove
from duepi.transport import BackoffError, DuepiTransport, TransportOptions

CODE = "TESTCODE42"
FAST = dict(login_delay=0.02, inter_command_delay=0)


async def fake_relay(silent_for=(), duplicate_once=None):
    key = codec.derive_key(CODE)
    stove = SimulatedStove()
    seen = {"logins": [], "commands": []}
    state = {"dup": duplicate_once}

    async def handle(reader, writer):
        buf = b""
        logged = False
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            buf += chunk
            if not logged:
                h = buf.find(b"#")
                if h < 0:
                    continue
                seen["logins"].append(buf[: h + 1].decode())
                buf = buf[h + 1 :]
                logged = True
            while (end := buf.find(b"&")) >= 0:
                body = buf[1:end].decode("latin-1")
                buf = buf[end + 1 :]
                cmd = codec.decrypt_payload(body, key)[1:6]
                seen["commands"].append(cmd)
                if cmd in silent_for:
                    continue
                reply = stove.reply(cmd)
                iv = os.urandom(16)
                e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
                plain = (reply + "\0").encode("latin-1") + os.urandom(7)
                frame = b"\x1b" + base64.b64encode(iv + e.update(plain) + e.finalize()) + b"&"
                if state["dup"] == cmd:
                    state["dup"] = None
                    writer.write(frame)
                writer.write(frame)
                await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], seen


def test_cloud_logs_in_once_and_reads_snapshot():
    async def run():
        server, port, seen = await fake_relay()
        t = DuepiTransport("cloud", "127.0.0.1", port, CODE, TransportOptions(**FAST))
        try:
            c = DuepiClient(t, settle=0.05)
            s1 = await c.snapshot()
            s2 = await c.snapshot()
            assert s1["status"] in ("off", "ignition", "heating", "cooling")
            assert s1["hours_total"] == 1735 and s1["hours_service"] == 12
            assert s1["ambient"] is None and s1["missing"] == []
            assert s2["hours_total"] == 1735
            assert seen["logins"] == [f"master:{CODE}#"]
            assert not any(x.startswith("F") or x == "D6000" for x in seen["commands"])
        finally:
            await t.close()
            server.close()

    asyncio.run(run())


def test_missing_reply_drops_session_and_reconnects():
    async def run():
        server, port, seen = await fake_relay(silent_for=("EE000",))
        t = DuepiTransport("cloud", "127.0.0.1", port, CODE, TransportOptions(reply_timeout=0.3, **FAST))
        try:
            c = DuepiClient(t, settle=0.05)
            s = await c.snapshot()
            assert s["missing"] == ["hours_service"]
            assert len(seen["logins"]) == 1
            assert t.connected is False
            assert await c.read("status") in ("off", "ignition", "heating", "cooling")
            assert len(seen["logins"]) == 2
        finally:
            await t.close()
            server.close()

    asyncio.run(run())


def test_backoff():
    async def run():
        t = DuepiTransport("cloud", "127.0.0.1", 1, CODE, TransportOptions(backoff_min=60))
        with pytest.raises(ConnectionError):
            await t.request("D9000")
        with pytest.raises(BackoffError):
            await t.request("D9000")
        await t.close()

    asyncio.run(run())


def test_local_plain_frames():
    async def run():
        stove = SimulatedStove()

        async def handle(reader, writer):
            writer.write(b"*HELLO*")
            buf = b""
            while chunk := await reader.read(1024):
                buf += chunk
                while (end := buf.find(b"&")) >= 0:
                    frame, buf = buf[: end + 1], buf[end + 1 :]
                    writer.write(b"\x1b" + stove.reply(frame[2:7].decode()).encode() + b"&")
                    await writer.drain()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        t = DuepiTransport("local", "127.0.0.1", port, options=TransportOptions(**FAST))
        try:
            assert await DuepiClient(t).read("hours_total") == 1735
        finally:
            await t.close()
            server.close()

    asyncio.run(run())


def test_duplicated_frame_discards_snapshot():
    async def run():
        server, port, _ = await fake_relay(duplicate_once="D9000")
        t = DuepiTransport("cloud", "127.0.0.1", port, CODE, TransportOptions(**FAST))
        try:
            c = DuepiClient(t, settle=0.1)
            with pytest.raises(DesyncError):
                await c.snapshot()
            s = await c.snapshot()
            assert s["error"].code == 0 and s["hours_total"] == 1735
        finally:
            await t.close()
            server.close()

    asyncio.run(run())
