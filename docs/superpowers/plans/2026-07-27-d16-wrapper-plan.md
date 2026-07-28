# D16 Streaming Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the real `htrflow-batch` wrapper (`batch_run` package): IIIF-manifest in, streaming download → htrflow-as-library → per-page S3 upload, verify gate, `iiif.json` + `manifest.json` publish — then bake it into the cu128 image and smoke it on the k3s GPU.

**Architecture:** Small Python package (`wrapper/src/htrflow_batch/`) with one module per responsibility: env contract (`config`), IIIF manifest parsing (`iiif`), S3 I/O (`store`), async downloader (`fetch`), producer/consumer orchestration (`stream`), htrflow integration (`driver`), viewer manifest (`viewer`), wiring + exit codes (`main`). htrflow itself is only imported inside `driver.py`, so every other module is unit-testable on the host without torch.

**Tech Stack:** Python ≥3.10 (image ships 3.10), httpx (async downloads), boto3 (S3), pytest + moto\[s3\] + httpx.MockTransport for tests. Image: existing `docker/htrflow-batch.dockerfile` base (stock htrflow + torch cu128), in-cluster registry `127.0.0.1:30500`.

## Global Constraints

- **Spec:** `~/htrflow-batch/DESIGN.md` §5.1 (wrapper contract), §5.4 (output contract + D19 `iiif.json`), §2.1/D8 (verify gate), §6 (memory), §13 (test-log conventions).
- **Python 3.10 compatible** — the image venv is 3.10; no 3.11+ syntax (no `Self`, no `tomllib`).
- **No git** — user preference (2026-07-27: "just create a folder for now"). Skip all commit steps; a task ends when its tests are green. Revisit if the user asks for a repo.
- **Exit codes:** `0` success, `13` permanent config error (bad/empty manifest, pipeline load failure), anything else transient. `13` is `EXIT_PERMANENT` in `main.py`; never invent other special codes.
- **S3 key layout (fixed):** `{S3_PREFIX}/{PIPELINE_ID}/{VOLUME_REF}/alto/NNNN.xml`, `.../page/NNNN.xml`, `.../iiif.json`, `.../pipeline.yaml`, `.../manifest.json` (LAST, sole completion marker).
- **Content types:** `.xml → application/xml`, `.json → application/json`, `.yaml → text/yaml`. Never upload without an explicit ContentType.
- **IIIF size syntax:** width cap is `/full/{w},/0/default.jpg` — the `!w,h` form returns 501 on lbiiif (verified 2026-07-27).
- **htrflow imports only inside `driver.py` functions** (and `Export` append) — keeps host tests torch-free.
- Host test env: `cd ~/htrflow-batch/wrapper && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`. Use `.venv/bin/pytest` in all test steps (or `uv venv`/`uv pip` if preferred — same layout).

## File Structure

```
~/htrflow-batch/
├── wrapper/
│   ├── pyproject.toml
│   ├── src/htrflow_batch/
│   │   ├── __init__.py        # empty
│   │   ├── __main__.py        # sys.exit(main())
│   │   ├── config.py          # Config.from_env — the env contract
│   │   ├── iiif.py            # PageRef, fetch_manifest, pages_from_manifest
│   │   ├── store.py           # ResultStore (boto3): done/uploaded/upload/put_json
│   │   ├── fetch.py           # async downloader thread, bounded lookahead
│   │   ├── stream.py          # consumer loop, stall accounting, statuses
│   │   ├── driver.py          # htrflow: load_pipeline, process_page
│   │   ├── viewer.py          # parse_alto_dims, build_viewer_manifest (iiif.json)
│   │   └── main.py            # stages, verify, publish, exit codes, termination log
│   └── tests/
│       ├── conftest.py        # moto S3 fixture, sample manifest fixture
│       ├── test_config.py
│       ├── test_iiif.py
│       ├── test_store.py
│       ├── test_fetch.py
│       ├── test_stream.py
│       ├── test_viewer.py
│       └── test_main.py
├── docker/htrflow-batch.dockerfile   # Modify: add wrapper install + entrypoint
└── k8s/
    ├── pipeline-demo-v1.yaml         # Create: immutable per-version ConfigMap (D17)
    └── job-real-wrapper.yaml         # Create: GPU smoke Job using the wrapper
```

---

### Task 1: Scaffold + `config.py`

**Files:**
- Create: `wrapper/pyproject.toml`, `wrapper/src/htrflow_batch/__init__.py`, `wrapper/src/htrflow_batch/config.py`
- Test: `wrapper/tests/test_config.py`

**Interfaces:**
- Produces: `Config` frozen dataclass with fields `volume_ref, manifest_url, pipeline_path, pipeline_id, s3_endpoint, s3_bucket, s3_prefix, public_results_base, max_image_width:int=2500, resume:bool=True, lookahead_pages:int=64, max_pages:int=0, workdir:str="/work", download_concurrency:int=12`; classmethod `Config.from_env(env: Mapping[str,str]) -> Config` raising `ConfigError(msg)` (subclass of `ValueError`) on missing required keys; property `volume_prefix -> str` = `"{s3_prefix}/{pipeline_id}/{volume_ref}"` with empty `s3_prefix` handled (no leading slash, no `//`).

- [ ] **Step 1: Write `pyproject.toml` and empty package**

```toml
[project]
name = "htrflow-batch-wrapper"
version = "0.1.0"
description = "D16 streaming wrapper: IIIF -> htrflow -> S3 (DESIGN.md §5.1)"
requires-python = ">=3.10"
dependencies = ["httpx>=0.27", "boto3>=1.34"]

[project.optional-dependencies]
dev = ["pytest>=8", "moto[s3]>=5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/htrflow_batch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `wrapper/src/htrflow_batch/__init__.py` (empty file). Then:

Run: `cd ~/htrflow-batch/wrapper && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
Expected: installs cleanly.

- [ ] **Step 2: Write the failing tests**

`wrapper/tests/test_config.py`:

```python
import pytest
from htrflow_batch.config import Config, ConfigError

REQUIRED = {
    "VOLUME_REF": "SE-RA-1234",
    "IIIF_MANIFEST_URL": "https://iiif.example/mock-vol/manifest.json",
    "PIPELINE_PATH": "/config/pipeline.yaml",
    "PIPELINE_ID": "demo-v1",
    "S3_ENDPOINT": "http://rustfs:9000",
    "S3_BUCKET": "htr-results",
    "PUBLIC_RESULTS_BASE": "http://10.16.51.53:30900/htr-results",
}

def test_from_env_defaults():
    cfg = Config.from_env(REQUIRED)
    assert cfg.volume_ref == "SE-RA-1234"
    assert cfg.max_image_width == 2500
    assert cfg.resume is True
    assert cfg.lookahead_pages == 64
    assert cfg.max_pages == 0
    assert cfg.s3_prefix == ""

def test_from_env_overrides():
    env = dict(REQUIRED, MAX_IMAGE_WIDTH="1200", RESUME="false",
               LOOKAHEAD_PAGES="8", MAX_PAGES="4", S3_PREFIX="batch")
    cfg = Config.from_env(env)
    assert cfg.max_image_width == 1200
    assert cfg.resume is False
    assert cfg.lookahead_pages == 8
    assert cfg.max_pages == 4
    assert cfg.volume_prefix == "batch/demo-v1/SE-RA-1234"

def test_volume_prefix_no_prefix():
    cfg = Config.from_env(REQUIRED)
    assert cfg.volume_prefix == "demo-v1/SE-RA-1234"

def test_missing_required_raises():
    env = dict(REQUIRED); del env["VOLUME_REF"]
    with pytest.raises(ConfigError, match="VOLUME_REF"):
        Config.from_env(env)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError` / `ImportError`.

