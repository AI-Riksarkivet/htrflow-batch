# Campaign GitOps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Git-driven batch submission (campaigns repo → reconciler CronJob → Kueue Jobs) plus a read-only Svelte campaign browser served by the viewer nginx.

**Architecture:** Desired state lives in a separate `htr-campaigns` repo (campaign + pipeline YAML). A stateless reconciler (new `reconciler/` Python package, CronJob every 5 min) derives per-volume status from S3 (`manifest.json` = done) and k8s Jobs, submits missing work round-robin up to a window, and writes `status/status.json` to the bucket. A Svelte SPA (built with Bun, static output, served by the `uv4-viewer` nginx at `/`) renders it; every volume links into UV. Spec: `docs/superpowers/specs/2026-07-29-campaign-gitops-design.md`.

**Tech Stack:** Python 3.10+ (uv, pytest, ruff — mirror `wrapper/`), kubernetes + boto3 + httpx + pyyaml, Svelte 4 + Vite + Bun (build-time only), Helm chart additions, dagger Go module extensions.

## Global Constraints

- NEVER `git push` — the user pushes. NEVER mention Claude/AI or add Co-Authored-By in commit messages.
- Never `docker push` anywhere except the in-cluster PoC registry `127.0.0.1:30500`. Never `dagger call publish*` against a real registry.
- The live k3s cluster stays untouched by this plan: chart changes are verified with `helm lint` / `helm template` ONLY — no `helm install/upgrade`, no `kubectl apply` of reconciler resources.
- Python: run tests as `cd <pkg> && uv run --no-sync pytest -q` after a one-time `uv sync --extra dev` in that package. Format/lint: `uv run --no-sync ruff format src tests && uv run --no-sync ruff check src tests` (same config style as `wrapper/pyproject.toml`: line-length 88, target py310, select E,F,I,W).
- Bun/npm on RA hosts need `NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt` (firewall TLS interception; file exists on dmlpai01). Never disable TLS verification.
- The RA firewall blocks most external hosts (alvin, kb.se, wikimedia…); tests must not fetch the network except huggingface.co/lbiiif/tile.loc.gov — and unit tests must not fetch at all.
- Deterministic job names: `job_name(pipeline_id, volume_id)` from Task 5 is the single source of truth — never inline `f"htr-{p}-{v}"` elsewhere.
- Commit after every task (small, descriptive, no AI mentions).

**Repo layout after this plan** (htrflow-batch):

```
reconciler/                    # new uv package, mirrors wrapper/ layout
  pyproject.toml  uv.lock
  src/htrflow_reconciler/
    __init__.py  __main__.py
    models.py    parse.py     status.py   plan.py
    synthetic.py guards.py    jobspec.py
    s3.py        k8s.py       gitrepo.py  main.py
  tests/
frontend/                      # new Svelte SPA (Bun build-time only)
  package.json  vite.config.js  index.html
  src/App.svelte  src/lib/derive.js  src/lib/derive.test.js  src/main.js
  public/status.sample.json
.docker/htrflow-reconciler.dockerfile
charts/htrflow-batch/templates/reconciler.yaml   # CronJob + RBAC
~/htr-campaigns/               # separate repo scaffold (Task 12)
```

---

### Task 1: IIIF Presentation 2 support in the wrapper

**Files:**
- Modify: `wrapper/src/htrflow_batch/iiif.py`
- Modify: `wrapper/src/htrflow_batch/viewer.py` (use the new `painting_body` helper; normalize string labels)
- Test: `wrapper/tests/test_iiif.py`, `wrapper/tests/test_viewer.py`

**Interfaces:**
- Consumes: existing `PageRef`, `pages_from_manifest(manifest, width)`.
- Produces: `pages_from_manifest` now accepts P2 manifests (`sequences[0].canvases`); new public helper `painting_body(canvas: dict) -> dict` in `iiif.py` returning a P3-style annotation body for P3 *and* P2 canvases (P2 services emitted with `@id`/`@type: ImageService2` + level2 profile — the UV rule from the spec §7.4); new helper `_service_id(service) -> str | None` accepting dict or list.

- [ ] **Step 1: Write the failing tests** — append to `wrapper/tests/test_iiif.py`:

```python
import copy

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


def test_p2_manifest_yields_pages():
    pages = pages_from_manifest(P2_MANIFEST, width=2500)
    assert len(pages) == 1
    assert pages[0].name == "0001"
    assert pages[0].image_url == "http://ex/img/full/2500,/0/default.jpg"


def test_p2_narrow_canvas_requests_max():
    m = copy.deepcopy(P2_MANIFEST)
    m["sequences"][0]["canvases"][0]["width"] = 1200
    pages = pages_from_manifest(m, width=2500)
    assert pages[0].image_url == "http://ex/img/full/max/0/default.jpg"


def test_p2_resource_without_service_uses_direct_url():
    m = copy.deepcopy(P2_MANIFEST)
    del m["sequences"][0]["canvases"][0]["images"][0]["resource"]["service"]
    pages = pages_from_manifest(m, width=2500)
    assert pages[0].image_url == "http://ex/img/full/full/0/default.jpg"


def test_painting_body_p2_emits_v2_style_service():
    from htrflow_batch.iiif import painting_body

    body = painting_body(P2_MANIFEST["sequences"][0]["canvases"][0])
    assert body["id"] == "http://ex/img/full/full/0/default.jpg"
    assert body["type"] == "Image"
    svc = body["service"][0]
    assert svc["@id"] == "http://ex/img"
    assert svc["@type"] == "ImageService2"
    assert "profile" in svc


def test_painting_body_p3_passthrough():
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    body = painting_body(canvas)
    assert body["service"][0]["id"] == "https://img/iiif/page-1"
```

Append to `wrapper/tests/test_viewer.py`:

```python
def test_viewer_manifest_normalizes_p2_string_label(cfg):
    """P2 canvas labels are plain strings; the published P3 manifest must
    carry dict labels or UV renders '[object Object]'-style breakage."""
    from htrflow_batch.iiif import pages_from_manifest
    from tests.test_iiif import P2_MANIFEST

    pages = pages_from_manifest(P2_MANIFEST, width=2500)
    m = build_viewer_manifest(cfg, P2_MANIFEST, pages, {"0001": (2500, 3333)})
    c = m["items"][0]
    assert c["label"] == {"none": ["f. 1r"]}
    assert m["label"] == {"none": ["P2 vol"]}
    body = c["items"][0]["items"][0]["body"]
    assert body["service"][0]["@type"] == "ImageService2"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/wrapper && uv run --no-sync pytest tests/test_iiif.py tests/test_viewer.py -q`
Expected: the 6 new tests FAIL (`painting_body` ImportError; P2 manifest raises `ManifestError("manifest has no canvases")`).

- [ ] **Step 3: Implement** — in `iiif.py`, replace `_image_url` and `pages_from_manifest` and add helpers:

```python
def _service_id(service) -> "str | None":
    if isinstance(service, list):
        service = service[0] if service else None
    if isinstance(service, dict):
        return service.get("id") or service.get("@id")
    return None


def _sized(sid: str, canvas: dict, width: int) -> str:
    # NOTE: lbiiif rejects "!w,h" (501); "w," is the supported form.
    # Level1 servers also reject upscaling (400), so a canvas narrower
    # than the cap must ask for max instead.
    cw = canvas.get("width")
    size = "max" if cw and cw <= width else f"{width},"
    return f"{sid.rstrip('/')}/full/{size}/0/default.jpg"


def _image_url(canvas: dict, width: int) -> "str | None":
    for ap in canvas.get("items", []):  # P3
        for anno in ap.get("items", []):
            body = anno.get("body") or {}
            sid = _service_id(body.get("service"))
            if sid:
                return _sized(sid, canvas, width)
            if body.get("id"):
                return body["id"]
    for img in canvas.get("images", []):  # P2
        res = img.get("resource") or {}
        sid = _service_id(res.get("service"))
        if sid:
            return _sized(sid, canvas, width)
        rid = res.get("@id") or res.get("id")
        if rid:
            return rid
    return None


def painting_body(canvas: dict) -> dict:
    """P3-style annotation body for a P3 or P2 canvas. P2 services are
    emitted with v2-style keys (@id/@type/profile) — UV silently shows no
    image otherwise (docs: wrapper)."""
    for ap in canvas.get("items", []):
        for anno in ap.get("items", []):
            if anno.get("body"):
                return anno["body"]
    for img in canvas.get("images", []):
        res = img.get("resource") or {}
        rid = res.get("@id") or res.get("id")
        if not rid:
            continue
        body = {"id": rid, "type": "Image", "format": res.get("format", "image/jpeg")}
        sid = _service_id(res.get("service"))
        if sid:
            body["service"] = [
                {
                    "@id": sid,
                    "@type": "ImageService2",
                    "profile": "http://iiif.io/api/image/2/level2.json",
                }
            ]
        return body
    return {}


def pages_from_manifest(manifest: dict, width: int) -> "list[PageRef]":
    canvases = manifest.get("items") or []
    if not canvases:
        seqs = manifest.get("sequences") or []
        canvases = (seqs[0].get("canvases") or []) if seqs else []
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

In `viewer.py`: import `painting_body` from `.iiif`; in `build_viewer_manifest` replace the inline body-extraction loop (`body = {}` / `for ap in src.get("items", [])...`) with `body = painting_body(src)`, and add a label normalizer used for both the canvas label and the manifest label:

```python
def _label(value, fallback: str) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        return {"none": [value]}
    return {"none": [fallback]}
```

Use `"label": _label(src.get("label"), page.name)` for canvases and `"label": _label(source_manifest.get("label"), cfg.volume_ref)` for the manifest.

- [ ] **Step 4: Run the full wrapper suite**

Run: `cd ~/htrflow-batch/wrapper && uv run --no-sync pytest -q && uv run --no-sync ruff format --check src tests && uv run --no-sync ruff check src tests`
Expected: all tests pass (50 existing + 6 new), lint clean.

- [ ] **Step 5: Commit**

```bash
git add wrapper && git commit -m "Wrapper: IIIF Presentation 2 support (pages, painting body, label normalization)"
```

---

### Task 2: Failure metrics survive a failed run

**Files:**
- Modify: `wrapper/src/htrflow_batch/main.py`
- Test: `wrapper/tests/test_main.py`

**Interfaces:**
- Produces: `publish_failure_metrics(store, cfg, stats, wall, stage, error)` in `main.py` — best-effort upload of `metrics-failed-latest.json` under the volume prefix. Reconciler (Task 10) and humans read it; schema keys: `volume, pipeline_id, stage, error, wall_seconds, gpu_stall_seconds, results`.

- [ ] **Step 1: Read the failure path** — Read `wrapper/src/htrflow_batch/main.py` in full. Locate the `except` block(s) after the stage machinery that classify errors into exit 13 (permanent) vs exit 1 (transient) — the verify-stage `RuntimeError` from `raise RuntimeError(f"verify failed: ...")` flows through the transient path. Note the local variable names for the store and stats objects in `main()` (store is created in the setup stage; stats is returned by the stream stage).

- [ ] **Step 2: Write the failing test** — append to `wrapper/tests/test_main.py` (uses stubs, no htrflow needed):

```python
from types import SimpleNamespace


