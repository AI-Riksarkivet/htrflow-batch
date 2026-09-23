import copy

import boto3
import pytest
from moto import mock_aws

from htrflow_batch import driver as driver_mod
from htrflow_batch import main as main_mod
from htrflow_batch import warmup as warmup_mod
from htrflow_batch.config import Config


@pytest.fixture(autouse=True)
def hard_exits(monkeypatch) -> list:
    """``_hard_exit`` is ``os._exit``: every failure exit path goes through it
    (W7), so without this the first test that fails a run would take the test
    process with it. The codes are recorded instead; a test that cares what
    was called asserts on this list."""
    codes: list = []
    for module in (main_mod, warmup_mod):
        monkeypatch.setattr(module, "_hard_exit", codes.append)
    return codes


@pytest.fixture(autouse=True)
def fresh_abandoned_threads(monkeypatch) -> None:
    """``driver._ABANDONED`` is process-wide; one test's released steps must
    not count against another's leak limit."""
    monkeypatch.setattr(driver_mod, "_ABANDONED", [])


def _canvas(i: int, service_id: str) -> dict:
    return {
        "id": f"{service_id}/canvas",
        "type": "Canvas",
        "label": {"none": [f"page {i}"]},
        "width": 3507,
        "height": 4962,
        "items": [
            {
                "type": "AnnotationPage",
                "items": [
                    {
                        "type": "Annotation",
                        "motivation": "painting",
                        "body": {
                            "id": f"{service_id}/full/max/0/default.jpg",
                            "type": "Image",
                            "service": [{"id": service_id, "type": "ImageService3"}],
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_manifest() -> dict:
    base = "https://iiif.example/mock-vol"
    return {
        "id": f"{base}/manifest.json",
        "type": "Manifest",
        "label": {"sv": ["Testvolym"]},
        "items": [_canvas(i, f"{base}/page-{i:05d}") for i in range(1, 4)],
    }


P2_MANIFEST = {
    "@context": "http://iiif.io/api/presentation/2/context.json",
    "@type": "sc:Manifest",
    "label": "P2 vol",
    "sequences": [
        {
            "canvases": [
                {
                    "@id": "http://ex/canvas/1",
                    "label": "f. 1r",
                    "width": 3000,
                    "height": 4000,
                    "images": [
                        {
                            "resource": {
                                "@id": "http://ex/img/full/full/0/default.jpg",
                                "format": "image/jpeg",
                                "service": {
                                    "@id": "http://ex/img",
                                    "profile": "http://iiif.io/api/image/2/level1.json",
                                },
                            }
                        }
                    ],
                }
            ]
        }
    ],
}


@pytest.fixture
def p2_manifest() -> dict:
    """IIIF Presentation 2 manifest (Bodleian-shaped); safe to mutate."""
    return copy.deepcopy(P2_MANIFEST)


REQUIRED_ENV = {
    "VOLUME_REF": "SE-RA-1234",
    "IIIF_MANIFEST_URL": "https://x/manifest",
    "PIPELINE_PATH": "/config/pipeline.yaml",
    "PIPELINE_ID": "demo-v1",
    "S3_ENDPOINT": "",  # empty -> boto3 default endpoint (moto intercepts)
    "S3_BUCKET": "htr-results",
    "PUBLIC_RESULTS_BASE": "http://public/htr-results",
}


@pytest.fixture
def cfg(tmp_path) -> Config:
    env = dict(REQUIRED_ENV, WORKDIR_PATH=str(tmp_path / "work"))
    return Config.from_env(env)


@pytest.fixture
def s3(cfg):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=cfg.s3_bucket)
        yield client


def _gzip_bomb(decoded_mib: int, head: bytes = b"") -> bytes:
    """A gzip body a few hundred KB long that decodes to ``decoded_mib`` MiB,
    built a MiB at a time so the test itself never holds the decoded size."""
    import zlib

    packer = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    parts = [packer.compress(head)]
    zeros = bytes(1 << 20)
    parts += [packer.compress(zeros) for _ in range(decoded_mib)]
    return b"".join(parts) + packer.flush()


def _peak_mib(fn) -> float:
    """What ``fn()`` allocated at its peak, in MiB (Python allocations: the
    decoders' output buffers are among them)."""
    import tracemalloc

    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1] / (1 << 20)
    finally:
        tracemalloc.stop()


@pytest.fixture
def gzip_bomb():
    return _gzip_bomb


@pytest.fixture
def peak_mib():
    return _peak_mib


@pytest.fixture
def drip_server():
    """A real HTTP server that answers every request one byte at a time,
    ``gap`` seconds apart, for ever: in the status line and headers
    (``where="head"``) or in an endless body after them (``where="body"``).
    Each byte resets a per-read timeout, so only a wall-clock deadline ends
    the download. Yields a factory returning the base URL."""
    import socket
    import threading
    import time

    stop = threading.Event()
    sockets: list = []

    def serve(listener, where, gap):
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            sockets.append(conn)
            threading.Thread(target=drip, args=(conn, where, gap), daemon=True).start()

    def drip(conn, where, gap):
        head = b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
        try:
            conn.recv(65536)
            if where == "body":
                # no Content-Length: the body ends when the connection does
                conn.sendall(head + b"Connection: close\r\n\r\n\xff\xd8\xff")
            else:
                conn.sendall(head[:9])
            while not stop.is_set():
                conn.sendall(b"\x00" if where == "body" else b"X")
                time.sleep(gap)
        except OSError:
            pass

    def start(where="body", gap=0.02):
        listener = socket.create_server(("127.0.0.1", 0))
        sockets.append(listener)
        threading.Thread(target=serve, args=(listener, where, gap), daemon=True).start()
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    yield start
    stop.set()
    for s in sockets:
        s.close()
