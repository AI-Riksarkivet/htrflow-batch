# packages/web/tests/test_results_files.py
import boto3
import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from fastapi.testclient import TestClient
from moto import mock_aws

from htrflow_web.results import ClientCache, ResultsConfig, create_results_app
from htrflow_web.session import COOKIE, SessionCodec

KEY = bytes(range(32))


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="htr-results")
        s3.put_object(
            Bucket="htr-results",
            Key="htr-test/demo-v1/R1/iiif.json",
            Body=b'{"id": 1}',
            ContentType="application/json",
        )
        s3.put_object(
            Bucket="htr-results",
            Key="htr-test/demo-v1/R1/x.html",
            Body=b"<script>1</script>",
            ContentType="text/html",
        )
        s3.put_object(
            Bucket="htr-results",
            Key="status/logs/demo-v1/R1.txt",
            Body=b"log line\n",
            ContentType="text/plain; charset=utf-8",
        )
        cfg = ResultsConfig.from_env(
            {
                "HTRFLOW_RESULTS_NAMESPACE": "htr-test",
                "S3_BUCKET": "htr-results",
                "HTRFLOW_KEY_DERIVATION": "none",
            }
        )
        codec = SessionCodec(KEY, hours=8)
        c = TestClient(create_results_app(cfg, codec), base_url="https://testserver")
        c.cookies.set(COOKIE, codec.seal("testing", "testing", "testing"))
        yield c, codec


def _err(code, http=None, headers=None):
    meta = {"HTTPStatusCode": http, "HTTPHeaders": headers or {}} if http else {}
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": meta}, "GetObject")


def test_a_result_file_streams_with_its_headers(setup):
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json")
    assert r.status_code == 200
    assert r.content == b'{"id": 1}'
    assert r.headers["content-type"] == "application/json"
    assert r.headers["content-length"] == "9"
    assert r.headers["etag"]
    assert r.headers["last-modified"]
    assert r.headers["cache-control"] == "private, no-cache"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_head_has_the_headers_and_no_body(setup):
    c, _ = setup
    r = c.head("/results/status/logs/demo-v1/R1.txt")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-length"] == "9"
    assert r.headers["content-type"] == "text/plain; charset=utf-8"


def test_if_none_match_gives_304(setup):
    c, _ = setup
    etag = c.get("/results/htr-test/demo-v1/R1/iiif.json").headers["etag"]
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers["etag"] == etag
    assert r.content == b""


@pytest.mark.parametrize("method", ["get", "head"])
def test_a_store_304_error_is_passed_as_304(setup, monkeypatch, method):
    # botocore raises ClientError code "304" (HTTP 304) for a conditional read.
    err = _err("304", 304, {"etag": '"x"'})

    class Conditional:
        def get_object(self, **kw):
            assert kw["IfNoneMatch"] == '"x"'
            raise err

        head_object = get_object

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Conditional())
    c, _ = setup
    r = getattr(c, method)(
        "/results/htr-test/demo-v1/R1/iiif.json", headers={"If-None-Match": '"x"'}
    )
    assert r.status_code == 304
    assert r.headers["etag"] == '"x"'
    assert r.headers["cache-control"] == "private, no-cache"
    assert r.content == b""


def test_html_is_never_rendered(setup):
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/x.html")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"


def test_missing_is_404_and_foreign_keys_never_reach_the_store(setup, monkeypatch):
    c, _ = setup
    assert c.get("/results/htr-test/demo-v1/R1/nope.json").status_code == 404
    monkeypatch.setattr(ClientCache, "get", lambda *a: pytest.fail("store asked"))
    assert c.get("/results/other-ns/x").status_code == 404
    assert c.get("/results/htr-test/a%2F..%2Fb").status_code == 404


def test_no_session_is_401(setup):
    c, _ = setup
    c.cookies.clear()
    assert c.get("/results/htr-test/demo-v1/R1/iiif.json").status_code == 401


@pytest.mark.parametrize(
    "code,status",
    [
        ("AccessDenied", 403),
        ("InvalidAccessKeyId", 401),
        ("SignatureDoesNotMatch", 401),
        ("InternalError", 502),
    ],
)
def test_store_answers_map_to_statuses(setup, monkeypatch, code, status):
    class Answering:
        def get_object(self, **kw):
            raise _err(code)

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Answering())
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json")
    assert r.status_code == status
    if status == 401:
        assert COOKIE in r.headers["set-cookie"]  # cleared


def test_a_head_answered_with_a_bare_403_is_403(setup, monkeypatch):
    class Bare:
        def head_object(self, **kw):
            raise _err("403")

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Bare())
    c, _ = setup
    r = c.head("/results/htr-test/demo-v1/R1/iiif.json")
    assert r.status_code == 403
    assert COOKIE not in r.headers.get("set-cookie", "")


def test_a_timeout_is_504(setup, monkeypatch):
    class Slow:
        def get_object(self, **kw):
            raise ReadTimeoutError(endpoint_url="https://store")

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Slow())
    c, _ = setup
    assert c.get("/results/htr-test/demo-v1/R1/iiif.json").status_code == 504


def test_the_body_stream_is_closed_after_serving(setup, monkeypatch):
    import datetime

    closed = []

    class Body:
        def iter_chunks(self, n):
            yield b"abc"

        def close(self):
            closed.append(True)

    class Store:
        def get_object(self, **kw):
            return {
                "Body": Body(),
                "ContentLength": 3,
                "ContentType": "text/plain",
                "ETag": '"e"',
                "LastModified": datetime.datetime(
                    2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc
                ),
            }

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Store())
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json")
    assert r.content == b"abc"
    assert r.headers["content-type"] == "text/plain"
    assert r.headers["last-modified"] == "Fri, 02 Jan 2026 03:04:05 GMT"
    assert closed  # closed (idempotent; by the generator and the background task)


def test_other_methods_are_405(setup):
    c, _ = setup
    assert (
        c.put("/results/htr-test/demo-v1/R1/iiif.json", content=b"x").status_code == 405
    )
    assert c.delete("/results/htr-test/demo-v1/R1/iiif.json").status_code == 405
