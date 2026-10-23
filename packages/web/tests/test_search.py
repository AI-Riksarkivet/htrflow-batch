"""The /search/select pass-through (search.py): GET-only, the select
handler only, a parameter whitelist, rows capped, wt forced to json, and
the app's session gate in front of it all."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from htrflow_web.search import _MAX_ROWS, search_route

ANSWER = {"response": {"numFound": 1, "docs": [{"id": "p/v/0001"}]}}


def _no_session():
    """Stands in for the app's current_session dependency: logged in."""
    return None


def app_with(handler, session=_no_session):
    app = FastAPI()
    search_route(
        app,
        "http://solr:8983/solr/htr",
        session,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return TestClient(app)


def ok(req):
    return httpx.Response(200, json=ANSWER)


def test_the_query_reaches_the_core_with_wt_json():
    seen = []

    def handler(req):
        seen.append(req.url)
        return ok(req)

    r = app_with(handler).get("/search/select", params={"q": "content_txt:helsa"})
    assert r.status_code == 200
    assert r.json() == ANSWER
    (url,) = seen
    assert url.host == "solr" and url.path == "/solr/htr/select"
    assert url.params["q"] == "content_txt:helsa"
    assert url.params["wt"] == "json"


def test_every_fq_is_forwarded():
    seen = []

    def handler(req):
        seen.append(req.url.params)
        return ok(req)

    app_with(handler).get(
        "/search/select",
        params=[("q", "x"), ("fq", "pipeline_s:a"), ("fq", "pipeline_s:b")],
    )
    assert seen[0].get_list("fq") == ["pipeline_s:a", "pipeline_s:b"]


def test_what_is_not_whitelisted_never_leaves():
    def handler(req):
        # echo/omitParamHandler would expose the update API; debug=timing
        # the query's innards; a second wt the answer's shape.
        assert "echoParams" not in req.url.params
        assert "debug" not in req.url.params
        assert "shards" not in req.url.params
        assert req.url.params.get_list("wt") == ["json"]
        return ok(req)

    r = app_with(handler).get(
        "/search/select",
        params={"q": "x", "echoParams": "all", "debug": "timing", "shards": "s1", "wt": "xml"},
    )
    assert r.status_code == 200


def test_rows_is_capped_and_an_unparsable_one_dropped():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return ok(req)

    c = app_with(handler)
    c.get("/search/select", params={"q": "x", "rows": "100000"})
    assert seen[0]["rows"] == str(_MAX_ROWS)
    c.get("/search/select", params={"q": "x", "rows": "lots"})
    assert "rows" not in seen[1]


def test_the_whitelist_includes_what_the_page_sends():
    """The page's full parameter set (query, filters, paging, highlighting,
    faceting) must round-trip: a name dropped here is a feature silently
    off, with no error anywhere."""
    wanted = {
        "q", "fq", "fl", "rows", "start", "sort",
        "hl", "hl.fl", "hl.snippets", "hl.fragsize",
        "facet", "facet.field", "facet.mincount",
    }

    def handler(req):
        for key in wanted - {"fq"}:
            assert key in req.url.params, f"{key} dropped"
        return ok(req)

    params = [(k, "1") for k in wanted if k != "fq"]
    r = app_with(handler).get("/search/select", params=params)
    assert r.status_code == 200


def test_a_solr_error_status_comes_back_as_is():
    """A 400 (a query Solr's parser refused) is the reader's to see mapped
    by the page, not swallowed into a 502 that says the service is down."""

    def handler(req):
        return httpx.Response(400, json={"error": {"msg": "org.apache.solr.search.SyntaxError"}})

    r = app_with(handler).get("/search/select", params={"q": "content_txt:("})
    assert r.status_code == 400
    assert "SyntaxError" in r.text


def test_an_unreachable_solr_is_a_502():
    def handler(req):
        raise httpx.ConnectError("refused")

    r = app_with(handler).get("/search/select", params={"q": "x"})
    assert r.status_code == 502
    assert r.json() == {"detail": "the search service did not answer"}


def test_post_is_not_a_verb_this_route_has():
    r = app_with(ok).post("/search/select", json={"q": "x"})
    assert r.status_code == 405


def test_the_session_dependency_gates_the_route():
    from fastapi import HTTPException

    def nobody():
        raise HTTPException(status_code=401, detail="not logged in")

    r = app_with(ok, session=nobody).get("/search/select", params={"q": "x"})
    assert r.status_code == 401


# ---- Wired into the app: only when HTRFLOW_SOLR_URL is set -----------------

from types import SimpleNamespace  # noqa: E402

from htrflow_web.app import create_app  # noqa: E402


class FakeReader:
    """The smallest reader create_app accepts: a cfg, and list methods the
    routes this test never calls. Local, like test_app's own — the doubles
    are each module's to shape."""

    cfg: SimpleNamespace

    def __init__(self, solr_url: str = ""):
        self.cfg = SimpleNamespace(
            results_url="https://results.example.org",
            namespaces=("htr-test",),
            solr_url=solr_url,
        )

    def list_jobs(self):
        return []

    def list_warmups(self):
        return []

    def get_job(self, namespace, name):
        return None

    def get_configmap(self, namespace, name):
        return None

    def list_configmaps(self):
        return []

    def list_pipelines(self):
        return []

    def list_pods(self, namespace, job_name):
        return []

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = "htrflow-web"
    ) -> str | None:
        return None


class FakeProgress:
    def fetch(self, results_base, volume_id, state):
        return None

    def cached(self, results_base, volume_id, state):
        return False, None

    def forbidden(self, results_base, volume_id, state):
        return False

    def tally(self, results_base, key):
        from htrflow_web.projection import Tally

        return Tally()

    def for_session(self, session):
        return self


class FakeSessions:
    def __init__(self, users=None):
        self.users = users if users is not None else {"tok": "anna"}

    def check(self, cookie):
        from htrflow_web.login_check import Session

        user = self.users.get(cookie or "")
        return Session(user, cookie) if user else None


def test_no_solr_url_means_no_route():
    c = TestClient(create_app(FakeReader(), progress=FakeProgress()))
    assert c.get("/search/select", params={"q": "x"}).status_code == 404


def test_the_route_answers_through_the_app_behind_the_session():
    reader = FakeReader("http://solr:8983/solr/htr")
    c = TestClient(
        create_app(reader, progress=FakeProgress(), sessions=FakeSessions()), base_url="https://site.example"
    )
    assert c.get("/search/select", params={"q": "x"}).status_code == 401


def test_the_route_with_a_session_reaches_solr(monkeypatch):
    """End to end through create_app: a logged-in reader, a Solr that
    answers, one 200. The transport is swapped, not the cluster."""
    reader = FakeReader("http://solr:8983/solr/htr")

    class SolrTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json=ANSWER)

    real_client = httpx.AsyncClient

    def client_for(*args, **kwargs):
        kwargs["transport"] = SolrTransport()
        return real_client(*args, **kwargs)

    monkeypatch.setattr("htrflow_web.search.httpx.AsyncClient", client_for)
    c = TestClient(
        create_app(reader, progress=FakeProgress(), sessions=FakeSessions()), base_url="https://site.example"
    )
    c.cookies.set("__Host-htr_session", "tok")
    r = c.get("/search/select", params={"q": "content_txt:helsa"})
    assert r.status_code == 200
    assert r.json() == ANSWER
