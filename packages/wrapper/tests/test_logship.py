import logging
import sys
import time

from htrflow_batch.logship import TRUNCATION_MARKER, LogCapture


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
    capture = LogCapture()
    capture.start_shipping(uploads.append, interval=0.05)
    capture._append("a\n")
    deadline = time.monotonic() + 2
    while not uploads and time.monotonic() < deadline:
        time.sleep(0.01)
    assert uploads == ["a\n"]
    capture._append("b\n")
    capture.finish()
    assert uploads[-1] == "a\nb\n"
    assert capture._thread is not None and not capture._thread.is_alive()


def test_buffer_is_capped_keeping_head_and_tail():
    capture = LogCapture(cap_bytes=200, head_bytes=50)
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
