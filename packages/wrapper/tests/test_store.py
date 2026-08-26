from pathlib import Path

import pytest

from htrflow_batch.store import ResultStore


def _mk(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_done_pages_empty(cfg, s3):
    store = ResultStore(cfg)
    assert store.done_pages() == set()


def test_upload_page_and_listing(cfg, s3, tmp_path):
    store = ResultStore(cfg)
    alto = _mk(tmp_path, "alto/0001.xml", "<alto/>")
    page = _mk(tmp_path, "page/0001.xml", "<PcGts/>")
    store.upload_page("0001", {"alto": alto, "page": page})
    assert store.done_pages() == {"0001"}
    obj = s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/alto/0001.xml")
    assert obj["ContentType"] == "application/xml"
    assert obj["Body"].read() == b"<alto/>"


def test_put_json_content_type(cfg, s3):
    store = ResultStore(cfg)
    store.put_json("manifest.json", {"ok": True})
    obj = s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")
    assert obj["ContentType"] == "application/json"


def test_put_text(cfg, s3):
    store = ResultStore(cfg)
    store.put_text("pipeline.yaml", "steps: []", "text/yaml")
    obj = s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/pipeline.yaml")
    assert obj["ContentType"] == "text/yaml"


def test_get_bytes(cfg, s3):
    store = ResultStore(cfg)
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/alto/0001.xml", Body=b"<alto/>"
    )
    assert store.get_bytes("alto/0001.xml") == b"<alto/>"


def test_upload_page_puts_page_before_alto(cfg, s3, tmp_path, monkeypatch):
    """W2: ALTO is what done_pages()/the viewer key on, so it must land last —
    a crash between the two uploads then leaves no ALTO without its PAGE."""
    store = ResultStore(cfg)
    order = []
    real = store.client.put_object

    def spy(**kw):
        order.append(kw["Key"].split("/")[-2])
        return real(**kw)

    monkeypatch.setattr(store.client, "put_object", spy)
    alto = _mk(tmp_path, "alto/0001.xml", "<alto/>")
    page = _mk(tmp_path, "page/0001.xml", "<PcGts/>")
    store.upload_page("0001", {"alto": alto, "page": page})
    assert order == ["page", "alto"]


def test_done_pages_requires_both_formats(cfg, s3):
    store = ResultStore(cfg)
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/alto/0001.xml", Body=b"<a/>"
    )
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/page/0002.xml", Body=b"<p/>"
    )
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/alto/0003.xml", Body=b"<a/>"
    )
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/page/0003.xml", Body=b"<p/>"
    )
    assert store.done_pages() == {"0003"}


def test_upload_page_refuses_missing_format(cfg, s3, tmp_path):
    store = ResultStore(cfg)
    alto = _mk(tmp_path, "alto/0001.xml", "<alto/>")
    with pytest.raises(ValueError, match="page"):
        store.upload_page("0001", {"alto": alto})
    assert store.done_pages() == set()


def test_upload_page_rejects_malformed_xml_before_any_put(cfg, s3, tmp_path):
    """W3: an unparseable ALTO used to be uploaded, fail publish, and then be
    accepted as 'done' on the retry. Parse both files first; PUT nothing."""
    store = ResultStore(cfg)
    alto = _mk(tmp_path, "alto/0001.xml", "<alto><Layout></alto>")
    page = _mk(tmp_path, "page/0001.xml", "<PcGts/>")
    with pytest.raises(ValueError, match="not well-formed"):
        store.upload_page("0001", {"alto": alto, "page": page})
    assert s3.list_objects_v2(Bucket=cfg.s3_bucket).get("Contents", []) == []


def test_upload_page_rejects_malformed_page_xml(cfg, s3, tmp_path):
    store = ResultStore(cfg)
    alto = _mk(tmp_path, "alto/0001.xml", "<alto/>")
    page = _mk(tmp_path, "page/0001.xml", "<PcGts><Page></PcGts>")
    with pytest.raises(ValueError, match="page XML is not well-formed"):
        store.upload_page("0001", {"alto": alto, "page": page})


def test_main_client_has_bounded_timeouts_and_retries(cfg, s3):
    """W6: default boto timeouts (60 s connect, legacy retries) turned an S3
    outage into a 6 h zombie; the result client must give up in minutes."""
    store = ResultStore(cfg)
    c = store.client.meta.config
    assert c.connect_timeout == 10
    assert c.read_timeout == 60
    # botocore normalises max_attempts=3 (retries) to 4 total attempts
    assert c.retries == {"mode": "standard", "total_max_attempts": 4}
