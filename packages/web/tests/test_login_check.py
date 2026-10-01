import httpx
import pytest

from htrflow_web.login_check import Session, SessionChecker, SessionsUnavailable


def checker(handler, clock=lambda: 0.0):
    return SessionChecker(
        "http://proxy/results",
        httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock,
    )


def test_a_valid_cookie_is_a_session_and_is_forwarded():
    seen = []

    def handler(req):
        seen.append(req.headers.get("cookie"))
        return httpx.Response(200, json={"user": "anna"})

    s = checker(handler).check("tok")
    assert s == Session("anna", "tok")
    assert seen == ["htr_session=tok"]


def test_no_cookie_never_asks():
    assert checker(lambda r: pytest.fail("asked")).check(None) is None


@pytest.mark.parametrize("cookie", ["å-not-ascii", "x" * 4097])
def test_a_cookie_no_session_could_be_never_asks(cookie):
    """Not ASCII would be a 500 building the header; over 4 KB would be a
    cache key for anything a client cares to send."""
    assert checker(lambda r: pytest.fail("asked")).check(cookie) is None


def test_401_is_no_session():
    assert checker(lambda r: httpx.Response(401)).check("tok") is None


def test_answers_are_cached_for_30_seconds():
    calls, now = [], [0.0]

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"user": "anna"})

    c = checker(handler, clock=lambda: now[0])
    c.check("tok")
    c.check("tok")
    assert len(calls) == 1
    now[0] += 31
    c.check("tok")
    assert len(calls) == 2


def test_a_proxy_that_does_not_answer_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(SessionsUnavailable):
        checker(handler).check("tok")
