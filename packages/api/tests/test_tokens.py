import threading
import time

import httpx
import pytest
from api_fakes import Signer

from htrflow_api.tokens import REFETCH_SECONDS, Identity, Refused, Verifier


def test_a_good_token_gives_the_user_and_the_groups(cfg, signer):
    who = Verifier(cfg, signer.jwks).verify(signer.token(groups=["a", "htr-operators"]))
    assert who == Identity("ada", ("a", "htr-operators"))


@pytest.mark.parametrize(
    "overrides, word",
    [
        ({"exp": 1}, "expired"),
        ({"iss": "https://elsewhere.example/realms/x"}, "issuer"),
        ({"aud": "htrflow-train"}, "htrflow-batch"),
        ({"aud": None}, "htrflow-batch"),
        ({"exp": None}, "expiry"),
    ],
)
def test_a_token_that_is_not_ours_is_a_401(cfg, signer, overrides, word):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(**overrides))
    assert e.value.status == 401 and word in e.value.detail
    assert e.value.hint == "Sign in again: htrflow-campaigns login"


def test_another_issuer_is_named_with_the_one_this_service_expects(cfg, signer):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(iss="https://other.example/r"))
    assert e.value.detail == (
        "This token comes from another issuer than https://id.example/realms/htrflow."
    )
    assert "other.example" not in e.value.detail


@pytest.mark.parametrize(
    "claims",
    [{"iat": 2}, {"iat": 120}, {"nbf": 2}, {"exp": -10}],
    ids=["iat 2 s ahead", "iat 120 s ahead", "nbf 2 s ahead", "exp 10 s ago"],
)
def test_clocks_a_little_apart_do_not_refuse_a_token(cfg, signer, claims):
    now = int(time.time())
    token = signer.token(**{k: now + v for k, v in claims.items()})
    assert Verifier(cfg, signer.jwks).verify(token).user == "ada"


def test_a_token_not_valid_for_minutes_yet_says_whose_clock_is_ahead(cfg, signer):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(nbf=int(time.time()) + 120))
    assert e.value.status == 401
    assert e.value.detail == (
        "This token is not valid yet: the sign-in service's clock is ahead of "
        "this service's."
    )
    assert e.value.hint == (
        "Tell whoever runs the cluster; signing in again will not help."
    )


def test_a_token_expired_minutes_ago_is_expired(cfg, signer):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(exp=int(time.time()) - 120))
    assert e.value.status == 401 and "expired" in e.value.detail


def test_a_token_signed_by_another_key_is_a_401(cfg, signer):
    other = Signer(kid=signer.kid)
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(other.token())
    assert e.value.status == 401


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_a_symmetric_or_absent_algorithm_is_a_401(cfg, signer, alg):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(alg=alg))
    assert e.value.status == 401


@pytest.mark.parametrize("text", ["", "abc", "a.b", "a.b.c.d"])
def test_text_that_is_not_a_token_is_a_401_not_an_error(cfg, signer, text):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(text)
    assert e.value.status == 401


@pytest.mark.parametrize("user", [None, "", 5, ["ada"]])
def test_a_token_without_a_usable_user_is_a_401(cfg, signer, user):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(preferred_username=user))
    assert e.value.status == 401
    assert e.value.hint == "Sign in again: htrflow-campaigns login"


def test_a_token_without_the_group_is_a_403_that_names_it(cfg, signer):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(groups=["other"]))
    assert e.value.status == 403 and "htr-operators" in e.value.detail


@pytest.mark.parametrize(
    "groups",
    [["/htr-operators"], ["/team/htr-operators"], None, "htr-operators", [1, 2]],
)
def test_group_paths_and_malformed_claims_are_a_403_with_the_mapper_in_the_hint(
    cfg, signer, groups
):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(groups=groups))
    assert e.value.status == 403 and "full group path" in e.value.hint


def test_the_claim_names_come_from_the_config(cfg, signer):
    cfg2 = cfg.model_copy(update={"user_claim": "email", "groups_claim": "roles"})
    token = signer.token(email="ada@example", roles=["htr-operators"], groups=None)
    assert Verifier(cfg2, signer.jwks).verify(token).user == "ada@example"


def test_keys_are_fetched_once_for_many_tokens(cfg, signer):
    calls = []
    v = Verifier(cfg, lambda: calls.append(1) or signer.jwks())
    for _ in range(3):
        v.verify(signer.token())
    assert len(calls) == 1


def test_an_unknown_key_is_fetched_again_but_only_once_a_minute(cfg, signer):
    now, calls = [0.0], []
    v = Verifier(cfg, lambda: calls.append(1) or signer.jwks(), clock=lambda: now[0])
    v.verify(signer.token())
    with pytest.raises(Refused):
        v.verify(Signer(kid="rotated").token())
    assert len(calls) == 1
    now[0] = REFETCH_SECONDS + 1
    for _ in range(5):
        with pytest.raises(Refused):
            v.verify(Signer(kid="rotated").token())
    assert len(calls) == 2
    now[0] = 2 * REFETCH_SECONDS + 2
    with pytest.raises(Refused):
        v.verify(Signer(kid="rotated").token())
    assert len(calls) == 3


