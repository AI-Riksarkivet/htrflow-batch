import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from htrflow_web.app import create_app
from htrflow_web.passthrough import results_route


class _Bytes(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        yield self.data


def _streaming(resp):
    """A MockTransport answer built from content= is already read; a real
    transport's is a stream, which is what the pass-through must consume."""
    if not hasattr(resp, "_content"):
        return resp
    return httpx.Response(
        resp.status_code, headers=resp.headers.raw, stream=_Bytes(resp.content)
    )


def app_with(handler, calls=None):
    def wrapped(req):
        if calls is not None:
            calls.append(req)
        return _streaming(handler(req))

    app = FastAPI()
    results_route(
        app,
        "http://proxy:8082/results",
        httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return TestClient(app, base_url="https://site.example")


def test_a_file_comes_through_with_its_headers():
    def handler(req):
        assert str(req.url) == "http://proxy:8082/results/ns/demo/R1/iiif.json"
        assert req.headers["cookie"] == "htr_session=tok"
        assert req.headers["x-forwarded-proto"] == "https"
        assert req.headers["x-forwarded-host"] == "site.example"
        return httpx.Response(
            200,
            content=b"{}",
            headers={
                "content-type": "application/json",
                "etag": '"e"',
                "content-security-policy": "default-src 'none'; sandbox",
            },
        )

    c = app_with(handler)
    c.cookies.set("htr_session", "tok")
    r = c.get("/results/ns/demo/R1/iiif.json")
    assert r.status_code == 200 and r.content == b"{}"
    assert r.headers["etag"] == '"e"'
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_304_and_set_cookie_pass_unchanged():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(
                204, headers={"set-cookie": "htr_session=x; HttpOnly"}
            )
        assert req.headers["if-none-match"] == '"e"'
        return httpx.Response(304, headers={"etag": '"e"'})

    c = app_with(handler)
    r304 = c.get("/results/ns/x", headers={"If-None-Match": '"e"'})
    assert r304.status_code == 304 and r304.content == b""
    r = c.post(
        "/results/_login",
        json={"username": "a", "password": "b"},
        headers={"Origin": "https://site.example"},
    )
    assert r.status_code == 204
    assert r.headers["set-cookie"].startswith("htr_session=x")


def test_several_set_cookie_headers_all_pass():
    def handler(req):
        return httpx.Response(
            200, headers=[("set-cookie", "a=1"), ("set-cookie", "b=2")]
        )

    r = app_with(handler).get("/results/ns/x")
    assert r.headers.get_list("set-cookie") == ["a=1", "b=2"]


def test_the_body_is_streamed_not_buffered():
    sent = b"x" * (3 * 1024 * 1024)
    c = app_with(
        lambda req: httpx.Response(
            200, content=sent, headers={"content-type": "text/plain"}
        )
    )
    with c.stream("GET", "/results/ns/big.txt") as r:
        chunks = list(r.iter_bytes())
    assert b"".join(chunks) == sent


def test_the_upstream_response_is_closed_after_streaming():
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"

        async def aclose(self):
            closed.append(True)

    c = app_with(lambda req: httpx.Response(200, stream=Body()))
    assert c.get("/results/ns/x").content == b"abc"
    assert closed


def test_only_the_session_cookie_is_forwarded():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    c.get("/results/ns/x", headers={"Cookie": "other=1; htr_session=tok; x=2"})
    assert calls[0].headers["cookie"] == "htr_session=tok"
    c.get("/results/ns/x", headers={"Cookie": "other=1"})
    assert "cookie" not in calls[1].headers


def test_a_non_ascii_request_header_value_is_not_a_500():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    r = c.get(
        "/results/ns/x",
        headers=[(b"if-none-match", b'"caf\xc3\xa9"'), (b"x-forwarded-for", b"a\xff")],
    )
    assert r.status_code == 200


def test_response_headers_are_copied_as_bytes_with_content_encoding():
    def handler(req):
        return httpx.Response(
            200,
            headers=[
                (b"content-disposition", b'attachment; filename="a.txt"'),
                (b"content-encoding", b"identity"),
                (b"x-secret", b"no"),
            ],
        )

    r = app_with(handler).get("/results/ns/x")
    assert r.status_code == 200
    assert (
        r.headers.raw.count((b"content-disposition", b'attachment; filename="a.txt"'))
        == 1
    )
    assert r.headers["content-encoding"] == "identity"
    assert "x-secret" not in r.headers


def test_a_declared_oversize_post_is_413_and_the_proxy_is_not_called():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    r = c.post("/results/_login", content=b"x" * 16385)
    assert r.status_code == 413 and calls == []
    assert c.post("/results/_login", content=b"x" * 16384).status_code == 200


def test_a_chunked_oversize_post_is_413_and_the_proxy_is_not_called():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)

    def gen():
        for _ in range(5):
            yield b"x" * 4096

    r = c.post("/results/_login", content=gen())
    assert r.status_code == 413 and calls == []


def test_a_mid_stream_failure_is_logged_and_raised(caplog):
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"a"
            raise httpx.ReadError("secret-cookie-value")

    c = app_with(lambda req: httpx.Response(200, stream=Broken()))
    with caplog.at_level("WARNING", logger="htrflow_web.passthrough"):
        try:
            c.get("/results/ns/x")
        except Exception:
            pass
    assert any("mid-answer" in m for m in caplog.messages)
    assert "secret-cookie-value" not in caplog.text


def test_head_passes_without_a_body():
    seen = []

    def handler(req):
        seen.append(req.method)
        return httpx.Response(
            200, headers={"content-length": "5", "content-type": "text/plain"}
        )

    r = app_with(handler).head("/results/ns/x")
    assert r.status_code == 200 and r.content == b"" and seen == ["HEAD"]


def test_a_proxy_that_does_not_answer_is_502():
    def handler(req):
        raise httpx.ConnectError("down")

    assert app_with(handler).get("/results/ns/x").status_code == 502


def test_an_encoded_slash_in_the_prefix_is_404_and_the_proxy_is_not_called():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    assert c.get("/results%2Fns/x").status_code == 404
    assert calls == []


def test_the_path_is_forwarded_still_encoded():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    assert c.get("/results/ns/a%20b").status_code == 200
    assert str(calls[0].url) == "http://proxy:8082/results/ns/a%20b"
    c.get("/results/ns/a%2Fb")
    assert str(calls[1].url) == "http://proxy:8082/results/ns/a%2Fb"


def test_the_query_string_is_forwarded_unchanged():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    c.get("/results/ns/x?a=1&b=%20")
    assert str(calls[0].url) == "http://proxy:8082/results/ns/x?a=1&b=%20"


def test_this_pod_appends_its_peer_and_sets_forwarded_headers():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    c.get("/results/ns/x")
    assert calls[0].headers["x-forwarded-for"] == "testclient"
    c.get(
        "/results/ns/x",
        headers={
            "X-Forwarded-For": "203.0.113.9",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "public.example",
        },
    )
    h = calls[1].headers
    assert h["x-forwarded-for"] == "203.0.113.9, testclient"
    assert h["x-forwarded-proto"] == "https"
    assert h["x-forwarded-host"] == "public.example"


def test_without_an_ingress_proto_and_host_come_from_the_request():
    calls = []
    app = FastAPI()
    results_route(
        app,
        "http://proxy:8082/results",
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: (calls.append(r), _streaming(httpx.Response(200)))[1]
            )
        ),
    )
    TestClient(app, base_url="http://plain.example:8080").get("/results/ns/x")
    h = calls[0].headers
    assert h["x-forwarded-proto"] == "http"
    assert h["x-forwarded-host"] == "plain.example:8080"


