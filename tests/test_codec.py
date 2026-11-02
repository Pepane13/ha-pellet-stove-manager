"""Codec tests, same vectors as the Homey app (com.pepane13.pelletstove)."""

import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from duepi import codec

CODE = "0123456789"
VECTOR = b"\x1bAAAAAAAAAAAAAAAAAAAAALLifCC1HkBdciXX+5IoTxY=&"


def _enc(key, iv, plain):
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + e.update(plain) + e.finalize()).decode()


def test_checksum_and_inner_frames():
    assert codec.inner_frame("D9000") == "RD90005F"
    assert codec.inner_frame("F0010") == "RF001059"
    assert codec.inner_frame("F2140") == "RF21405F"
    assert codec.plain_frame("DA000") == b"\x1bRDA00067&"


def test_rejects_malformed_commands():
    for bad in ("D90", "d9000", 123):
        with pytest.raises(ValueError):
            codec.inner_frame(bad)


def test_key_derivation_ignores_trailing_hash():
    assert codec.derive_key(CODE + "#") == codec.derive_key(CODE)
    with pytest.raises(ValueError):
        codec.derive_key("  ")


def test_reference_vector_zero_iv():
    assert codec.encrypt_frame("D9000", codec.derive_key(CODE), bytes(16)) == VECTOR


def test_decrypt_own_frames_and_ignore_after_nul():
    key = codec.derive_key(CODE)
    frame = codec.encrypt_frame("ED000", key).decode("latin-1")
    assert codec.decrypt_payload(frame[1:-1], key) == "RED0006B"
    iv = os.urandom(16)
    assert codec.decrypt_payload(_enc(key, iv, b"02010023\0" + os.urandom(7)), key) == "02010023"


def test_parser_banners_split_coalesced():
    key = codec.derive_key(CODE)
    parser = codec.FrameParser(key)
    enc = _enc(key, bytes([7] * 16), b"00030023".ljust(16, b"\0"))
    stream = b"*HELLO*\x1b00000020&\x1b" + enc.encode() + b"&"
    frames = parser.push(stream[:20]) + parser.push(stream[20:])
    assert len(frames) == 2
    assert frames[0] == codec.Frame("00000020", False, True)
    assert frames[1].payload == "00030023" and frames[1].encrypted and frames[1].valid


def test_parser_flags_undecipherable():
    parser = codec.FrameParser(codec.derive_key("other-code"))
    [f] = parser.push(codec.encrypt_frame("D9000", codec.derive_key(CODE)))
    assert f.valid is False
