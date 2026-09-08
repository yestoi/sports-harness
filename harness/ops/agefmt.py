"""The age v1 file format for one X25519 recipient, in pure Python.

There is no ``age`` binary on the Mac or the NAS and phase 4 adds no dependency for one,
so the backup job encrypts and decrypts the format itself, on top of the ``cryptography``
primitives already in the dependency set. Scored against the C2SP CCTV vectors in
``tests/test_agefmt.py``; that corpus, not this docstring, is the specification.

The format, as implemented here:

* Header: the line ``age-encryption.org/v1``, one or more recipient stanzas, then
  ``---`` SP <base64 header MAC> LF.
* A stanza is ``-> `` and space-separated arguments, LF, then the body as canonical
  unpadded base64 wrapped at 64 columns, ending in a line strictly shorter than 64
  columns (so a body that is an exact multiple of 64 columns ends with an empty line).
* An unrecognised stanza type is skipped, never an error. Type matching is exact and
  case-sensitive, so ``x25519`` is not ``X25519``.
* The X25519 stanza wraps a 16-byte file key with ChaCha20-Poly1305 under a key derived
  by HKDF-SHA-256 from the X25519 shared secret.
* The payload is a 16-byte nonce followed by 64 KiB STREAM chunks, each with a 12-byte
  nonce of an 11-byte big-endian counter and a final byte that is 1 on the last chunk.

Both directions stream: neither ``encrypt`` nor ``decrypt`` holds more than two chunks.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = [
    "AgeError", "HeaderError", "HmacError", "NoMatchError", "PayloadError",
    "generate_identity", "parse_identity", "parse_recipient", "encode_recipient",
    "encrypt", "decrypt", "header_chunk_count",
]

CHUNK_SIZE = 65536
"""Plaintext bytes per STREAM chunk."""

TAG_SIZE = 16
NONCE_SIZE = 16
FILE_KEY_SIZE = 16
_BLOCK = CHUNK_SIZE + TAG_SIZE
_MAX_COUNTER = (1 << 88) - 1

_INTRO = b"age-encryption.org/v1\n"
_STANZA_PREFIX = b"-> "
_MAC_PREFIX = b"--- "
_COLUMNS = 64
_MAX_HEADER_LINE = 8192

_IDENTITY_HRP = "age-secret-key-"
_RECIPIENT_HRP = "age"


class AgeError(Exception):
    """Any failure to read an age file."""


class HeaderError(AgeError):
    """The header could not be parsed, or a recipient stanza is malformed."""


class HmacError(AgeError):
    """The header parsed and a file key was unwrapped, but the header MAC is wrong."""


class NoMatchError(AgeError):
    """The header parsed, but no recipient stanza could be unwrapped with this identity."""


class PayloadError(AgeError):
    """The STREAM payload does not decrypt cleanly all the way to EOF."""


# --- base64 ---------------------------------------------------------------------------

_B64_CHARS = re.compile(rb"[A-Za-z0-9+/]*")


def _b64(data: bytes) -> bytes:
    """Canonical unpadded standard base64."""
    return base64.b64encode(data).rstrip(b"=")


def _unb64(text: bytes) -> bytes:
    """Decode canonical unpadded standard base64, or raise ``HeaderError``.

    Padding, whitespace, a non-alphabet character, an impossible length and non-zero
    trailing bits are all rejected: several vectors turn on exactly these.
    """
    if _B64_CHARS.fullmatch(text) is None:
        raise HeaderError("base64 value contains a character outside the alphabet")
    if len(text) % 4 == 1:
        raise HeaderError("base64 value has an impossible length")
    try:
        data = base64.b64decode(text + b"=" * (-len(text) % 4))
    except binascii.Error as exc:  # pragma: no cover - the guards above cover the cases
        raise HeaderError("base64 value could not be decoded") from exc
    if _b64(data) != text:
        raise HeaderError("base64 value is not canonically encoded")
    return data


# --- bech32 (BIP-173) -----------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_GENERATOR = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _bech32_polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                checksum ^= _BECH32_GENERATOR[i]
    return checksum


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _to_base32_values(data: bytes) -> list[int]:
    """Bytes as 5-bit groups, zero-padded to a whole group. Cannot fail."""
    acc = 0
    bits = 0
    out: list[int] = []
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    return out


def _from_base32_values(values: list[int]) -> bytes | None:
    """5-bit groups as bytes, rejecting an over-long or non-zero pad."""
    acc = 0
    bits = 0
    out = bytearray()
    for value in values:
        acc = (acc << 5) | value
        bits += 5
        while bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    if bits >= 5 or ((acc << (8 - bits)) & 0xFF):
        return None
    return bytes(out)


def _bech32_encode(hrp: str, data: bytes) -> str:
    values = _to_base32_values(data)
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + values + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in values + checksum)


def _bech32_decode(text: str, expected_hrp: str) -> bytes:
    """Decode a bech32 string with the expected human-readable part, or raise ``ValueError``."""
    if not isinstance(text, str):
        raise ValueError("a bech32 key must be a string")
    if text != text.lower() and text != text.upper():
        raise ValueError("a bech32 key must not mix cases")
    lowered = text.lower()
    position = lowered.rfind("1")
    if position < 1 or position + 7 > len(lowered):
        raise ValueError("malformed bech32 key")
    if lowered[:position] != expected_hrp:
        raise ValueError(f"expected a {expected_hrp!r} key")
    values = []
    for char in lowered[position + 1:]:
        if char not in _BECH32_CHARSET:
            raise ValueError("malformed bech32 key")
        values.append(_BECH32_CHARSET.index(char))
    if _bech32_polymod(_bech32_hrp_expand(expected_hrp) + values) != 1:
        raise ValueError("bech32 checksum does not match")
    decoded = _from_base32_values(values[:-6])
    if decoded is None:
        raise ValueError("malformed bech32 key")
    return decoded


# --- identities -----------------------------------------------------------------------


def generate_identity() -> tuple[str, str]:
    """A fresh X25519 keypair as ``(identity, recipient)`` in age's bech32 encodings."""
    key = X25519PrivateKey.generate()
    identity = _bech32_encode(_IDENTITY_HRP, key.private_bytes_raw()).upper()
    return identity, encode_recipient(key.public_key())