def test_publish_failure_metrics_records_run_evidence():
    """A failed run must leave its timing/stall evidence in the bucket
    (spec §4.8) — today it dies with the pod."""
    from htrflow_batch.main import publish_failure_metrics

    calls = []
    store = SimpleNamespace(put_json=lambda key, obj: calls.append((key, obj)))
    cfg = SimpleNamespace(volume_ref="vol-x", pipeline_id="demo-v1")
    stats = SimpleNamespace(
        stall_seconds=12.34,
        results={
            "0001": SimpleNamespace(status="ok", seconds=3.2, error=None),
            "0002": SimpleNamespace(status="failed", seconds=9.9, error="HTTP 400"),
        },
    )
    publish_failure_metrics(store, cfg, stats, 100.0, "verify", "verify failed: x")
    (key, obj) = calls[0]
    assert key == "metrics-failed-latest.json"
    assert obj["stage"] == "verify"
    assert obj["gpu_stall_seconds"] == 12.3
    assert obj["results"]["0002"]["error"] == "HTTP 400"


def test_publish_failure_metrics_never_raises():
    from htrflow_batch.main import publish_failure_metrics

    def boom(key, obj):
        raise OSError("bucket gone")

    store = SimpleNamespace(put_json=boom)
    cfg = SimpleNamespace(volume_ref="v", pipeline_id="p")
    stats = SimpleNamespace(stall_seconds=0.0, results={})
    publish_failure_metrics(store, cfg, stats, 1.0, "stream", "x")  # must not raise
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ~/htrflow-batch/wrapper && uv run --no-sync pytest tests/test_main.py -q`
Expected: 2 new tests FAIL with ImportError.

- [ ] **Step 4: Implement** — add to `main.py` (module level, near the other helpers):

```python
def publish_failure_metrics(store, cfg, stats, wall: float, stage: str, error: str):
    """Best-effort: preserve run evidence when a run fails (docs: wrapper).
    Must never raise — it runs on the failure path."""
    try:
        store.put_json(
            "metrics-failed-latest.json",
            {
                "volume": cfg.volume_ref,
                "pipeline_id": cfg.pipeline_id,
                "stage": stage,
                "error": str(error)[:2000],
                "wall_seconds": round(wall, 1),
                "gpu_stall_seconds": round(stats.stall_seconds, 1),
                "results": {
                    n: {
                        "status": r.status,
                        "seconds": round(r.seconds, 2),
                        **({"error": r.error} if r.error else {}),
                    }
                    for n, r in sorted(stats.results.items())
                },
            },
        )
    except Exception:
        log.warning("could not publish failure metrics", exc_info=True)
```

Wire it into the error-classification `except` block(s) found in Step 1, guarded so setup-stage failures (no store/stats yet) skip it:

```python
        if store is not None and stats is not None:
            publish_failure_metrics(
                store, cfg, stats, time.monotonic() - t_start, stage, str(e)
            )
```

(Initialize `store = None` / `stats = None` before the stage machinery if not already; use the actual local names found in Step 1.)

- [ ] **Step 5: Run suite + lint, commit**

Run: `cd ~/htrflow-batch/wrapper && uv run --no-sync pytest -q && uv run --no-sync ruff format --check src tests && uv run --no-sync ruff check src tests`
Expected: PASS.

```bash
git add wrapper && git commit -m "Wrapper: publish metrics-failed-latest.json so failed runs keep their evidence"
```

---

### Task 3: Reconciler package scaffold + campaign/pipeline parsing

**Files:**
- Create: `reconciler/pyproject.toml`, `reconciler/src/htrflow_reconciler/__init__.py`, `models.py`, `parse.py`
- Test: `reconciler/tests/test_parse.py`

**Interfaces:**
- Produces (used by every later task):
  - `models.Volume(id: str, manifest_url: str | None = None, images: tuple[str, ...] = ())` (frozen dataclass)
  - `models.Campaign(name: str, pipeline_id: str, volumes: list[Volume], error: str | None = None)`
  - `models.PipelineSpec(id: str, image: str, steps_yaml: str, steps_sha256: str)` (frozen)
  - `parse.parse_campaign(name: str, text: str) -> Campaign` — never raises; file-level problems land in `Campaign.error` with empty volumes
  - `parse.parse_pipeline(pipeline_id: str, text: str) -> PipelineSpec` — raises `parse.PipelineError` on missing/tag-only image or missing steps
  - `parse.RA_MANIFEST_TEMPLATE = "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"`

- [ ] **Step 1: Scaffold the package** — `reconciler/pyproject.toml`:

```toml
[project]
name = "htrflow-reconciler"
version = "0.1.0"
description = "GitOps reconciler for htrflow-batch campaigns (docs: how-it-works/campaigns)"
requires-python = ">=3.10"
dependencies = [
    "pyyaml>=6",
    "boto3>=1.34",
    "httpx>=0.27",
    "kubernetes>=29",
]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.4"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/htrflow_reconciler"]

[tool.pytest.ini_options]
addopts = "--import-mode=importlib"
testpaths = ["tests"]

[tool.ruff]
line-length = 88
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
```

Create empty `reconciler/src/htrflow_reconciler/__init__.py`, then `cd ~/htrflow-batch/reconciler && uv sync --extra dev`.

- [ ] **Step 2: Write the failing tests** — `reconciler/tests/test_parse.py`:

```python
import pytest

from htrflow_reconciler.parse import (
    PipelineError,
    parse_campaign,
    parse_pipeline,
)

CAMPAIGN = """
pipeline: demo-v1
volumes:
  - R0001203
  - id: dodsbok-1698
    manifest: https://iiif.example.org/xyz/manifest
  - id: loose-scans
    images:
      - https://example.org/scan1.jpg
      - https://example.org/scan2.jpg
"""


def test_parse_campaign_three_forms():
    c = parse_campaign("trolldom", CAMPAIGN)
    assert c.error is None
    assert c.pipeline_id == "demo-v1"
    v = {x.id: x for x in c.volumes}
    assert v["R0001203"].manifest_url == (
        "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest"
    )
    assert v["dodsbok-1698"].manifest_url == "https://iiif.example.org/xyz/manifest"
    assert v["loose-scans"].images == (
        "https://example.org/scan1.jpg",
        "https://example.org/scan2.jpg",
    )


def test_parse_campaign_malformed_yaml_is_contained():
    c = parse_campaign("bad", "pipeline: [unclosed")
    assert c.error is not None
    assert c.volumes == []


def test_parse_campaign_rejects_unsafe_id():
    c = parse_campaign("bad", "pipeline: p\nvolumes:\n  - id: 'a/b'\n    manifest: http://x\n")
    assert c.error is not None and "a/b" in c.error


def test_parse_campaign_rejects_duplicate_ids():
    c = parse_campaign("dup", "pipeline: p\nvolumes:\n  - R1\n  - R1\n")
    assert c.error is not None and "R1" in c.error


PIPELINE = """
image: docker.io/riksarkivet/htrflow-batch@sha256:abc123
steps:
  - step: Segmentation
"""


def test_parse_pipeline_extracts_image_and_steps_hash():
    p = parse_pipeline("demo-v1", PIPELINE)
    assert p.image.endswith("@sha256:abc123")
    assert "Segmentation" in p.steps_yaml
    assert "image:" not in p.steps_yaml  # ConfigMap gets steps only (spec §3)
    assert len(p.steps_sha256) == 64


def test_parse_pipeline_rejects_tag_image():
    with pytest.raises(PipelineError, match="digest"):
        parse_pipeline("demo-v1", "image: repo/img:v5\nsteps: []\n")


def test_parse_pipeline_requires_steps():
    with pytest.raises(PipelineError, match="steps"):
        parse_pipeline("demo-v1", "image: r/i@sha256:a\n")
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q`
Expected: ImportError (modules don't exist).

- [ ] **Step 4: Implement** — `models.py`:

```python
"""Domain types for the campaign reconciler (docs: how-it-works/campaigns)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Volume:
    id: str
    manifest_url: str | None = None
    images: tuple[str, ...] = ()


@dataclass
class Campaign:
    name: str
    pipeline_id: str
    volumes: list[Volume] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class PipelineSpec:
    id: str
    image: str
    steps_yaml: str
    steps_sha256: str
```

`parse.py`:

```python
"""Campaign/pipeline YAML -> domain types. Campaign problems are contained
(Campaign.error); pipeline problems raise (a broken pipeline must block
submission, spec §3)."""

from __future__ import annotations

import hashlib
import re

import yaml

from .models import Campaign, PipelineSpec, Volume

RA_MANIFEST_TEMPLATE = "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PipelineError(ValueError):
    pass


def _volume(entry) -> Volume:
    if isinstance(entry, str):
        if not _ID_RE.match(entry):
            raise ValueError(f"unsafe volume id: {entry!r}")
        return Volume(id=entry, manifest_url=RA_MANIFEST_TEMPLATE.format(ref=entry))
    if not isinstance(entry, dict) or "id" not in entry:
        raise ValueError(f"volume entry needs an id: {entry!r}")
    vid = str(entry["id"])
    if not _ID_RE.match(vid):
        raise ValueError(f"unsafe volume id: {vid!r}")
    if entry.get("manifest"):
        return Volume(id=vid, manifest_url=str(entry["manifest"]))
    if entry.get("images"):
        return Volume(id=vid, images=tuple(str(u) for u in entry["images"]))
    raise ValueError(f"volume {vid!r} needs manifest: or images:")


def parse_campaign(name: str, text: str) -> Campaign:
    try:
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict):
            raise ValueError("campaign file is not a mapping")
        pipeline_id = str(doc.get("pipeline") or "")
        if not pipeline_id:
            raise ValueError("campaign needs pipeline:")
        volumes = [_volume(e) for e in doc.get("volumes") or []]
        seen: set[str] = set()
        for v in volumes:
            if v.id in seen:
                raise ValueError(f"duplicate volume id: {v.id}")
            seen.add(v.id)
        return Campaign(name=name, pipeline_id=pipeline_id, volumes=volumes)
    except Exception as e:
        return Campaign(name=name, pipeline_id="", volumes=[], error=str(e))


def parse_pipeline(pipeline_id: str, text: str) -> PipelineSpec:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PipelineError(f"bad pipeline yaml: {e}") from e
    if not isinstance(doc, dict):
        raise PipelineError("pipeline file is not a mapping")
    image = str(doc.get("image") or "")
    if "@sha256:" not in image:
        raise PipelineError(f"pipeline {pipeline_id}: image must be digest-pinned")
    if "steps" not in doc:
        raise PipelineError(f"pipeline {pipeline_id}: missing steps")
    steps_yaml = yaml.safe_dump({"steps": doc["steps"]}, sort_keys=False)
    return PipelineSpec(
        id=pipeline_id,
        image=image,
        steps_yaml=steps_yaml,
        steps_sha256=hashlib.sha256(steps_yaml.encode()).hexdigest(),
    )
```

- [ ] **Step 5: Run tests + lint, commit**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q && uv run --no-sync ruff format src tests && uv run --no-sync ruff check src tests`
Expected: 7 passed.

```bash
git add reconciler && git commit -m "Reconciler: package scaffold, campaign and pipeline parsing"
```

---

### Task 4: Status derivation

**Files:**
- Create: `reconciler/src/htrflow_reconciler/status.py`
- Test: `reconciler/tests/test_status.py`

**Interfaces:**
- Consumes: `models.Volume`.
- Produces:
  - `status.JobState(active: bool, failed: bool, exit_code: int | None)` (frozen dataclass)
  - `status.job_name(pipeline_id: str, volume_id: str) -> str` — deterministic, ≤63 chars, k8s-safe (lowercase; long names truncated with an 8-char sha256 suffix)
  - `status.derive(volume: Volume, pipeline_id: str, done: set[str], jobs: dict[str, JobState], attempts: dict[str, int], attempt_cap: int) -> str` returning one of `"done" | "running" | "queued" | "retry" | "needs-attention" | "pending"` (spec §6; `done` holds volume ids with a published manifest.json; `jobs` is keyed by job name)

- [ ] **Step 1: Write the failing tests** — `reconciler/tests/test_status.py`:

```python
from htrflow_reconciler.models import Volume
from htrflow_reconciler.status import JobState, derive, job_name

V = Volume(id="R1", manifest_url="http://m")


def test_job_name_deterministic_and_k8s_safe():
    n = job_name("demo-v1", "R0001203")
    assert n == "htr-demo-v1-r0001203"
    long = job_name("demo-v1", "x" * 80)
    assert len(long) <= 63
    assert long == job_name("demo-v1", "x" * 80)  # stable


def test_done_wins_over_everything():
    jobs = {job_name("p", "R1"): JobState(active=True, failed=False, exit_code=None)}
    assert derive(V, "p", {"R1"}, jobs, {}, 3) == "done"


def test_running_and_queued():
    n = job_name("p", "R1")
    assert derive(V, "p", set(), {n: JobState(True, False, None)}, {}, 3) == "running"
    assert derive(V, "p", set(), {n: JobState(False, False, None)}, {}, 3) == "queued"


def test_failed_transient_below_cap_is_retry():
    n = job_name("p", "R1")
    jobs = {n: JobState(False, True, 1)}
    assert derive(V, "p", set(), jobs, {"R1": 1}, 3) == "retry"


def test_failed_permanent_is_needs_attention():
    n = job_name("p", "R1")
    assert derive(V, "p", set(), {n: JobState(False, True, 13)}, {}, 3) == "needs-attention"


def test_failed_at_cap_is_needs_attention():
    n = job_name("p", "R1")
    jobs = {n: JobState(False, True, 1)}
    assert derive(V, "p", set(), jobs, {"R1": 3}, 3) == "needs-attention"


def test_no_job_no_result_is_pending():
    assert derive(V, "p", set(), {}, {}, 3) == "pending"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest tests/test_status.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement** — `status.py`:

```python
"""Per-volume status derivation — the spec §6 three-way join, as a pure
function so every row of the table is unit-testable."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import Volume