- [ ] **Step 4: Implement `config.py`**

```python
"""Env contract for the wrapper (DESIGN.md §5.1)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class ConfigError(ValueError):
    pass


_REQUIRED = [
    ("volume_ref", "VOLUME_REF"),
    ("manifest_url", "IIIF_MANIFEST_URL"),
    ("pipeline_path", "PIPELINE_PATH"),
    ("pipeline_id", "PIPELINE_ID"),
    ("s3_endpoint", "S3_ENDPOINT"),
    ("s3_bucket", "S3_BUCKET"),
    ("public_results_base", "PUBLIC_RESULTS_BASE"),
]


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    volume_ref: str
    manifest_url: str
    pipeline_path: str
    pipeline_id: str
    s3_endpoint: str
    s3_bucket: str
    public_results_base: str
    s3_prefix: str = ""
    max_image_width: int = 2500
    resume: bool = True
    lookahead_pages: int = 64
    max_pages: int = 0
    workdir: str = "/work"
    download_concurrency: int = 12

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        kwargs = {}
        missing = [k for _, k in _REQUIRED if not env.get(k)]
        if missing:
            raise ConfigError(f"missing required env: {', '.join(missing)}")
        for attr, key in _REQUIRED:
            kwargs[attr] = env[key]
        kwargs["s3_prefix"] = env.get("S3_PREFIX", "").strip("/")
        kwargs["max_image_width"] = int(env.get("MAX_IMAGE_WIDTH", "2500"))
        kwargs["resume"] = _bool(env.get("RESUME", "true"))
        kwargs["lookahead_pages"] = int(env.get("LOOKAHEAD_PAGES", "64"))
        kwargs["max_pages"] = int(env.get("MAX_PAGES", "0"))
        kwargs["workdir"] = env.get("WORKDIR_PATH", "/work")
        kwargs["download_concurrency"] = int(env.get("DOWNLOAD_CONCURRENCY", "12"))
        return cls(**kwargs)

    @property
    def volume_prefix(self) -> str:
        parts = [p for p in (self.s3_prefix, self.pipeline_id, self.volume_ref) if p]
        return "/".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: 4 passed.

---

### Task 2: `iiif.py` — manifest → ordered page list

**Files:**
- Create: `wrapper/src/htrflow_batch/iiif.py`
- Test: `wrapper/tests/test_iiif.py`, fixture in `wrapper/tests/conftest.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `@dataclass PageRef(index: int, name: str, image_url: str, canvas: dict)` (`name` = zero-padded 4-digit, `canvas` = the raw source canvas dict for later `iiif.json` building); `fetch_manifest(url: str, client: httpx.Client) -> dict` (raises `ManifestError` on non-200/non-JSON); `pages_from_manifest(manifest: dict, width: int) -> list[PageRef]` (raises `ManifestError` if no canvases). `ManifestError(Exception)` means **permanent** (exit 13) to `main.py`.

- [ ] **Step 1: Add the sample-manifest fixture to `conftest.py`**

`wrapper/tests/conftest.py` (structure mirrors a real lbiiif P3 manifest — canvas → AnnotationPage → Annotation → body with image service):

```python
import pytest


def _canvas(i: int, service_id: str) -> dict:
    return {
        "id": f"{service_id}/canvas",
        "type": "Canvas",
        "label": {"none": [f"page {i}"]},
        "width": 3507, "height": 4962,
        "items": [{
            "type": "AnnotationPage",
            "items": [{
                "type": "Annotation",
                "motivation": "painting",
                "body": {
                    "id": f"{service_id}/full/max/0/default.jpg",
                    "type": "Image",
                    "service": [{"id": service_id, "type": "ImageService3"}],
                },
            }],
        }],
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
```

- [ ] **Step 2: Write the failing tests**

`wrapper/tests/test_iiif.py`:

```python
import httpx
import pytest
from htrflow_batch.iiif import ManifestError, PageRef, fetch_manifest, pages_from_manifest


def test_pages_from_manifest(sample_manifest):
    pages = pages_from_manifest(sample_manifest, width=2500)
    assert [p.name for p in pages] == ["0001", "0002", "0003"]
    assert pages[0].index == 1
    assert pages[0].image_url == (
        "https://iiif.example/mock-vol/page-00001/full/2500,/0/default.jpg"
    )
    assert pages[0].canvas["type"] == "Canvas"


def test_pages_without_service_falls_back_to_body_id(sample_manifest):
    del sample_manifest["items"][0]["items"][0]["items"][0]["body"]["service"]
    pages = pages_from_manifest(sample_manifest, width=2500)
    # no service -> use the painting body URL as-is (no width control)
    assert pages[0].image_url.endswith("/full/max/0/default.jpg")


def test_empty_manifest_raises():
    with pytest.raises(ManifestError):
        pages_from_manifest({"items": []}, width=2500)


def test_fetch_manifest_ok(sample_manifest):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=sample_manifest))
    client = httpx.Client(transport=transport)
    m = fetch_manifest("https://x/manifest", client)
    assert m["type"] == "Manifest"


def test_fetch_manifest_404_raises():
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    client = httpx.Client(transport=transport)
    with pytest.raises(ManifestError):
        fetch_manifest("https://x/manifest", client)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_iiif.py -q` — Expected: ImportError FAIL.

- [ ] **Step 4: Implement `iiif.py`**

```python
"""IIIF Presentation 3 manifest -> ordered page list (DESIGN.md §5.1 stage 1)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx


class ManifestError(Exception):
    """Permanent: bad/empty/unreachable manifest -> exit 13."""


@dataclass(frozen=True)
class PageRef:
    index: int          # 1-based position in manifest order
    name: str           # zero-padded, e.g. "0001" — S3 key + filename stem
    image_url: str      # width-capped IIIF image request
    canvas: dict        # raw source canvas (for viewer.build_viewer_manifest)


def fetch_manifest(url: str, client: httpx.Client) -> dict:
    try:
        resp = client.get(url, timeout=60, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ManifestError(f"manifest fetch failed: {url}: {e}") from e
    if resp.status_code != 200:
        raise ManifestError(f"manifest fetch failed: {url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise ManifestError(f"manifest is not JSON: {url}") from e


def _image_url(canvas: dict, width: int) -> str | None:
    for ap in canvas.get("items", []):
        for anno in ap.get("items", []):
            body = anno.get("body") or {}
            services = body.get("service") or []
            if services:
                sid = services[0].get("id") or services[0].get("@id")
                if sid:
                    # NOTE: lbiiif rejects "!w,h" (501); "w," is the supported form
                    return f"{sid.rstrip('/')}/full/{width},/0/default.jpg"
            if body.get("id"):
                return body["id"]
    return None


def pages_from_manifest(manifest: dict, width: int) -> list[PageRef]:
    canvases = manifest.get("items") or []
    pages: list[PageRef] = []
    for i, canvas in enumerate(canvases, start=1):
        url = _image_url(canvas, width)
        if url is None:
            raise ManifestError(f"canvas {i} has no image")
        pages.append(PageRef(index=i, name=f"{i:04d}", image_url=url, canvas=canvas))
    if not pages:
        raise ManifestError("manifest has no canvases")
    return pages
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_iiif.py -q` — Expected: 5 passed.

---

### Task 3: `store.py` — S3 result store

**Files:**
- Create: `wrapper/src/htrflow_batch/store.py`
- Test: `wrapper/tests/test_store.py`; add moto fixture to `conftest.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces: `class ResultStore:` — `__init__(self, cfg: Config)` (boto3 client from cfg + env creds); `done_pages(self) -> set[str]` (names under `{volume_prefix}/alto/`); `upload_page(self, name: str, files: dict[str, "Path"]) -> None` (keys `{volume_prefix}/{fmt}/{name}.xml`, ContentType `application/xml`); `uploaded_pages(self) -> set[str]` (same listing as `done_pages`, fresh call); `put_json(self, rel_key: str, obj: dict) -> None`; `put_text(self, rel_key: str, text: str, content_type: str) -> None`. All keys relative to `cfg.volume_prefix`.

- [ ] **Step 1: Add moto fixture to `conftest.py`** (append)

```python
import boto3
from moto import mock_aws

