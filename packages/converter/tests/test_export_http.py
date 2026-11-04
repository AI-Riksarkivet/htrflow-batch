import httpx
import pytest

from htrflow_converter.export_http import (
    Forbidden,
    Results,
    ResultsUnavailable,
    SessionLost,
    SizeUnreadable,
    image_size,
)

URL = "https://htr.example/results"
COOKIE = "__Host-htr_session=abc; Path=/; Secure; HttpOnly"


def make(handler):
    seen: list[httpx.Request] = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return Results(URL, "ann", "s3cret", client), seen


def test_login_posts_the_form_with_the_sites_origin():
    def handler(request):
        if request.url.path.endswith("/_login"):
            return httpx.Response(204, headers={"set-cookie": COOKIE})
        return httpx.Response(200, content=b"x")

    results, seen = make(handler)
    results.login()
    assert results.get("ns/p/V1/manifest.json") == b"x"
    login, get = seen
    assert (login.method, str(login.url)) == ("POST", f"{URL}/_login")
    assert login.headers["origin"] == "https://htr.example"
    assert login.content == b"username=ann&password=s3cret"
    assert (get.method, str(get.url)) == ("GET", f"{URL}/ns/p/V1/manifest.json")
    assert "__Host-htr_session=abc" in get.headers["cookie"]


def test_login_failure_carries_the_proxys_detail():
    results, _ = make(
        lambda r: httpx.Response(429, json={"detail": "too many attempts"})
    )
    with pytest.raises(SessionLost, match="too many attempts"):
        results.login()


def test_get_maps_404_to_none_and_403_to_forbidden():
    def handler(request):
        if request.url.path.endswith("/_login"):
            return httpx.Response(204)
        status = {"gone": 404, "denied": 403, "bad": 502}[
            request.url.path.rsplit("/", 1)[1]
        ]
        return httpx.Response(status, json={"detail": "nope"})

    results, _ = make(handler)
    results.login()
    assert results.get("x/gone") is None
    with pytest.raises(Forbidden):
        results.get("x/denied")
    with pytest.raises(ResultsUnavailable, match="502.*nope"):
        results.get("x/bad")


def test_an_expired_session_logs_in_once_more():
    gets = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(204)
        gets.append(1)
        if len(gets) == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, content=b"ok")

    results, seen = make(handler)
    results.login()
    assert results.get("a") == b"ok"
    assert [r.method for r in seen].count("POST") == 2


def test_a_second_401_stops_the_run():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(401, json={"detail": "expired"})

    results, _ = make(handler)
    results.login()
    with pytest.raises(SessionLost):
        results.get("a")


def sized(response):
    return httpx.Client(transport=httpx.MockTransport(lambda r: response))


def test_image_size_reads_info_json():
    ok = httpx.Response(200, json={"width": 3494, "height": 2472})
    assert image_size(sized(ok), "https://iiif.example/R1/info.json") == (3494, 2472)
    for bad in (
        httpx.Response(404),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"width": 0, "height": 5}),
        httpx.Response(200, json={"height": 5}),
    ):
        with pytest.raises(SizeUnreadable):
            image_size(sized(bad), "https://iiif.example/R1/info.json")


def test_a_transport_error_on_login_is_session_lost():
    def boom(request):
        raise httpx.ConnectError("no route")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(SessionLost, match="ConnectError"):
        Results("https://htr.example/results", "ann", "pw", client).login()


def test_a_transport_error_on_a_get_is_results_unavailable():
    def handler(request):
        if request.url.path.endswith("/_login"):
            return httpx.Response(204)
        raise httpx.ReadTimeout("slow")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    results = Results("https://htr.example/results", "ann", "pw", client)
    results.login()
    with pytest.raises(ResultsUnavailable, match="ReadTimeout"):
        results.get("ns/p/V1/manifest.json")
