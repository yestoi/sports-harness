"""The age v1 file format for one X25519 recipient, scored against the C2SP CCTV vectors.

The vector corpus is the specification. Every in-scope vector must score exactly its
recorded ``expect`` value, and every ``success`` or ``payload failure`` vector's released
plaintext must match its recorded sha256 -- including the plaintext released before a
payload error, which the CCTV README requires the API to have handed to the application.
"""

import base64
import hashlib
import hmac
import io
import json
import zlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from harness.ops import agefmt

CCTV = Path(__file__).parent / "fixtures" / "age" / "cctv"
MANIFEST = Path(__file__).parent / "fixtures" / "age" / "cctv-manifest.json"


def _load(path: Path) -> tuple[dict, bytes]:
    """Split a CCTV vector into its textual header and its (possibly zlib'd) age file."""
    raw = path.read_bytes()
    head, _, body = raw.partition(b"\n\n")
    meta: dict[str, list[str]] = {}
    for line in head.decode().splitlines():
        key, _, value = line.partition(": ")
        meta.setdefault(key, []).append(value)
    if meta.get("compressed", [""])[0] == "zlib":
        body = zlib.decompress(body)
    return meta, body


def _stanza_types(body: bytes) -> list[str]:
    """The recipient stanza types in an age header, in order. Reads only as far as the `---`
    MAC line, so a payload byte can never be mistaken for a stanza."""
    out = []
    for line in body.split(b"\n"):
        if line.startswith(b"---"):
            break
        if line.startswith(b"-> "):
            out.append(line[3:].split(b" ")[0].decode("latin1"))
    return out


def _in_scope(meta: dict, body: bytes) -> bool:
    """Three conditions, each of which marks a vector this format's job does not cover.

    1. Armor and scrypt are not this format's job.
    2. An ML-KEM-**only** header is not either: no single-X25519-recipient implementation
       can decrypt those, and nine of them expect a `header failure` for a defect only an
       ML-KEM implementation can detect.
    3. A vector that supplies identities but no plain `AGE-SECRET-KEY-1` identity is out of
       scope: it is addressed entirely to keys this reader cannot represent. Exactly one
       vector is caught only by this condition -- `hybrid_and_x25519`, whose derivation is
       in the test below.

    Condition 3 is stated as "supplies identities but none of them plain" rather than
    "supplies no plain identity" so that `empty`, which carries no `identity:` key at all,
    stays in scope: its malformed header must fail before any identity is used. And
    `x25519_lowercase` stays in scope, proving an unknown stanza **type** is skipped rather
    than rejected.
    """
    if "armored" in meta or "passphrase" in meta:
        return False
    types = _stanza_types(body)
    if any(t.lower().startswith("mlkem") for t in types) and not any(t == "X25519" for t in types):
        return False
    identities = meta.get("identity", [])
    return not (identities and not any(i.startswith("AGE-SECRET-KEY-1") for i in identities))


def _scan() -> list[tuple[str, str]]:
    """(name, expect) for every in-scope vector, holding one decompressed body at a time.

    Three vectors decompress to about 17 MB each, so the scan deliberately discards each
    body rather than materialising the whole corpus.
    """
    out = []
    for path in sorted(CCTV.iterdir()):
        if path.name == "README.md":
            continue
        meta, body = _load(path)
        if _in_scope(meta, body):
            out.append((path.name, meta["expect"][0]))
    return out


#: Measured against the fixtures on 2026-09-08. A drift here without a matching change to
#: cctv-manifest.json means the corpus silently shrank, which is what this dict exists to catch.
_EXPECTED_COUNTS = {"success": 14, "payload failure": 18, "header failure": 32,
                    "no match": 3, "HMAC failure": 1}
_EXPECTED_TOTAL = 68

_SCOPE = _scan()


