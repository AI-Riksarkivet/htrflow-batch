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


def test_an_alto_put_that_fails_takes_the_page_put_with_it(
    cfg, s3, tmp_path, monkeypatch
):
    """Audit 0923 W-1: the PAGE PUT landed, the ALTO PUT did not, and the
    orphan page/NNNN.xml stayed behind for a page the run then records as not
    done. The half pair goes, and the store's error is what the caller sees."""
    store = ResultStore(cfg)
    real = store.client.put_object

    def put(**kw):
        if kw["Key"].endswith("alto/0001.xml"):
            raise ConnectionError("SlowDown")
        return real(**kw)

    monkeypatch.setattr(store.client, "put_object", put)
    alto = _mk(tmp_path, "alto/0001.xml", "<alto/>")
    page = _mk(tmp_path, "page/0001.xml", "<PcGts/>")
    with pytest.raises(ConnectionError, match="SlowDown"):
        store.upload_page("0001", {"alto": alto, "page": page})
    assert store.stored_pages() == {"page": set(), "alto": set()}


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


def test_the_clients_send_what_an_s3_compatible_store_accepts(cfg, s3, tmp_path):
    """Audit 0923 W-10: botocore's default flexible checksums send PutObject
    as `aws-chunked` with a CRC32 trailer and DeleteObjects with a CRC32 in
    place of the Content-MD5 the S3 API first required -- which several
    S3-compatible stores (HCP, older MinIO and Ceph) refuse. Checksums only
    where an operation requires one, and DeleteObjects carries Content-MD5."""
    store = ResultStore(cfg)
    for client in (store.client, store._log_client):
        c = client.meta.config
        assert c.request_checksum_calculation == "when_required"
        assert c.response_checksum_validation == "when_required"
    sent = {}

    def record(request, **_):
        op = "delete" if "delete" in request.url else "put"
        sent.setdefault(op, dict(request.headers))

    store.client.meta.events.register("before-send", record)
    store._log_client.meta.events.register("before-send", record)
    store.upload_page(
        "0001",
        {
            "alto": _mk(tmp_path, "alto/0001.xml", "<alto/>"),
            "page": _mk(tmp_path, "page/0001.xml", "<PcGts/>"),
        },
    )
    store.delete_pages(["0001"])
    assert "aws-chunked" not in str(sent["put"].get("Content-Encoding", b""))
    assert not any(k.lower().startswith("x-amz-trailer") for k in sent["put"])
    assert "Content-MD5" in sent["delete"]
    assert store.done_pages() == set()


def test_the_log_client_makes_two_attempts_not_three(cfg, s3):
    """Audit 0923 W-7: `max_attempts` counts RETRIES, so the log client's
    `max_attempts: 2` was three attempts -- the SIGTERM budget assumed two."""
    c = ResultStore(cfg)._log_client.meta.config
    assert c.retries == {"mode": "standard", "total_max_attempts": 2}
    assert (c.connect_timeout, c.read_timeout) == (5, 15)


def test_get_json_or_none(cfg, s3):
    store = ResultStore(cfg)
    assert store.get_json_or_none("manifest.json") is None
    store.put_json("manifest.json", {"pages": 1})
    assert store.get_json_or_none("manifest.json") == {"pages": 1}
    store.put_text("manifest.json", "not json", "application/json")
    assert store.get_json_or_none("manifest.json") is None


def test_delete_pages_removes_both_formats(cfg, s3, tmp_path):
    """W3: a page about to be reprocessed must not leave the previous run's
    objects behind -- if this run then FAILS the page, the stale pair would
    make the verify gate count it as accounted for and publish would read the
    stale ALTO into iiif.json while manifest.json says the page failed."""
    store = ResultStore(cfg)
    for name in ("0001", "0002"):
        store.upload_page(
            name,
            {
                "alto": _mk(tmp_path, f"alto/{name}.xml", "<alto/>"),
                "page": _mk(tmp_path, f"page/{name}.xml", "<PcGts/>"),
            },
        )
    store.delete_pages({"0001"})
    assert store.done_pages() == {"0002"}
    assert store.stored_pages() == {"page": {"0002"}, "alto": {"0002"}}


def test_delete_pages_tolerates_a_page_that_is_not_there(cfg, s3):
    """Nothing to delete is the normal case on a first run."""
    ResultStore(cfg).delete_pages({"0007"})


def test_delete_pages_batches_to_the_api_limit(cfg, s3, monkeypatch):
    """RESUME=false clears a whole volume: one DeleteObjects call per 1000
    keys, not one DELETE per object."""
    store = ResultStore(cfg)
    batches: list[int] = []
    real = store.client.delete_objects

    def spy(**kw):
        batches.append(len(kw["Delete"]["Objects"]))
        return real(**kw)

    monkeypatch.setattr(store.client, "delete_objects", spy)
    store.delete_pages({f"{i:04d}" for i in range(1, 502)})
    assert batches == [1000, 2]


def test_delete_pages_fails_loudly_when_a_key_survives(cfg, s3, monkeypatch):
    store = ResultStore(cfg)
    monkeypatch.setattr(
        store.client,
        "delete_objects",
        lambda **kw: {"Errors": [{"Key": "k", "Message": "AccessDenied"}]},
    )
    with pytest.raises(RuntimeError, match="could not delete 1 stale"):
        store.delete_pages({"0001"})


def test_page_sources_reads_back_the_digest_each_alto_was_stamped_with(
    cfg, s3, tmp_path
):
    """3096: what resume compares a page's current source against."""
    store = ResultStore(cfg)
    store.upload_page(
        "0001",
        {
            "alto": _mk(tmp_path, "alto/0001.xml", "<alto/>"),
            "page": _mk(tmp_path, "page/0001.xml", "<PcGts/>"),
        },
        source="abc123",
    )
    # an older wrapper's page: no digest on it
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/alto/0002.xml", Body=b"<a/>"
    )
    assert store.page_sources({"0001", "0002"}) == {"0001": "abc123", "0002": None}
