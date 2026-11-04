import csv
import json
import shutil
from pathlib import Path

import httpx
import pytest

from htrflow_converter import export as export_mod
from htrflow_converter.cli import main
from htrflow_converter.export import Options, Row, run_export, write_report
from htrflow_converter.export_pages import parse_page_set

FIXTURES = Path(__file__).parent / "fixtures"
ALTO = (FIXTURES / "export/alto-2500.xml").read_bytes()
IMAGES = "https://img.example/iiif"
COLUMNS = [
    "volume",
    "outcome",
    "pages",
    "pages_with_text",
    "pages_empty_filled",
    "pages_empty_chosen",
    "quality_mean",
    "quality_pages",
    "reason",
]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "good", root)
    for old in (root / "campaigns").glob("*.yaml"):
        old.unlink()
    (root / "campaigns" / "pub.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - V0000001\n  - V0000002\n"
    )
    return root / "campaigns" / "pub.yaml"


def manifest(vid, quality):
    return {
        "pages": 2,
        "results": {
            "0001": {"status": "ok", "quality": quality},
            "0002": {"status": "ok", "quality": quality},
        },
        "page_sources": {
            f"000{n}": f"{IMAGES}/{vid}_{n:05d}/full/max/0/default.jpg" for n in (1, 2)
        },
    }


class Server:
    def __init__(self, manifests, login=204):
        self.manifests = manifests  # volume -> dict | None | int (status)
        self.login = login
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/_login"):
            return httpx.Response(self.login, json={"detail": "no"})
        if path.endswith("/manifest.json"):
            vid = path.split("/")[-2]
            m = self.manifests[vid]
            if isinstance(m, int):
                return httpx.Response(m, json={"detail": "x"})
            if m is None:
                return httpx.Response(404)
            return httpx.Response(200, content=json.dumps(m).encode())
        if "/alto/" in path:
            return httpx.Response(200, content=ALTO)
        if path.endswith("/info.json"):
            return httpx.Response(200, json={"width": 5000, "height": 3538})
        return httpx.Response(500)


def go(repo, tmp_path, server, **kw):
    target = tmp_path / "out"
    target.mkdir(exist_ok=True)
    client = httpx.Client(transport=httpx.MockTransport(server))
    args = dict(
        results_url=None,
        user="ann",
        password="pw",
        opts=Options(target, 0.75, frozenset({1})),
        report=tmp_path / "r.csv",
        dry_run=False,
        client=client,
    )
    args.update(kw)
    return run_export(repo, target, **args), target


def test_a_missing_target_is_refused_before_any_request(repo, tmp_path, monkeypatch):
    requests = []
    monkeypatch.setattr(
        export_mod.httpx,
        "Client",
        lambda *a, **k: requests.append(1) or httpx.Client(*a, **k),
    )
    code = main(["export", str(repo), "--to", str(tmp_path / "not-mounted")])
    assert code == 2 and requests == []
    assert not (tmp_path / "not-mounted").exists()


@pytest.mark.parametrize(
    "extra",
    [["--min-quality", "1.5"], ["--empty-pages", "x"], ["--min-quality", "-0.1"]],
)
def test_bad_options_are_refused_with_2(repo, tmp_path, extra, capsys):
    (tmp_path / "t").mkdir()
    code = main(["export", str(repo), "--to", str(tmp_path / "t"), *extra])
    assert code == 2 and capsys.readouterr().err.strip()


def test_an_unknown_campaign_file_is_refused(repo, tmp_path):
    other = repo.with_name("nope.yaml")
    other.write_text(repo.read_text())
    other.unlink()
    (tmp_path / "t").mkdir()
    assert main(["export", str(other), "--to", str(tmp_path / "t")]) == 2


def test_an_invalid_repo_is_refused(repo, tmp_path):
    (repo.parent.parent / "pipelines" / "demo-v1.yaml").write_text("nonsense: [")
    (tmp_path / "t").mkdir()
    assert main(["export", str(repo), "--to", str(tmp_path / "t")]) == 2


def test_the_report_has_one_row_per_volume_and_stays_out_of_the_target(repo, tmp_path):
    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": None})
    code, target = go(repo, tmp_path, server)
    assert code == 0
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["volume"] for r in rows] == ["V0000001", "V0000002"]
    assert list(rows[0].keys()) == COLUMNS
    assert rows[0]["outcome"] == "exported" and rows[0]["quality_mean"] == "0.9"
    assert rows[1]["outcome"] == "not finished" and rows[1]["quality_mean"] == ""
    assert not list(target.glob("*.csv"))
    assert sorted(p.name for p in target.iterdir()) == ["V0000001"]


