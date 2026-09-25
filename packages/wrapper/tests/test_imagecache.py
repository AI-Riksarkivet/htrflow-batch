import logging

import boto3
import pytest
from moto import mock_aws

from htrflow_batch.iiif import PageRef
from htrflow_batch.imagecache import MAX_PAGE, ImageCache

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
BUCKET = "images-batch"


def _page(i: int) -> PageRef:
    return PageRef(
        index=i, name=f"{i:04d}", image_url=f"https://iiif.example/p{i}", canvas={}
    )


@pytest.fixture
def client():
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket=BUCKET)
        yield c


def _cache(client, **kw) -> ImageCache:
    return ImageCache(
        client, BUCKET, "R0001203", max_bytes=kw.get("max_bytes", 1 << 20), max_pixels=0
    )


def test_the_key_is_ref_slash_ref_underscore_five_digit_page(client):
    assert _cache(client).key(_page(1)) == "R0001203/R0001203_00001.jpg"
    assert _cache(client).key(_page(637)) == "R0001203/R0001203_00637.jpg"
    assert _cache(client).key(_page(MAX_PAGE)) == "R0001203/R0001203_99999.jpg"


def test_a_miss_then_a_store_then_a_hit(client, tmp_path):
    cache, path = _cache(client), tmp_path / "0001.jpg"
    assert cache.get(_page(1), path) is False
    path.write_bytes(JPEG)
    cache.put(_page(1), path)
    body = client.get_object(Bucket=BUCKET, Key="R0001203/R0001203_00001.jpg")[
        "Body"
    ].read()
    assert body == JPEG
    path.unlink()
    assert cache.get(_page(1), path) is True
    assert path.read_bytes() == JPEG
    assert cache.report() == {"bucket": BUCKET, "hits": 1, "misses": 1, "stored": 1}


@pytest.mark.parametrize(
    "junk", [b"<html>login</html>", b"", b"\xff\xd8"], ids=["html", "empty", "short"]
)
def test_a_cached_object_that_is_not_an_image_is_a_miss(client, tmp_path, caplog, junk):
    client.put_object(Bucket=BUCKET, Key="R0001203/R0001203_00002.jpg", Body=junk)
    path = tmp_path / "0002.jpg"
    with caplog.at_level(logging.WARNING):
        assert _cache(client).get(_page(2), path) is False
    assert not path.exists()
    assert "R0001203/R0001203_00002.jpg" in caplog.text


def test_a_cached_object_over_the_byte_cap_is_a_miss(client, tmp_path):
    client.put_object(Bucket=BUCKET, Key="R0001203/R0001203_00003.jpg", Body=JPEG * 100)
    path = tmp_path / "0003.jpg"
    assert _cache(client, max_bytes=100).get(_page(3), path) is False
    assert not path.exists()


def test_a_missing_bucket_is_one_sentence_naming_it_and_every_page_a_miss(
    tmp_path, caplog
):
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        cache = ImageCache(
            c, "no-such-bucket", "R0001203", max_bytes=1 << 20, max_pixels=0
        )
        with caplog.at_level(logging.WARNING):
            for i in range(1, 4):
                assert cache.get(_page(i), tmp_path / f"{i}.jpg") is False
            (tmp_path / "x.jpg").write_bytes(JPEG)
            cache.put(_page(1), tmp_path / "x.jpg")  # never raises
    lines = [r for r in caplog.records if "no-such-bucket" in r.getMessage()]
    assert len(lines) == 1
    assert cache.report()["misses"] == 3 and cache.report()["stored"] == 0


def test_a_second_put_of_the_same_key_overwrites(client, tmp_path):
    cache, path = _cache(client), tmp_path / "p.jpg"
    path.write_bytes(JPEG)
    cache.put(_page(4), path)
    path.write_bytes(JPEG + b"\x01")
    cache.put(_page(4), path)
    body = client.get_object(Bucket=BUCKET, Key="R0001203/R0001203_00004.jpg")[
        "Body"
    ].read()
    assert body == JPEG + b"\x01"


def test_for_volume_is_none_without_a_bucket(client):
    pages = [_page(1)]
    assert (
        ImageCache.for_volume(client, "", "R1", pages, max_bytes=1, max_pixels=0)
        is None
    )


def test_for_volume_is_none_past_the_five_digit_limit_and_says_so_once(client, caplog):
    pages = [_page(1), _page(MAX_PAGE + 1)]
    with caplog.at_level(logging.WARNING):
        got = ImageCache.for_volume(
            client, BUCKET, "R1", pages, max_bytes=1, max_pixels=0
        )
    assert got is None
    assert sum("99999" in r.getMessage() for r in caplog.records) == 1


def test_for_volume_builds_one_otherwise(client):
    got = ImageCache.for_volume(
        client, BUCKET, "R1", [_page(1)], max_bytes=1, max_pixels=0
    )
    assert isinstance(got, ImageCache)
