import base64
import hashlib

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from htrflow_web.session import COOKIE, SessionCodec, derive_keys, load_key

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
    # Seal with associated data b"htr_session"
    codec = SessionCodec(KEY, hours=8)
    token = codec.seal("anna", "AK", "SK")
    # Try to decrypt the raw ciphertext with a different associated data
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    nonce = raw[:12]
    ciphertext = raw[12:]
    aead = AESGCM(KEY)
    # Decryption with wrong associated data should raise InvalidTag
    try:
        aead.decrypt(nonce, ciphertext, b"wrong_ad")
        assert False, "Should have raised InvalidTag"
    except InvalidTag:
        pass  # Expected: wrong associated data detected


def test_the_key_file_must_hold_32_bytes(tmp_path):
    good = tmp_path / "good"
    good.write_text(base64.b64encode(KEY).decode() + "\n")
    assert load_key(str(good)) == KEY
    short = tmp_path / "short"
    short.write_text(base64.b64encode(b"x" * 16).decode())
    with pytest.raises(ValueError, match="32 bytes"):
        load_key(str(short))


def test_the_cookie_name():
    assert COOKIE == "htr_session"
