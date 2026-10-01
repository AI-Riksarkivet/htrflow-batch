import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from htrflow_web.session import (
    KeyFileCodec,
    SessionCodec,
    SessionKeyUnavailable,
    derive_keys,
    load_key,
)

KEY = bytes(range(32))


def test_a_sealed_session_opens_to_what_went_in():
    codec = SessionCodec(KEY, hours=8, clock=lambda: 1000.0)
    token = codec.seal("anna", "AK", "SK")
    data = codec.open(token)
    assert (data.user, data.access_key, data.secret_key) == ("anna", "AK", "SK")
    assert data.expires == 1000.0 + 8 * 3600


def test_the_secret_key_is_not_readable_in_the_cookie():
    token = SessionCodec(KEY, hours=8).seal("anna", "AK", "SK")
    assert b"SK" not in base64.urlsafe_b64decode(token + "==")


def test_an_expired_session_is_no_session():
    now = [1000.0]
    codec = SessionCodec(KEY, hours=1, clock=lambda: now[0])
    token = codec.seal("anna", "AK", "SK")
    now[0] += 3601
    assert codec.open(token) is None


@pytest.mark.parametrize("token", ["", "not-base64!!", "AAAA", "A" * 400])
def test_garbage_is_no_session(token):
    assert SessionCodec(KEY, hours=8).open(token) is None


def test_a_tampered_cookie_is_no_session():
    token = SessionCodec(KEY, hours=8).seal("anna", "AK", "SK")
    raw = bytearray(base64.urlsafe_b64decode(token + "=="))
    raw[-1] ^= 1
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    assert SessionCodec(KEY, hours=8).open(tampered) is None


def test_a_cookie_under_another_key_is_no_session():
    token = SessionCodec(KEY, hours=8).seal("anna", "AK", "SK")
    assert SessionCodec(bytes(32), hours=8).open(token) is None


def test_hcp_keys_are_base64_user_and_md5_password():
    ak, sk = derive_keys("anna", "p\\ss", "hcp")
    assert ak == base64.b64encode(b"anna").decode()
    assert sk == hashlib.md5(b"p\\ss").hexdigest()


def test_no_derivation_takes_the_keys_as_given():
    assert derive_keys("AKID", "SECRET", "none") == ("AKID", "SECRET")


def test_derived_keys_do_not_contain_the_password():
    ak, sk = derive_keys("anna", "pw", "hcp")
    assert "pw" not in ak
    assert "pw" not in sk


def test_a_token_sealed_with_different_associated_data_does_not_open():
    """SessionCodec rejects tokens sealed with wrong associated data."""
    now = 1000.0
    codec = SessionCodec(KEY, hours=8, clock=lambda: now)
    aead = AESGCM(KEY)
    nonce = os.urandom(12)
    future_time = now + 8 * 3600  # 8 hours from now

    # Build a token with no associated data
    body_none = json.dumps(
        {"u": "anna", "a": "AK", "s": "SK", "e": future_time}
    ).encode()
    ciphertext_none = aead.encrypt(nonce, body_none, None)
    token_none = base64.urlsafe_b64encode(nonce + ciphertext_none).decode().rstrip("=")
    assert codec.open(token_none) is None  # SessionCodec requires htr_session AD

    # Build a token with wrong associated data
    body_other = json.dumps(
        {"u": "anna", "a": "AK", "s": "SK", "e": future_time}
    ).encode()
    ciphertext_other = aead.encrypt(nonce, body_other, b"other")
    token_other = (
        base64.urlsafe_b64encode(nonce + ciphertext_other).decode().rstrip("=")
    )
    assert codec.open(token_other) is None  # SessionCodec requires htr_session AD

    # Build a token with correct associated data - should open
    body_correct = json.dumps(
        {"u": "anna", "a": "AK", "s": "SK", "e": future_time}
    ).encode()
    ciphertext_correct = aead.encrypt(nonce, body_correct, b"htr_session")
    token_correct = (
        base64.urlsafe_b64encode(nonce + ciphertext_correct).decode().rstrip("=")
    )
    data = codec.open(token_correct)
    assert data is not None
    assert (data.user, data.access_key, data.secret_key) == ("anna", "AK", "SK")


