"""Console progress of `htrflow-campaigns export`: what a person sees while it runs."""

import io

from htrflow_converter.export import Row
from htrflow_converter.export_progress import Progress, duration


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_durations_read_like_a_clock():
    assert duration(0.4) == "0s"
    assert duration(41.6) == "42s"
    assert duration(185) == "3m 05s"
    assert duration(3725) == "1h 02m"


def test_a_log_gets_one_line_per_volume_and_no_page_counter():
    out = io.StringIO()
    p = Progress(out, 30)
    p.start(3, "30004269")
    p.pages("reading page sizes", 120, 193)
    p.finish(Row("30004269", "would export", pages=193), 41.2)
    assert out.getvalue() == "[ 3/30] 30004269  would export  193 pages  (41s)\n"


def test_a_failed_volume_shows_its_reason():
    out = io.StringIO()
    p = Progress(out, 2)
    p.start(1, "V1")
    p.finish(Row("V1", "failed", pages=5, reason="page 0002: timed out"), 3)
    assert out.getvalue() == "[1/2] V1  failed  5 pages  (3s)  page 0002: timed out\n"


def test_a_volume_with_no_pages_read_says_no_count():
    out = io.StringIO()
    p = Progress(out, 1)
    p.start(1, "V1")
    p.finish(Row("V1", "not finished"), 0.1)
    assert out.getvalue() == "[1/1] V1  not finished  (0s)\n"


def test_a_terminal_gets_a_live_counter_cleared_by_the_result():
    out = Tty()
    p = Progress(out, 30)
    p.start(4, "30004270")
    assert out.getvalue() == "\r\x1b[K[ 4/30] 30004270  reading results"
    p.pages("reading page sizes", 120, 229)
    text = out.getvalue()
    assert text.endswith("\r\x1b[K[ 4/30] 30004270  reading page sizes 120/229")
    p.finish(Row("30004270", "would export", pages=229), 50)
    assert (
        out.getvalue()
        == text + "\r\x1b[K[ 4/30] 30004270  would export  229 pages  (50s)\n"
    )


def test_the_end_line_counts_volumes_and_time():
    out = io.StringIO()
    Progress(out, 30).done(185)
    assert out.getvalue() == "30 volumes in 3m 05s\n"
    out = Tty()
    p = Progress(out, 1)
    p.start(1, "V1")
    p.pages("building ALTO", 1, 2)
    p.done(2)  # a run that stopped mid-volume clears the counter first
    assert out.getvalue().endswith("\r\x1b[K1 volume in 2s\n")