def test_the_vector_corpus_is_the_one_the_manifest_records():
    manifest = json.loads(MANIFEST.read_text())
    recorded = {f["name"]: f for f in manifest["files"]}
    on_disk = {p.name for p in CCTV.iterdir() if p.name != "README.md"}
    assert on_disk == set(recorded)
    for name in sorted(on_disk):
        data = (CCTV / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == recorded[name]["sha256"], name
        assert len(data) == recorded[name]["bytes"], name


def test_every_in_scope_vector_is_exercised():
    counts: dict[str, int] = {}
    for _, expect in _SCOPE:
        counts[expect] = counts.get(expect, 0) + 1
    assert counts == _EXPECTED_COUNTS
    assert sum(counts.values()) == _EXPECTED_TOTAL


def test_the_two_boundary_vectors_are_in_scope():
    names = {n for n, _ in _SCOPE}
    assert {"empty", "x25519_lowercase"} <= names


def test_the_ml_kem_only_vectors_are_out_of_scope():
    names = {n for n, _ in _SCOPE}
    assert "hybrid" not in names and "hybrid_grease" not in names
    assert "hybrid_multiple_recipients" not in names and "hybrid_uppercase" not in names


def test_hybrid_and_x25519_is_out_of_scope_because_its_x25519_stanza_is_not_ours():
    """The one place this suite departs from the task brief, with the evidence inline.

    The brief keeps this vector in scope on the claim that "the unknown stanza is skipped
    and the X25519 one unwraps". It does not unwrap. The vector supplies exactly one
    identity and it is the hybrid `AGE-SECRET-KEY-PQ-` one, while its `-> X25519` stanza is
    byte-identical to the first stanza of `x25519_multiple_recipients`, which is addressed
    to the corpus's *second* plain identity -- the key the testkit hands out for the
    `no match` vectors, and one this vector never supplies. Decrypting it therefore requires
    unwrapping its `mlkem768x25519` stanza, which makes it a fourth ML-KEM-dependent
    success rather than a hybrid header a single-X25519-recipient reader can open.
    """
    meta, body = _load(CCTV / "hybrid_and_x25519")
    assert meta["expect"] == ["success"]
    assert _stanza_types(body) == ["X25519", "mlkem768x25519"]
    assert meta["identity"] == ["AGE-SECRET-KEY-PQ-1HZLGZUPT4ETPKDEV8HSGFDCYZ4E522W0A7PU2LHT8EH9W6YLNC3SW78XKG"]
    assert not any(i.startswith("AGE-SECRET-KEY-1") for i in meta["identity"])

    other, other_body = _load(CCTV / "x25519_multiple_recipients")
    assert body.split(b"\n")[1:3] == other_body.split(b"\n")[1:3]
    # The identity that vector does supply unwraps its *second* stanza, never the shared first.
    assert other["identity"] == ["AGE-SECRET-KEY-1EGTZVFFV20835NWYV6270LXYVK2VKNX2MMDKWYKLMGR48UAWX40Q2P2LM0"]

    assert "hybrid_and_x25519" not in {n for n, _ in _SCOPE}


@pytest.mark.parametrize("name", [n for n, _ in _SCOPE])
def test_cctv_vector(name):
    meta, body = _load(CCTV / name)
    expect = meta["expect"][0]
    identity = meta.get("identity", [None])[0]
    out = io.BytesIO()
    if expect == "success":
        agefmt.decrypt(io.BytesIO(body), out, identity)
        assert hashlib.sha256(out.getvalue()).hexdigest() == meta["payload"][0]
        return
    exc = {"header failure": agefmt.HeaderError,
           "HMAC failure": agefmt.HmacError,
           "no match": agefmt.NoMatchError,
           "payload failure": agefmt.PayloadError}[expect]
    # `identity` is None for `empty`, which carries no `identity:` key: the header must fail
    # before any identity is parsed.
    with pytest.raises(exc):
        agefmt.decrypt(io.BytesIO(body), out, identity)
    if expect == "payload failure":
        # Everything released before the error must still match the payload hash.
        assert hashlib.sha256(out.getvalue()).hexdigest() == meta["payload"][0]


@pytest.mark.parametrize("name", [n for n, e in _SCOPE if e == "success"])
def test_the_stream_encryption_reproduces_each_success_vector_byte_for_byte(name):
    """The only known-answer test the corpus allows for the encrypt direction.

    The CCTV README says a `success` vector can score a STREAM encrypt round trip with the
    help of its recorded `file key`. Re-encrypting the vector's own plaintext under its own
    file key and payload nonce must reproduce its ciphertext exactly, which pins the chunk
    size, the 11-byte big-endian counter and the last-chunk flag against published bytes
    rather than against this module's own decryptor.
    """
    meta, body = _load(CCTV / name)
    file_key = bytes.fromhex(meta["file key"][0])

    source = io.BytesIO(body)
    agefmt._read_header(source)
    nonce = source.read(16)
    payload = source.read()

    plain = io.BytesIO()
    agefmt._stream_decrypt(io.BytesIO(nonce + payload), plain, file_key)
    assert hashlib.sha256(plain.getvalue()).hexdigest() == meta["payload"][0]

    rebuilt = io.BytesIO()
    agefmt._stream_encrypt(io.BytesIO(plain.getvalue()), rebuilt, file_key, nonce)
    assert rebuilt.getvalue() == nonce + payload


# --- helpers for the hand-built files ------------------------------------------------

IDENTITY, RECIPIENT = agefmt.generate_identity()


def _b64(data: bytes) -> bytes:
    return base64.b64encode(data).rstrip(b"=")


def _with_valid_mac(header: bytes, file_key: bytes | None = None) -> bytes:
    """Close `header` with a well-formed `--- <mac>` line, a 16-byte nonce and no chunks.

    With a `file_key` the MAC is the real one, so a test that gets past the MAC check is
    testing what it says it is. Without one there is no file key to derive a MAC from --
    the caller's header carries no stanza this reader can unwrap -- so a well-formed
    placeholder is used, which is never reached because the unwrap fails first.
    """
    without_mac = header + b"---"
    if file_key is None:
        mac = bytes(32)
    else:
        mac_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=b"header").derive(file_key)
        mac = hmac.new(mac_key, without_mac, hashlib.sha256).digest()
    return without_mac + b" " + _b64(mac) + b"\n" + bytes(16)


