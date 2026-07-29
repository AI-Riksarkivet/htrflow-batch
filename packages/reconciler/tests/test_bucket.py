import boto3
import pytest
from moto import mock_aws

from htrflow_reconciler.s3 import Bucket, manifest_key


@pytest.fixture
def bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="htr-results")
        yield Bucket(client, "htr-results")


def test_done_volumes_requires_manifest(bucket):
    bucket.write_json(manifest_key("demo-v1", "R1"), {"pages": 1})
    bucket.put_text("demo-v1/R2/alto/0001.xml", "<alto/>")  # partial, no manifest
    assert bucket.done_volumes("demo-v1") == {"R1"}


def test_done_volumes_probes_with_head_not_get(bucket):
    """Manifests are large (pipeline YAML + every page); the tick must not
    download one per volume just to learn it exists."""
    bucket.write_json(manifest_key("demo-v1", "R1"), {"pages": 1})

    def no_downloads(**kwargs):
        raise AssertionError("done_volumes must probe with head_object")

    bucket.c.get_object = no_downloads
    assert bucket.done_volumes("demo-v1") == {"R1"}


def test_read_json_missing_returns_none(bucket):
    assert bucket.read_json("nope.json") is None


def test_count_pages(bucket):
    for i in range(3):
        bucket.put_text(f"demo-v1/R1/alto/{i:04d}.xml", "<alto/>")
    assert bucket.count_pages("demo-v1", "R1") == 3
    assert bucket.count_pages("demo-v1", "R2") == 0