@dataclass(frozen=True)
class JobState:
    active: bool
    failed: bool
    exit_code: "int | None"


def job_name(pipeline_id: str, volume_id: str) -> str:
    raw = f"htr-{pipeline_id}-{volume_id}".lower()
    safe = re.sub(r"[^a-z0-9-]", "-", raw)
    if len(safe) <= 63:
        return safe
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{safe[:54]}-{digest}"


def derive(
    volume: Volume,
    pipeline_id: str,
    done: "set[str]",
    jobs: "dict[str, JobState]",
    attempts: "dict[str, int]",
    attempt_cap: int,
) -> str:
    if volume.id in done:
        return "done"
    job = jobs.get(job_name(pipeline_id, volume.id))
    if job is None:
        return "pending"
    if job.failed:
        if job.exit_code == 13 or attempts.get(volume.id, 0) >= attempt_cap:
            return "needs-attention"
        return "retry"
    if job.active:
        return "running"
    return "queued"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reconciler && git commit -m "Reconciler: status derivation and deterministic job names"
```

---

### Task 5: Submission planner (round-robin window)

**Files:**
- Create: `reconciler/src/htrflow_reconciler/plan.py`
- Test: `reconciler/tests/test_plan.py`

**Interfaces:**
- Consumes: `models.Volume`.
- Produces: `plan.plan_submissions(pending: dict[str, list[Volume]], in_flight: int, window: int) -> list[tuple[str, Volume]]` — `pending` maps campaign name → submittable volumes in file order (statuses `pending`/`retry` whose failed Job has already been deleted); result is interleaved round-robin across campaigns, capped at `max(0, window - in_flight)`.

- [ ] **Step 1: Write the failing tests** — `reconciler/tests/test_plan.py`:

```python
from htrflow_reconciler.models import Volume
from htrflow_reconciler.plan import plan_submissions


def _vols(prefix, n):
    return [Volume(id=f"{prefix}{i}", manifest_url="http://m") for i in range(n)]


def test_round_robin_across_campaigns():
    pending = {"big": _vols("b", 5), "small": _vols("s", 2)}
    out = plan_submissions(pending, in_flight=0, window=4)
    ids = [v.id for _, v in out]
    assert ids == ["b0", "s0", "b1", "s1"]  # small campaign not starved


def test_window_minus_in_flight():
    pending = {"c": _vols("v", 10)}
    assert len(plan_submissions(pending, in_flight=18, window=20)) == 2
    assert plan_submissions(pending, in_flight=20, window=20) == []
    assert plan_submissions(pending, in_flight=25, window=20) == []


def test_empty_campaigns_skipped():
    out = plan_submissions({"a": [], "b": _vols("x", 1)}, 0, 5)
    assert [v.id for _, v in out] == ["x0"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest tests/test_plan.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement** — `plan.py`:

```python
"""Bounded, campaign-fair submission planning (spec §4.7)."""

from __future__ import annotations

from itertools import zip_longest

from .models import Volume


def plan_submissions(
    pending: "dict[str, list[Volume]]",
    in_flight: int,
    window: int,
) -> "list[tuple[str, Volume]]":
    budget = max(0, window - in_flight)
    if budget == 0:
        return []
    lanes = [
        [(name, v) for v in vols] for name, vols in pending.items() if vols
    ]
    interleaved = [
        item
        for round_ in zip_longest(*lanes)
        for item in round_
        if item is not None
    ]
    return interleaved[:budget]
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reconciler && git commit -m "Reconciler: round-robin submission planner with bounded window"
```

---

### Task 6: Synthetic manifests + source pre-validation

**Files:**
- Create: `reconciler/src/htrflow_reconciler/synthetic.py`
- Test: `reconciler/tests/test_synthetic.py`

**Interfaces:**
- Consumes: `models.Volume`.
- Produces:
  - `synthetic.build_manifest(volume_id: str, image_urls: Sequence[str], manifest_id: str) -> dict` — minimal valid P3 manifest; one canvas per image, bodies are plain `{"id", "type": "Image", "format": "image/jpeg"}` (no service, no dims — dims come from ALTO later)
  - `synthetic.classify_manifest(doc: dict) -> str` returning `"p3" | "p2" | "unsupported"` — used by the tick to pre-validate `manifest:` volumes (spec §4.4)

- [ ] **Step 1: Write the failing tests** — `reconciler/tests/test_synthetic.py`:

```python
from htrflow_reconciler.synthetic import build_manifest, classify_manifest


def test_build_manifest_one_canvas_per_image():
    m = build_manifest(
        "loose", ["http://x/1.jpg", "http://x/2.jpg"], "http://s3/loose/manifest.json"
    )
    assert m["@context"] == "http://iiif.io/api/presentation/3/context.json"
    assert m["id"] == "http://s3/loose/manifest.json"
    assert len(m["items"]) == 2
    body = m["items"][0]["items"][0]["items"][0]["body"]
    assert body == {"id": "http://x/1.jpg", "type": "Image", "format": "image/jpeg"}
    anno = m["items"][0]["items"][0]["items"][0]
    assert anno["motivation"] == "painting"
    assert anno["target"] == m["items"][0]["id"]


def test_classify_manifest():
    assert classify_manifest({"items": [{}]}) == "p3"
    assert classify_manifest({"sequences": [{"canvases": [{}]}]}) == "p2"
    assert classify_manifest({"collections": []}) == "unsupported"
    assert classify_manifest({}) == "unsupported"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest tests/test_synthetic.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement** — `synthetic.py`:

```python
"""Synthetic P3 manifests for images: volumes, and source pre-validation.
Proven pattern: LoC Lincoln papers run 2026-07-29 (spec §7.4)."""

from __future__ import annotations

from typing import Sequence


def build_manifest(
    volume_id: str, image_urls: "Sequence[str]", manifest_id: str
) -> dict:
    canvases = []
    for i, url in enumerate(image_urls, start=1):
        cid = f"{manifest_id.rsplit('/', 1)[0]}/canvas/{i}"
        canvases.append(
            {
                "id": cid,
                "type": "Canvas",
                "label": {"none": [f"Image {i}"]},
                "items": [
                    {
                        "id": f"{cid}/ap",
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "id": f"{cid}/anno",
                                "type": "Annotation",
                                "motivation": "painting",
                                "target": cid,
                                "body": {
                                    "id": url,
                                    "type": "Image",
                                    "format": "image/jpeg",
                                },
                            }
                        ],
                    }
                ],
            }
        )
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": manifest_id,
        "type": "Manifest",
        "label": {"none": [volume_id]},
        "items": canvases,
    }


def classify_manifest(doc: dict) -> str:
    if doc.get("items"):
        return "p3"
    seqs = doc.get("sequences") or []
    if seqs and (seqs[0].get("canvases") or []):
        return "p2"
    return "unsupported"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reconciler && git commit -m "Reconciler: synthetic P3 manifests and manifest pre-validation"
```

---

### Task 7: Pipeline immutability guards

**Files:**
- Create: `reconciler/src/htrflow_reconciler/guards.py`
- Test: `reconciler/tests/test_guards.py`

**Interfaces:**
- Consumes: `models.PipelineSpec`.
- Produces: `guards.check_drift(pipeline: PipelineSpec, configmap_steps: str | None, published: dict | None) -> tuple[bool, str | None]` — `(ok, message)`. `configmap_steps` is the existing ConfigMap's `pipeline.yaml` content (None if absent); `published` is one already-published `manifest.json` dict under this pipeline prefix (None if none exist). Rules (spec §3): ConfigMap content mismatch → error; published `pipeline_sha256` mismatch → error; published `image_digest` present-and-different → error; `image_digest` `"unknown"` → OK with a grandfather **warning** message (ok=True, message set).

- [ ] **Step 1: Write the failing tests** — `reconciler/tests/test_guards.py`:

```python
from htrflow_reconciler.guards import check_drift
from htrflow_reconciler.models import PipelineSpec

P = PipelineSpec(
    id="demo-v1",
    image="r/i@sha256:abc",
    steps_yaml="steps: []\n",
    steps_sha256="s" * 64,
)


def _published(sha, digest):
    return {"pipeline_sha256": sha, "image_digest": digest}


def test_fresh_pipeline_ok():
    ok, msg = check_drift(P, None, None)
    assert ok and msg is None


def test_configmap_mismatch_is_error():
    ok, msg = check_drift(P, "steps: [DIFFERENT]\n", None)
    assert not ok and "drift" in msg.lower()


def test_published_sha_mismatch_is_error():
    ok, msg = check_drift(P, P.steps_yaml, _published("x" * 64, P.image))
    assert not ok


def test_published_image_mismatch_is_error():
    ok, msg = check_drift(P, P.steps_yaml, _published(P.steps_sha256, "r/i@sha256:OTHER"))
    assert not ok


def test_unknown_image_digest_grandfathered_with_warning():
    ok, msg = check_drift(P, P.steps_yaml, _published(P.steps_sha256, "unknown"))
    assert ok and msg is not None and "unknown" in msg


def test_everything_matching_ok():
    ok, msg = check_drift(P, P.steps_yaml, _published(P.steps_sha256, P.image))
    assert ok and msg is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest tests/test_guards.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement** — `guards.py`:

