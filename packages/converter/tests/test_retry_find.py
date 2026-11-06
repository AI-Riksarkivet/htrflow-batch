import httpx
import pytest

from htrflow_converter.export_http import Forbidden, Results, ResultsUnavailable
from htrflow_converter.retry import (
    Outcome,
    classify_log,
    find_unfinished,
    reason_group,
)

URL = "https://htr.example.org/results"
LOG_T = (
    "2026-11-01 12:00:01 ERROR transient failure in fetch on the last attempt: "
    "[Errno 28] No space left on device: '/work/p/0003.jpg' — no retry follows: "
    "this was the index's last attempt\n"
)
LOG_P = (
    "2026-11-01 12:00:02 ERROR permanent failure in manifest: canvas 4 has no "
    "image — a retry changes nothing — fix the campaign or pipeline file\n"
)


def test_classify_log():
    assert classify_log(None) == (False, "no run log: the volume never started")
    assert classify_log("INFO page 1 done\n") == (
        False,
        "no failure line in the run log",
    )
    assert classify_log(LOG_T) == (
        False,
        "fetch: [Errno 28] No space left on device: '/work/p/0003.jpg'",
    )
    assert classify_log(LOG_P) == (True, "manifest: canvas 4 has no image")
    assert classify_log(LOG_T + LOG_P)[0] is True  # the LAST failure line decides
    assert classify_log(LOG_P + LOG_T)[0] is False


def test_a_traceback_after_the_failure_line_does_not_hide_it():
    text = LOG_P + "Traceback (most recent call last):\n  File x\nOSError: boom\n"
    assert classify_log(text) == (True, "manifest: canvas 4 has no image")


def test_reason_group_folds_quoted_paths_and_caps_length():
    assert (
        reason_group("fetch: [Errno 28] No space left on device: '/work/p/0003.jpg'")
        == "fetch: [Errno 28] No space left on device: '…'"
    )
    assert reason_group("open \"a b\" and 'c'") == "open '…' and '…'"
    assert len(reason_group("x" * 500)) == 100


class Server:
    """Files by path (without the leading slash); a missing one is a 404, an
    int is that status."""

    def __init__(self, files):
        self.files = files
        self.paths = []

    def __call__(self, request):
        path = request.url.path
        if path.endswith("/_login"):
            return httpx.Response(204)
        self.paths.append(path)
        entry = self.files.get(path.removeprefix("/results/"))
        if entry is None:
            return httpx.Response(404)
        if isinstance(entry, int):
            return httpx.Response(entry, json={"detail": "x"})
        return httpx.Response(200, content=entry.encode())


def results_for(files):
    server = Server(files)
    client = httpx.Client(transport=httpx.MockTransport(server))
    results = Results(URL, "ann", "pw", client)
    results.login()
    return results, server


def test_find_unfinished():
    results, _ = results_for(
        {
            "ns/p1/A/manifest.json": "{}",
            "ns/status/logs/p1/B.txt": LOG_T,
            "ns/status/logs/p1/C.txt": LOG_P,
        }
    )
    lines = ["A\timages:u", "B\timages:u", "C\timages:u", "D\timages:u"]
    outs = find_unfinished(results, "ns", "p1", lines)
    assert [(o.id, o.state) for o in outs] == [
        ("A", "done"),
        ("B", "retry"),
        ("C", "permanent"),
        ("D", "retry"),
    ]
    assert outs[1].line == "B\timages:u"
    assert outs[3].reason == "no run log: the volume never started"


def test_a_finished_volume_does_not_fetch_its_log():
    results, server = results_for({"ns/p1/A/manifest.json": "{}"})
    find_unfinished(results, "ns", "p1", ["A\timages:u"], workers=1)
    assert not any("/status/" in p for p in server.paths)


def test_an_unsafe_volume_id_is_retried_not_fetched():
    results, server = results_for({})
    outs = find_unfinished(results, "ns", "p1", ["a/b\timages:u"])
    assert outs == [
        Outcome("a/b", "a/b\timages:u", "retry", "id not readable as a results path")
    ]
    assert server.paths == []


@pytest.mark.parametrize("status, error", [(403, Forbidden), (503, ResultsUnavailable)])
def test_a_403_or_5xx_stops_the_search(status, error):
    results, _ = results_for({"ns/p1/B/manifest.json": status})
    with pytest.raises(error):
        find_unfinished(results, "ns", "p1", ["A\timages:u", "B\timages:u"])


def test_a_403_cancels_the_fetches_still_queued():
    files = {"ns/p1/V0/manifest.json": 403}
    results, server = results_for(files)
    lines = [f"V{i}\timages:u" for i in range(20)]
    with pytest.raises(Forbidden):
        find_unfinished(results, "ns", "p1", lines, workers=1)
    assert len(server.paths) < len(lines)