from htrflow_batch.config import Config

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
```

Note: `Config.from_env` requires non-empty values for required keys — `S3_ENDPOINT` of `""` must be allowed. Adjust Task 1's `_REQUIRED` check: `S3_ENDPOINT` moves out of `_REQUIRED` into an optional with default `""` (`store.py` passes `endpoint_url=cfg.s3_endpoint or None`). Update `test_config.py`: remove `S3_ENDPOINT` from the missing-key test expectations if needed.

- [ ] **Step 2: Write the failing tests**

`wrapper/tests/test_store.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_store.py -q` — Expected: ImportError FAIL.

- [ ] **Step 4: Implement `store.py`** (and the Task-1 `S3_ENDPOINT`-optional adjustment)

```python
"""S3 result store: deterministic keys, explicit content types (DESIGN.md §5.4)."""
from __future__ import annotations

import json
from pathlib import Path

import boto3

from .config import Config


class ResultStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bucket = cfg.s3_bucket
        self.prefix = cfg.volume_prefix
        self.client = boto3.client("s3", endpoint_url=cfg.s3_endpoint or None)

    def _key(self, rel: str) -> str:
        return f"{self.prefix}/{rel}"

    def done_pages(self) -> set[str]:
        names: set[str] = set()
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket,
                                       Prefix=self._key("alto/")):
            for obj in page.get("Contents", []):
                stem = obj["Key"].rsplit("/", 1)[-1]
                if stem.endswith(".xml"):
                    names.add(stem[:-4])
        return names

    # fresh listing after the run — the D8 verify gate reads this
    uploaded_pages = done_pages

    def upload_page(self, name: str, files: dict[str, Path]) -> None:
        for fmt, path in files.items():
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(f"{fmt}/{name}.xml"),
                Body=path.read_bytes(),
                ContentType="application/xml",
            )

    def put_json(self, rel_key: str, obj: dict) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(rel_key),
            Body=json.dumps(obj, ensure_ascii=False).encode(),
            ContentType="application/json",
        )

    def put_text(self, rel_key: str, text: str, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(rel_key),
            Body=text.encode(), ContentType=content_type,
        )
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q` — Expected: all green (config tests updated for optional `S3_ENDPOINT`).

---

### Task 4: `fetch.py` — bounded-lookahead async downloader

**Files:**
- Create: `wrapper/src/htrflow_batch/fetch.py`
- Test: `wrapper/tests/test_fetch.py`

**Interfaces:**
- Consumes: `PageRef` (Task 2).
- Produces:
  - `@dataclass FetchResult(page: PageRef, path: "Path|None", error: "str|None", size: int = 0)` — `path=None` ⇔ failed after retries.
  - `run_downloader(pages, dest_dir, out_queue, slots, client, concurrency=12, retries=3, backoff=0.5) -> int` — synchronous function meant to run in a `threading.Thread`; downloads `pages` **in order of submission** with ≤`concurrency` HTTP requests in flight, writes `{dest_dir}/{page.name}.jpg`, puts one `FetchResult` per page on `out_queue` (a `queue.Queue`), acquires `slots` (a `threading.Semaphore` sized `lookahead_pages`) **before** starting each page (the consumer releases one slot per finished page — this is the bounded lookahead), retries each page `retries` times with exponential backoff, and finally puts the sentinel `None`. Returns total bytes fetched.

- [ ] **Step 1: Write the failing tests**

`wrapper/tests/test_fetch.py`:

```python
import queue
import threading
from pathlib import Path

import httpx
from htrflow_batch.fetch import FetchResult, run_downloader
from htrflow_batch.iiif import PageRef


def _pages(n):
    return [PageRef(i, f"{i:04d}", f"https://img/{i}", {}) for i in range(1, n + 1)]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_downloads_all_pages(tmp_path):
    def handler(req):
        return httpx.Response(200, content=b"JPEG" + req.url.path.encode())
    q, slots = queue.Queue(), threading.Semaphore(64)
    total = run_downloader(_pages(3), tmp_path, q, slots, _client(handler))
    results = [q.get() for _ in range(3)]
    assert q.get() is None                      # sentinel
    assert all(isinstance(r, FetchResult) and r.path for r in results)
    assert sorted(p.name for p in tmp_path.iterdir()) == \
        ["0001.jpg", "0002.jpg", "0003.jpg"]
    assert total == sum(r.size for r in results)


def test_failed_page_reports_error_not_exception(tmp_path):
    def handler(req):
        if req.url.path == "/2":
            return httpx.Response(500)
        return httpx.Response(200, content=b"ok")
    q, slots = queue.Queue(), threading.Semaphore(64)
    run_downloader(_pages(3), tmp_path, q, slots, _client(handler),
                   retries=2, backoff=0.0)
    results = {r.page.name: r for r in (q.get(), q.get(), q.get())}
    assert q.get() is None
    assert results["0002"].path is None
    assert "500" in results["0002"].error
    assert results["0001"].path and results["0003"].path


def test_lookahead_blocks(tmp_path):
    """With 1 slot and a consumer that never releases, only 1 page downloads."""
    def handler(req):
        return httpx.Response(200, content=b"ok")
    q, slots = queue.Queue(), threading.Semaphore(1)
    t = threading.Thread(
        target=run_downloader,
        args=(_pages(3), tmp_path, q, slots, _client(handler)),
        daemon=True)
    t.start()
    first = q.get(timeout=5)
    assert first.page.name == "0001"
    t.join(timeout=0.5)
    assert t.is_alive()          # blocked waiting for a slot
    slots.release(); slots.release()
    t.join(timeout=5)
    assert not t.is_alive()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fetch.py -q` — Expected: ImportError FAIL.

- [ ] **Step 3: Implement `fetch.py`**

```python
"""Bounded-lookahead downloader (DESIGN.md §5.1 downloader pool).

Runs in a plain thread; uses a bounded pool of worker threads via httpx sync
client for simplicity and determinism. `slots` (Semaphore(lookahead_pages))
bounds pages-in-flight-or-unconsumed so tmpfs never holds more than the window.
"""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx

from .iiif import PageRef


@dataclass
class FetchResult:
    page: PageRef
    path: Path | None
    error: str | None
    size: int = 0


def _fetch_one(page: PageRef, dest_dir: Path, client: httpx.Client,
               retries: int, backoff: float) -> FetchResult:
    last = "unknown error"
    for attempt in range(retries):
        try:
            resp = client.get(page.image_url, timeout=120, follow_redirects=True)
            if resp.status_code == 200:
                path = dest_dir / f"{page.name}.jpg"
                path.write_bytes(resp.content)
                return FetchResult(page, path, None, len(resp.content))
            last = f"HTTP {resp.status_code}"
        except httpx.HTTPError as e:
            last = str(e)
        time.sleep(backoff * (2 ** attempt))
    return FetchResult(page, None, last)


def run_downloader(pages: list[PageRef], dest_dir: Path, out_queue: queue.Queue,
                   slots: threading.Semaphore, client: httpx.Client,
                   concurrency: int = 12, retries: int = 3,
                   backoff: float = 0.5) -> int:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for page in pages:
            slots.acquire()          # bounded lookahead: consumer releases
            futures.append(pool.submit(
                _fetch_one, page, dest_dir, client, retries, backoff))
        for fut in futures:
            result = fut.result()
            total += result.size
            out_queue.put(result)
    out_queue.put(None)
    return total
