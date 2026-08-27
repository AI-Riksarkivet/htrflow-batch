from datetime import datetime, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from htrflow_reconciler.s3 import Bucket, manifest_key


def _client_error(code: str, op: str = "HeadObject") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


@pytest.fixture
def bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="htr-results")
        yield Bucket(client, "htr-results")


def test_done_volumes_requires_manifest(bucket):
    bucket.write_json(manifest_key("demo-v1", "R1"), {"pages": 1})
    bucket.put_text("demo-v1/R2/alto/0001.xml", "<alto/>")  # partial, no manifest
    assert bucket.done_volumes("demo-v1").keys() == {"R1"}


def test_done_volumes_probes_with_head_not_get(bucket):
    """Manifests are large (pipeline YAML + every page); the tick must not
    download one per volume just to learn it exists."""
    bucket.write_json(manifest_key("demo-v1", "R1"), {"pages": 1})

    def no_downloads(**kwargs):
        raise AssertionError("done_volumes must probe with head_object")

    bucket.c.get_object = no_downloads
    assert bucket.done_volumes("demo-v1").keys() == {"R1"}


def test_done_volumes_returns_manifest_mtimes(bucket, monkeypatch):
    bucket.write_json(manifest_key("demo-v1", "R0000001"), {"pages": 1})

    real_head_object = bucket.c.head_object

    def stub_head_object(**kwargs):
        result = real_head_object(**kwargs)
        result["LastModified"] = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
        return result

    monkeypatch.setattr(bucket.c, "head_object", stub_head_object)
    done = bucket.done_volumes("demo-v1")
    assert done == {"R0000001": "2026-08-25T10:00:00Z"}


def test_read_json_missing_returns_none(bucket):
    assert bucket.read_json("nope.json") is None


def test_count_pages(bucket):
    for i in range(3):
        bucket.put_text(f"demo-v1/R1/alto/{i:04d}.xml", "<alto/>")
    assert bucket.count_pages("demo-v1", "R1") == 3
    assert bucket.count_pages("demo-v1", "R2") == 0


def test_bucket_counts_round_trips(bucket):
    """The tick summary reports S3 calls per tick (audit O5)."""
    bucket.write_json("a.json", {})
    bucket.read_json("a.json")
    bucket.exists("a.json")
    assert bucket.calls == 3


def test_done_volumes_heads_run_in_a_thread_pool(bucket, monkeypatch):
    """X1: the per-volume HEADs are the only O(volumes) call left; they run
    concurrently, and a head-per-prefix still yields exactly one call each."""
    import threading

    for i in range(6):
        bucket.write_json(manifest_key("demo-v1", f"R{i}"), {"pages": 1})
    real_head = bucket.c.head_object
    threads = set()

    def head(**kw):
        threads.add(threading.get_ident())
        return real_head(**kw)

    monkeypatch.setattr(bucket.c, "head_object", head)
    assert len(bucket.done_volumes("demo-v1")) == 6
    assert threading.get_ident() not in threads


# -- T7 additions: exists / read_text / read_json / errors through boto ---------


def test_exists_is_a_head_probe(bucket):
    bucket.put_text("status/logs/demo-v1/R1.txt", "line\n")
    assert bucket.exists("status/logs/demo-v1/R1.txt") is True
    assert bucket.exists("status/logs/demo-v1/R2.txt") is False


@pytest.mark.parametrize("code", ["404", "NotFound", "NoSuchKey"])
def test_exists_treats_every_missing_code_as_absent(bucket, monkeypatch, code):
    """S3-compatible stores disagree on the HEAD error code for a missing
    key (RustFS/MinIO vs AWS); all of them mean 'not there'."""
    monkeypatch.setattr(
        bucket.c, "head_object", lambda **kw: (_ for _ in ()).throw(_client_error(code))
    )
    assert bucket.exists("x") is False


def test_exists_reraises_access_denied(bucket, monkeypatch):
    """A 403 is a credentials/policy problem, never 'absent': reading it as
    absent would make the tick re-upload logs and mis-link volumes forever."""
    monkeypatch.setattr(
        bucket.c,
        "head_object",
        lambda **kw: (_ for _ in ()).throw(_client_error("403")),
    )
    with pytest.raises(ClientError):
        bucket.exists("x")


def test_read_text_missing_is_none_and_present_is_decoded(bucket):
    assert bucket.read_text("status/logs/demo-v1/R1.txt") is None
    bucket.put_text("status/logs/demo-v1/R1.txt", "råd\n")
    assert bucket.read_text("status/logs/demo-v1/R1.txt") == "råd\n"
    bucket.c.put_object(Bucket="htr-results", Key="bad.txt", Body=b"bad \xff\n")
    assert bucket.read_text("bad.txt") == "bad �\n"


def test_read_text_reraises_other_errors(bucket, monkeypatch):
    monkeypatch.setattr(
        bucket.c,
        "get_object",
        lambda **kw: (_ for _ in ()).throw(_client_error("403", "GetObject")),
    )
    with pytest.raises(ClientError):
        bucket.read_text("x")


def test_read_json_corrupt_body_raises_value_error(bucket):
    """A truncated upload is a ValueError from the adapter; the tick's
    ``_owned_json`` turns that into a warning and treats the file as absent
    (see test_tick::test_corrupt_owned_json_is_a_warning_not_a_poison_pill).
    The adapter itself must NOT swallow it: for a non-owned file the caller
    has to know."""
    bucket.c.put_object(
        Bucket="htr-results", Key="status/attempts.json", Body=b"{trunc"
    )
    with pytest.raises(ValueError):
        bucket.read_json("status/attempts.json")


def test_read_json_round_trips_with_content_type(bucket):
    bucket.write_json("status/status.json", {"a": [1, 2]})
    head = bucket.c.head_object(Bucket="htr-results", Key="status/status.json")
    assert head["ContentType"] == "application/json"
    assert bucket.read_json("status/status.json") == {"a": [1, 2]}
    bucket.put_text("status/failures/x.txt", "boom")
    head = bucket.c.head_object(Bucket="htr-results", Key="status/failures/x.txt")
    assert head["ContentType"] == "text/plain"


def test_delete_removes_the_key_and_tolerates_absence(bucket):
    bucket.put_text("status/logs/demo-v1/R1.txt", "x")
    bucket.delete("status/logs/demo-v1/R1.txt")
    assert bucket.exists("status/logs/demo-v1/R1.txt") is False
    bucket.delete("status/logs/demo-v1/R1.txt")  # S3 DELETE is idempotent


def test_done_volumes_reraises_access_denied(bucket, monkeypatch):
    bucket.write_json(manifest_key("demo-v1", "R1"), {"pages": 1})
    monkeypatch.setattr(
        bucket.c,
        "head_object",
        lambda **kw: (_ for _ in ()).throw(_client_error("403")),
    )
    with pytest.raises(ClientError):
        bucket.done_volumes("demo-v1")
