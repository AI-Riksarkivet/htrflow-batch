import json
from pathlib import Path

import httpx
import pytest

from htrflow_converter.export import Options, export_volume, swap_in
from htrflow_converter.export_http import Results, SessionLost

FIXTURE = (Path(__file__).parent / "fixtures/export/alto-2500.xml").read_bytes()
RESULTS = "https://htr.example/results"
IMAGES = "https://img.example/iiif"
PREFIX = "ns/pipe"
VID = "V0000001"


def source(n, vid=VID):
    return f"{IMAGES}/{vid}_{n:05d}/full/max/0/default.jpg"


def manifest(**over):
    m = {
        "pages": 3,
        "results": {
            "0001": {"status": "ok", "quality": 0.9},
            "0002": {"status": "ok", "quality": 0.8},
            "0003": {"status": "failed", "error": "boom"},
        },
        "page_sources": {f"000{n}": source(n) for n in (1, 2, 3)},
    }
    m.update(over)
    return m


class Server:
    def __init__(self, man=None, status=None, alto_404=(), info_status=None):
        self.man = manifest() if man is None else (man or None)
        self.status = status  # status for the manifest request, if set
        self.alto_404 = set(alto_404)
        self.info_status = info_status or {}
        self.altos = []

    def __call__(self, request):
        path = request.url.path
        if path.endswith("/_login"):
            return httpx.Response(204)
        if path.endswith("/manifest.json"):
            if self.status:
                return httpx.Response(self.status, json={"detail": "x"})
            if self.man is None:
                return httpx.Response(404)
            return httpx.Response(200, content=json.dumps(self.man).encode())
        if "/alto/" in path:
            key = path.rsplit("/", 1)[1][:-4]
            self.altos.append(key)
            if key in self.alto_404:
                return httpx.Response(404)
            return httpx.Response(200, content=FIXTURE)
        if path.endswith("/info.json"):
            n = path.split("_")[-1][:5]
            if n in self.info_status:
                return httpx.Response(self.info_status[n])
            return httpx.Response(200, json={"width": 5000, "height": 3538})
        return httpx.Response(500)


def run(server, tmp_path, min_quality=0.75, empty=frozenset({1}), dry=False, vid=VID):
    client = httpx.Client(transport=httpx.MockTransport(server))
    results = Results(RESULTS, "ann", "pw", client)
    results.login()
    return export_volume(
        results, client, PREFIX, vid, Options(tmp_path, min_quality, empty), dry
    )


def test_a_good_volume_is_exported_complete(tmp_path):
    srv = Server()
    row = run(srv, tmp_path)
    assert sorted(p.name for p in (tmp_path / VID).iterdir()) == [
        "V0000001_00001.xml",
        "V0000001_00002.xml",
        "V0000001_00003.xml",
    ]
    assert (
        row.outcome,
        row.pages,
        row.pages_with_text,
        row.pages_empty_chosen,
        row.pages_empty_filled,
    ) == ("exported", 3, 1, 1, 1)
    assert row.quality_mean == pytest.approx(0.8) and row.quality_pages == 1
    assert not list(tmp_path.glob(".*"))
    assert "0003" not in srv.altos and "0001" not in srv.altos
    assert b'WIDTH="5000"' in (tmp_path / VID / "V0000001_00002.xml").read_bytes()


def test_below_threshold_writes_nothing(tmp_path):
    row = run(Server(), tmp_path, min_quality=0.95)
    assert row.outcome == "below threshold"
    assert row.quality_mean == pytest.approx(0.8)
    assert list(tmp_path.iterdir()) == []


def test_no_manifest_is_not_finished(tmp_path):
    row = run(Server(man=False), tmp_path)
    assert row.outcome == "not finished"
    assert list(tmp_path.iterdir()) == []


def test_a_403_is_forbidden(tmp_path):
    assert run(Server(status=403), tmp_path).outcome == "forbidden"


def test_a_403_on_an_alto_is_forbidden(tmp_path):
    srv = Server()
    inner = srv.__call__

    def handler(request):
        if request.url.path.endswith("/alto/0002.xml"):
            return httpx.Response(403)
        return inner(request)

    assert run(handler, tmp_path).outcome == "forbidden"
    assert list(tmp_path.iterdir()) == []


def test_no_scores_with_the_filter_on(tmp_path):
    man = manifest(results={k: {"status": "ok"} for k in ("0001", "0002", "0003")})
    row = run(Server(man=man), tmp_path)
    assert row.outcome == "no scores"
    assert list(tmp_path.iterdir()) == []


def test_no_scores_with_the_filter_off_exports(tmp_path):
    man = manifest(results={k: {"status": "ok"} for k in ("0001", "0002", "0003")})
    row = run(Server(man=man), tmp_path, min_quality=None)
    assert row.outcome == "exported"
    assert row.quality_mean is None


def test_an_unreadable_size_fails_and_keeps_the_old_folder(tmp_path):
    (tmp_path / VID).mkdir()
    (tmp_path / VID / "old.xml").write_text("old")
    row = run(Server(info_status={"00002": 500}), tmp_path)
    assert row.outcome == "failed" and "0002" in row.reason
    assert (tmp_path / VID / "old.xml").read_text() == "old"
    assert not list(tmp_path.glob(".*"))