```

*(Design note: the spec says "async httpx"; a thread pool over the sync client is behaviorally identical here — ≤N requests in flight — and far easier to test and reason about with the semaphore contract. Results are enqueued in submission order, so the consumer processes pages in page order.)*

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fetch.py -q` — Expected: 3 passed.

---

### Task 5: `stream.py` — consumer loop with stall accounting

**Files:**
- Create: `wrapper/src/htrflow_batch/stream.py`
- Test: `wrapper/tests/test_stream.py`

**Interfaces:**
- Consumes: `FetchResult` (Task 4), `PageRef` (Task 2).
- Produces:
  - `@dataclass PageOutcome(status: str, seconds: float = 0.0, error: "str|None" = None)` — status ∈ `"ok" | "failed" | "skipped"`.
  - `@dataclass StreamStats(results: dict[str, PageOutcome], stall_seconds: float)`.
  - `consume(out_queue, slots, process, upload, keep_images=False) -> StreamStats` — drains the queue until sentinel `None`; for each `FetchResult`: failed fetch → `PageOutcome("failed", error=...)`; else calls `process(path) -> dict[str, Path]` (page outputs by format) then `upload(name, files)`, deletes the image (rolling cleanup, unless `keep_images`), records `PageOutcome("ok", seconds=...)`; a raised exception from `process`/`upload` → `PageOutcome("failed", error=repr(e))`, **loop continues** (drain-what-you-can, §5.1 stage 3); releases one `slots` per page **always**; accumulates queue-wait time into `stall_seconds`.

- [ ] **Step 1: Write the failing tests**

`wrapper/tests/test_stream.py`:

```python
import queue
import threading
import time
from pathlib import Path

from htrflow_batch.fetch import FetchResult
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import consume


def _fr(tmp_path, i, fail=False):
    page = PageRef(i, f"{i:04d}", f"https://img/{i}", {})
    if fail:
        return FetchResult(page, None, "HTTP 500")
    p = tmp_path / f"{i:04d}.jpg"
    p.write_bytes(b"jpg")
    return FetchResult(page, p, None, 3)


def test_ok_flow_uploads_and_deletes(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1)); q.put(None)
    uploaded = {}

    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: uploaded.update({n: f}))
    assert stats.results["0001"].status == "ok"
    assert "0001" in uploaded
    assert not (tmp_path / "0001.jpg").exists()   # rolling cleanup
    assert slots._value == 1                       # released


def test_process_failure_recorded_and_loop_continues(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1)); q.put(_fr(tmp_path, 2)); q.put(None)

    def process(path: Path):
        if path.stem == "0001":
            raise RuntimeError("boom")
        out = tmp_path / f"{path.stem}.alto.xml"; out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: None)
    assert stats.results["0001"].status == "failed"
    assert "boom" in stats.results["0001"].error
    assert stats.results["0002"].status == "ok"


def test_fetch_failure_recorded(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1, fail=True)); q.put(None)
    stats = consume(q, slots, lambda p: {}, lambda n, f: None)
    assert stats.results["0001"].status == "failed"
    assert "500" in stats.results["0001"].error


def test_stall_accounting(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)

    def feed():
        time.sleep(0.3)
        q.put(_fr(tmp_path, 1)); q.put(None)

    threading.Thread(target=feed, daemon=True).start()
    stats = consume(q, slots, lambda p: {}, lambda n, f: None)
    assert stats.stall_seconds >= 0.25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_stream.py -q` — Expected: ImportError FAIL.

- [ ] **Step 3: Implement `stream.py`**

```python
"""Consumer side of the streaming loop (DESIGN.md §5.1 stage 3)."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fetch import FetchResult


@dataclass
class PageOutcome:
    status: str                  # "ok" | "failed" | "skipped"
    seconds: float = 0.0
    error: str | None = None


@dataclass
class StreamStats:
    results: dict[str, PageOutcome] = field(default_factory=dict)
    stall_seconds: float = 0.0


ProcessFn = Callable[[Path], "dict[str, Path]"]
UploadFn = Callable[[str, "dict[str, Path]"], None]


def consume(out_queue: "queue.Queue", slots: threading.Semaphore,
            process: ProcessFn, upload: UploadFn,
            keep_images: bool = False) -> StreamStats:
    stats = StreamStats()
    while True:
        t_wait = time.monotonic()
        item = out_queue.get()
        stats.stall_seconds += time.monotonic() - t_wait
        if item is None:
            return stats
        assert isinstance(item, FetchResult)
        name = item.page.name
        try:
            if item.path is None:
                stats.results[name] = PageOutcome("failed", error=item.error)
                continue
            t0 = time.monotonic()
            files = process(item.path)
            upload(name, files)
            if not keep_images:
                item.path.unlink(missing_ok=True)
            stats.results[name] = PageOutcome("ok", seconds=time.monotonic() - t0)
        except Exception as e:  # drain-what-you-can; verify gate decides later
            stats.results[name] = PageOutcome("failed", error=repr(e))
        finally:
            slots.release()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_stream.py -q` — Expected: 4 passed.

---

### Task 6: `driver.py` — htrflow integration + `viewer.py` ALTO dims

**Files:**
- Create: `wrapper/src/htrflow_batch/driver.py`, `wrapper/src/htrflow_batch/viewer.py` (dims half)
- Test: `wrapper/tests/test_viewer.py` (dims), driver is import-guarded (validated in-image, Task 8/9)