def test_an_unreachable_provider_is_a_503_and_cached_keys_still_verify(cfg, signer):
    up = [True]

    def fetch():
        if not up[0]:
            raise httpx.ConnectError("down")
        return signer.jwks()

    now = [0.0]
    v = Verifier(cfg, fetch, clock=lambda: now[0])
    v.verify(signer.token())
    up[0] = False
    now[0] = REFETCH_SECONDS + 1
    assert v.verify(signer.token()).user == "ada"
    with pytest.raises(Refused) as e:
        v.verify(Signer(kid="rotated").token())
    assert e.value.status == 503
    assert (
        e.value.detail
        == "The sign-in service could not be reached to check this token."
    )
    assert e.value.hint == (
        "Try again in a minute; if it stays, tell whoever runs the cluster."
    )


def test_no_refusal_quotes_the_token(cfg, signer):
    token = signer.token(aud="other")
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(token)
    assert token not in e.value.detail + e.value.hint


def test_a_key_of_an_unknown_type_is_skipped(cfg, signer):
    doc = signer.jwks()
    doc["keys"].insert(0, {"kty": "XYZ", "kid": "weird"})
    assert Verifier(cfg, lambda: doc).verify(signer.token()).user == "ada"


# The default fetch, against a fake provider.
def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_default_fetch_reads_the_jwks_url_when_set(cfg, signer):
    cfg2 = cfg.model_copy(update={"jwks_url": "http://keys.local/jwks"})
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=signer.jwks())

    v = Verifier(cfg2, http=_client(handler))
    assert v.verify(signer.token()).user == "ada"
    assert seen == ["http://keys.local/jwks"]


def test_the_default_fetch_uses_discovery_when_the_jwks_url_is_empty(cfg, signer):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": "http://in.cluster/keys"})
        return httpx.Response(200, json=signer.jwks())

    v = Verifier(cfg, http=_client(handler))
    assert v.verify(signer.token()).user == "ada"
    assert seen == [
        "https://id.example/realms/htrflow/.well-known/openid-configuration",
        "http://in.cluster/keys",
    ]


@pytest.mark.parametrize(
    "handler",
    [
        lambda req: httpx.Response(500),
        lambda req: httpx.Response(200, text="not json"),
        lambda req: httpx.Response(200, json={"nokeys": 1}),
        lambda req: httpx.Response(200, json={"jwks_uri": 5}),
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")),
    ],
)
def test_a_failing_provider_is_a_503(cfg, signer, handler):
    with pytest.raises(Refused) as e:
        Verifier(cfg, http=_client(handler)).verify(signer.token())
    assert e.value.status == 503


# Whatever the provider or the fetch does wrong, the answer is a refusal.
def test_a_bad_entry_is_skipped_and_the_good_one_beside_it_is_used(cfg, signer):
    doc = {"keys": ["junk", 5, {"no": "kid"}, *signer.jwks()["keys"]]}
    assert Verifier(cfg, lambda: doc).verify(signer.token()).user == "ada"


def test_a_deeply_nested_body_is_a_503(cfg, signer):
    def handler(req):
        return httpx.Response(200, content=b"[" * 100_000)

    with pytest.raises(Refused) as e:
        Verifier(cfg, http=_client(handler)).verify(signer.token())
    assert e.value.status == 503


def test_a_malformed_jwks_uri_is_a_503(cfg, signer):
    def handler(req):
        return httpx.Response(200, json={"jwks_uri": "http://[::1"})

    with pytest.raises(Refused) as e:
        Verifier(cfg, http=_client(handler)).verify(signer.token())
    assert e.value.status == 503


def test_a_fetch_that_raises_anything_is_a_503(cfg, signer):
    def fetch():
        raise OSError("disk")

    with pytest.raises(Refused) as e:
        Verifier(cfg, fetch).verify(signer.token())
    assert e.value.status == 503


def test_a_first_fetch_that_fails_is_a_503_then_recovers_a_minute_later(cfg, signer):
    up, now = [False], [0.0]

    def fetch():
        if not up[0]:
            raise OSError("down")
        return signer.jwks()

    v = Verifier(cfg, fetch, clock=lambda: now[0])
    for _ in range(2):
        with pytest.raises(Refused) as e:
            v.verify(signer.token())
        assert e.value.status == 503
    up[0], now[0] = True, REFETCH_SECONDS + 1
    assert v.verify(signer.token()).user == "ada"


def test_a_cached_key_does_not_wait_for_a_slow_fetch(cfg, signer):
    release, started = threading.Event(), threading.Event()
    slow = [False]

    def fetch():
        if slow[0]:
            started.set()
            release.wait(10)
        return signer.jwks()

    now = [0.0]
    v = Verifier(cfg, fetch, clock=lambda: now[0])
    v.verify(signer.token())
    slow[0], now[0] = True, REFETCH_SECONDS + 1
    stuck = threading.Thread(
        target=lambda: pytest.raises(Refused, v.verify, Signer(kid="x").token())
    )
    stuck.start()
    assert started.wait(5)
    done = []
    quick = threading.Thread(target=lambda: done.append(v.verify(signer.token())))
    quick.start()
    quick.join(3)
    finished_while_blocked = not quick.is_alive()
    release.set()
    stuck.join(5)
    assert finished_while_blocked and done[0].user == "ada"
