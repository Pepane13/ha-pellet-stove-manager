"""Duepi EVO frame codec.

Plain serial frame:   ESC + "R" + 5-char command + 2-hex checksum + "&"
Cloud (module M1):    ESC + Base64(IV || AES-128-CBC(inner frame, zero padded)) + "&"

Key derivation for the original WiFi module (interoperability notes published in
motionbug/testcodestove, MIT licence):
    key = MD5(device code without trailing "#") XOR 23FADC18A422678312C86AC29281AC01

Replies carry an 8-character payload; encrypted replies are followed by a NUL byte
and undefined memory, which is ignored.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ESC = 0x1B
END = 0x26  # '&'
MASK = bytes.fromhex("23FADC18A422678312C86AC29281AC01")

_COMMAND = re.compile(r"^[0-9A-F]{5}$")
_HEX8 = re.compile(r"^[0-9A-Fa-f]{8}$")
_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def checksum(body: str) -> str:
    """Two-digit uppercase hex checksum (sum of ASCII codes, low byte)."""
    return f"{sum(ord(c) for c in body) & 0xFF:02X}"


def inner_frame(command: str) -> str:
    """'D9000' -> 'RD90005F'."""
    if not isinstance(command, str) or not _COMMAND.match(command):
        raise ValueError(f"Invalid command: {command}")
    body = f"R{command}"
    return body + checksum(body)


def plain_frame(command: str) -> bytes:
    return b"\x1b" + inner_frame(command).encode("ascii") + b"&"


def derive_key(device_code: str) -> bytes:
    code = str(device_code or "").strip().rstrip("#")
    if not code:
        raise ValueError("Device code is empty")
    digest = hashlib.md5(code.encode("utf-8")).digest()  # noqa: S324 - protocol-defined
    return bytes(b ^ m for b, m in zip(digest, MASK))


def _aes(key: bytes, iv: bytes) -> Cipher:
    return Cipher(algorithms.AES(key), modes.CBC(iv))


def encrypt_frame(command: str, key: bytes, iv: bytes | None = None) -> bytes:
    iv = os.urandom(16) if iv is None else iv
    plain = inner_frame(command).encode("ascii").ljust(16, b"\0")
    enc = _aes(key, iv).encryptor()
    ct = enc.update(plain) + enc.finalize()
    return bytes([ESC]) + base64.b64encode(iv + ct) + bytes([END])


def decrypt_payload(b64: str, key: bytes) -> str:
    raw = base64.b64decode(b64)
    if len(raw) < 32 or len(raw) % 16:
        raise ValueError(f"Bad encrypted frame length {len(raw)}")
    dec = _aes(key, raw[:16]).decryptor()
    plain = dec.update(raw[16:]) + dec.finalize()
    nul = plain.find(b"\0")
    return plain[: nul if nul >= 0 else len(plain)].decode("latin-1")


@dataclass(frozen=True)
class Frame:
    payload: str | None
    encrypted: bool
    valid: bool
    error: str | None = None


class FrameParser:
    """Accumulates bytes from a TCP stream and yields complete frames."""

    def __init__(self, key: bytes | None = None) -> None:
        self.key = key
        self.buffer = b""

    def push(self, chunk: bytes) -> list[Frame]:
        self.buffer += chunk
        frames: list[Frame] = []
        while True:
            start = self.buffer.find(bytes([ESC]))
            if start < 0:
                self.buffer = b""  # banners such as *HELLO* carry no frame
                break
            end = self.buffer.find(bytes([END]), start + 1)
            if end < 0:
                self.buffer = self.buffer[start:]
                if len(self.buffer) > 256:  # garbage guard
                    self.buffer = b""
                break
            body = self.buffer[start + 1 : end].decode("latin-1")
            self.buffer = self.buffer[end + 1 :]
            frames.append(self._decode(body))
        return frames

    def _decode(self, body: str) -> Frame:
        if _HEX8.match(body):
            return Frame(body.upper(), False, True)
        if self.key and len(body) >= 44 and _B64.match(body):
            try:
                payload = decrypt_payload(body, self.key)
            except (ValueError, binascii.Error) as err:
                return Frame(None, True, False, str(err))
            return Frame(payload, True, bool(_HEX8.match(payload)))
        return Frame(body, False, False)
