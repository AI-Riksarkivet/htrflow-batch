import io
import logging
import sys
import threading

from htrflow_batch.logship import TRUNCATION_MARKER, LogCapture, _Tee


def test_tee_writes_through_and_captures(capsys):
    capture = LogCapture.install()
    try:
        print("to stdout")
        print("to stderr", file=sys.stderr)
    finally:
        capture.finish()
    out = capsys.readouterr()
    assert out.out == "to stdout\n"
    assert out.err == "to stderr\n"
    assert capture.text() == "to stdout\nto stderr\n"
    # streams restored
    assert not hasattr(sys.stdout, "_capture")


def test_logging_handler_bound_after_install_is_captured(capsys):
    capture = LogCapture.install()
    try:
        logger = logging.getLogger("test_logship")
        handler = logging.StreamHandler()  # binds the CURRENT sys.stderr = tee
        logger.addHandler(handler)
        logger.warning("hello from logging")
        logger.removeHandler(handler)
    finally:
        capture.finish()
    assert "hello from logging" in capture.text()
    assert "hello from logging" in capsys.readouterr().err


def test_ship_uploads_only_when_changed():
    uploads: list[str] = []
    capture = LogCapture()
    capture.start_shipping(uploads.append, interval=0)  # manual ship()
    assert capture.ship() is False  # nothing yet
    capture._append("line 1\n")
    assert capture.ship() is True
    assert capture.ship() is False  # unchanged
    capture._append("line 2\n")
    assert capture.ship() is True
    assert uploads == ["line 1\n", "line 1\nline 2\n"]