def parse_identity(text: str) -> X25519PrivateKey:
    """An ``AGE-SECRET-KEY-1...`` string as a private key. Raises ``ValueError`` if malformed."""
    raw = _bech32_decode(text, _IDENTITY_HRP)
    if len(raw) != 32:
        raise ValueError("an age identity holds 32 bytes")
    return X25519PrivateKey.from_private_bytes(raw)


def parse_recipient(text: str) -> X25519PublicKey:
    """An ``age1...`` string as a public key. Raises ``ValueError`` if malformed."""
    raw = _bech32_decode(text, _RECIPIENT_HRP)
    if len(raw) != 32:
        raise ValueError("an age recipient holds 32 bytes")
    return X25519PublicKey.from_public_bytes(raw)


def encode_recipient(key: X25519PublicKey) -> str:
    """A public key as an ``age1...`` recipient string."""
    return _bech32_encode(_RECIPIENT_HRP, key.public_bytes_raw())


# --- header ---------------------------------------------------------------------------


def _header_line(src: BinaryIO) -> bytes:
    """One header line without its LF. Raises ``HeaderError`` at EOF or on an unterminated line."""
    line = src.readline(_MAX_HEADER_LINE)
    if not line.endswith(b"\n"):
        raise HeaderError("header line is not terminated by a line feed")
    return line[:-1]


def _stanza_arguments(line: bytes) -> list[bytes]:
    """The space-separated arguments of a stanza line, each non-empty printable ASCII."""
    arguments = line[len(_STANZA_PREFIX):].split(b" ")
    for argument in arguments:
        if not argument:
            raise HeaderError("stanza argument is empty")
        if any(byte < 33 or byte > 126 for byte in argument):
            raise HeaderError("stanza argument holds a character outside printable ASCII")
    return arguments


def _read_header(src: BinaryIO) -> tuple[list[tuple[list[bytes], bytes]], bytes, bytes]:
    """Parse a header into ``(stanzas, mac, header_without_the_mac)``.

    ``header_without_the_mac`` is every byte from the intro line through the literal
    ``---``, which is what the header MAC covers.
    """
    if src.readline(len(_INTRO)) != _INTRO:
        raise HeaderError("not an age v1 file")
    header = bytearray(_INTRO)
    stanzas: list[tuple[list[bytes], bytes]] = []

    line = _header_line(src)
    if not line.startswith(_STANZA_PREFIX):
        raise HeaderError("header carries no recipient stanza")

    while True:
        if line.startswith(_STANZA_PREFIX):
            arguments = _stanza_arguments(line)
            header += line + b"\n"
            body_lines: list[bytes] = []
            while True:
                body_line = _header_line(src)
                header += body_line + b"\n"
                if len(body_line) > _COLUMNS:
                    raise HeaderError("stanza body line is longer than 64 columns")
                if _B64_CHARS.fullmatch(body_line) is None:
                    raise HeaderError("stanza body line is not base64")
                body_lines.append(body_line)
                if len(body_line) < _COLUMNS:
                    break
            stanzas.append((arguments, _unb64(b"".join(body_lines))))
            line = _header_line(src)
            continue
        if line.startswith(_MAC_PREFIX):
            mac = _unb64(line[len(_MAC_PREFIX):])
            if len(mac) != 32:
                raise HeaderError("header MAC is not 32 bytes")
            header += b"---"
            return stanzas, mac, bytes(header)
        raise HeaderError("expected a recipient stanza or the header MAC line")