```python
"""D17 immutability guards: a pipeline id names one recipe, forever
(spec §3). The S3 ground-truth check is the one that protects results."""

from __future__ import annotations

from .models import PipelineSpec


def check_drift(
    pipeline: PipelineSpec,
    configmap_steps: "str | None",
    published: "dict | None",
) -> "tuple[bool, str | None]":
    if configmap_steps is not None and configmap_steps != pipeline.steps_yaml:
        return False, f"pipeline {pipeline.id}: ConfigMap drift — refusing to submit"
    if published is not None:
        if published.get("pipeline_sha256") != pipeline.steps_sha256:
            return False, (
                f"pipeline {pipeline.id}: steps differ from published results"
            )
        digest = published.get("image_digest")
        if digest == "unknown":
            return True, (
                f"pipeline {pipeline.id}: published results predate image "
                "pinning (image_digest unknown) — grandfathered"
            )
        if digest != pipeline.image:
            return False, (
                f"pipeline {pipeline.id}: image differs from published results"
            )
    return True, None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reconciler && git commit -m "Reconciler: pipeline drift guards with grandfathering for pre-pinning results"
```

---

### Task 8: Job spec builder

**Files:**
- Create: `reconciler/src/htrflow_reconciler/jobspec.py`
- Test: `reconciler/tests/test_jobspec.py`