def test_a_reexport_replaces_the_folder(tmp_path):
    (tmp_path / VID).mkdir()
    (tmp_path / VID / "old.xml").write_text("old")
    assert run(Server(), tmp_path).outcome == "exported"
    assert not (tmp_path / VID / "old.xml").exists()
    assert len(list((tmp_path / VID).iterdir())) == 3
    assert not list(tmp_path.glob(".*"))


def test_an_ok_page_without_its_alto_is_filled(tmp_path):
    row = run(Server(alto_404={"0002"}), tmp_path)
    assert (row.pages_empty_filled, row.pages_with_text) == (2, 0)


def test_a_leftover_temp_folder_is_cleared(tmp_path):
    for name in (".V0000001.tmp-old", ".V0000001.old-x"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f").write_text("x")
    (tmp_path / ".V0000002.tmp-keep").mkdir()
    assert run(Server(), tmp_path).outcome == "exported"
    assert not (tmp_path / ".V0000001.tmp-old").exists()
    assert not (tmp_path / ".V0000001.old-x").exists()
    assert (tmp_path / ".V0000002.tmp-keep").exists()


def test_dry_run_writes_nothing(tmp_path):
    row = run(Server(), tmp_path, dry=True)
    assert (row.outcome, row.pages_with_text, row.pages_empty_filled) == (
        "exported",
        1,
        1,
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("vid", ["", "a/b", "a\\b", ".hidden", ".."])
def test_an_unsafe_volume_id_fails_before_anything(tmp_path, vid):
    row = run(Server(), tmp_path, vid=vid)
    assert row.outcome == "failed"
    assert list(tmp_path.iterdir()) == []


def test_a_foreign_source_image_fails_with_the_page_key(tmp_path):
    man = manifest()
    man["page_sources"]["0002"] = source(2, "V0000009")
    row = run(Server(man=man), tmp_path)
    assert row.outcome == "failed" and "0002" in row.reason


def test_duplicate_numbers_fail(tmp_path):
    man = manifest()
    man["page_sources"]["0002"] = source(1)
    row = run(Server(man=man), tmp_path)
    assert row.outcome == "failed" and "unique" in row.reason
    assert list(tmp_path.iterdir()) == []


def test_a_count_mismatch_fails(tmp_path):
    row = run(Server(man=manifest(pages=4)), tmp_path)
    assert row.outcome == "failed"
    assert list(tmp_path.iterdir()) == []


def test_an_alto_of_the_wrong_shape_fails(tmp_path):
    man = manifest()
    srv = Server(man=man)
    inner = srv.__call__

    def handler(request):
        if request.url.path.endswith("/info.json"):
            return httpx.Response(200, json={"width": 5000, "height": 1000})
        return inner(request)

    row = run(handler, tmp_path)
    assert row.outcome == "failed" and "0002" in row.reason
    assert not list(tmp_path.glob(".*"))


def test_session_lost_propagates(tmp_path):
    def handler(request):
        return httpx.Response(401, json={"detail": "no"})

    with pytest.raises(SessionLost):
        _lost(handler, tmp_path)


def _lost(handler, tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    results = Results(RESULTS, "ann", "pw", client)
    export_volume(
        results, client, PREFIX, VID, Options(tmp_path, 0.75, frozenset()), False
    )


def test_a_malformed_alto_fails_its_volume(tmp_path):
    (tmp_path / VID).mkdir()
    (tmp_path / VID / "old.xml").write_text("old")
    srv = Server()
    inner = srv.__call__

    def handler(request):
        if request.url.path.endswith("/alto/0002.xml"):
            return httpx.Response(200, content=b"<alto")
        return inner(request)

    row = run(handler, tmp_path)
    assert row.outcome == "failed" and "0002" in row.reason
    assert not list(tmp_path.glob(".*"))
    assert (tmp_path / VID / "old.xml").read_text() == "old"


def test_a_failed_swap_puts_the_old_folder_back(tmp_path, monkeypatch):
    final = tmp_path / VID
    final.mkdir()
    (final / "old.xml").write_text("old")
    built = tmp_path / ".built"
    built.mkdir()
    real = Path.rename

    def rename(self, target):
        if self == built:
            raise OSError("disk says no")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="disk says no"):
        swap_in(built, final)
    assert (final / "old.xml").read_text() == "old"
    assert not list(tmp_path.glob(".*old-*"))


def test_a_failed_cleanup_of_the_old_folder_keeps_the_export(tmp_path, monkeypatch):
    (tmp_path / VID).mkdir()
    (tmp_path / VID / "old.xml").write_text("old")

    def rmtree(path, ignore_errors=False, **kw):
        if not ignore_errors:
            raise OSError("cannot delete")

    monkeypatch.setattr("htrflow_converter.export.shutil.rmtree", rmtree)
    assert run(Server(), tmp_path).outcome == "exported"
    assert len(list((tmp_path / VID).glob("*.xml"))) == 3
    assert not (tmp_path / VID / "old.xml").exists()