def test_unlisted_request_headers_are_not_forwarded():
    calls = []
    c = app_with(lambda req: httpx.Response(200), calls)
    c.get("/results/ns/x", headers={"Authorization": "Bearer z", "X-Other": "1"})
    assert "authorization" not in calls[0].headers
    assert "x-other" not in calls[0].headers


def _create(monkeypatch, handler, proxy="http://proxy:8082/results"):
    from types import SimpleNamespace  # noqa: PLC0415

    import htrflow_web.app as app_mod  # noqa: PLC0415

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: _streaming(handler(r)))
    )
    monkeypatch.setattr(
        app_mod, "results_route", lambda app, base: results_route(app, base, client)
    )
    reader = SimpleNamespace(
        cfg=SimpleNamespace(results_url="", namespaces=("ns",), results_proxy=proxy)
    )
    return create_app(
        reader, static_dir="/nonexistent", sessions=None, progress=object()
    )


def test_the_proxys_own_security_headers_win_over_the_web_fronts(monkeypatch):
    csp = "default-src 'none'; sandbox"
    app = _create(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            headers={
                "content-security-policy": csp,
                "x-content-type-options": "nosniff",
            },
        ),
    )
    r = TestClient(app).get("/results/ns/x")
    assert r.headers["content-security-policy"] == csp


def test_route_absent_when_no_results_proxy(monkeypatch):
    app = _create(monkeypatch, lambda req: httpx.Response(200), proxy="")
    assert TestClient(app).get("/results/ns/x").status_code == 404


def test_route_absent_in_site_only_mode():
    from types import SimpleNamespace  # noqa: PLC0415

    app = create_app(SimpleNamespace(cfg=None), static_dir="/nonexistent")
    assert TestClient(app).get("/results/ns/x").status_code in (404, 503)


