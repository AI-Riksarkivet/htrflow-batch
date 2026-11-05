import csv
import json
from pathlib import Path

import httpx
import pytest

from htrflow_converter import export as export_mod
from htrflow_converter.cli import main
from htrflow_converter.export import (
    Options,
    Row,
    Source,
    VolumeListError,
    read_volumes,
    run_export,
    write_report,
)
from htrflow_converter.export_pages import parse_page_set

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://htr.example.org/results"
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
def repo():
    """The source every test reads: two volumes of demo-v1 in htr-test."""
    return Source(URL, "htr-test", "demo-v1", ("V0000001", "V0000002"))


def cli(target, *extra, volumes="V0000001,V0000002"):
    """``export`` arguments for the same source, aimed at ``target``."""
    args = ["export", "--pipeline", "demo-v1", "--namespace", "htr-test"]
    args += ["--results-url", URL, "--to", str(target)]
    if volumes is not None:
        args += ["--volumes", volumes]
    return [*args, *extra]


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
        "source_manifest": f"{IMAGES}/{vid}/manifest",
    }


class Server:
    def __init__(self, manifests, login=204, login_body=None):
        self.manifests = manifests  # volume -> dict | None | int (status)
        self.login = login
        self.login_body = login_body or {"detail": "no"}
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/_login"):
            return httpx.Response(self.login, json=self.login_body)
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
    code = main(cli(str(tmp_path / "not-mounted")))
    assert code == 2 and requests == []
    assert not (tmp_path / "not-mounted").exists()


@pytest.mark.parametrize(
    "extra",
    [["--min-quality", "1.5"], ["--empty-pages", "x"], ["--min-quality", "-0.1"]],
)
def test_bad_options_are_refused_with_2(repo, tmp_path, extra, capsys):
    (tmp_path / "t").mkdir()
    code = main(cli(str(tmp_path / "t"), *extra))
    assert code == 2 and capsys.readouterr().err.strip()


@pytest.mark.parametrize(
    "flag, value",
    [
        ("--namespace", "Htr_Test"),
        ("--namespace", "a.b"),
        ("--pipeline", "../demo"),
        ("--pipeline", "Demo"),
    ],
)
def test_a_bad_namespace_or_pipeline_is_refused(tmp_path, monkeypatch, flag, value):
    _no_prompts_and_no_client(monkeypatch)
    (tmp_path / "t").mkdir()
    args = cli(str(tmp_path / "t"))
    args[args.index(flag) + 1] = value
    assert main(args) == 2


def test_a_results_url_not_ending_in_results_is_refused(tmp_path, monkeypatch, capsys):
    _no_prompts_and_no_client(monkeypatch)
    (tmp_path / "t").mkdir()
    args = cli(str(tmp_path / "t"))
    args[args.index("--results-url") + 1] = "https://htr.example.org/bucket"
    assert main(args) == 2
    assert "/results" in capsys.readouterr().err


def test_no_results_url_is_refused_and_the_env_is_read(tmp_path, monkeypatch):
    _no_prompts_and_no_client(monkeypatch)
    monkeypatch.delenv("HTRFLOW_RESULTS_URL", raising=False)
    (tmp_path / "t").mkdir()
    args = cli(str(tmp_path / "t"))
    i = args.index("--results-url")
    del args[i : i + 2]
    assert main(args) == 2
    seen = {}
    monkeypatch.setattr(
        "htrflow_converter.cli.run_export", lambda src, *a, **k: seen.update(src=src)
    )
    monkeypatch.setenv("HTRFLOW_RESULTS_URL", "https://env.example/results")
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    main(args)
    assert seen["src"].results_url == "https://env.example/results"


@pytest.mark.parametrize("volumes", [None, "", " , "])
def test_no_volumes_is_refused(tmp_path, monkeypatch, capsys, volumes):
    _no_prompts_and_no_client(monkeypatch)
    (tmp_path / "t").mkdir()
    assert main(cli(str(tmp_path / "t"), volumes=volumes)) == 2
    assert "no volumes" in capsys.readouterr().err