**Interfaces:**
- Consumes: `models.Volume`, `models.PipelineSpec`, `status.job_name`.
- Produces:
  - `jobspec.ReconcilerConfig` (dataclass): `namespace="htr-batch"`, `queue="htr-batch"`, `s3_secret="htr-batch-s3"`, `public_results_base`, `data_pvc="htr-test-data"`, `window=20`, `attempt_cap=3`, `active_deadline_seconds=21600`
  - `jobspec.build_job(pipeline: PipelineSpec, volume: Volume, manifest_url: str, cfg: ReconcilerConfig) -> dict` — a `batch/v1` Job dict, same shape as the hand-run jobs: Kueue queue label, `suspend: True`, `runtimeClassName: nvidia`, GPU 1 / CPU 4 / 8–16Gi, pipeline ConfigMap mount at `/config`, PVC at `/data` (`HF_HOME=/data/hf`), memory-backed `/work`, `envFrom` the S3 secret, and env `VOLUME_REF, IIIF_MANIFEST_URL, PIPELINE_PATH=/config/pipeline.yaml, PIPELINE_ID, PUBLIC_RESULTS_BASE, IMAGE_DIGEST=<pipeline.image>, WORKDIR_PATH=/work`. `ttlSecondsAfterFinished=86400`, `backoffLimit=0` (retries are the reconciler's job now — k8s-level backoff would double-count attempts).

- [ ] **Step 1: Write the failing tests** — `reconciler/tests/test_jobspec.py`:

```python
from htrflow_reconciler.jobspec import ReconcilerConfig, build_job
from htrflow_reconciler.models import PipelineSpec, Volume
from htrflow_reconciler.status import job_name

P = PipelineSpec("demo-v1", "r/i@sha256:abc", "steps: []\n", "s" * 64)
V = Volume(id="R0001203", manifest_url="https://lbiiif.riksarkivet.se/arkis!R0001203/manifest")
CFG = ReconcilerConfig(public_results_base="http://localhost:30900/htr-results")


def _env(job):
    c = job["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in c["env"]}


def test_job_identity_and_queue():
    job = build_job(P, V, V.manifest_url, CFG)
    assert job["metadata"]["name"] == job_name("demo-v1", "R0001203")
    assert job["metadata"]["namespace"] == "htr-batch"
    assert job["metadata"]["labels"]["kueue.x-k8s.io/queue-name"] == "htr-batch"
    assert job["spec"]["suspend"] is True
    assert job["spec"]["backoffLimit"] == 0


def test_job_env_carries_provenance():
    env = _env(build_job(P, V, V.manifest_url, CFG))
    assert env["VOLUME_REF"] == "R0001203"
    assert env["PIPELINE_ID"] == "demo-v1"
    assert env["IMAGE_DIGEST"] == "r/i@sha256:abc"
    assert env["IIIF_MANIFEST_URL"] == V.manifest_url
    assert env["PUBLIC_RESULTS_BASE"] == "http://localhost:30900/htr-results"


def test_job_image_from_pipeline_pin():
    c = build_job(P, V, V.manifest_url, CFG)["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == "r/i@sha256:abc"
    assert c["resources"]["limits"]["nvidia.com/gpu"] == "1"


def test_job_mounts_pipeline_configmap():
    vols = build_job(P, V, V.manifest_url, CFG)["spec"]["template"]["spec"]["volumes"]
    byname = {v["name"]: v for v in vols}
    assert byname["pipeline"]["configMap"]["name"] == "htr-pipeline-demo-v1"
    assert byname["data"]["persistentVolumeClaim"]["claimName"] == "htr-test-data"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest tests/test_jobspec.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement** — `jobspec.py`:

```python
"""Job dicts for campaign volumes — same shape as the proven hand-run jobs
(R0001203, loc-mal2459400), image + IMAGE_DIGEST from the pipeline pin."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PipelineSpec, Volume
from .status import job_name


@dataclass(frozen=True)
class ReconcilerConfig:
    public_results_base: str
    namespace: str = "htr-batch"
    queue: str = "htr-batch"
    s3_secret: str = "htr-batch-s3"
    data_pvc: str = "htr-test-data"
    window: int = 20
    attempt_cap: int = 3
    active_deadline_seconds: int = 21600


def build_job(
    pipeline: PipelineSpec,
    volume: Volume,
    manifest_url: str,
    cfg: ReconcilerConfig,
) -> dict:
    name = job_name(pipeline.id, volume.id)
    env = [
        {"name": "VOLUME_REF", "value": volume.id},
        {"name": "IIIF_MANIFEST_URL", "value": manifest_url},
        {"name": "PIPELINE_PATH", "value": "/config/pipeline.yaml"},
        {"name": "PIPELINE_ID", "value": pipeline.id},
        {"name": "PUBLIC_RESULTS_BASE", "value": cfg.public_results_base},
        {"name": "IMAGE_DIGEST", "value": pipeline.image},
        {"name": "HF_HOME", "value": "/data/hf"},
        {"name": "WORKDIR_PATH", "value": "/work"},
    ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": cfg.namespace,
            "labels": {
                "app": "htrflow-batch",
                "batch.htrflow/volume": volume.id.lower(),
                "batch.htrflow/pipeline": pipeline.id.lower(),
                "kueue.x-k8s.io/queue-name": cfg.queue,
            },
        },
        "spec": {
            "suspend": True,
            "backoffLimit": 0,
            "activeDeadlineSeconds": cfg.active_deadline_seconds,
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": {"app": "htrflow-batch"}},
                "spec": {
                    "restartPolicy": "Never",
                    "runtimeClassName": "nvidia",
                    "containers": [
                        {
                            "name": "wrapper",
                            "image": pipeline.image,
                            "env": env,
                            "envFrom": [{"secretRef": {"name": cfg.s3_secret}}],
                            "volumeMounts": [
                                {"name": "pipeline", "mountPath": "/config"},
                                {"name": "data", "mountPath": "/data"},
                                {"name": "work", "mountPath": "/work"},
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": "4",
                                    "memory": "8Gi",
                                    "nvidia.com/gpu": "1",
                                },
                                "limits": {
                                    "cpu": "4",
                                    "memory": "16Gi",
                                    "nvidia.com/gpu": "1",
                                },
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "pipeline",
                            "configMap": {"name": f"htr-pipeline-{pipeline.id}"},
                        },
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": cfg.data_pvc},
                        },
                        {
                            "name": "work",
                            "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"},
                        },
                    ],
                },
            },
        },
    }
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reconciler && git commit -m "Reconciler: Job spec builder with pipeline-pinned image and provenance env"
```

---

### Task 9: Adapters (S3, k8s, git)

**Files:**
- Create: `reconciler/src/htrflow_reconciler/s3.py`, `k8s.py`, `gitrepo.py`
- Test: `reconciler/tests/test_s3_keys.py`

**Interfaces:**
- Produces (thin I/O shells; the tick in Task 10 injects fakes for them, so only pure key-helpers get unit tests here):
  - `s3.Bucket(client, bucket: str)` with methods: `done_volumes(pipeline_id: str) -> set[str]` (list `<pipeline>/`, collect ids whose `<pipeline>/<id>/manifest.json` exists), `read_json(key) -> dict | None` (None on missing), `write_json(key, obj)`, `count_pages(pipeline_id, volume_id) -> int` (count `alto/` keys), `put_text(key, text)`
  - `s3.manifest_key(pipeline_id, volume_id) -> str`, `s3.status_key() -> "status/status.json"`, `s3.attempts_key() -> "status/attempts.json"`, `s3.validation_key() -> "status/validation.json"`, `s3.failure_log_key(pipeline_id, volume_id) -> str`, `s3.synthetic_manifest_key(pipeline_id, volume_id) -> str`
  - `k8s.Cluster(namespace)` with: `jobs() -> dict[str, JobState]` (label selector `app=htrflow-batch`; `exit_code` from the failed pod's container status), `create_job(job: dict)` (AlreadyExists → no-op), `delete_job(name)` (propagation Foreground), `get_configmap_steps(pipeline_id) -> str | None`, `ensure_configmap(pipeline_id, steps_yaml)`, `failed_job_logs(name, tail=50) -> str`
  - `gitrepo.checkout(url: str, dest: Path) -> Path` — shallow clone or `git -C dest pull`, via subprocess
- [ ] **Step 1: Write the failing key-helper tests** — `reconciler/tests/test_s3_keys.py`:

```python
from htrflow_reconciler import s3


def test_key_layout_matches_wrapper_contract():
    assert s3.manifest_key("demo-v1", "R1") == "demo-v1/R1/manifest.json"
    assert s3.synthetic_manifest_key("demo-v1", "loose") == (
        "sources/demo-v1/loose/manifest.json"
    )
    assert s3.failure_log_key("demo-v1", "R1") == "status/failures/demo-v1/R1.txt"
    assert s3.status_key() == "status/status.json"
    assert s3.attempts_key() == "status/attempts.json"
    assert s3.validation_key() == "status/validation.json"
```

- [ ] **Step 2: Run to verify failure, then implement** — `s3.py`:

```python
"""S3 adapter. Key helpers are pure; Bucket is a thin boto3 shell that the
tick swaps for a fake in tests (docs: how-it-works/campaigns)."""

from __future__ import annotations

import json


def manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"{pipeline_id}/{volume_id}/manifest.json"


def synthetic_manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"sources/{pipeline_id}/{volume_id}/manifest.json"


def failure_log_key(pipeline_id: str, volume_id: str) -> str:
    return f"status/failures/{pipeline_id}/{volume_id}.txt"


def status_key() -> str:
    return "status/status.json"


def attempts_key() -> str:
    return "status/attempts.json"


def validation_key() -> str:
    return "status/validation.json"


class Bucket:
    def __init__(self, client, bucket: str):
        self.c = client
        self.bucket = bucket

    def read_json(self, key: str) -> "dict | None":
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return json.loads(body)
        except self.c.exceptions.NoSuchKey:
            return None

    def write_json(self, key: str, obj: dict) -> None:
        self.c.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(obj).encode(),
            ContentType="application/json",
        )

    def put_text(self, key: str, text: str) -> None:
        self.c.put_object(
            Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="text/plain"
        )

    def done_volumes(self, pipeline_id: str) -> "set[str]":
        done: set[str] = set()
        paginator = self.c.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self.bucket, Prefix=f"{pipeline_id}/", Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []):
                vid = cp["Prefix"].rstrip("/").split("/", 1)[1]
                if self.read_json(manifest_key(pipeline_id, vid)) is not None:
                    done.add(vid)
        return done

    def count_pages(self, pipeline_id: str, volume_id: str) -> int:
        n = 0
        paginator = self.c.get_paginator("list_objects_v2")
        prefix = f"{pipeline_id}/{volume_id}/alto/"
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            n += len(page.get("Contents", []))
        return n
```

`k8s.py` (thin shell over the official client; no unit tests — exercised via fakes in Task 10 and the compose smoke):

```python
"""k8s adapter — thin shell over kubernetes client. In-cluster config in
the CronJob; kubeconfig fallback for local dev."""

from __future__ import annotations

from kubernetes import client, config

from .status import JobState


def _exit_code(pod) -> "int | None":
    for cs in (pod.status.container_statuses or []):
        term = cs.state.terminated
        if term is not None:
            return term.exit_code
    return None


class Cluster:
    def __init__(self, namespace: str = "htr-batch"):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.ns = namespace
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def jobs(self) -> "dict[str, JobState]":
        out: dict[str, JobState] = {}
        jobs = self.batch.list_namespaced_job(
            self.ns, label_selector="app=htrflow-batch"
        )
        for j in jobs.items:
            failed = any(
                c.type == "Failed" and c.status == "True"
                for c in (j.status.conditions or [])
            )
            exit_code = None
            if failed:
                pods = self.core.list_namespaced_pod(
                    self.ns,
                    label_selector=(
                        f"batch.kubernetes.io/job-name={j.metadata.name}"
                    ),
                ).items
                codes = [_exit_code(p) for p in pods]
                exit_code = next((c for c in codes if c is not None), None)
            out[j.metadata.name] = JobState(
                active=bool(j.status.active),
                failed=failed,
                exit_code=exit_code,
            )
        return out

    def create_job(self, job: dict) -> None:
        try:
            self.batch.create_namespaced_job(self.ns, job)
        except client.ApiException as e:
            if e.status != 409:  # AlreadyExists is a harmless race (spec §7)
                raise

    def delete_job(self, name: str) -> None:
        try:
            self.batch.delete_namespaced_job(
                self.ns, name, propagation_policy="Foreground"
            )
        except client.ApiException as e:
            if e.status != 404:
                raise

    def get_configmap_steps(self, pipeline_id: str) -> "str | None":
        try:
            cm = self.core.read_namespaced_config_map(
                f"htr-pipeline-{pipeline_id}", self.ns
            )
            return (cm.data or {}).get("pipeline.yaml")
        except client.ApiException as e:
            if e.status == 404:
                return None
            raise

    def ensure_configmap(self, pipeline_id: str, steps_yaml: str) -> None:
        body = {
            "metadata": {"name": f"htr-pipeline-{pipeline_id}"},
            "data": {"pipeline.yaml": steps_yaml},
        }
        try:
            self.core.create_namespaced_config_map(self.ns, body)
        except client.ApiException as e:
            if e.status != 409:
                raise

    def failed_job_logs(self, name: str, tail: int = 50) -> str:
        pods = self.core.list_namespaced_pod(
            self.ns, label_selector=f"batch.kubernetes.io/job-name={name}"
        ).items
        if not pods:
            return ""
        pod = sorted(pods, key=lambda p: p.metadata.creation_timestamp)[-1]
        try:
            return self.core.read_namespaced_pod_log(
                pod.metadata.name, self.ns, tail_lines=tail
            )
        except client.ApiException:
            return ""
```

`gitrepo.py`:

```python
"""Shallow clone/pull of the campaigns repo via subprocess git."""

from __future__ import annotations

import subprocess
from pathlib import Path


def checkout(url: str, dest: Path) -> Path:
    dest = Path(dest)
    if (dest / ".git").exists():
        subprocess.run(
            ["git", "-C", str(dest), "pull", "--ff-only"], check=True, timeout=120
        )
    else:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)], check=True, timeout=300
        )
    return dest
```

- [ ] **Step 3: Run tests + lint**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q && uv run --no-sync ruff format src tests && uv run --no-sync ruff check src tests`
Expected: PASS (key tests) — `kubernetes` import must resolve (it's a dependency).

- [ ] **Step 4: Commit**

```bash
git add reconciler && git commit -m "Reconciler: S3, k8s and git adapters"
```

---

### Task 10: The tick — wiring + status.json

**Files:**
- Create: `reconciler/src/htrflow_reconciler/main.py`, `reconciler/src/htrflow_reconciler/__main__.py`
- Test: `reconciler/tests/test_tick.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `main.tick(campaigns_dir: Path, bucket, cluster, cfg: ReconcilerConfig, now_iso: str, fetch_json=None) -> dict` — runs one reconcile pass, returns the status document it wrote. `bucket`/`cluster` are duck-typed (the real adapters or test fakes). `fetch_json: Callable[[str], dict | None] | None` fetches a source manifest for pre-validation (spec §4.4); `None` skips validation entirely (unit-test default). Verdicts are cached forever in `status/validation.json` as `{url: {"format": "p3"|"p2"|"unsupported"|"unreachable", "thumbnail": str|None}}` — one fetch per URL, ever. `unsupported`/`unreachable` volumes are reported with that status and never submitted (no job burned). `thumbnail` is derived from the first canvas via `painting_body`-equivalent logic: service id + `/full/200,/0/default.jpg` when a service exists, else the direct image URL, else null.
  - `__main__.py` runs `tick()` with real adapters from env: `CAMPAIGNS_REPO_URL`, `S3_ENDPOINT`, `S3_BUCKET`, `PUBLIC_RESULTS_BASE`, `RECONCILER_WINDOW` (default 20), `RECONCILER_ATTEMPT_CAP` (default 3), plus AWS creds from the secret.
  - **status.json schema** (consumed verbatim by the frontend, Task 13):

```json
{
  "generated_at": "2026-07-29T09:00:00Z",
  "tick_seconds": 300,
  "warnings": ["pipeline demo-v1: ... grandfathered"],
  "campaigns": [
    {
      "name": "trolldom",
      "pipeline": "demo-v1",
      "error": null,
      "totals": {"done": 1, "total": 3},
      "volumes": [
        {
          "id": "R0001203",
          "status": "done",
          "attempts": 0,
          "pages_done": 638,
          "pages_total": 638,
          "error": null,
          "viewer_manifest": "http://localhost:30900/htr-results/demo-v1/R0001203/iiif.json",
          "source_manifest": "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest",
          "thumbnail": "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/full/200,/0/default.jpg"
        }
      ],
      "orphans": ["old-vol-removed-from-git"]
    }
  ]
}
```

  (`viewer_manifest` is null unless done; `source_manifest` is the volume's manifest URL — for `images:` volumes, the synthetic manifest's public URL; `pages_done`/`pages_total`/`thumbnail` may be null when unknown. `orphans` = volume ids with published results under this campaign's pipeline prefix that no longer appear in git — spec §6 last row: listed, flagged, never deleted; computed as `done_volumes(pid) - {v.id for v in campaign.volumes}` aggregated per pipeline and attached to the first campaign using that pipeline.)

- [ ] **Step 1: Write the failing tick test** — `reconciler/tests/test_tick.py`:

```python
from pathlib import Path

from htrflow_reconciler.jobspec import ReconcilerConfig
from htrflow_reconciler.main import tick
from htrflow_reconciler.status import JobState, job_name

PIPELINE = """image: r/i@sha256:abc
steps:
  - step: Segmentation
"""
CAMPAIGN = """pipeline: demo-v1
volumes:
  - R0000001
  - R0000002
  - id: loose
    images: [http://x/1.jpg]
"""


class FakeBucket:
    def __init__(self, done=(), stored=None):
        self._done = set(done)
        self.stored = stored or {}
        self.written = {}

    def done_volumes(self, pipeline_id):
        return set(self._done)

    def read_json(self, key):
        return self.stored.get(key) or self.written.get(key)

    def write_json(self, key, obj):
        self.written[key] = obj

    def put_text(self, key, text):
        self.written[key] = text

    def count_pages(self, pipeline_id, volume_id):
        return 638 if volume_id in self._done else 0


class FakeCluster:
    def __init__(self, jobs=None):
        self._jobs = jobs or {}
        self.created, self.deleted, self.configmaps = [], [], {}

    def jobs(self):
        return dict(self._jobs)

    def create_job(self, job):
        self.created.append(job)

    def delete_job(self, name):
        self.deleted.append(name)

    def get_configmap_steps(self, pipeline_id):
        return self.configmaps.get(pipeline_id)

    def ensure_configmap(self, pipeline_id, steps_yaml):
        self.configmaps[pipeline_id] = steps_yaml

    def failed_job_logs(self, name, tail=50):
        return "boom traceback"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "trolldom.yaml").write_text(CAMPAIGN)
    (tmp_path / "pipelines" / "demo-v1.yaml").write_text(PIPELINE)
    return tmp_path


CFG = ReconcilerConfig(public_results_base="http://pub/htr-results", window=20)
NOW = "2026-07-29T09:00:00Z"


def test_tick_submits_missing_and_writes_status(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    # R0000001 done; R0000002 + loose submitted
    assert len(cluster.created) == 2
    assert cluster.configmaps["demo-v1"].startswith("steps:")
    camp = doc["campaigns"][0]
    assert camp["totals"] == {"done": 1, "total": 3}
    byid = {v["id"]: v for v in camp["volumes"]}
    assert byid["R0000001"]["status"] == "done"
    assert byid["R0000001"]["viewer_manifest"] == (
        "http://pub/htr-results/demo-v1/R0000001/iiif.json"
    )
    assert byid["R0000002"]["viewer_manifest"] is None
    # synthetic manifest uploaded for the images: volume, and used as source
    assert "sources/demo-v1/loose/manifest.json" in bucket.written
    assert byid["loose"]["source_manifest"].endswith(
        "sources/demo-v1/loose/manifest.json"
    )
    assert doc["generated_at"] == NOW
    assert bucket.written["status/status.json"] == doc


def test_tick_respects_window(tmp_path):
    cfg = ReconcilerConfig(public_results_base="http://pub/htr-results", window=1)
    bucket, cluster = FakeBucket(), FakeCluster()
    tick(_repo(tmp_path), bucket, cluster, cfg, NOW)
    assert len(cluster.created) == 1


def test_tick_retries_failed_transient(tmp_path):
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(False, True, 1)})
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    # captured logs, deleted the failed job, bumped attempts
    assert bucket.written["status/failures/demo-v1/R0000002.txt"] == "boom traceback"
    assert n in cluster.deleted
    assert bucket.written["status/attempts.json"]["R0000002"] == 1
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "retry"


def test_tick_permanent_failure_needs_attention_not_deleted(tmp_path):
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(False, True, 13)})
    doc = tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "needs-attention"
    assert n not in cluster.deleted


def test_tick_drift_blocks_pipeline(tmp_path):
    cluster = FakeCluster()
    cluster.configmaps["demo-v1"] = "steps: [OLD]\n"
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.created == []
    assert any("drift" in w.lower() for w in doc["warnings"])


def test_tick_broken_campaign_contained(tmp_path):
    repo = _repo(tmp_path)
    (repo / "campaigns" / "broken.yaml").write_text("pipeline: [x")
    doc = tick(repo, FakeBucket(), FakeCluster(), CFG, NOW)
    broken = [c for c in doc["campaigns"] if c["name"] == "broken"][0]
    assert broken["error"] is not None
    ok = [c for c in doc["campaigns"] if c["name"] == "trolldom"][0]
    assert ok["error"] is None


def test_tick_prevalidation_blocks_unreachable_and_caches(tmp_path):
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return None  # unreachable

    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "unreachable"
    # unreachable volumes burn no jobs; only the images: volume (no manifest
    # to validate) is submitted
    assert [j["metadata"]["labels"]["batch.htrflow/volume"] for j in cluster.created] == ["loose"]
    # verdicts cached: both manifest URLs fetched exactly once, cache written
    assert len(fetched) == 2
    cache = bucket.written["status/validation.json"]
    assert all(v["format"] == "unreachable" for v in cache.values())
    # second tick fetches nothing (cache hit via written -> read_json)
    fetched.clear()
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    assert fetched == []


def test_tick_prevalidation_extracts_thumbnail(tmp_path):
    def fetch_json(url):
        return {
            "items": [
                {
                    "items": [
                        {
                            "items": [
                                {
                                    "body": {
                                        "id": "http://img/full/max/0/default.jpg",
                                        "service": [{"id": "http://img"}],
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["thumbnail"] == "http://img/full/200,/0/default.jpg"
    assert byid["R0000001"]["status"] == "pending" or byid["R0000001"]["status"] in (
        "running",
        "queued",
    )


def test_tick_reports_orphans(tmp_path):
    bucket = FakeBucket(done={"R0000001", "ghost-vol"})
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    assert doc["campaigns"][0]["orphans"] == ["ghost-vol"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest tests/test_tick.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement** — `main.py`:

```python
"""One reconcile pass (docs: how-it-works/campaigns). Pure orchestration:
adapters injected, no I/O of its own beyond them."""

from __future__ import annotations

from pathlib import Path

from . import s3 as keys
from .guards import check_drift
from .jobspec import ReconcilerConfig, build_job
from .models import Campaign, PipelineSpec, Volume
from .parse import PipelineError, parse_campaign, parse_pipeline
from .plan import plan_submissions
from .status import derive, job_name
from .synthetic import build_manifest


def _load_repo(campaigns_dir: Path):
    campaigns = [
        parse_campaign(p.stem, p.read_text())
        for p in sorted((campaigns_dir / "campaigns").glob("*.yaml"))
    ]
    pipelines: dict[str, PipelineSpec] = {}
    errors: list[str] = []
    for p in sorted((campaigns_dir / "pipelines").glob("*.yaml")):
        try:
            pipelines[p.stem] = parse_pipeline(p.stem, p.read_text())
        except PipelineError as e:
            errors.append(str(e))
    return campaigns, pipelines, errors


def _source_manifest_url(
    volume: Volume, pipeline_id: str, bucket, cfg: ReconcilerConfig
) -> str:
    if volume.manifest_url:
        return volume.manifest_url
    key = keys.synthetic_manifest_key(pipeline_id, volume.id)
    url = f"{cfg.public_results_base.rstrip('/')}/{key}"
    if bucket.read_json(key) is None:
        bucket.write_json(key, build_manifest(volume.id, list(volume.images), url))
    return url


def _first_canvas(doc: dict) -> dict:
    canvases = doc.get("items") or []
    if not canvases:
        seqs = doc.get("sequences") or []
        canvases = (seqs[0].get("canvases") or []) if seqs else []
    return canvases[0] if canvases else {}


def _thumbnail(doc: dict) -> "str | None":
    """First-page thumbnail: sized request when a service exists, else the
    direct image URL (spec §5). Handles P3 and P2 canvases."""
    canvas = _first_canvas(doc)
    for ap in canvas.get("items", []):
        for anno in ap.get("items", []):
            body = anno.get("body") or {}
            for svc in body.get("service") or []:
                sid = svc.get("id") or svc.get("@id")
                if sid:
                    return f"{sid.rstrip('/')}/full/200,/0/default.jpg"
            if body.get("id"):
                return body["id"]
    for img in canvas.get("images", []):
        res = img.get("resource") or {}
        svc = res.get("service")
        svc = svc[0] if isinstance(svc, list) and svc else svc
        sid = (svc or {}).get("@id") or (svc or {}).get("id") if svc else None
        if sid:
            return f"{sid.rstrip('/')}/full/200,/0/default.jpg"
        if res.get("@id") or res.get("id"):
            return res.get("@id") or res.get("id")
    return None


def _validate(url: str, cache: dict, fetch_json) -> dict:
    """Once-ever verdict per manifest URL (spec §4.4): format + thumbnail."""
    if url in cache:
        return cache[url]
    doc = fetch_json(url)
    if doc is None:
        verdict = {"format": "unreachable", "thumbnail": None}
    else:
        verdict = {"format": classify_manifest(doc), "thumbnail": _thumbnail(doc)}
    cache[url] = verdict
    return verdict


def tick(
    campaigns_dir: Path,
    bucket,
    cluster,
    cfg: ReconcilerConfig,
    now_iso: str,
    fetch_json=None,
) -> dict:
    campaigns, pipelines, warnings = _load_repo(Path(campaigns_dir))
    jobs = cluster.jobs()
    attempts: dict = bucket.read_json(keys.attempts_key()) or {}
    validation: dict = bucket.read_json(keys.validation_key()) or {}
    blocked: set[str] = set()

    for pid, spec in pipelines.items():
        published = None
        done_probe = bucket.done_volumes(pid)
        if done_probe:
            published = bucket.read_json(keys.manifest_key(pid, sorted(done_probe)[0]))
        ok, msg = check_drift(spec, cluster.get_configmap_steps(pid), published)
        if msg:
            warnings.append(msg)
        if not ok:
            blocked.add(pid)
        else:
            cluster.ensure_configmap(pid, spec.steps_yaml)

    doc: dict = {
        "generated_at": now_iso,
        "tick_seconds": 300,
        "warnings": warnings,
        "campaigns": [],
    }
    pending: dict[str, list[tuple[Volume, str]]] = {}
    in_flight = sum(1 for j in jobs.values() if not j.failed)

    for camp in campaigns:
        entry = {
            "name": camp.name,
            "pipeline": camp.pipeline_id or None,
            "error": camp.error,
            "totals": {"done": 0, "total": len(camp.volumes)},
            "volumes": [],
        }
        doc["campaigns"].append(entry)
        if camp.error or camp.pipeline_id not in pipelines:
            if not camp.error:
                entry["error"] = f"unknown pipeline: {camp.pipeline_id}"
            continue
        pid = camp.pipeline_id
        done = bucket.done_volumes(pid)
        entry["orphans"] = sorted(done - {v.id for v in camp.volumes})
        lane: list[tuple[Volume, str]] = []
        for v in camp.volumes:
            st = derive(v, pid, done, jobs, attempts, cfg.attempt_cap)
            src = _source_manifest_url(v, pid, bucket, cfg)
            thumb = None
            if v.manifest_url and fetch_json is not None and st != "done":
                verdict = _validate(v.manifest_url, validation, fetch_json)
                thumb = verdict["thumbnail"]
                if verdict["format"] in ("unreachable", "unsupported"):
                    st = verdict["format"]  # no job burned (spec §4.4)
            if st == "retry":
                name = job_name(pid, v.id)
                bucket.put_text(
                    keys.failure_log_key(pid, v.id), cluster.failed_job_logs(name)
                )
                cluster.delete_job(name)
                attempts[v.id] = attempts.get(v.id, 0) + 1
                lane.append((v, src))
            elif st == "pending" and pid not in blocked:
                lane.append((v, src))
            if st == "done":
                entry["totals"]["done"] += 1
            entry["volumes"].append(
                {
                    "id": v.id,
                    "status": st,
                    "attempts": attempts.get(v.id, 0),
                    "pages_done": bucket.count_pages(pid, v.id)
                    if st in ("done", "running")
                    else None,
                    "pages_total": None,
                    "error": None,
                    "viewer_manifest": (
                        f"{cfg.public_results_base.rstrip('/')}/{pid}/{v.id}/iiif.json"
                        if st == "done"
                        else None
                    ),
                    "source_manifest": src,
                    "thumbnail": thumb,
                }
            )
        if pid not in blocked and lane:
            pending[camp.name] = lane

    lanes = {name: [v for v, _ in lane] for name, lane in pending.items()}
    srcs = {(n, v.id): s for n, lane in pending.items() for v, s in lane}
    for camp_name, volume in plan_submissions(lanes, in_flight, cfg.window):
        camp = next(c for c in campaigns if c.name == camp_name)
        spec = pipelines[camp.pipeline_id]
        cluster.create_job(
            build_job(spec, volume, srcs[(camp_name, volume.id)], cfg)
        )

    bucket.write_json(keys.validation_key(), validation)
    bucket.write_json(keys.attempts_key(), attempts)
    bucket.write_json(keys.status_key(), doc)
    return doc
```

(Imports for the new helpers: add `classify_manifest` to the existing
`from .synthetic import build_manifest` line. The `pending`-status guard in
the lane means `unreachable`/`unsupported` volumes never enter it, since
their `st` is no longer `"pending"`. `entry["orphans"]` defaults to `[]` in
the entry dict initializer for campaigns that error out early — add
`"orphans": []` to the initial `entry = {...}` literal.)

`__main__.py`:

```python
"""CronJob entrypoint: one tick with real adapters, config from env."""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
import httpx

from .gitrepo import checkout
from .jobspec import ReconcilerConfig
from .k8s import Cluster
from .main import tick
from .s3 import Bucket


def _fetch_json(url: str):
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def run() -> None:
    cfg = ReconcilerConfig(
        public_results_base=os.environ["PUBLIC_RESULTS_BASE"],
        window=int(os.environ.get("RECONCILER_WINDOW", "20")),
        attempt_cap=int(os.environ.get("RECONCILER_ATTEMPT_CAP", "3")),
    )
    repo = checkout(
        os.environ["CAMPAIGNS_REPO_URL"],
        Path(os.environ.get("CAMPAIGNS_DIR", tempfile.gettempdir() + "/campaigns")),
    )
    client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT") or None)
    bucket = Bucket(client, os.environ.get("S3_BUCKET", "htr-results"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tick(repo, bucket, Cluster(), cfg, now, fetch_json=_fetch_json)


if __name__ == "__main__":
    run()
```

Note the retry semantics the tests encode: a `retry` volume gets its logs captured, its failed Job deleted, its attempt count bumped, **and is submittable in the same tick** (it goes into the lane; the deterministic name is free again because the delete precedes the create — Foreground propagation. If the create still races a slow delete, the 409 no-op means it simply goes next tick).

- [ ] **Step 4: Run the full reconciler suite + lint**

Run: `cd ~/htrflow-batch/reconciler && uv run --no-sync pytest -q && uv run --no-sync ruff format src tests && uv run --no-sync ruff check src tests`
Expected: all reconciler tests pass.

- [ ] **Step 5: Commit**

```bash
git add reconciler && git commit -m "Reconciler: tick orchestration, retry capture, status.json emission"
```

---

### Task 11: Reconciler image, chart CronJob + RBAC, dagger/Make/CI wiring

**Files:**
- Create: `.docker/htrflow-reconciler.dockerfile`, `charts/htrflow-batch/templates/reconciler.yaml`
- Modify: `charts/htrflow-batch/values.yaml`, `.dagger/test.go`, `.dagger/build.go`, `Makefile`, `.github/workflows/ci.yml` (only if it lists test targets explicitly — inspect first; dagger `check`/`test` may already cover)

**Interfaces:**
- Consumes: `python -m htrflow_reconciler` entrypoint (Task 10 env contract).
- Produces: values block `reconciler: {enabled: false, image, campaignsRepoUrl, schedule: "*/5 * * * *", window: 20, attemptCap: 3, publicResultsBase: ""}`; CronJob `htr-reconciler` with `concurrencyPolicy: Forbid`, ServiceAccount `htr-reconciler` + Role/RoleBinding (jobs: get/list/create/delete; configmaps: get/create; pods: get/list; pods/log: get).

- [ ] **Step 1: Dockerfile** — `.docker/htrflow-reconciler.dockerfile`:

```dockerfile
# Reconciler: slim, CPU-only, no torch. Build context = repo root.
# RA firewall CA is baked in so in-cluster git clone of the campaigns repo
# works through TLS interception (spec §7.1).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY reconciler /src/reconciler
RUN pip install --no-cache-dir /src/reconciler
ENV GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENTRYPOINT ["python", "-m", "htrflow_reconciler"]
```

Verify it builds: `cd ~/htrflow-batch && docker build -f .docker/htrflow-reconciler.dockerfile -t 127.0.0.1:30500/htrflow-reconciler:dev . && docker run --rm --entrypoint python 127.0.0.1:30500/htrflow-reconciler:dev -c "import htrflow_reconciler.main"`
Expected: builds, import succeeds. Do NOT push or deploy.

- [ ] **Step 2: Chart template** — `charts/htrflow-batch/templates/reconciler.yaml`:

```yaml
{{- if .Values.reconciler.enabled }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: htr-reconciler
  namespace: {{ .Release.Namespace }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: htr-reconciler
  namespace: {{ .Release.Namespace }}
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list", "create", "delete"]
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "create"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: htr-reconciler
  namespace: {{ .Release.Namespace }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: htr-reconciler
subjects:
  - kind: ServiceAccount
    name: htr-reconciler
    namespace: {{ .Release.Namespace }}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: htr-reconciler
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "htrflow-batch.labels" . | nindent 4 }}
spec:
  schedule: {{ .Values.reconciler.schedule | quote }}
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      activeDeadlineSeconds: 240
      template:
        spec:
          serviceAccountName: htr-reconciler
          restartPolicy: Never
          containers:
            - name: reconciler
              image: {{ .Values.reconciler.image | required "reconciler.image is required when reconciler.enabled" }}
              env:
                - name: CAMPAIGNS_REPO_URL
                  value: {{ .Values.reconciler.campaignsRepoUrl | required "reconciler.campaignsRepoUrl is required" | quote }}
                - name: PUBLIC_RESULTS_BASE
                  value: {{ .Values.reconciler.publicResultsBase | default .Values.publicResultsBase | quote }}
                - name: RECONCILER_WINDOW
                  value: {{ .Values.reconciler.window | quote }}
                - name: RECONCILER_ATTEMPT_CAP
                  value: {{ .Values.reconciler.attemptCap | quote }}
              envFrom:
                - secretRef: { name: {{ .Values.s3.existingSecret }} }
              resources:
                requests: { cpu: 100m, memory: 256Mi }
                limits: { cpu: "1", memory: 512Mi }
{{- end }}
```

Append to `charts/htrflow-batch/values.yaml`:

```yaml
reconciler:                # GitOps campaign reconciler (spec: campaign-gitops)
  enabled: false
  image: ""                # e.g. 127.0.0.1:30500/htrflow-reconciler:dev on the PoC
  campaignsRepoUrl: ""     # e.g. https://github.com/<org>/htr-campaigns
  schedule: "*/5 * * * *"
  window: 20               # max not-yet-done Jobs existing at once
  attemptCap: 3
  publicResultsBase: ""    # defaults to global publicResultsBase
```

- [ ] **Step 3: Verify with helm only (NO install)**

Run: `helm lint charts/htrflow-batch && helm template t charts/htrflow-batch --set reconciler.enabled=true --set reconciler.image=x --set reconciler.campaignsRepoUrl=https://example/r --set publicResultsBase=http://pub | grep -E 'kind:|concurrencyPolicy|serviceAccountName'`
Expected: lint clean; output shows ServiceAccount, Role, RoleBinding, CronJob, `concurrencyPolicy: Forbid`. Also verify default render (`helm template t charts/htrflow-batch | grep -c reconciler` → 0).

- [ ] **Step 4: Wire test/build tooling** — Read `.dagger/test.go` and `.dagger/build.go`; extend: in `test.go` add a `TestReconciler` function mirroring the wrapper's uv test container but with source `reconciler/` (uv sync --extra dev, `uv run pytest -q`), and make the aggregate check (whatever `Checks`/`Test` currently calls — see `checks.go`) include it. In `Makefile`, extend the `test` target to run both suites:

```makefile
test:
	cd wrapper && uv run --no-sync pytest -q
	cd reconciler && uv run --no-sync pytest -q
```

(match the existing target's exact current form — extend, don't replace semantics). Run `make test` to confirm both suites pass; run `cd .dagger && go build ./...` to confirm the module compiles.

- [ ] **Step 5: Commit**

```bash
git add .docker charts .dagger Makefile .github && git commit -m "Reconciler: image, chart CronJob with RBAC, test wiring"
```

---

### Task 12: htr-campaigns repo scaffold

**Files (in a NEW repo at `~/htr-campaigns` — git init, NO remote, NO push):**
- Create: `README.md`, `pipelines/demo-v1.yaml`, `campaigns/htr-demo-examples.yaml`, `.github/workflows/guard.yml`, `scripts/check_immutable.sh`

**Interfaces:**
- Consumes: campaign/pipeline formats from Task 3; the wrapper image digest currently in the PoC registry.

- [ ] **Step 1: Scaffold** — `mkdir -p ~/htr-campaigns/{campaigns,pipelines,scripts,.github/workflows} && cd ~/htr-campaigns && git init -b main`. Get the current wrapper image digest:

```bash
docker inspect --format '{{index .RepoDigests 0}}' 127.0.0.1:30500/htrflow-batch:v5
```

`pipelines/demo-v1.yaml` (use the digest from above — note it is grandfathered against existing demo-v1 results, spec §3 guard 3):

```yaml
image: 127.0.0.1:30500/htrflow-batch@sha256:<DIGEST-FROM-ABOVE>
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

`campaigns/htr-demo-examples.yaml`:

```yaml
pipeline: demo-v1
volumes:
  - id: htr-demo-examples
    images:
      - https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/main/.gradio_cache/examples/A0062408_00006.jpg
      - https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/main/.gradio_cache/examples/A0073477_00025.jpg
```

- [ ] **Step 2: The CI guard** — `scripts/check_immutable.sh`:

```bash
#!/usr/bin/env bash
# Pipelines are immutable (D17): a PR may ADD pipelines/*.yaml, never modify
# or delete an existing one. Usage: check_immutable.sh <base-ref>
set -euo pipefail
base="${1:-origin/main}"
bad=$(git diff --name-status "$base"...HEAD -- pipelines/ | awk '$1 != "A" {print $2}')
if [ -n "$bad" ]; then
  echo "ERROR: existing pipeline files modified or deleted (immutable):"
  echo "$bad"
  exit 1
fi
echo "OK: pipelines only added"
```

`chmod +x scripts/check_immutable.sh`. `.github/workflows/guard.yml`:

```yaml
name: guard
on:
  pull_request:
jobs:
  immutable-pipelines:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
        with:
          fetch-depth: 0
      - run: scripts/check_immutable.sh "origin/${{ github.base_ref }}"
```

- [ ] **Step 3: Test the guard locally**

```bash
cd ~/htr-campaigns && git add -A && git commit -m "Initial campaigns repo: demo-v1 pipeline, htr-demo-examples campaign, immutability guard"
git checkout -b test-guard
echo "# tweak" >> pipelines/demo-v1.yaml && git commit -am "tweak pipeline"
scripts/check_immutable.sh main && echo "GUARD FAILED TO FIRE" || echo "guard fired correctly"
git checkout main && git branch -D test-guard
```

Expected: "guard fired correctly" (script exits 1 on the modification).

- [ ] **Step 4: README** — `README.md` covering: what the repo is (desired state; adding a volume = a commit), the three volume forms with the Task 3 examples, the immutability rule ("tune a pipeline → new file, new id"), digest-pinning (`docker inspect --format '{{index .RepoDigests 0}}' <image>`), a pointer to the htrflow-batch docs site, and the note that the default branch must be protected (spec §7.2 — repo write ≈ cluster code execution). Commit:

```bash
cd ~/htr-campaigns && git add README.md && git commit -m "README: formats, immutability, security notes"
```

Report to the user that they need to create the GitHub repo and push (never push for them).

---

### Task 13: Campaign browser frontend (Svelte + Bun)

**Files (all under `~/htrflow-batch/frontend/`):**
- Create: `package.json`, `vite.config.js`, `index.html`, `src/main.js`, `src/App.svelte`, `src/lib/derive.js`, `src/lib/derive.test.js`, `public/status.sample.json`, `.gitignore` (`node_modules/`, `dist/`)
- Modify: `Makefile` (add `frontend-install`, `frontend-test`, `frontend-build`, `frontend-dev`)

**Interfaces:**
- Consumes: the status.json schema from Task 10 (copied into `public/status.sample.json`).
- Produces: `bun run build` → `frontend/dist/` static files with **relative** asset paths (`base: "./"`), fetching `../htr-results/status/status.json`... — NO: the SPA fetches the status URL from a same-origin config: `window.STATUS_URL` if set, else `http://localhost:30900/htr-results/status/status.json` (the PoC default; the bucket serves CORS-open public reads). `derive.js` exports `viewerHref(volume)` and `progress(campaign)` used by App.svelte and unit-tested with `bun test`.

- [ ] **Step 1: Scaffold without network surprises** — `frontend/package.json`:

```json
{
  "name": "htrflow-campaign-browser",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "bun test src"
  },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^3.1.0",
    "svelte": "^4.2.0",
    "vite": "^5.2.0"
  }
}
```

`vite.config.js`:

```javascript
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [svelte()],
  server: { host: true }, // reachable over the LAN/tunnel, rask lesson
});
```

Install (CA bundle for the RA firewall): `cd ~/htrflow-batch/frontend && NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt bun install`
Expected: lockfile `bun.lockb` created. Commit the lockfile.

- [ ] **Step 2: Failing unit tests** — `src/lib/derive.test.js`:

```javascript
import { expect, test } from "bun:test";
import { isStale, progress, viewerHref } from "./derive.js";

const done = {
  id: "R1",
  status: "done",
  viewer_manifest: "http://pub/htr-results/demo-v1/R1/iiif.json",
  source_manifest: "https://lbiiif.riksarkivet.se/arkis!R1/manifest",
};
const pending = { ...done, status: "pending", viewer_manifest: null };

test("done volumes open the published manifest", () => {
  expect(viewerHref(done)).toBe(
    "uv.html#?manifest=http://pub/htr-results/demo-v1/R1/iiif.json"
  );
});

test("pending volumes open the source manifest", () => {
  expect(viewerHref(pending)).toBe(
    "uv.html#?manifest=https://lbiiif.riksarkivet.se/arkis!R1/manifest"
  );
});

test("progress percentage", () => {
  expect(progress({ totals: { done: 2, total: 8 } })).toBe(25);
  expect(progress({ totals: { done: 0, total: 0 } })).toBe(0);
});

test("stale when older than 3 ticks", () => {
  const now = new Date("2026-07-29T09:20:00Z");
  expect(isStale("2026-07-29T09:00:00Z", 300, now)).toBe(true);
  expect(isStale("2026-07-29T09:11:00Z", 300, now)).toBe(false);
});
```

Run: `cd ~/htrflow-batch/frontend && bun test src` — expected FAIL (module missing).

- [ ] **Step 3: Implement** — `src/lib/derive.js`:

```javascript
// Pure view-derivation over status.json (schema: reconciler main.py).

export function viewerHref(volume) {
  const manifest =
    volume.status === "done" && volume.viewer_manifest
      ? volume.viewer_manifest
      : volume.source_manifest;
  return `uv.html#?manifest=${manifest}`;
}

export function progress(campaign) {
  const { done, total } = campaign.totals;
  return total === 0 ? 0 : Math.round((100 * done) / total);
}

export function isStale(generatedAt, tickSeconds, now = new Date()) {
  const age = (now - new Date(generatedAt)) / 1000;
  return age > 3 * tickSeconds;
}
```

`index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>HTR Campaigns</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

`src/main.js`:

```javascript
import App from "./App.svelte";

new App({ target: document.getElementById("app") });
```

`src/App.svelte` (single-file app: campaign list + volume grid, STALE banner; status chips colored by state; every volume is an `<a>` into UV):

```svelte
<script>
  import { isStale, progress, viewerHref } from "./lib/derive.js";

  const STATUS_URL =
    window.STATUS_URL ?? "http://localhost:30900/htr-results/status/status.json";
  let doc = null;
  let error = null;
  let open = null; // campaign name expanded

  async function load() {
    try {
      const res = await fetch(STATUS_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      doc = await res.json();
    } catch (e) {
      error = String(e);
    }
  }
  load();
  setInterval(load, 60_000);
</script>

<main>
  <h1>HTR Campaigns</h1>
  {#if error}
    <p class="error">Cannot load status: {error}</p>
  {:else if !doc}
    <p>Loading…</p>
  {:else}
    {#if isStale(doc.generated_at, doc.tick_seconds)}
      <p class="stale">STALE — last reconcile {doc.generated_at}. The
        reconciler may be dead (this is not "no news").</p>
    {/if}
    <p class="meta">generated {doc.generated_at}</p>
    {#each doc.warnings as w}<p class="warn">{w}</p>{/each}
    {#each doc.campaigns as c}
      <section>
        <h2 on:click={() => (open = open === c.name ? null : c.name)}>
          {c.name}
          {#if c.error}<span class="chip needs-attention">broken</span>
          {:else}
            <span class="chip">{c.pipeline}</span>
            <progress max="100" value={progress(c)}></progress>
            {c.totals.done}/{c.totals.total}
          {/if}
        </h2>
        {#if c.error}<p class="error">{c.error}</p>{/if}
        {#if open === c.name && !c.error}
          <div class="grid">
            {#each c.volumes as v}
              <a class="card" href={viewerHref(v)} target="_blank" rel="noopener">
                {#if v.thumbnail}
                  <img src={v.thumbnail} alt="" loading="lazy" />
                {/if}
                <span class="chip {v.status}">{v.status}</span>
                <strong>{v.id}</strong>
                {#if v.pages_done != null}<small>{v.pages_done} pages</small>{/if}
                {#if v.attempts > 0}<small>attempts: {v.attempts}</small>{/if}
              </a>
            {/each}
          </div>
        {/if}
      </section>
    {/each}
  {/if}
</main>

<style>
  main { font-family: system-ui, sans-serif; max-width: 60rem; margin: 0 auto; padding: 1rem; }
  .stale { background: #b91c1c; color: #fff; padding: 0.5rem 1rem; border-radius: 4px; }
  .warn { background: #fef3c7; padding: 0.25rem 0.75rem; border-radius: 4px; }
  .error { color: #b91c1c; }
  .meta { color: #6b7280; font-size: 0.85rem; }
  h2 { cursor: pointer; display: flex; align-items: center; gap: 0.75rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 0.75rem; }
  .card { display: flex; flex-direction: column; gap: 0.25rem; border: 1px solid #d1d5db;
          border-radius: 6px; padding: 0.75rem; text-decoration: none; color: inherit; }
  .card:hover { border-color: #2563eb; }
  .chip { font-size: 0.7rem; padding: 0.1rem 0.5rem; border-radius: 999px; background: #e5e7eb; width: fit-content; }
  .chip.done { background: #bbf7d0; }
  .chip.running { background: #bfdbfe; }
  .chip.queued, .chip.retry { background: #fef08a; }
  .chip.needs-attention, .chip.unreachable, .chip.unsupported { background: #fecaca; }
  .card img { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 4px; }
</style>
```

`public/status.sample.json` — one campaign, three volumes (statuses `done`/`running`/`needs-attention`) matching the Task 10 schema exactly, including `generated_at`, `tick_seconds: 300`, one grandfather warning. For dev: `bun run dev` serves it at `/status.sample.json`; set `window.STATUS_URL = "/status.sample.json"` via the browser console, or temporarily in `index.html` — document this in a comment at the top of the sample file? JSON has no comments — document it in `frontend/README.md` (3 lines) instead.

- [ ] **Step 4: Tests + build green**

Run: `cd ~/htrflow-batch/frontend && bun test src && NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt bun run build && ls dist/index.html`
Expected: 4 tests pass, `dist/` produced.

- [ ] **Step 5: Makefile targets + commit** — append to `Makefile` (match existing style):

```makefile
frontend-install:
	cd frontend && NODE_EXTRA_CA_CERTS=$(CA_BUNDLE) bun install

frontend-test:
	cd frontend && bun test src

frontend-build:
	cd frontend && NODE_EXTRA_CA_CERTS=$(CA_BUNDLE) bun run build

frontend-dev:
	cd frontend && bun run dev
```

```bash
git add frontend Makefile && git commit -m "Campaign browser: Svelte SPA with status-derived views and STALE banner"
```

---

### Task 14: Viewer image integration, chart update, docs

**Files:**
- Modify: `.docker/uv4-viewer.dockerfile`, `charts/htrflow-batch/templates/viewer.yaml`, `charts/htrflow-batch/values.yaml`, `.dagger/viewer.go`, `Makefile`
- Create: `docs/how-it-works/campaigns.md`, `docs/getting-started/campaigns.md`
- Modify: `zensical.toml` (nav), `docs/index.md` (one-line pointer), `docs/roadmap/open-items.md` (mark status page item done)

**Interfaces:**
- Consumes: `frontend/dist/` (Task 13), existing UV dist build flow.
- Produces: viewer image that serves the SPA at `/` and UV at `/uv.html`; chart `viewer.defaultManifest` deprecated (redirect only rendered when set — default now serves the SPA).

- [ ] **Step 1: Dockerfile** — `.docker/uv4-viewer.dockerfile` gains a second COPY; context stays the universalviewer4 repo root, and the SPA is staged into it by the builder (dagger or Makefile) as `campaign-app/`:

```dockerfile
FROM nginx:alpine
COPY dist /usr/share/nginx/html
# Campaign browser SPA (staged as campaign-app/ by `make viewer-image` or
# dagger BuildViewer) — overwrites UV's demo index.html; UV itself lives at
# /uv.html and is untouched.
COPY campaign-app /usr/share/nginx/html
```

- [ ] **Step 2: Makefile assembly target** — append:

```makefile
viewer-image: frontend-build   ## stage SPA into the UV repo and build the nginx image
	rm -rf $(UV4_DIR)/campaign-app && cp -r frontend/dist $(UV4_DIR)/campaign-app
	docker build -f .docker/uv4-viewer.dockerfile -t 127.0.0.1:30500/uv4:dev $(UV4_DIR)
```

with `UV4_DIR ?= $(HOME)/universalviewer4` near the top (beside `CA_BUNDLE`). Run `make viewer-image`; then verify both apps are present:

```bash
docker run --rm --entrypoint sh 127.0.0.1:30500/uv4:dev -c 'ls /usr/share/nginx/html/index.html /usr/share/nginx/html/uv.html && grep -q "HTR Campaigns" /usr/share/nginx/html/index.html && echo SPA-OK'
```

Expected: both files listed, `SPA-OK`. Do not push; do not touch the running deployment (the live rollout is a separate, user-approved step).

- [ ] **Step 3: dagger BuildViewer** — Read `.dagger/viewer.go`. Extend `BuildViewer` to build the SPA and layer it in: add a bun stage (`oven/bun:1` container, `WithDirectory("/app", frontend source dir)`, `bun install && bun run build` with the optional caBundle wired like the existing node stage) and `WithDirectory` its `dist` output into the nginx container at `/usr/share/nginx/html` *after* the UV dist copy. Follow the existing function's structure/naming exactly. Verify: `cd .dagger && go build ./...` (full `dagger call build-viewer` is slow; run it only if the workstation has a warm dagger engine — otherwise compile-check suffices here, CI runs it).

- [ ] **Step 4: Chart viewer template** — in `charts/htrflow-batch/templates/viewer.yaml`, make the `/` redirect conditional: the nginx config's `location = /` redirect to `uv.html#?manifest=...` is rendered **only when** `.Values.viewer.defaultManifest` is non-empty; otherwise `/` falls through to static files (the SPA's `index.html`). Update the `values.yaml` comment: `defaultManifest: ""  # DEPRECATED: when set, / redirects to UV instead of the campaign browser`. Verify: `helm lint charts/htrflow-batch && helm template t charts/htrflow-batch | grep -A3 'location = /'` (no redirect in default render) and `helm template t charts/htrflow-batch --set viewer.defaultManifest=http://m | grep -c 'return 302'` ≥ 1.

- [ ] **Step 5: Docs + commit** — `docs/how-it-works/campaigns.md`: condensed spec (architecture diagram, campaign/pipeline formats, status table §6, drift guards, known issues) — link to the spec file for full detail. `docs/getting-started/campaigns.md`: operator walkthrough (create campaigns repo from the Task 12 scaffold, protect main, get an image digest, enable `reconciler.*` values, where the status page lives, how to read STALE). Add both to `zensical.toml` nav (mirror existing nav syntax), a pointer line in `docs/index.md`, and tick the status-page item in `docs/roadmap/open-items.md`. Verify docs build: `make docs-build` — expected: zero warnings. Then:

```bash
git add .docker .dagger charts Makefile docs zensical.toml
git commit -m "Viewer image serves the campaign browser at /; chart + docs for campaign GitOps"
```

---

## Final verification (after all tasks)

- [ ] `make test` — wrapper + reconciler suites green (expect ~60 wrapper + ~30 reconciler tests).
- [ ] `cd frontend && bun test src` — green.
- [ ] `make ci` (or the repo's aggregate dagger check) — green.
- [ ] `helm lint charts/htrflow-batch` — clean; default `helm template` renders no reconciler objects.
- [ ] `make docs-build` — zero warnings.
- [ ] `git log --oneline` — one commit per task, no AI mentions.
- [ ] Report to user: what landed, that `~/htr-campaigns` exists locally and needs a GitHub repo + branch protection + push (user does this), and that enabling the reconciler on the live cluster (`reconciler.enabled=true` + image push to the PoC registry) is a separate user-approved step.