def _unwrap(stanzas: list[tuple[list[bytes], bytes]], key: X25519PrivateKey) -> bytes:
    """The file key from the first X25519 stanza this identity can open.

    An unrecognised stanza type is skipped. A stanza that is structurally wrong is a
    ``HeaderError``; one that simply fails to authenticate is not this identity's, so the
    search continues and ends in ``NoMatchError``.
    """
    public = key.public_key().public_bytes_raw()
    for arguments, body in stanzas:
        if arguments[0] != b"X25519":
            continue
        if len(arguments) != 2:
            raise HeaderError("an X25519 stanza takes exactly one argument")
        share = _unb64(arguments[1])
        if len(share) != 32:
            raise HeaderError("the X25519 share is not 32 bytes")
        if len(body) < TAG_SIZE:
            raise HeaderError("the X25519 stanza body is shorter than its tag")
        try:
            shared = key.exchange(X25519PublicKey.from_public_bytes(share))
        except ValueError as exc:
            raise HeaderError("the X25519 share yields an all-zero shared secret") from exc
        if not any(shared):  # pragma: no cover - cryptography raises before this is reached
            raise HeaderError("the X25519 share yields an all-zero shared secret")
        wrap_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=share + public,
            info=b"age-encryption.org/v1/X25519",
        ).derive(shared)
        try:
            file_key = ChaCha20Poly1305(wrap_key).decrypt(bytes(12), body, None)
        except InvalidTag:
            continue
        if len(file_key) != FILE_KEY_SIZE:
            raise HeaderError("the unwrapped file key is not 16 bytes")
        return file_key
    raise NoMatchError("no recipient stanza matched this identity")


def _check_mac(file_key: bytes, header: bytes, mac: bytes) -> None:
    mac_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=b"header").derive(file_key)
    expected = hmac.new(mac_key, header, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise HmacError("the header MAC does not match")


# --- STREAM ---------------------------------------------------------------------------


def _read_block(src: BinaryIO, size: int) -> bytes:
    """Up to ``size`` bytes, short only at EOF."""
    buffer = bytearray()
    while len(buffer) < size:
        piece = src.read(size - len(buffer))
        if not piece:
            break
        buffer += piece
    return bytes(buffer)


def _chunk_nonce(counter: int, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def _stream_key(file_key: bytes, nonce: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce, info=b"payload").derive(file_key)


def _open_final(aead: ChaCha20Poly1305, counter: int, block: bytes) -> bytes:
    try:
        return aead.decrypt(_chunk_nonce(counter, True), block, None)
    except InvalidTag as exc:
        raise PayloadError(f"STREAM chunk {counter} does not authenticate") from exc


def _stream_decrypt(src: BinaryIO, dst: BinaryIO, file_key: bytes) -> None:
    """Decrypt the STREAM payload, writing each chunk as soon as it authenticates.

    Writing before looking at what follows is what the CCTV rule requires: all the
    plaintext released to the application must match the payload hash even when the
    decryption eventually fails. So a final chunk's plaintext is released first and the
    bytes that should not follow it are rejected second.
    """
    nonce = _read_block(src, NONCE_SIZE)
    if len(nonce) != NONCE_SIZE:
        raise HeaderError("the payload nonce is missing or truncated")
    aead = ChaCha20Poly1305(_stream_key(file_key, nonce))

    counter = 0
    first = True
    while True:
        block = _read_block(src, _BLOCK)
        if len(block) < TAG_SIZE:
            raise PayloadError(
                "the payload holds no chunks" if first else
                "the payload ends without a final chunk"
            )

        if len(block) == _BLOCK:
            # A full chunk may be an interior chunk or the final one, and only its tag
            # says which. Try the interior flag first, then the final flag.
            try:
                plaintext = aead.decrypt(_chunk_nonce(counter, False), block, None)
                last = False
            except InvalidTag:
                plaintext = _open_final(aead, counter, block)
                last = True
        else:
            # A short read means EOF, so this chunk can only be the final one.
            if len(block) == TAG_SIZE and not first:
                raise PayloadError("the final chunk is empty but is not the only chunk")
            plaintext = _open_final(aead, counter, block)
            last = True

        dst.write(plaintext)
        if last:
            if src.read(1):
                raise PayloadError("the payload carries bytes after its final chunk")
            return
        if counter == _MAX_COUNTER:
            raise PayloadError("the STREAM chunk counter would wrap")
        counter += 1
        first = False


def _stream_encrypt(src: BinaryIO, dst: BinaryIO, file_key: bytes, nonce: bytes) -> None:
    """Write the 16-byte payload nonce and the STREAM chunks.

    The nonce is a parameter rather than generated here so the encrypt direction can be
    scored against the CCTV vectors' recorded file keys, which is the only known-answer
    test the corpus allows for encryption.
    """
    dst.write(nonce)
    aead = ChaCha20Poly1305(_stream_key(file_key, nonce))

    counter = 0
    block = _read_block(src, CHUNK_SIZE)
    while True:
        following = _read_block(src, CHUNK_SIZE) if len(block) == CHUNK_SIZE else b""
        last = not following
        dst.write(aead.encrypt(_chunk_nonce(counter, last), block, None))
        if last:
            return
        if counter == _MAX_COUNTER:  # pragma: no cover - 2**88 chunks is not reachable
            raise PayloadError("the STREAM chunk counter would wrap")
        counter += 1
        block = following


# --- the two directions ---------------------------------------------------------------


def _wrap_lines(data: bytes) -> bytes:
    """A stanza body as canonical base64 at 64 columns, ending in a short line."""
    text = _b64(data)
    lines = [text[i:i + _COLUMNS] for i in range(0, len(text), _COLUMNS)] or [b""]
    if len(lines[-1]) == _COLUMNS:
        lines.append(b"")
    return b"\n".join(lines) + b"\n"


def encrypt(src: BinaryIO, dst: BinaryIO, recipient: str) -> None:
    """Encrypt ``src`` to ``dst`` for one X25519 ``recipient``, streaming in 64 KiB chunks."""
    public = parse_recipient(recipient)
    file_key = os.urandom(FILE_KEY_SIZE)

    ephemeral = X25519PrivateKey.generate()
    share = ephemeral.public_key().public_bytes_raw()
    shared = ephemeral.exchange(public)
    wrap_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=share + public.public_bytes_raw(),
        info=b"age-encryption.org/v1/X25519",
    ).derive(shared)
    body = ChaCha20Poly1305(wrap_key).encrypt(bytes(12), file_key, None)

    header = _INTRO + _STANZA_PREFIX + b"X25519 " + _b64(share) + b"\n" + _wrap_lines(body) + b"---"
    mac_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=b"header").derive(file_key)
    mac = hmac.new(mac_key, header, hashlib.sha256).digest()
    dst.write(header + b" " + _b64(mac) + b"\n")

    _stream_encrypt(src, dst, file_key, os.urandom(NONCE_SIZE))