**Interfaces:**
- Consumes: nothing host-testable from htrflow (imports are function-local).
- Produces:
  - `driver.load_pipeline(pipeline_path: str, out_dir: Path) -> object` — `Pipeline.from_config` + appends `Export(out_dir/"alto", "alto")` and `Export(out_dir/"page", "page")` (mirrors upstream `cli.py`'s `--output` behavior; pipeline YAML must contain **model steps only, no Export**).
  - `driver.process_page(pipeline, image_path: Path, out_dir: Path) -> dict[str, Path]` — runs one page, returns `{fmt: written_xml_path}` for the formats found; raises `RuntimeError(f"no outputs for page ...")` if none found (feeds the D8 verify).
  - `driver.htrflow_version() -> str`.
  - `viewer.parse_alto_dims(path: Path) -> tuple[int, int]` — `(WIDTH, HEIGHT)` of the first element carrying both attrs; raises `ValueError` if absent.

- [ ] **Step 1: Write the failing dims test**

`wrapper/tests/test_viewer.py`:

```python
from pathlib import Path

import pytest
from htrflow_batch.viewer import parse_alto_dims

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="2500" HEIGHT="3538" ID="p1"/></Layout>
</alto>"""


def test_parse_alto_dims(tmp_path):
    p = tmp_path / "0001.xml"
    p.write_text(ALTO)
    assert parse_alto_dims(p) == (2500, 3538)


def test_parse_alto_dims_missing(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<alto/>")
    with pytest.raises(ValueError):
        parse_alto_dims(p)
```

- [ ] **Step 2: Run to verify it fails**, then **implement**

`viewer.py` (first half):

```python
"""Viewer-facing outputs: ALTO dims + IIIF P3 manifest (DESIGN.md D19)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def parse_alto_dims(path: Path) -> tuple[int, int]:
    for _, elem in ET.iterparse(str(path)):
        w, h = elem.get("WIDTH"), elem.get("HEIGHT")
        if w is not None and h is not None:
            return int(float(w)), int(float(h))
    raise ValueError(f"no WIDTH/HEIGHT element in {path}")
```

`driver.py`:

```python
"""htrflow integration. ALL htrflow imports live inside functions so the
wrapper package imports cleanly on hosts without torch (DESIGN.md constraint)."""
from __future__ import annotations

from pathlib import Path


def load_pipeline(pipeline_path: str, out_dir: Path):
    from htrflow.pipeline.pipeline import Pipeline
    from htrflow.pipeline.steps import Export

    pipeline = Pipeline.from_config(pipeline_path)
    for step in pipeline.steps:
        if step.__class__.__name__ == "Export":
            raise ValueError(
                "pipeline YAML must not contain Export steps; "
                "the wrapper appends them (DESIGN.md §5.7)")
    pipeline.steps.append(Export(str(out_dir / "alto"), "alto"))
    pipeline.steps.append(Export(str(out_dir / "page"), "page"))
    return pipeline


def process_page(pipeline, image_path: Path, out_dir: Path) -> dict[str, Path]:
    from htrflow.pipeline.steps import auto_import

    for document in auto_import([str(image_path)]):
        pipeline.run(document)
    stem = image_path.stem
    files: dict[str, Path] = {}
    for fmt in ("alto", "page"):
        matches = sorted((out_dir / fmt).glob(f"**/{stem}*.xml")) \
            if (out_dir / fmt).exists() else []
        if matches:
            files[fmt] = matches[0]
    if not files:
        raise RuntimeError(f"no outputs written for page {stem}")
    return files


def htrflow_version() -> str:
    try:
        from importlib.metadata import version
        return version("htrflow")
    except Exception:
        return "unknown"
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest tests/test_viewer.py -q` — Expected: 2 passed. Also `.venv/bin/python -c "import htrflow_batch.driver"` — Expected: imports cleanly without htrflow installed.

---

### Task 7: `viewer.py` — `build_viewer_manifest` (iiif.json, D19)

**Files:**
- Modify: `wrapper/src/htrflow_batch/viewer.py`
- Test: extend `wrapper/tests/test_viewer.py`

**Interfaces:**
- Consumes: `PageRef` (Task 2), `Config` (Task 1), dims from `parse_alto_dims`.
- Produces: `build_viewer_manifest(cfg: Config, source_manifest: dict, pages: list[PageRef], dims: dict[str, tuple[int, int]]) -> dict` — IIIF P3 manifest: id `{public_base}/{volume_prefix}/iiif.json`, label copied from source, one canvas per page **that has dims** (i.e. processed), canvas width/height = capped dims, painting annotation with the source body + service copied through, canvas `seeAlso` = `[{"id": "{public_base}/{volume_prefix}/alto/{name}.xml", "type": "Dataset", "profile": "http://www.loc.gov/standards/alto/ns-v4#", "format": "application/xml+alto", "label": {"none": ["ALTO"]}}]`. Where `public_base = cfg.public_results_base.rstrip('/')` and it already includes the bucket.

- [ ] **Step 1: Write the failing test** (append to `test_viewer.py`)

```python
from htrflow_batch.config import Config
from htrflow_batch.iiif import pages_from_manifest
from htrflow_batch.viewer import build_viewer_manifest


def test_build_viewer_manifest(sample_manifest, cfg):
    pages = pages_from_manifest(sample_manifest, width=2500)
    dims = {"0001": (2500, 3538), "0002": (2500, 3520)}  # page 3 unprocessed
    m = build_viewer_manifest(cfg, sample_manifest, pages, dims)
    assert m["type"] == "Manifest"
    assert m["id"] == "http://public/htr-results/demo-v1/SE-RA-1234/iiif.json"
    assert m["label"] == sample_manifest["label"]
    assert len(m["items"]) == 2                      # only processed pages
    c1 = m["items"][0]
    assert (c1["width"], c1["height"]) == (2500, 3538)   # capped dims (D19)
    body = c1["items"][0]["items"][0]["body"]
    assert body["service"][0]["id"].endswith("page-00001")
    sa = c1["seeAlso"][0]
    assert sa["id"] == \
        "http://public/htr-results/demo-v1/SE-RA-1234/alto/0001.xml"
    assert "alto" in sa["profile"]
```

- [ ] **Step 2: Run to verify it fails**, then **implement** (append to `viewer.py`)

```python
from .config import Config
from .iiif import PageRef


def build_viewer_manifest(cfg: Config, source_manifest: dict,
                          pages: "list[PageRef]",
                          dims: "dict[str, tuple[int, int]]") -> dict:
    base = cfg.public_results_base.rstrip("/")
    vol = f"{base}/{cfg.volume_prefix}"
    canvases = []
    for page in pages:
        if page.name not in dims:
            continue
        w, h = dims[page.name]
        src = page.canvas
        body = {}
        for ap in src.get("items", []):
            for anno in ap.get("items", []):
                if anno.get("body"):
                    body = anno["body"]
        canvas_id = f"{vol}/canvas/{page.name}"
        canvases.append({
            "id": canvas_id,
            "type": "Canvas",
            "label": src.get("label", {"none": [page.name]}),
            "width": w, "height": h,   # capped processing dims (D19 alignment)
            "seeAlso": [{
                "id": f"{vol}/alto/{page.name}.xml",
                "type": "Dataset",
                "profile": "http://www.loc.gov/standards/alto/ns-v4#",
                "format": "application/xml+alto",
                "label": {"none": ["ALTO"]},
            }],
            "items": [{
                "id": f"{canvas_id}/ap",
                "type": "AnnotationPage",
                "items": [{
                    "id": f"{canvas_id}/anno",
                    "type": "Annotation",
                    "motivation": "painting",
                    "target": canvas_id,
                    "body": body,
                }],
            }],
        })
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": f"{vol}/iiif.json",
        "type": "Manifest",
        "label": source_manifest.get("label", {"none": [cfg.volume_ref]}),
        "items": canvases,
    }
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest tests/test_viewer.py -q` — Expected: 3 passed.

---

### Task 8: `main.py` + `__main__.py` — wiring, verify gate, publish order, exit codes

**Files:**
- Create: `wrapper/src/htrflow_batch/main.py`, `wrapper/src/htrflow_batch/__main__.py`
- Test: `wrapper/tests/test_main.py`

**Interfaces:**
- Consumes: everything above, exact names as defined.
- Produces: `main(env=os.environ, process_page_factory=None) -> int`. `process_page_factory(cfg) -> ProcessFn` seam: default builds the real htrflow driver (`load_pipeline` once, closure calling `driver.process_page`); tests inject a fake. Constants `EXIT_OK = 0`, `EXIT_PERMANENT = 13`, `EXIT_TRANSIENT = 1`. Termination reason JSON written to `env["TERMINATION_LOG_PATH"]` or `/dev/termination-log`.

- [ ] **Step 1: Write the failing tests**

`wrapper/tests/test_main.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
from htrflow_batch import main as main_mod
from htrflow_batch.main import EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, main


@pytest.fixture
def env(tmp_path, cfg, sample_manifest, monkeypatch):
    """Full env + mocked HTTP (manifest + images) + moto S3 via cfg/s3 fixtures."""
    def handler(req):
        if req.url.path.endswith("/manifest"):
            return httpx.Response(200, json=sample_manifest)
        return httpx.Response(200, content=b"JPEGDATA")
    monkeypatch.setattr(main_mod, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("steps: []\n")
    return {
        "VOLUME_REF": "SE-RA-1234",
        "IIIF_MANIFEST_URL": "https://iiif.example/mock-vol/manifest.json",
        "PIPELINE_PATH": str(pipeline),
        "PIPELINE_ID": "demo-v1",
        "S3_ENDPOINT": "",
        "S3_BUCKET": "htr-results",
        "PUBLIC_RESULTS_BASE": "http://public/htr-results",
        "WORKDIR_PATH": str(tmp_path / "work"),
        "TERMINATION_LOG_PATH": str(tmp_path / "term.log"),
    }


def fake_factory(cfg):
    """Writes a plausible ALTO per page."""
    def process(path: Path):
        out = Path(cfg.workdir) / "outputs" / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/>'
                       "</Layout></alto>")
        return {"alto": out}
    return process


def _keys(s3, cfg):
    resp = s3.list_objects_v2(Bucket=cfg.s3_bucket)
    return sorted(o["Key"] for o in resp.get("Contents", []))


def test_happy_path(env, cfg, s3):
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys
    assert "demo-v1/SE-RA-1234/iiif.json" in keys
    assert "demo-v1/SE-RA-1234/pipeline.yaml" in keys
    assert "demo-v1/SE-RA-1234/manifest.json" in keys
    body = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read())
    assert body["pages"] == 3
    assert body["results"]["0001"]["status"] == "ok"
    assert "gpu_stall_seconds" in body and "wall_seconds" in body
    assert body["viewer_url"].endswith("iiif.json")


def test_resume_skips_done(env, cfg, s3):
    s3.put_object(Bucket=cfg.s3_bucket,
                  Key="demo-v1/SE-RA-1234/alto/0001.xml", Body=b"<alto/>")
    calls = []

    def factory(c):
        inner = fake_factory(c)
        def process(path):
            calls.append(path.stem)
            return inner(path)
        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    assert "0001" not in calls and calls == ["0002", "0003"]
    body = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read())
    assert body["results"]["0001"]["status"] == "skipped"


def test_bad_manifest_is_permanent(env, cfg, s3, monkeypatch):
    def handler(req):
        return httpx.Response(404)
    monkeypatch.setattr(main_mod, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "setup"


def test_page_failure_is_transient_and_blocks_completion(env, cfg, s3):
    def factory(c):
        inner = fake_factory(c)
        def process(path):
            if path.stem == "0002":
                raise RuntimeError("cuda hiccup")
            return inner(path)
        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_TRANSIENT
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/manifest.json" not in keys   # no false complete
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys       # partials kept
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify" and "0002" in str(term)


def test_max_pages_caps(env, cfg, s3):
    env = dict(env, MAX_PAGES="2")
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    body = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read())
    assert body["pages"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -q` — Expected: ImportError FAIL.

- [ ] **Step 3: Implement `main.py` and `__main__.py`**

`main.py`:

```python
"""Stage wiring: setup -> resume -> stream -> verify -> publish (DESIGN.md §5.1)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Mapping, Optional

import httpx

from .config import Config, ConfigError
from .fetch import run_downloader
from .iiif import ManifestError, fetch_manifest, pages_from_manifest
from .store import ResultStore
from .stream import PageOutcome, consume
from .viewer import build_viewer_manifest, parse_alto_dims

log = logging.getLogger("htrflow_batch")

EXIT_OK = 0
EXIT_PERMANENT = 13
EXIT_TRANSIENT = 1


class SetupError(Exception):
    """Permanent config/setup failure -> EXIT_PERMANENT."""


def _http_client() -> httpx.Client:
    return httpx.Client()


def _terminate(env: Mapping[str, str], reason: dict) -> None:
    path = env.get("TERMINATION_LOG_PATH", "/dev/termination-log")
    try:
        Path(path).write_text(json.dumps(reason)[:4096])
    except OSError:
        log.warning("could not write termination log to %s", path)


def _default_factory(cfg: Config):
    from . import driver  # htrflow imports stay function-local

    out_dir = Path(cfg.workdir) / "outputs"
    pipeline = driver.load_pipeline(cfg.pipeline_path, out_dir)

    def process(image_path: Path):
        return driver.process_page(pipeline, image_path, out_dir)

    return process


def main(env: Optional[Mapping[str, str]] = None,
         process_page_factory: Optional[Callable] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    env = dict(env if env is not None else os.environ)
    t_start = time.monotonic()
    stage = "setup"
    try:
        cfg = Config.from_env(env)
        store = ResultStore(cfg)
        workdir = Path(cfg.workdir)
        input_dir = workdir / "input"
        client = _http_client()

        # -- stage 1: setup -------------------------------------------------
        source_manifest = fetch_manifest(cfg.manifest_url, client)
        pages = pages_from_manifest(source_manifest, cfg.max_image_width)
        if cfg.max_pages:
            pages = pages[: cfg.max_pages]
        log.info("[%s] %d pages in manifest", cfg.volume_ref, len(pages))

        # factory AFTER downloader start would be ideal (overlap, §5.6) but
        # correctness first: start downloads, then load models concurrently.
        # -- stage 2: resume -------------------------------------------------
        done = store.done_pages() if cfg.resume else set()
        todo = [p for p in pages if p.name not in done]
        log.info("[%s] resume: %d done, %d to process",
                 cfg.volume_ref, len(done), len(todo))

        # -- stage 3: streaming loop ------------------------------------------
        stage = "stream"
        out_q: queue.Queue = queue.Queue()
        slots = threading.Semaphore(cfg.lookahead_pages)
        bytes_box = {}

        def dl():
            bytes_box["n"] = run_downloader(
                todo, input_dir, out_q, slots, client,
                concurrency=cfg.download_concurrency)

        dl_thread = threading.Thread(target=dl, daemon=True, name="downloader")
        dl_thread.start()

        # model load overlaps first downloads (DESIGN.md §5.6)
        factory = process_page_factory or _default_factory
        process = factory(cfg)

        stats = consume(out_q, slots, process, store.upload_page)
        dl_thread.join()
        for p in pages:
            if p.name in done:
                stats.results[p.name] = PageOutcome("skipped")

        # -- stage 4: verify (D8) ---------------------------------------------
        stage = "verify"
        uploaded = store.uploaded_pages()
        expected = {p.name for p in pages}
        missing = sorted(expected - uploaded)
        failed = sorted(n for n, r in stats.results.items()
                        if r.status == "failed")
        if missing or failed:
            raise RuntimeError(
                f"verify failed: missing={missing} failed={failed}")

        # -- stage 5: publish (iiif.json, pipeline.yaml, manifest.json LAST) --
        stage = "publish"
        dims = {}
        out_dir = Path(cfg.workdir) / "outputs"
        for p in pages:
            alto = sorted((out_dir / "alto").glob(f"**/{p.name}*.xml")) \
                if (out_dir / "alto").exists() else []
            if alto:
                try:
                    dims[p.name] = parse_alto_dims(alto[0])
                except ValueError:
                    pass
        if dims:
            store.put_json("iiif.json", build_viewer_manifest(
                cfg, source_manifest, pages, dims))
        pipeline_text = Path(cfg.pipeline_path).read_text()
        store.put_text("pipeline.yaml", pipeline_text, "text/yaml")

        wall = time.monotonic() - t_start
        ok_pages = [n for n, r in stats.results.items() if r.status == "ok"]
        viewer_url = (f"{cfg.public_results_base.rstrip('/')}/"
                      f"{cfg.volume_prefix}/iiif.json")
        store.put_json("manifest.json", {
            "volume": cfg.volume_ref,
            "pipeline_id": cfg.pipeline_id,
            "pipeline_sha256": hashlib.sha256(pipeline_text.encode()).hexdigest(),
            "pipeline_yaml": pipeline_text,
            "htrflow_version": _htrflow_version(),
            "image_digest": env.get("IMAGE_DIGEST", "unknown"),
            "pages": len(pages),
            "results": {n: {"status": r.status, "seconds": round(r.seconds, 2),
                            **({"error": r.error} if r.error else {})}
                        for n, r in sorted(stats.results.items())},
            "source_manifest": cfg.manifest_url,
            "max_image_width": cfg.max_image_width,
            "bytes_fetched": bytes_box.get("n", 0),
            "wall_seconds": round(wall, 1),
            "gpu_stall_seconds": round(stats.stall_seconds, 1),
            "pages_per_second": round(len(ok_pages) / wall, 3) if wall else 0,
            "viewer_url": viewer_url,
        })
        log.info("[%s] COMPLETE %d pages (%d processed) in %.1fs, viewer: %s",
                 cfg.volume_ref, len(pages), len(ok_pages), wall, viewer_url)
        shutil.rmtree(workdir, ignore_errors=True)
        return EXIT_OK

    except (ConfigError, ManifestError, SetupError, ValueError) as e:
        log.error("permanent failure in %s: %s", stage, e)
        _terminate(env, {"stage": stage, "permanent": True, "error": str(e)})
        return EXIT_PERMANENT
    except Exception as e:
        log.error("transient failure in %s: %s\n%s", stage, e,
                  traceback.format_exc())
        _terminate(env, {"stage": stage, "permanent": False, "error": str(e)})
        return EXIT_TRANSIENT


def _htrflow_version() -> str:
    try:
        from .driver import htrflow_version
        return htrflow_version()
    except Exception:
        return "unknown"
```

`__main__.py`:

```python
import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass. If `test_bad_manifest_is_permanent` sees stage `"setup"` mismatch, check that `ManifestError` is raised before `stage` changes.

*(Watch: `ValueError` in the permanent tuple also catches `ConfigError` (subclass) — intentional; a `ValueError` escaping from a page `process()` is already converted to a `PageOutcome` inside `consume`, so it cannot falsely reach the permanent branch.)*

---

### Task 9: Image build, registry push, in-image checks

**Files:**
- Modify: `docker/htrflow-batch.dockerfile`

**Interfaces:**
- Consumes: `wrapper/` package (Tasks 1–8).
- Produces: image `127.0.0.1:30500/htrflow-batch:v1` runnable as `python -m htrflow_batch`.

- [ ] **Step 1: Extend the Dockerfile**

```dockerfile
# htrflow-batch: stock upstream image + torch with Blackwell (sm_120) kernels
# + the D16 streaming wrapper (DESIGN.md §5.1).
FROM airiksarkivet/htrflow:v0.2.6-35f48a7

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# swap torch/torchvision for cu128 builds (Blackwell sm_120 kernels)
RUN uv pip install --python /app/.venv/bin/python --no-cache \
      --index-url https://download.pytorch.org/whl/cu128 \
      --upgrade torch torchvision

COPY wrapper /opt/wrapper
RUN uv pip install --python /app/.venv/bin/python --no-cache /opt/wrapper

ENTRYPOINT ["python", "-m", "htrflow_batch"]
```

Build context must include `wrapper/`, so build from the project root:

Run: `cd ~/htrflow-batch && docker build -t htrflow-batch:v1 -f docker/htrflow-batch.dockerfile .`
Expected: builds; cu128 layer comes from cache.

- [ ] **Step 2: In-image checks (§9 test 0 — library-API pin canary)**

Run:
```bash
docker run --rm --entrypoint python htrflow-batch:v1 -c "
import htrflow_batch.main, htrflow_batch.driver
from htrflow.pipeline.pipeline import Pipeline
from htrflow.pipeline.steps import Export, auto_import
import torch; print('torch', torch.__version__)
print('pin-check OK')"
```
Expected: `pin-check OK`. This is the canary that a base-image bump broke the D16 driver (fallback ladder L1/L2 per DESIGN.md §5.1 if it ever fails).

- [ ] **Step 3: Missing-env behavior check**

Run: `docker run --rm -e TERMINATION_LOG_PATH=/tmp/t htrflow-batch:v1; echo "exit=$?"`
Expected: `exit=13` (ConfigError → permanent).

- [ ] **Step 4: Push to the in-cluster registry**

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl -n registry port-forward svc/registry 30500:5000 & sleep 2
docker tag htrflow-batch:v1 127.0.0.1:30500/htrflow-batch:v1
docker push 127.0.0.1:30500/htrflow-batch:v1
kill %1
```
Expected: push succeeds (only new layers transfer).

---

### Task 10: Cluster GPU smoke with MOCKED IIIF (htr_demo fixture images)

No live lbiiif dependency: a `htr-fixtures` bucket in RustFS serves both a
hand-built IIIF P3 manifest and the Riksarkivet htr_demo example images.
Image URLs use the **NodePort public base** (`http://10.16.51.53:30900/...`)
so the same URLs work from inside pods AND from a browser viewing the
resulting `iiif.json`. The mock manifest has **no image service** (plain
`body.id` URLs), which deliberately exercises `iiif.py`'s fallback path;
the width-cap/service path is covered by unit tests (Task 2).

**Files:**
- Create: `k8s/pipeline-demo-v1.yaml` (immutable pipeline ConfigMap, D17), `k8s/job-real-wrapper.yaml`, `scripts/make_mock_manifest.py`
- Modify: `~/htrflow-batch/DESIGN.md` §13 (append results), `k8s/README.md` (one line per new file)

**Interfaces:**
- Consumes: image `127.0.0.1:30500/htrflow-batch:v1`, Kueue queue `htr-batch`, RustFS (buckets `htr-results` + new `htr-fixtures`, anonymous-read), PVC `htr-test-data` (HF model cache from earlier tests).

- [ ] **Step 0: Build the IIIF fixture set in RustFS**

Download 4 htr_demo images, generate the mock manifest, create the bucket
(+ anonymous read), upload. `scripts/make_mock_manifest.py`:

```python
"""Generate a minimal IIIF P3 manifest over the htr_demo fixture images.

Canvas width/height are placeholders (the wrapper never reads them; real
dims come from the ALTO at publish time per D19)."""
import json
import sys

BASE = "http://10.16.51.53:30900/htr-fixtures/mock-vol"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 4

manifest = {
    "@context": "http://iiif.io/api/presentation/3/context.json",
    "id": f"{BASE}/manifest.json",
    "type": "Manifest",
    "label": {"sv": ["Mock-volym (htr_demo exempelbilder)"]},
    "items": [],
}
for i in range(1, N + 1):
    cid = f"{BASE}/canvas/{i:04d}"
    manifest["items"].append({
        "id": cid, "type": "Canvas",
        "label": {"none": [f"sida {i}"]},
        "width": 2000, "height": 3000,  # placeholder, unused by wrapper
        "items": [{
            "id": f"{cid}/ap", "type": "AnnotationPage",
            "items": [{
                "id": f"{cid}/anno", "type": "Annotation",
                "motivation": "painting", "target": cid,
                "body": {"id": f"{BASE}/{i:04d}.jpg", "type": "Image",
                          "format": "image/jpeg"},
            }],
        }],
    })
print(json.dumps(manifest, ensure_ascii=False, indent=2))
```

Then:

```bash
FIX=/tmp/claude-1003/-home-morgan/1ddd010b-3b3e-4cbf-96d6-d5c0f9eab694/scratchpad/fixtures
mkdir -p $FIX && cd $FIX
HF=https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/main/.gradio_cache/examples
curl -sL -o 0001.jpg $HF/A0062408_00006.jpg
curl -sL -o 0002.jpg $HF/A0070302_00201.jpg
curl -sL -o 0003.jpg $HF/A0073477_00025.jpg
curl -sL -o 0004.jpg $HF/R0003364_00005.jpg
python3 ~/htrflow-batch/scripts/make_mock_manifest.py 4 > manifest.json

# create bucket + anonymous read + upload (docker->NodePort; no daemon changes)
docker run --rm -v $FIX:/x -e AWS_ACCESS_KEY_ID=rustfsadmin \
  -e AWS_SECRET_ACCESS_KEY=rustfsadmin amazon/aws-cli \
  --endpoint-url http://10.16.51.53:30900 s3 mb s3://htr-fixtures
docker run --rm -e AWS_ACCESS_KEY_ID=rustfsadmin \
  -e AWS_SECRET_ACCESS_KEY=rustfsadmin amazon/aws-cli \
  --endpoint-url http://10.16.51.53:30900 s3api put-bucket-policy \
  --bucket htr-fixtures --policy '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["*"]},"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::htr-fixtures/*"]}]}'
docker run --rm -v $FIX:/x -e AWS_ACCESS_KEY_ID=rustfsadmin \
  -e AWS_SECRET_ACCESS_KEY=rustfsadmin amazon/aws-cli \
  --endpoint-url http://10.16.51.53:30900 s3 cp /x s3://htr-fixtures/mock-vol/ --recursive

curl -s http://10.16.51.53:30900/htr-fixtures/mock-vol/manifest.json | head -5
curl -s -o /dev/null -w "%{http_code}\n" http://10.16.51.53:30900/htr-fixtures/mock-vol/0001.jpg
```
Expected: manifest JSON echoes back; image returns 200 anonymously.

- [ ] **Step 1: Create the pipeline ConfigMap (immutable, no Export steps)**

`k8s/pipeline-demo-v1.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: htr-pipeline-demo-v1
  namespace: htr-batch
immutable: true
data:
  pipeline.yaml: |
    steps:
    - step: Segmentation
      settings:
        model: yolo
        model_settings:
          model: Riksarkivet/yolov9-regions-1
        generation_settings:
          batch_size: 4
    - step: Segmentation
      settings:
        model: yolo
        model_settings:
          model: Riksarkivet/yolov9-lines-within-regions-1
        generation_settings:
          batch_size: 4
    - step: TextRecognition
      settings:
        model: TrOCR
        model_settings:
          model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        generation_settings:
          batch_size: 16
          num_beams: 1
```

- [ ] **Step 2: Create the smoke Job**

`k8s/job-real-wrapper.yaml` (mock volume from Step 0 — 4 htr_demo pages, no
external dependency; `MAX_PAGES` not needed):

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: htr-vol-301
  namespace: htr-batch
  labels:
    app: htrflow-batch
    batch.htrflow/volume: mock-vol
    kueue.x-k8s.io/queue-name: htr-batch
spec:
  suspend: true
  backoffLimit: 2
  activeDeadlineSeconds: 1800
  ttlSecondsAfterFinished: 7200
  template:
    metadata:
      labels: { app: htrflow-batch }
    spec:
      restartPolicy: Never
      runtimeClassName: nvidia
      containers:
        - name: wrapper
          image: 127.0.0.1:30500/htrflow-batch:v1
          env:
            - name: VOLUME_REF
              value: mock-vol
            - name: IIIF_MANIFEST_URL
              value: "http://10.16.51.53:30900/htr-fixtures/mock-vol/manifest.json"
            - name: PIPELINE_PATH
              value: /config/pipeline.yaml
            - name: PIPELINE_ID
              value: demo-v1
            - name: PUBLIC_RESULTS_BASE
              value: "http://10.16.51.53:30900/htr-results"
            - name: HF_HOME
              value: /data/hf
            - name: WORKDIR_PATH
              value: /work
          envFrom:
            - secretRef: { name: htr-batch-s3 }
          volumeMounts:
            - name: pipeline
              mountPath: /config
            - name: data
              mountPath: /data
            - name: work
              mountPath: /work
          resources:
            requests: { cpu: "4", memory: 8Gi, nvidia.com/gpu: "1" }
            limits: { cpu: "4", memory: 16Gi, nvidia.com/gpu: "1" }
      volumes:
        - name: pipeline
          configMap: { name: htr-pipeline-demo-v1 }
        - name: data
          persistentVolumeClaim: { claimName: htr-test-data }
        - name: work
          emptyDir: { medium: Memory, sizeLimit: 2Gi }
```

Note the Secret env names: the wrapper reads `S3_ENDPOINT`/`S3_BUCKET` from `htr-batch-s3` (already contains both) and boto3 reads `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from the same Secret.

- [ ] **Step 3: Run and watch**

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl apply -f k8s/pipeline-demo-v1.yaml -f k8s/job-real-wrapper.yaml
kubectl -n htr-batch get workloads | grep 301        # admitted by Kueue
kubectl -n htr-batch logs -f job/htr-vol-301         # watch streaming
```
Expected in logs: `4 pages in manifest`, resume line, per-page progress, `COMPLETE 4 pages ... viewer: http://10.16.51.53:30900/htr-results/demo-v1/mock-vol/iiif.json`.

- [ ] **Step 4: Verify the output contract**

```bash
curl -s http://10.16.51.53:30900/htr-results/demo-v1/mock-vol/manifest.json | python3 -m json.tool | head -30
curl -s http://10.16.51.53:30900/htr-results/demo-v1/mock-vol/iiif.json | python3 -c "
import json,sys; m=json.load(sys.stdin)
assert m['type']=='Manifest' and len(m['items'])==4
c=m['items'][0]; assert c['seeAlso'][0]['id'].endswith('alto/0001.xml')
assert c['items'][0]['items'][0]['body']['id'].startswith('http://10.16.51.53:30900/htr-fixtures/')
print('iiif.json OK:', c['width'], 'x', c['height'])"
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" \
  http://10.16.51.53:30900/htr-results/demo-v1/mock-vol/alto/0001.xml
```
Expected: manifest with `results` all `ok`/`skipped`, `gpu_stall_seconds` small; iiif.json with 4 canvases whose width/height are the **actual fixture-image dims from the ALTO** (no image service in the mock → wrapper downloads originals, so dims = original dims, not 2500 — the capped-width path is unit-tested in Task 2); ALTO serves `200 application/xml`; body ids point at the fixtures bucket (browser-viewable end to end).

- [ ] **Step 5: Resume check (cheap rerun)**

```bash
kubectl -n htr-batch delete job htr-vol-301
kubectl apply -f k8s/job-real-wrapper.yaml
kubectl -n htr-batch logs -f job/htr-vol-301 | grep resume
```
Expected: `resume: 4 done, 0 to process` and near-instant COMPLETE (idempotent re-run).

- [ ] **Step 6: Record results**

Append to DESIGN.md §13: wrapper smoke date, pages/sec, `gpu_stall_seconds` vs `wall_seconds` (the Phase 2 gate number from a *real* run), viewer URL; add the two new manifests to `k8s/README.md`'s table.

---

## Self-Review Notes

- **Spec coverage:** §5.1 stages 1–5 → Tasks 2 (setup), 8 (resume/verify/publish), 4–5 (streaming loop), 6 (htrflow driver, Export append, §5.6 overlap in `main`), 7+§5.4/D19 (`iiif.json`, content types, `PUBLIC_RESULTS_BASE`), D8 (verify gate test `test_page_failure_is_transient_and_blocks_completion`), D11 (provenance fields in manifest.json incl. pipeline hash/yaml + htrflow version; per-page model revisions already live in the ALTO per §13), exit codes + termination log (§5.1), §9 test 0 (Task 9 Step 2), instrumentation (`gpu_stall_seconds` etc. in manifest.json). Not in scope (matches user ask "the wrapper"): `htrq` CLI, priority lanes D13, NetworkPolicy D14 — remain §10 opens.
- **Deviation noted inline:** downloader uses a thread pool over sync httpx instead of asyncio — same in-flight bound, simpler tests; documented in Task 4.
- **Type consistency check:** `ProcessFn = Callable[[Path], dict[str, Path]]` used by Tasks 5, 6, 8; `upload(name, files)` matches `ResultStore.upload_page`; `PageRef` fields consistent across 2, 4, 7. `uploaded_pages` aliases `done_pages` (fresh listing each call) — intentional.
```