def test_ship_swallows_upload_errors_and_retries(caplog):
    calls = {"n": 0}

    def flaky(text: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("s3 down")

    capture = LogCapture()
    capture.start_shipping(flaky, interval=0)
    capture._append("x\n")
    with caplog.at_level(logging.WARNING, logger="htrflow_batch.logship"):
        assert capture.ship() is False
    assert "could not ship run log" in caplog.text
    assert capture.ship() is True  # same content retried
    assert calls["n"] == 2


def test_periodic_thread_ships_and_finish_does_final_upload():
    uploads: list[str] = []
    shipped = threading.Event()

    def upload(text: str) -> None:
        uploads.append(text)
        shipped.set()

    capture = LogCapture()
    capture.start_shipping(upload, interval=0.01)
    capture._append("a\n")
    assert shipped.wait(5), "the periodic thread never shipped"
    assert uploads == ["a\n"]
    capture._append("b\n")
    capture.finish()
    assert uploads[-1] == "a\nb\n"
    assert capture._thread is not None and not capture._thread.is_alive()


def test_buffer_is_capped_keeping_head_and_tail():
    capture = LogCapture(cap_bytes=200, head_bytes=50, tail_bytes=100)
    for i in range(60):
        capture._append(f"line {i:03d} xxxxxxxxxxxxxxxxxxxx\n")  # 30 chars each
    text = capture.text()
    assert len(text) <= 200 + len(TRUNCATION_MARKER)
    assert text.startswith("line 000")
    assert TRUNCATION_MARKER in text
    assert text.endswith("line 059 xxxxxxxxxxxxxxxxxxxx\n")
    # cuts land on line boundaries: no partial line right after the marker
    after = text.split(TRUNCATION_MARKER, 1)[1]
    assert after.startswith("line ")


def test_install_rebinds_a_handler_bound_before_it(capsys):
    """basicConfig in an earlier main() call left a StreamHandler on the raw
    stderr; install() must route it through the tee and finish() must put it
    back — without touching handlers on other streams (caplog)."""
    root = logging.getLogger()
    stale = logging.StreamHandler(sys.stderr)
    root.addHandler(stale)
    try:
        capture = LogCapture.install()
        try:
            assert stale.stream is sys.stderr  # now the tee
            logging.getLogger("test_logship").warning("rebound line")
        finally:
            capture.finish()
        assert stale.stream is sys.stderr  # restored to the raw stream
        assert "rebound line" in capture.text()
        assert "rebound line" in capsys.readouterr().err
    finally:
        root.removeHandler(stale)


def test_attach_logging_adds_a_handler_on_the_tee_and_removes_it(capsys):
    root = logging.getLogger()
    before = list(root.handlers)
    capture = LogCapture.install()
    try:
        capture.attach_logging()
        assert len(root.handlers) == len(before) + 1
        logging.getLogger("test_logship").info("attached line")
    finally:
        capture.finish()
    assert root.handlers == before
    assert "INFO attached line" in capture.text()
    assert "INFO attached line" in capsys.readouterr().err


def test_attach_logging_reuses_a_handler_already_on_the_tee():
    root = logging.getLogger()
    before = list(root.handlers)
    capture = LogCapture.install()
    try:
        logging.basicConfig(force=False)  # no-op under pytest (handlers exist)
        own = logging.StreamHandler(sys.stderr)  # bound to the tee
        root.addHandler(own)
        try:
            capture.attach_logging()
            assert len(root.handlers) == len(before) + 1  # nothing added
        finally:
            root.removeHandler(own)
    finally:
        capture.finish()
    assert root.handlers == before


def test_truncation_has_hysteresis():
    """Trim well below the cap so the join is not paid on every write after it."""
    capture = LogCapture(cap_bytes=1000, head_bytes=100, tail_bytes=300)
    calls = {"n": 0}
    orig = capture._truncate_locked

    def counting():
        calls["n"] += 1
        orig()

    capture._truncate_locked = counting  # type: ignore[method-assign]
    for i in range(200):
        capture._append(f"line {i:04d} xxxxxxxxxxxxxxxxxxx\n")  # 30 chars
    # 6000 chars written, ~600 kept per truncation -> a handful of trims, not ~170
    assert calls["n"] <= 12
    assert capture._size <= 1000


def test_start_shipping_ships_immediately():
    uploads: list[str] = []
    capture = LogCapture()
    capture._append("first line\n")
    capture.start_shipping(uploads.append, interval=60)
    try:
        assert uploads == ["first line\n"]
    finally:
        capture.finish()


def test_uploads_are_serialised_and_finish_waits_for_the_slow_one():
    import threading

    order: list[str] = []
    release = threading.Event()

    def slow(text: str) -> None:
        order.append("start:" + text.strip())
        release.wait(2)
        order.append("end:" + text.strip())

    capture = LogCapture()
    capture.start_shipping(slow, interval=0)
    capture._append("a\n")
    t = threading.Thread(target=capture.ship)
    t.start()
    while not order:
        pass
    capture._append("b\n")
    fin = threading.Thread(target=capture.finish)
    fin.start()
    release.set()
    t.join(3)
    fin.join(3)
    assert order == ["start:a", "end:a", "start:a\nb", "end:a\nb"]


def test_warning_resets_after_a_successful_upload(caplog):
    fail = {"on": True}

    def flaky(text: str) -> None:
        if fail["on"]:
            raise RuntimeError("s3 down")

    capture = LogCapture()
    capture.start_shipping(flaky, interval=0)
    with caplog.at_level(logging.WARNING, logger="htrflow_batch.logship"):
        capture._append("1\n")
        capture.ship()
        fail["on"] = False
        capture._append("2\n")
        assert capture.ship() is True
        fail["on"] = True
        capture._append("3\n")
        capture.ship()
    assert caplog.text.count("could not ship run log") == 2


def test_attached_logging_redacts_urls():
    """S6: the shipped run log is world-readable; URLs in log records lose
    userinfo and query strings."""
    import logging

    from htrflow_batch.logship import LogCapture

    capture = LogCapture.install()
    try:
        capture.attach_logging()
        logging.getLogger("x").error(
            "fetch failed: %s", "https://u:p@h/img.jpg?token=SECRET"
        )
    finally:
        capture.finish()
    text = capture.text()
    assert "https://h/img.jpg" in text
    assert "SECRET" not in text and "u:p@" not in text


def test_prints_and_foreign_handlers_are_redacted():
    """S6: RedactingFormatter only covers the handler attach_logging installs.
    A bare print(), and a handler a library installs on the tee itself, both
    reach the buffer through _Tee — so the redaction has to happen there: the
    shipped run log is world-readable."""
    capture = LogCapture.install()
    logger = logging.getLogger("test_logship_foreign")
    handler = logging.StreamHandler()  # no RedactingFormatter, like htrflow's
    try:
        print("see https://u:p@h/img.jpg?token=SECRET now")
        logger.addHandler(handler)
        logger.error("GET https://h/x?token=OTHER")
    finally:
        logger.removeHandler(handler)
        capture.finish()
    text = capture.text()
    assert "SECRET" not in text and "OTHER" not in text and "u:p@" not in text
    assert "https://h/img.jpg" in text and "https://h/x" in text


def test_tee_forwards_buffer_to_the_original_stream():
    """A library writing raw bytes to sys.stdout.buffer used to hit
    AttributeError: io.TextIOBase has no buffer of its own."""
    raw = io.BytesIO()
    original = io.TextIOWrapper(raw, encoding="utf-8")
    tee = _Tee(original, LogCapture())
    tee.buffer.write(b"bytes")
    tee.buffer.flush()
    assert raw.getvalue() == b"bytes"
