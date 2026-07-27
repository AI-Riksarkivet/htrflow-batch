from pathlib import Path

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
    obj = s3.get_object(Bucket=cfg.s3_bucket,
                        Key="demo-v1/SE-RA-1234/alto/0001.xml")
    assert obj["ContentType"] == "application/xml"
    assert obj["Body"].read() == b"<alto/>"


def test_put_json_content_type(cfg, s3):
    store = ResultStore(cfg)
    store.put_json("manifest.json", {"ok": True})
    obj = s3.get_object(Bucket=cfg.s3_bucket,
                        Key="demo-v1/SE-RA-1234/manifest.json")
    assert obj["ContentType"] == "application/json"


def test_put_text(cfg, s3):
    store = ResultStore(cfg)
    store.put_text("pipeline.yaml", "steps: []", "text/yaml")
    obj = s3.get_object(Bucket=cfg.s3_bucket,
                        Key="demo-v1/SE-RA-1234/pipeline.yaml")
    assert obj["ContentType"] == "text/yaml"


def test_get_bytes(cfg, s3):
    store = ResultStore(cfg)
    s3.put_object(Bucket=cfg.s3_bucket,
                  Key="demo-v1/SE-RA-1234/alto/0001.xml", Body=b"<alto/>")
    assert store.get_bytes("alto/0001.xml") == b"<alto/>"