def decrypt(src: BinaryIO, dst: BinaryIO, identity: str) -> None:
    """Decrypt ``src`` to ``dst`` with one X25519 ``identity``, streaming in 64 KiB chunks.

    The header is validated before the identity is touched, so a malformed file raises
    ``HeaderError`` even when the caller passed no identity at all. Every chunk that
    authenticates is written to ``dst`` before the next one is examined, so a partially
    decrypted payload is still whatever the reader was legitimately given.
    """
    stanzas, mac, header = _read_header(src)
    key = parse_identity(identity)
    file_key = _unwrap(stanzas, key)
    _check_mac(file_key, header, mac)
    _stream_decrypt(src, dst, file_key)


def header_chunk_count(path: Path, plaintext_bytes: int) -> int:
    """The number of STREAM chunks the ciphertext at ``path`` holds.

    Counted from the file's own size less its header and nonce, then cross-checked against
    the count ``plaintext_bytes`` implies. A file whose payload is not exactly the size
    that many chunks of that much plaintext would occupy raises ``PayloadError``: that is
    the structural check the backup job wants, without decrypting anything.
    """
    if plaintext_bytes < 0:
        raise ValueError("plaintext_bytes cannot be negative")
    path = Path(path)
    with path.open("rb") as handle:
        _read_header(handle)
        if len(_read_block(handle, NONCE_SIZE)) != NONCE_SIZE:
            raise HeaderError("the payload nonce is missing or truncated")
        payload = path.stat().st_size - handle.tell()

    expected = max(1, -(-plaintext_bytes // CHUNK_SIZE))
    if payload != plaintext_bytes + TAG_SIZE * expected:
        raise PayloadError(
            f"payload is {payload} bytes, not the {plaintext_bytes + TAG_SIZE * expected} "
            f"that {plaintext_bytes} plaintext bytes require"
        )
    counted = -(-payload // _BLOCK)
    if counted != expected:  # pragma: no cover - the size check above already forces this
        raise PayloadError(f"payload holds {counted} chunks, not the expected {expected}")
    return expected
