import pytest

from htrflow_web.results_rules import FILE_HEADERS, allowed_key, served_type

NS = "htrflow-batch"


@pytest.mark.parametrize(
    "raw,key",
    [
        ("htrflow-batch/demo-v1/R1/iiif.json", "htrflow-batch/demo-v1/R1/iiif.json"),
        (
            "htrflow-batch/demo-v1/R%201/alto/1.xml",
            "htrflow-batch/demo-v1/R 1/alto/1.xml",
        ),
        ("status/logs/demo-v1/R1.txt", "status/logs/demo-v1/R1.txt"),
        (
            "htrflow-batch/sources/demo-v1/R1/manifest.json",
            "htrflow-batch/sources/demo-v1/R1/manifest.json",
        ),
    ],
)
def test_result_keys_are_allowed(raw, key):
    assert allowed_key(raw, NS) == key


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "other-ns/demo-v1/R1/iiif.json",
        "htrflow-batch",
        "htrflow-batch/",
        "htrflow-batch//R1/iiif.json",
        "htrflow-batch/./R1",
        "htrflow-batch/../other/x",
        "htrflow-batch/a%2F..%2Fb",
        "htrflow-batch/a%2fb",
        "htrflow-batch/a%5Cb",
        "htrflow-batch/a\\b",
        "status/logsX/a",
        "status/other/a",
        "htrflow-batch/%2e%2e/x",
    ],
)
def test_anything_else_is_refused(raw):
    assert allowed_key(raw, NS) is None


@pytest.mark.parametrize(
    "stored,served",
    [
        ("application/json", ("application/json", False)),
        ("application/xml", ("application/xml", False)),
        ("text/xml; charset=utf-8", ("text/xml; charset=utf-8", False)),
        ("text/plain; charset=utf-8", ("text/plain; charset=utf-8", False)),
        ("text/html", ("application/octet-stream", True)),
        ("image/svg+xml", ("application/octet-stream", True)),
        ("TEXT/HTML", ("application/octet-stream", True)),
        (None, ("application/octet-stream", True)),
        ("", ("application/octet-stream", True)),
    ],
)
def test_only_the_types_the_wrapper_writes_pass_as_stored(stored, served):
    assert served_type(stored) == served


def test_the_fixed_headers():
    assert FILE_HEADERS == {
        "Cache-Control": "private, no-cache",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