def _x25519_file_wrapping(file_key: bytes) -> bytes:
    """A well-formed X25519 file for RECIPIENT whose wrapped key is exactly `file_key`."""
    recipient = agefmt.parse_recipient(RECIPIENT)
    ephemeral = X25519PrivateKey.generate()
    share = ephemeral.public_key().public_bytes_raw()
    shared = ephemeral.exchange(recipient)
    wrap_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=share + recipient.public_bytes_raw(),
                    info=b"age-encryption.org/v1/X25519").derive(shared)
    body = ChaCha20Poly1305(wrap_key).encrypt(bytes(12), file_key, None)
    header = b"age-encryption.org/v1\n-> X25519 " + _b64(share) + b"\n" + _b64(body) + b"\n"
    return _with_valid_mac(header, file_key)


class _CountingReader(io.BytesIO):
    """A source that records the largest single read it was asked for."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.max_read = 0

    def read(self, size=-1):
        self.max_read = max(self.max_read, size if size is not None and size >= 0 else len(self.getbuffer()))
        return super().read(size)


# --- round trips and the interface -----------------------------------------------------


@pytest.mark.parametrize("size", [0, 1, 65535, 65536, 65537, 131072, 200000])
def test_round_trip_at_every_chunk_boundary(size):
    identity, recipient = agefmt.generate_identity()
    plain = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    ct, out = io.BytesIO(), io.BytesIO()
    agefmt.encrypt(io.BytesIO(plain), ct, recipient)
    agefmt.decrypt(io.BytesIO(ct.getvalue()), out, identity)
    assert out.getvalue() == plain


def test_generated_identity_and_recipient_have_the_standard_prefixes():
    identity, recipient = agefmt.generate_identity()
    assert identity.startswith("AGE-SECRET-KEY-1") and identity.isupper()
    assert recipient.startswith("age1") and recipient.islower()
    assert agefmt.parse_recipient(recipient).public_bytes_raw() == \
           agefmt.parse_identity(identity).public_key().public_bytes_raw()


def test_the_corpus_identities_and_recipients_round_trip_through_bech32():
    """The bech32 codec is scored against a key the reference implementation produced."""
    known = "AGE-SECRET-KEY-1EGTZVFFV20835NWYV6270LXYVK2VKNX2MMDKWYKLMGR48UAWX40Q2P2LM0"
    key = agefmt.parse_identity(known)
    assert key.private_bytes_raw().hex() == \
           "ca1626252c53cf1a4dc46695e7fcc46594cb4ccadedb6712dfda0753f3ae355e"
    recipient = agefmt.encode_recipient(key.public_key())
    assert agefmt.parse_recipient(recipient).public_bytes_raw() == key.public_key().public_bytes_raw()


@pytest.mark.parametrize("bad", [
    "AGE-SECRET-KEY-1EGTZVFFV20835NWYV6270LXYVK2VKNX2MMDKWYKLMGR48UAWX40Q2P2LM1",  # checksum
    "AGE-SECRET-KEY-PQ-1HZLGZUPT4ETPKDEV8HSGFDCYZ4E522W0A7PU2LHT8EH9W6YLNC3SW78XKG",  # hybrid HRP
    "age1EGTZVFFV20835NWYV6270LXYVK2VKNX2MMDKWYKLMGR48UAWX40Q2P2LM0",  # wrong HRP
    "not-a-key",
])
def test_a_malformed_identity_is_rejected(bad):
    with pytest.raises(ValueError):
        agefmt.parse_identity(bad)


def test_an_unknown_stanza_type_is_skipped_not_rejected():
    # x25519_lowercase in one line: an unrecognised type leaves the header valid, and the file
    # simply carries no stanza this reader can unwrap.
    header = b"age-encryption.org/v1\n-> grease abc\nZm9v\n-> x25519 abc\nYmFy\n"
    with pytest.raises(agefmt.NoMatchError):
        agefmt.decrypt(io.BytesIO(_with_valid_mac(header)), io.BytesIO(), IDENTITY)


def test_a_file_key_that_is_not_sixteen_bytes_is_a_header_failure():
    # x25519_long_file_key in one line.
    with pytest.raises(agefmt.HeaderError):
        agefmt.decrypt(io.BytesIO(_x25519_file_wrapping(b"k" * 32)), io.BytesIO(), IDENTITY)


def test_a_sixteen_byte_file_key_gets_past_the_header(): # the control for the test above
    with pytest.raises(agefmt.PayloadError):  # a valid header, then no chunks at all
        agefmt.decrypt(io.BytesIO(_x25519_file_wrapping(b"k" * 16)), io.BytesIO(), IDENTITY)


def test_a_malformed_header_fails_before_the_identity_is_parsed():
    with pytest.raises(agefmt.HeaderError):
        agefmt.decrypt(io.BytesIO(b"not-an-age-file\n"), io.BytesIO(), None)


def test_a_wrong_identity_is_a_no_match():
    _, recipient = agefmt.generate_identity()
    other, _ = agefmt.generate_identity()
    ct = io.BytesIO()
    agefmt.encrypt(io.BytesIO(b"hello"), ct, recipient)
    with pytest.raises(agefmt.NoMatchError):
        agefmt.decrypt(io.BytesIO(ct.getvalue()), io.BytesIO(), other)


def test_a_flipped_ciphertext_byte_is_a_payload_failure():
    identity, recipient = agefmt.generate_identity()
    ct = io.BytesIO()
    agefmt.encrypt(io.BytesIO(b"x" * 100), ct, recipient)
    data = bytearray(ct.getvalue())
    data[-1] ^= 0x01
    with pytest.raises(agefmt.PayloadError):
        agefmt.decrypt(io.BytesIO(bytes(data)), io.BytesIO(), identity)


def test_a_flipped_header_byte_is_an_hmac_failure():
    identity, recipient = agefmt.generate_identity()
    ct = io.BytesIO()
    agefmt.encrypt(io.BytesIO(b"x" * 100), ct, recipient)
    data = ct.getvalue()
    cut = data.index(b"\n--- ") + 5
    tampered = bytearray(data)
    tampered[cut] = ord("A") if tampered[cut] != ord("A") else ord("B")
    with pytest.raises(agefmt.HmacError):
        agefmt.decrypt(io.BytesIO(bytes(tampered)), io.BytesIO(), identity)


def test_encrypt_and_decrypt_read_in_bounded_chunks():
    """Streaming, not buffering: neither direction ever asks for the whole file."""
    identity, recipient = agefmt.generate_identity()
    plain = _CountingReader(b"z" * 300000)
    ct = io.BytesIO()
    agefmt.encrypt(plain, ct, recipient)
    assert plain.max_read <= 65536

    source = _CountingReader(ct.getvalue())
    agefmt.decrypt(source, io.BytesIO(), identity)
    assert source.max_read <= 65536 + 16


def test_header_chunk_count_matches_the_plaintext_length(tmp_path):
    identity, recipient = agefmt.generate_identity()
    path = tmp_path / "f.age"
    with path.open("wb") as fh:
        agefmt.encrypt(io.BytesIO(b"y" * 200000), fh, recipient)
    assert agefmt.header_chunk_count(path, 200000) == 4


@pytest.mark.parametrize("size,chunks", [(0, 1), (1, 1), (65536, 1), (65537, 2), (131072, 2), (200000, 4)])
def test_header_chunk_count_over_the_chunk_boundaries(tmp_path, size, chunks):
    _, recipient = agefmt.generate_identity()
    path = tmp_path / f"f{size}.age"
    with path.open("wb") as fh:
        agefmt.encrypt(io.BytesIO(b"y" * size), fh, recipient)
    assert agefmt.header_chunk_count(path, size) == chunks


def test_header_chunk_count_rejects_a_file_of_the_wrong_size(tmp_path):
    _, recipient = agefmt.generate_identity()
    path = tmp_path / "short.age"
    with path.open("wb") as fh:
        agefmt.encrypt(io.BytesIO(b"y" * 200000), fh, recipient)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(agefmt.PayloadError):
        agefmt.header_chunk_count(path, 200000)