def test_site_only_mode_passes_results_through_when_given_a_proxy(monkeypatch):
    """The compose stack runs the web front site-only (no apiserver) next to
    a results proxy: the viewer there reads /results on the site's own
    origin like anywhere else. /api/v1 still answers 503, not 401."""
    import htrflow_web.app as app_mod  # noqa: PLC0415
    from htrflow_web.app import NoCluster  # noqa: PLC0415

    seen = []

    def handler(req):
        seen.append(str(req.url))
        return _streaming(httpx.Response(200, content=b"{}"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        app_mod, "results_route", lambda app, base: results_route(app, base, client)
    )
    app = create_app(
        NoCluster(),
        static_dir="/nonexistent",
        results_proxy="http://results:8082/results",
    )
    c = TestClient(app)
    assert c.get("/results/ns/x").status_code == 200
    assert seen == ["http://results:8082/results/ns/x"]
    assert c.get("/api/v1/jobs").status_code == 503


def test_the_entrypoint_hands_the_proxy_to_the_app_in_site_only_mode(monkeypatch):
    import htrflow_web.__main__ as main_mod  # noqa: PLC0415

    monkeypatch.setenv("HTRFLOW_WEB_SITE_ONLY", "1")
    monkeypatch.setenv("HTRFLOW_RESULTS_PROXY", "http://results:8082/results")
    got = {}
    monkeypatch.setattr(
        main_mod, "create_app", lambda *a, **k: got.update(k) or FastAPI()
    )
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda *a, **k: None)
    main_mod.main()
    assert got["results_proxy"] == "http://results:8082/results"


def test_one_persons_login_never_rides_along_on_another_request():
    """httpx keeps every Set-Cookie in the client's own jar and adds it to
    any later request that has no Cookie header. The pass-through's client
    is shared by everyone: after one login, an anonymous request must still
    reach the proxy with no cookie at all."""
    from htrflow_web.passthrough import proxy_client  # noqa: PLC0415

    seen = []

    def handler(req):
        seen.append(req.headers.get("cookie"))
        if req.url.path.endswith("/_login"):
            return httpx.Response(
                204, headers={"set-cookie": "htr_session=alice; Path=/; HttpOnly"}
            )
        return httpx.Response(401, content=b"{}")

    app = FastAPI()
    results_route(
        app,
        "http://proxy:8082/results",
        proxy_client(transport=httpx.MockTransport(lambda r: _streaming(handler(r)))),
    )
    c = TestClient(app, base_url="https://site.example")
    login = c.post(
        "/results/_login",
        json={"username": "alice", "password": "pw"},
        headers={"Origin": "https://site.example"},
    )
    assert login.status_code == 204
    anonymous = TestClient(app, base_url="https://site.example")  # no cookies
    assert anonymous.get("/results/ns/x").status_code == 401
    assert seen == [None, None]


def test_the_web_fronts_other_proxy_clients_keep_no_cookies_either():
    from htrflow_web.progress import ProgressReader  # noqa: PLC0415
    from htrflow_web.sessions import SessionChecker  # noqa: PLC0415

    for client in (
        SessionChecker("http://proxy:8082/results")._client,
        ProgressReader()._client,
    ):
        client.cookies.extract_cookies(
            httpx.Response(
                200,
                headers={"set-cookie": "htr_session=alice; Path=/"},
                request=httpx.Request("GET", "http://proxy:8082/results/_session"),
            )
        )
        assert list(client.cookies.jar) == []


def test_the_default_client_carries_no_one_elses_login(monkeypatch):
    """The same guarantee, through the client ``results_route`` builds for
    itself -- the one production uses. Every httpx transport is answered
    here, so a default that were a plain ``httpx.AsyncClient`` would reach
    this handler too, keep alice's Set-Cookie in its jar and send it on the
    anonymous request."""
    seen = []

    def handler(req):
        seen.append(req.headers.get("cookie"))
        if req.url.path.endswith("/_login"):
            return httpx.Response(
                204, headers={"set-cookie": "htr_session=alice; Path=/; HttpOnly"}
            )
        return httpx.Response(401, content=b"{}")

    async def answer(self, request):
        return _streaming(handler(request))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answer)
    app = FastAPI()
    results_route(app, "http://proxy:8082/results")  # its own default client
    c = TestClient(app, base_url="https://site.example")
    login = c.post(
        "/results/_login",
        json={"username": "alice", "password": "pw"},
        headers={"Origin": "https://site.example"},
    )
    assert login.status_code == 204
    assert seen == [None]
    anonymous = TestClient(app, base_url="https://site.example")
    assert anonymous.get("/results/ns/x").status_code == 401
    bob = TestClient(app, base_url="https://site.example")
    bob.cookies.set("htr_session", "bob")
    assert bob.get("/results/ns/x").status_code == 401
    assert seen == [None, None, "htr_session=bob"]