def test_a_report_inside_the_target_is_refused(repo, tmp_path):
    server = Server({})
    code, _ = go(repo, tmp_path, server, report=tmp_path / "out" / "r.csv")
    assert code == 2 and server.requests == []


def test_login_uses_the_url_from_converter_yaml(repo, tmp_path):
    server = Server({"V0000001": None, "V0000002": None})
    go(repo, tmp_path, server)
    assert str(server.requests[0].url) == "https://htr.example.org/results/_login"
    assert server.requests[1].url.path == (
        "/results/htr-test/demo-v1/V0000001/manifest.json"
    )


def test_results_url_overrides_converter_yaml(repo, tmp_path):
    server = Server({"V0000001": None, "V0000002": None})
    go(repo, tmp_path, server, results_url="https://other.example/results")
    assert str(server.requests[0].url) == "https://other.example/results/_login"


def test_exit_status(repo, tmp_path):
    server = Server({"V0000001": manifest("V0000001", 0.1), "V0000002": None})
    code, target = go(repo, tmp_path, server)
    assert code == 0 and list(target.iterdir()) == []  # below threshold + not finished

    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": 403})
    assert go(repo, tmp_path, server)[0] == 0  # forbidden

    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": 500})
    assert go(repo, tmp_path, server)[0] == 1  # failed


def test_session_lost_stops_the_run_with_status_1(repo, tmp_path):
    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": 401})
    code, _ = go(repo, tmp_path, server)
    assert code == 1
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["outcome"] for r in rows] == ["exported", "failed"]
    assert rows[1]["reason"].startswith("run stopped: ")


def test_a_refused_login_fails_every_volume(repo, tmp_path):
    code, _ = go(repo, tmp_path, Server({}, login=401))
    assert code == 1
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["outcome"] for r in rows] == ["failed", "failed"]
    assert rows[0]["reason"].startswith("run stopped: login refused")


def test_an_unusable_target_stops_the_run(repo, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(export_mod, "export_volume", boom)
    code, _ = go(repo, tmp_path, Server({}))
    assert code == 1
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["outcome"] for r in rows] == ["failed", "failed"]
    assert "read-only" in rows[0]["reason"]


def test_the_defaults_are_075_and_page_1(repo, tmp_path, monkeypatch):
    seen = {}

    def fake(campaign_file, target, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr("htrflow_converter.cli.run_export", fake)
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    (tmp_path / "t").mkdir()
    assert main(["export", str(repo), "--to", str(tmp_path / "t")]) == 0
    assert seen["opts"].min_quality == 0.75
    assert seen["opts"].empty_pages == parse_page_set("1") == {1}
    assert seen["user"] == "ann" and seen["report"] == Path("export-pub.csv")
    main(["export", str(repo), "--to", str(tmp_path / "t"), "--no-quality-filter"])
    assert seen["opts"].min_quality is None


def test_the_filter_options_exclude_each_other(repo, tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "export",
                str(repo),
                "--to",
                "x",
                "--no-quality-filter",
                "--min-quality",
                "0.5",
            ]
        )


def test_write_report_rounds_and_prints_a_summary(tmp_path):
    rows = [
        Row("V1", "exported", 2, 1, 1, 0, 0.123456, 1),
        Row("V2", "below threshold", 2, quality_mean=0.5, quality_pages=2),
    ]
    table = write_report(rows, tmp_path / "x.csv")
    assert "0.123" in (tmp_path / "x.csv").read_text()
    lines = table.splitlines()
    assert len(lines) == 4 and "1 exported" in lines[-1]
    assert "1 below threshold" in lines[-1]


def test_an_unreachable_host_stops_the_run_with_a_report(repo, tmp_path):
    inner = Server({})

    def handler(request):
        if request.url.path.endswith("/_login"):
            raise httpx.ConnectError("no route")
        return inner(request)

    code, _ = go(repo, tmp_path, handler)
    assert code == 1
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["outcome"] for r in rows] == ["failed", "failed"]
    assert rows[0]["reason"].startswith("run stopped:")


def test_a_timeout_fails_one_volume_and_the_run_continues(repo, tmp_path):
    inner = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": None})

    def handler(request):
        if request.url.path.endswith("/V0000002/manifest.json"):
            raise httpx.ReadTimeout("slow")
        return inner(request)

    code, _ = go(repo, tmp_path, handler)
    assert code == 1
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["outcome"] for r in rows] == ["exported", "failed"]