def test_the_key_file_must_hold_32_bytes(tmp_path):
    good = tmp_path / "good"
    good.write_text(base64.b64encode(KEY).decode() + "\n")
    assert load_key(str(good)) == KEY
    short = tmp_path / "short"
    short.write_text(base64.b64encode(b"x" * 16).decode())
    with pytest.raises(ValueError, match="32 bytes"):
        load_key(str(short))


def _write_key(path, key: bytes, bump: int) -> None:
    """A new key file, as the kubelet swaps one in: a new file (new inode)
    whose mtime differs too."""
    tmp = path.with_suffix(".new")
    tmp.write_text(base64.b64encode(key).decode())
    os.utime(tmp, ns=(bump, bump))
    os.replace(tmp, path)


def test_a_rotated_key_file_ends_every_session_sealed_under_the_old_one(tmp_path):
    """Rotating the session Secret is how an operator ends a leaked session:
    the running proxy follows the new key, with no restart."""
    path = tmp_path / "key"
    _write_key(path, KEY, 1)
    codec = KeyFileCodec(str(path), hours=8)
    old = codec.seal("anna", "AK", "SK")
    assert codec.open(old) is not None
    _write_key(path, bytes(reversed(KEY)), 2)
    assert codec.open(old) is None
    new = codec.seal("anna", "AK", "SK")
    assert codec.open(new) is not None


def _kubelet_volume(root, key: bytes, stamp: str) -> None:
    """A Secret volume as the kubelet lays it out and updates it: the files
    in a timestamped directory, `..data` a symlink to it, `key` a symlink
    through `..data`; an update writes a new directory and renames a new
    `..data` link over the old one."""
    target = root / stamp
    target.mkdir()
    (target / "key").write_text(base64.b64encode(key).decode())
    (root / "..data_tmp").symlink_to(stamp)
    os.replace(root / "..data_tmp", root / "..data")
    if not (root / "key").is_symlink():
        (root / "key").symlink_to("..data/key")


def test_a_kubelet_secret_update_ends_the_old_sessions(tmp_path):
    _kubelet_volume(tmp_path, KEY, "..2026_10_01_09_00_00.1")
    codec = KeyFileCodec(str(tmp_path / "key"), hours=8)
    old = codec.seal("anna", "AK", "SK")
    assert codec.open(old) is not None
    _kubelet_volume(tmp_path, bytes(reversed(KEY)), "..2026_10_01_09_05_00.2")
    assert codec.open(old) is None
    assert codec.open(codec.seal("anna", "AK", "SK")) is not None


def test_an_unchanged_key_file_is_not_read_again(tmp_path, monkeypatch):
    import htrflow_web.session as session_mod

    path = tmp_path / "key"
    _write_key(path, KEY, 1)
    reads = []
    real = session_mod.load_key
    monkeypatch.setattr(session_mod, "load_key", lambda p: reads.append(p) or real(p))
    codec = KeyFileCodec(str(path), hours=8)
    token = codec.seal("anna", "AK", "SK")
    for _ in range(5):
        assert codec.open(token) is not None
    assert len(reads) == 1


def test_a_key_file_that_turns_unreadable_opens_no_session(tmp_path, caplog):
    """Fail closed: a rotation meant to end sessions must never leave the
    old key in use because the new file was wrong."""
    path = tmp_path / "key"
    _write_key(path, KEY, 1)
    codec = KeyFileCodec(str(path), hours=8)
    token = codec.seal("anna", "AK", "SK")
    _write_key(path, b"short", 2)
    assert codec.open(token) is None
    with pytest.raises(SessionKeyUnavailable):
        codec.seal("anna", "AK", "SK")
    assert "session key" in caplog.text
    _write_key(path, KEY, 3)
    assert codec.open(token) is not None


def test_a_bad_key_file_still_stops_startup(tmp_path):
    path = tmp_path / "key"
    _write_key(path, b"short", 1)
    with pytest.raises(ValueError, match="32 bytes"):
        KeyFileCodec(str(path), hours=8)