def test_read_volumes_joins_the_flag_and_the_file_in_order(tmp_path):
    listing = tmp_path / "vols.txt"
    listing.write_text("# batch 1\nR0000003\n\n  R0000004  # late\n")
    assert read_volumes("R0000001, R0000002", listing) == (
        "R0000001",
        "R0000002",
        "R0000003",
        "R0000004",
    )
    assert read_volumes(None, listing) == ("R0000003", "R0000004")


@pytest.mark.parametrize(
    "listed, why",
    [
        ("R1,R1", "twice"),
        ("R1,../R2", "not a volume id"),
        ("R1,.hidden", "not a volume id"),
        ("R1,a b", "not a volume id"),
    ],
)
def test_read_volumes_refuses_duplicates_and_unsafe_ids(listed, why):
    with pytest.raises(VolumeListError, match=why):
        read_volumes(listed, None)


def test_an_unreadable_volumes_file_is_refused_with_2(tmp_path, monkeypatch, capsys):
    _no_prompts_and_no_client(monkeypatch)
    (tmp_path / "t").mkdir()
    missing = str(tmp_path / "nope.txt")
    code = main(cli(str(tmp_path / "t"), "--volumes-file", missing, volumes=None))
    assert code == 2 and "cannot read" in capsys.readouterr().err


def test_the_volumes_file_alone_is_enough(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "htrflow_converter.cli.run_export", lambda src, *a, **k: seen.update(src=src)
    )
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    (tmp_path / "t").mkdir()
    listing = tmp_path / "vols.txt"
    listing.write_text("V0000002\nV0000001\n")
    main(cli(str(tmp_path / "t"), "--volumes-file", str(listing), volumes=None))
    assert seen["src"] == Source(URL, "htr-test", "demo-v1", ("V0000002", "V0000001"))


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


def test_reads_namespace_pipeline_volume_under_the_results_url(repo, tmp_path):
    server = Server({"V0000001": None, "V0000002": None})
    go(repo, tmp_path, server)
    assert str(server.requests[0].url) == "https://htr.example.org/results/_login"
    assert [r.url.path for r in server.requests[1:]] == [
        "/results/htr-test/demo-v1/V0000001/manifest.json",
        "/results/htr-test/demo-v1/V0000002/manifest.json",
    ]


def test_exit_status(repo, tmp_path):
    server = Server({"V0000001": manifest("V0000001", 0.1), "V0000002": None})
    code, target = go(repo, tmp_path, server)
    assert code == 0 and list(target.iterdir()) == []  # below threshold + not finished

    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": 403})
    assert go(repo, tmp_path, server)[0] == 1  # forbidden

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


def test_a_422_login_leaks_the_password_nowhere(repo, tmp_path, capsys):
    echo = [{"input": "username=ann&password=hunter2-secret"}]
    server = Server({}, login=422, login_body={"detail": echo})
    code, _ = go(repo, tmp_path, server, password="hunter2-secret")
    assert code == 1
    seen = capsys.readouterr()
    for text in ((tmp_path / "r.csv").read_text(), seen.out, seen.err):
        assert "hunter2-secret" not in text


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

    def fake(source, target, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr("htrflow_converter.cli.run_export", fake)
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    (tmp_path / "t").mkdir()
    assert main(cli(str(tmp_path / "t"))) == 0
    assert seen["opts"].min_quality == 0.75
    assert seen["opts"].empty_pages == parse_page_set("1") == {1}
    assert seen["user"] == "ann" and seen["report"] == Path("export-demo-v1.csv")
    main(cli(str(tmp_path / "t"), "--no-quality-filter"))
    assert seen["opts"].min_quality is None


def test_the_filter_options_exclude_each_other(repo, tmp_path):
    with pytest.raises(SystemExit):
        main(cli("x", "--no-quality-filter", "--min-quality", "0.5"))


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


def test_a_dry_run_says_would_export_and_writes_only_the_report(repo, tmp_path, capsys):
    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": None})
    code, target = go(repo, tmp_path, server, dry_run=True)
    assert code == 0 and list(target.iterdir()) == []
    with (tmp_path / "r.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [r["outcome"] for r in rows] == ["would export", "not finished"]
    assert "1 would export" in capsys.readouterr().out


def _no_prompts_and_no_client(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("asked or connected before the refusal")

    monkeypatch.setattr("builtins.input", refuse)
    monkeypatch.setattr("getpass.getpass", refuse)
    monkeypatch.setattr(export_mod.httpx, "Client", refuse)
    monkeypatch.delenv("HTRFLOW_USER", raising=False)
    monkeypatch.delenv("HTRFLOW_PASSWORD", raising=False)


@pytest.mark.parametrize(
    "url",
    ["http://htr.example/results", "ftp://htr.example/results", "http://10.0.0.1/r"],
)
def test_a_results_url_without_tls_is_refused(repo, tmp_path, monkeypatch, capsys, url):
    _no_prompts_and_no_client(monkeypatch)
    (tmp_path / "t").mkdir()
    args = cli(str(tmp_path / "t"))
    args[args.index("--results-url") + 1] = url
    code = main(args)
    err = capsys.readouterr().err
    assert code == 2 and len(err.strip().splitlines()) == 1 and "https" in err


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/results",
        "http://127.0.0.1/results",
        "http://[::1]/results",
    ],
)
def test_plain_http_to_this_machine_is_allowed(repo, tmp_path, monkeypatch, url):
    monkeypatch.setattr("htrflow_converter.cli.run_export", lambda *a, **k: 0)
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    (tmp_path / "t").mkdir()
    args = cli(str(tmp_path / "t"))
    args[args.index("--results-url") + 1] = url
    assert main(args) == 0


def test_no_user_at_the_prompt_is_refused_with_2(repo, tmp_path, monkeypatch, capsys):
    _no_prompts_and_no_client(monkeypatch)

    def eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    (tmp_path / "t").mkdir()
    assert main(cli(str(tmp_path / "t"))) == 2
    assert capsys.readouterr().err.strip() == (
        "no user name: pass --user or set HTRFLOW_USER"
    )


def test_no_password_at_the_prompt_is_refused_with_2(repo, tmp_path, monkeypatch):
    _no_prompts_and_no_client(monkeypatch)

    def eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("getpass.getpass", eof)
    (tmp_path / "t").mkdir()
    assert main(cli(str(tmp_path / "t"), "--user", "a")) == 2


def test_a_report_in_a_missing_folder_is_refused(repo, tmp_path, monkeypatch):
    _no_prompts_and_no_client(monkeypatch)
    (tmp_path / "t").mkdir()
    report = str(tmp_path / "no-such" / "r.csv")
    code = main(cli(str(tmp_path / "t"), "--report", report))
    assert code == 2
    assert not (tmp_path / "no-such").exists()


def test_every_volume_not_finished_points_at_the_config(repo, tmp_path, capsys):
    code, _ = go(repo, tmp_path, Server({"V0000001": None, "V0000002": None}))
    out, err = capsys.readouterr()
    assert code == 0
    assert "every volume read as not finished" in err
    assert all(flag in err for flag in ("--namespace", "--pipeline", "--results-url"))
    assert "report:" in out


def test_one_finished_volume_needs_no_config_note(repo, tmp_path, capsys):
    server = Server({"V0000001": manifest("V0000001", 0.9), "V0000002": None})
    go(repo, tmp_path, server)
    assert "every volume" not in capsys.readouterr().err


def test_the_runs_own_client_follows_no_redirect(repo, tmp_path, monkeypatch):
    real, made = httpx.Client, []

    def client(*a, **k):
        server = Server({"V0000001": None, "V0000002": None})
        made.append(real(*a, transport=httpx.MockTransport(server), **k))
        return made[-1]

    monkeypatch.setattr(export_mod.httpx, "Client", client)
    target = tmp_path / "out"
    target.mkdir()
    code = run_export(
        repo,
        target,
        user="ann",
        password="pw",
        opts=Options(target, 0.75, frozenset({1})),
        report=tmp_path / "r.csv",
        dry_run=False,
    )
    assert code == 0 and len(made) == 1
    assert made[0].follow_redirects is False
