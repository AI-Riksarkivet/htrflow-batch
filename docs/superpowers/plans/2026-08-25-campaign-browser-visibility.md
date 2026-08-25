# Campaign Browser Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the campaigns repo URL, per-volume/per-campaign page counts, and planned (pending) pipelines+volumes in the campaign browser.

**Architecture:** The reconciler enriches `status.json` (repo URL, `pipeline_steps`, real `pages_total`, page totals per campaign); the SvelteKit SPA renders the new fields (header repo link, `D/T volumes · d/t pages`, expanded-by-default sections, planned-card styling, pipeline steps line). All schema changes are nullable/optional both ways.

**Tech Stack:** Python 3.13 + pydantic (uv workspace `packages/reconciler`, pytest), SvelteKit 5 runes + zod (bun, `frontend/`).

**Spec:** `docs/superpowers/specs/2026-08-25-campaign-browser-visibility-design.md`

## Global Constraints

- Branch: `feat/campaign-browser-visibility` in `/home/morgan/htrflow-batch`.
- Python tests: `uv run --all-packages pytest -q` from repo root (or `uv run pytest packages/reconciler/tests/<file> -q` for one file).
- Frontend: `cd frontend && bun run test` (vitest) and `bun run check`.
- No new dependencies on either side.
- Commit messages: conventional (`feat:`/`test:`/`docs:`), no co-author trailers.
- Do not modify `pipelines.yaml` chart templates, wrapper, guards, or job submission logic.

---

### Task 1: Pipeline step summaries (reconciler)

**Files:**
- Modify: `packages/reconciler/src/htrflow_reconciler/parse.py` (add function at end)
- Test: `packages/reconciler/tests/test_parse.py` (append)

**Interfaces:**
- Produces: `step_summaries(steps_yaml: str) -> list[str]` — used by Task 3 in `main.py`.

- [ ] **Step 1: Write the failing tests** (append to `packages/reconciler/tests/test_parse.py`)

```python
from htrflow_reconciler.parse import step_summaries


def test_step_summaries_full_form():
    yaml_text = """steps:
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
"""
    assert step_summaries(yaml_text) == [
        "Segmentation: yolo (Riksarkivet/yolov9-regions-1)",
        "TextRecognition: TrOCR (Riksarkivet/trocr-base-handwritten-hist-swe-2)",
    ]


def test_step_summaries_fallbacks():
    yaml_text = """steps:
  - step: Export
  - step: Segmentation
    settings:
      model: yolo
"""
    assert step_summaries(yaml_text) == ["Export", "Segmentation: yolo"]


def test_step_summaries_junk_is_empty():
    assert step_summaries("steps: notalist") == []
    assert step_summaries(": not yaml [") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/reconciler/tests/test_parse.py -q -k step_summaries`
Expected: FAIL with `ImportError: cannot import name 'step_summaries'`

- [ ] **Step 3: Implement** (append to `packages/reconciler/src/htrflow_reconciler/parse.py`)

```python
def step_summaries(steps_yaml: str) -> list[str]:
    """One display line per pipeline step: ``Step: model (weights)``.

    Display-only derivation for status.json — total over junk: anything
    unparseable yields [] rather than failing the tick.
    """
    try:
        doc = yaml.safe_load(steps_yaml)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("steps"), list):
        return []
    out: list[str] = []
    for step in doc["steps"]:
        if not isinstance(step, dict) or not step.get("step"):
            continue
        label = str(step["step"])
        settings = step.get("settings") or {}
        if isinstance(settings, dict) and settings.get("model"):
            label += f": {settings['model']}"
            ms = settings.get("model_settings") or {}
            if isinstance(ms, dict) and ms.get("model"):
                label += f" ({ms['model']})"
        out.append(label)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/reconciler/tests/test_parse.py -q`
Expected: PASS (all, including pre-existing)

- [ ] **Step 5: Commit**

```bash
git add packages/reconciler/src/htrflow_reconciler/parse.py packages/reconciler/tests/test_parse.py
git commit -m "feat(reconciler): derive pipeline step summaries for status.json"
```

---

### Task 2: Canvas count in validation verdict (reconciler)

**Files:**
- Modify: `packages/reconciler/src/htrflow_reconciler/main.py` (`_validate` at line ~153, add `_page_count` helper next to `_thumbnail`)
- Test: `packages/reconciler/tests/test_tick.py` (append)

**Interfaces:**
- Consumes: existing `_first_canvas`/`_as_list` helpers in `main.py`.
- Produces: `_validate` verdicts now carry `"page_count": int | None`; `_page_count(doc) -> int | None` module-private. Task 3 reads `verdict.get("page_count")` and cached `validation` entries via `.get("page_count")`.

- [ ] **Step 1: Write the failing test** (append to `packages/reconciler/tests/test_tick.py`)

```python
def _p3_manifest(n: int) -> dict:
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "type": "Manifest",
        "items": [
            {"type": "Canvas", "items": [{"items": [{"body": {"id": f"http://x/{i}.jpg"}}]}]}
            for i in range(n)
        ],
    }


def test_validate_caches_page_count(tmp_path):
    bucket, cluster = FakeBucket(), FakeCluster()
    fetched = {}

    def fetch(url):
        fetched[url] = fetched.get(url, 0) + 1
        return _p3_manifest(3)

    tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch)
    validation = bucket.written["status/validation.json"]
    ref = "https://lbiiif.riksarkivet.se/arkis!R0000002/manifest"
    assert validation[ref]["page_count"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/reconciler/tests/test_tick.py -q -k page_count`
Expected: FAIL with `KeyError: 'page_count'`
(If the URL key assert fails first, print `validation` keys and match the
form-1 expansion used by `parse_campaign` — the test must use the exact key.)

- [ ] **Step 3: Implement** — in `main.py`, add after `_thumbnail`:

```python
def _page_count(doc: object) -> int | None:
    """Canvas count for P3 (items) and P2 (sequences[0].canvases) manifests."""
    if not isinstance(doc, dict):
        return None
    canvases = _as_list(doc.get("items"))
    if not canvases:
        seqs = _as_list(doc.get("sequences"))
        first_seq = seqs[0] if seqs else None
        if isinstance(first_seq, dict):
            canvases = _as_list(first_seq.get("canvases"))
    return len(canvases) or None
```

and extend `_validate`'s verdict dicts (both branches):

```python
    if doc is None:
        return {"format": "unreachable", "thumbnail": None, "page_count": None}
    verdict = {
        "format": _classify(doc),
        "thumbnail": _thumbnail(doc),
        "page_count": _page_count(doc),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/reconciler/tests/test_tick.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/reconciler/src/htrflow_reconciler/main.py packages/reconciler/tests/test_tick.py
git commit -m "feat(reconciler): capture manifest page counts in validation cache"
```

---

### Task 3: Emit repo URL, pipeline_steps, pages_total, page totals (reconciler)

**Files:**
- Modify: `packages/reconciler/src/htrflow_reconciler/jobspec.py` (ReconcilerConfig, line ~10)
- Modify: `packages/reconciler/src/htrflow_reconciler/__main__.py` (run(), line ~45)
- Modify: `packages/reconciler/src/htrflow_reconciler/main.py` (tick(), campaign loop)
- Test: `packages/reconciler/tests/test_tick.py` (append + update one assert)

**Interfaces:**
- Consumes: `step_summaries` (Task 1), `page_count` verdict key (Task 2).
- Produces: `status.json` shape consumed by Task 4:
  top-level `campaigns_repo_url: str`; per-campaign `pipeline_steps: list[str] | None`
  and `totals: {done, total, pages_done: int|None, pages_total: int|None}`;
  per-volume `pages_total: int | None`.

- [ ] **Step 1: Write the failing test** (append to `packages/reconciler/tests/test_tick.py`)

```python
def test_status_carries_repo_url_steps_and_page_totals(tmp_path):
    cfg = ReconcilerConfig(
        public_results_base="http://pub/htr-results",
        window=20,
        campaigns_repo_url="git://example/campaigns",
    )
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()

    def fetch(url):
        return _p3_manifest(4)

    doc = tick(_repo(tmp_path), bucket, cluster, cfg, NOW, fetch_json=fetch)
    assert doc["campaigns_repo_url"] == "git://example/campaigns"
    camp = doc["campaigns"][0]
    assert camp["pipeline_steps"] == ["Segmentation"]
    byid = {v["id"]: v for v in camp["volumes"]}
    assert byid["loose"]["pages_total"] == 1          # len(images)
    assert byid["R0000002"]["pages_total"] == 4       # canvas count from fetch
    assert byid["R0000001"]["pages_total"] == 638     # done fallback = pages_done
    assert camp["totals"]["pages_total"] == 638 + 4 + 1
    assert camp["totals"]["pages_done"] == 638


def test_page_totals_null_when_unknown(tmp_path):
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)  # no fetch_json
    camp = doc["campaigns"][0]
    byid = {v["id"]: v for v in camp["volumes"]}
    assert byid["R0000002"]["pages_total"] is None
    assert byid["loose"]["pages_total"] == 1
    assert camp["totals"]["pages_total"] == 1
    assert camp["totals"]["pages_done"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/reconciler/tests/test_tick.py -q -k "repo_url or null_when"`
Expected: FAIL (`ValidationError` for unknown `campaigns_repo_url` kwarg first)

- [ ] **Step 3: Implement**

`jobspec.py` — add to `ReconcilerConfig`:

```python
    campaigns_repo_url: str = ""
```

`__main__.py` — thread it in `run()`'s `ReconcilerConfig(...)` call:

```python
        campaigns_repo_url=settings.campaigns_repo_url,
```

`main.py` — import `step_summaries` from `.parse`; in `tick()`:

1. top of doc:

```python
    doc: dict[str, Any] = {
        "generated_at": now_iso,
        "tick_seconds": TICK_SECONDS,
        "campaigns_repo_url": cfg.campaigns_repo_url,
        "warnings": warnings,
        "campaigns": [],
    }
```

2. campaign entry gains (after `"pipeline": ...`):

```python
            "pipeline_steps": (
                step_summaries(pipelines[camp.pipeline_id].steps_yaml)
                if camp.pipeline_id in pipelines
                else None
            ),
```

and totals:

```python
            "totals": {"done": 0, "total": len(camp.volumes),
                       "pages_done": None, "pages_total": None},
```

3. in the volume loop, compute pages (place right before `entry["volumes"].append`; note the existing `pages_done` expression moves into a variable):

```python
            pages_done = (
                bucket.count_pages(pid, v.id) if st in ("done", "running") else None
            )
            if v.images:
                pages_total: int | None = len(v.images)
            else:
                cached_v = validation.get(v.manifest_url) if v.manifest_url else None
                pages_total = cached_v.get("page_count") if cached_v else None
            if pages_total is None and st == "done":
                pages_total = pages_done
```

use `"pages_done": pages_done, "pages_total": pages_total,` in the appended
volume dict (replacing the inline `bucket.count_pages(...)` expression and
the hardcoded `"pages_total": None`).

4. after the volume loop (before the `if pid not in blocked and lane:` line), aggregate:

```python
        known_totals = [
            v["pages_total"] for v in entry["volumes"] if v["pages_total"] is not None
        ]
        known_done = [
            v["pages_done"] for v in entry["volumes"] if v["pages_done"] is not None
        ]
        entry["totals"]["pages_total"] = sum(known_totals) if known_totals else None
        entry["totals"]["pages_done"] = sum(known_done) if known_done else None
```

5. update the pre-existing assert in `test_tick_submits_missing_and_writes_status`:

```python
    assert camp["totals"] == {
        "done": 1, "total": 3, "pages_done": 638, "pages_total": 638 + 1
    }
```

(R0000001 done → fallback 638; loose → 1; R0000002 unknown without fetch_json.)

- [ ] **Step 4: Run the full reconciler suite**

Run: `uv run --all-packages pytest -q`
Expected: PASS (all packages — wrapper suite must stay green too)

- [ ] **Step 5: Commit**

```bash
git add packages/reconciler/src/htrflow_reconciler/jobspec.py \
        packages/reconciler/src/htrflow_reconciler/__main__.py \
        packages/reconciler/src/htrflow_reconciler/main.py \
        packages/reconciler/tests/test_tick.py
git commit -m "feat(reconciler): status.json repo url, pipeline steps, page totals"
```

---

### Task 4: Frontend schema + derive helpers

**Files:**
- Modify: `frontend/src/lib/status.ts`
- Modify: `frontend/src/lib/derive.ts`
- Test: `frontend/src/lib/derive.test.ts` (append)
- Create: `frontend/src/lib/status.test.ts`

**Interfaces:**
- Consumes: Task 3's status.json shape.
- Produces: `pagesLabel(totals: {pages_done: number | null; pages_total: number | null}): string | null`; schema types `CampaignEntry.pipeline_steps: string[] | null`, `StatusDoc.campaigns_repo_url: string | null`, `totals.pages_done/pages_total: number | null`. Task 5 renders these.

- [ ] **Step 1: Write the failing tests**

`frontend/src/lib/status.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { statusDocSchema } from "./status.js";

const volume = {
  id: "v1",
  status: "pending",
  attempts: 0,
  pages_done: null,
  pages_total: null,
  error: null,
  viewer_manifest: null,
  source_manifest: "http://s/m.json",
  thumbnail: null,
};

const oldDoc = {
  generated_at: "2026-08-25T10:00:00Z",
  tick_seconds: 300,
  warnings: [],
  campaigns: [
    {
      name: "c",
      pipeline: "p",
      error: null,
      totals: { done: 0, total: 1 },
      volumes: [volume],
    },
  ],
};

describe("statusDocSchema", () => {
  it("parses a pre-visibility document (missing new fields)", () => {
    const doc = statusDocSchema.parse(oldDoc);
    expect(doc.campaigns_repo_url).toBeNull();
    expect(doc.campaigns[0].pipeline_steps).toBeNull();
    expect(doc.campaigns[0].totals.pages_total).toBeNull();
  });

  it("parses a new document with the visibility fields", () => {
    const doc = statusDocSchema.parse({
      ...oldDoc,
      campaigns_repo_url: "git://example/campaigns",
      campaigns: [
        {
          ...oldDoc.campaigns[0],
          pipeline_steps: ["Segmentation: yolo (weights)"],
          totals: { done: 0, total: 1, pages_done: 0, pages_total: 2 },
        },
      ],
    });
    expect(doc.campaigns_repo_url).toBe("git://example/campaigns");
    expect(doc.campaigns[0].totals.pages_total).toBe(2);
  });
});
```

Append to `frontend/src/lib/derive.test.ts`:

```typescript
import { pagesLabel } from "./derive.js";

describe("pagesLabel", () => {
  it("renders d/t when total known", () => {
    expect(pagesLabel({ pages_done: 1, pages_total: 2 })).toBe("1/2 pages");
  });
  it("treats null done as 0", () => {
    expect(pagesLabel({ pages_done: null, pages_total: 2 })).toBe("0/2 pages");
  });
  it("hides when total unknown", () => {
    expect(pagesLabel({ pages_done: 3, pages_total: null })).toBeNull();
  });
});
```

(Match the existing import style in `derive.test.ts` — merge the `pagesLabel`
import into the existing `./derive.js` import line if one exists.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && bun run test`
Expected: FAIL (`pagesLabel` not exported; schema strips/lacks new fields)

- [ ] **Step 3: Implement**

`status.ts` — extend schemas:

```typescript
export const campaignEntrySchema = z.object({
  name: z.string(),
  pipeline: z.string().nullable(),
  pipeline_steps: z.array(z.string()).nullable().default(null),
  error: z.string().nullable(),
  totals: z.object({
    done: z.number(),
    total: z.number(),
    pages_done: z.number().nullable().default(null),
    pages_total: z.number().nullable().default(null),
  }),
  volumes: z.array(volumeEntrySchema),
  orphans: z.array(z.string()).default([]),
});

export const statusDocSchema = z.object({
  generated_at: z.string(),
  tick_seconds: z.number(),
  campaigns_repo_url: z.string().nullable().default(null),
  warnings: z.array(z.string()),
  campaigns: z.array(campaignEntrySchema),
});
```

`derive.ts` — append:

```typescript
export function pagesLabel(totals: {
  pages_done: number | null;
  pages_total: number | null;
}): string | null {
  if (totals.pages_total === null) return null;
  return `${totals.pages_done ?? 0}/${totals.pages_total} pages`;
}
```

- [ ] **Step 4: Run tests + typecheck**

Run: `cd frontend && bun run test && bun run check`
Expected: PASS / 0 errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/status.ts frontend/src/lib/derive.ts \
        frontend/src/lib/derive.test.ts frontend/src/lib/status.test.ts
git commit -m "feat(frontend): schema + helpers for repo url, page counts, steps"
```

---

### Task 5: Frontend page — repo link, expanded default, planned style, steps line

**Files:**
- Modify: `frontend/src/routes/+page.svelte`

**Interfaces:**
- Consumes: `pagesLabel` (Task 4), schema fields from Task 4.
- Produces: final UI; no downstream consumers.

- [ ] **Step 1: Implement the template changes**

In the `<script>` block: import `pagesLabel` alongside the existing derive
imports, and replace the single-open state with a collapsed set:

```typescript
  import { isStale, pagesLabel, progress, viewerHref } from "$lib/derive.js";
  // …
  let collapsed = $state<Set<string>>(new Set()); // expanded by default
  function toggle(name: string): void {
    const next = new Set(collapsed);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    collapsed = next;
  }
```

Header (after `<h1>`): repo reference, link only when browsable:

```svelte
  {#if doc !== null && doc.campaigns_repo_url !== null}
    <p class="repo">
      campaigns repo:
      {#if doc.campaigns_repo_url.startsWith("http")}
        <a href={doc.campaigns_repo_url} target="_blank" rel="noopener">
          {doc.campaigns_repo_url}
        </a>
      {:else}
        <code>{doc.campaigns_repo_url}</code>
      {/if}
    </p>
  {/if}
```

Campaign button: `onclick={() => toggle(c.name)}`; after the volume count add
the pages segment:

```svelte
            {c.totals.done}/{c.totals.total} volumes
            {#if pagesLabel(c.totals) !== null}
              <span class="pages">· {pagesLabel(c.totals)}</span>
            {/if}
```

Pipeline steps line (right after the `</button>`, before the error/orphans
paragraphs):

```svelte
        {#if c.pipeline_steps !== null && c.pipeline_steps.length > 0}
          <p class="steps">{c.pipeline_steps.join(" → ")}</p>
        {/if}
```

Grid gate becomes collapse-set-driven: `{#if !collapsed.has(c.name) && c.error === null}`.

Volume card: planned styling + display rename (status value untouched):

```svelte
              <a
                class="card"
                class:planned={v.status === "pending"}
                href={viewerHref(v)}
                target="_blank"
                rel="noopener"
              >
                {#if v.thumbnail !== null}
                  <img src={v.thumbnail} alt="" loading="lazy" />
                {/if}
                <span class="chip {v.status}">
                  {v.status === "pending" ? "planned" : v.status}
                </span>
                <strong>{v.id}</strong>
                {#if v.pages_total !== null || v.pages_done !== null}
                  <small>{pagesLabel(v) ?? `${v.pages_done} pages`}</small>
                {/if}
                {#if v.attempts > 0}<small>attempts: {v.attempts}</small>{/if}
              </a>
```

Styles (append):

```css
  .repo {
    color: #6b7280;
    font-size: 0.85rem;
  }
  .steps {
    color: #6b7280;
    font-size: 0.8rem;
    margin: 0 0 0.5rem;
  }
  .pages {
    font-weight: 400;
    color: #6b7280;
  }
  .card.planned {
    border-style: dashed;
  }
```

- [ ] **Step 2: Typecheck + test + build**

Run: `cd frontend && bun run check && bun run test && bun run build`
Expected: 0 errors, tests pass, build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/+page.svelte
git commit -m "feat(frontend): repo link, page counts, planned cards, steps line"
```

---

### Task 6: Rollout to the local cluster + live verification

**Files:** none in-repo (build/deploy only)

**Interfaces:**
- Consumes: everything above; the local registry `127.0.0.1:30500`, release `htr` in ns `htr-batch`.

- [ ] **Step 1: Build + push the reconciler image under a NEW tag**

```bash
cd /home/morgan/htrflow-batch
docker build -f .docker/htrflow-reconciler.dockerfile -t 127.0.0.1:30500/htrflow-reconciler:dev2 .
docker push 127.0.0.1:30500/htrflow-reconciler:dev2
```

- [ ] **Step 2: Build + push the viewer image under a NEW tag**

```bash
dagger call build-viewer --ca-bundle /etc/ssl/certs/ca-certificates.crt \
  export --path /tmp/viewer2.tar
docker load -i /tmp/viewer2.tar   # note the image id it prints
docker tag <IMAGE_ID> 127.0.0.1:30500/uv4:dev2
docker push 127.0.0.1:30500/uv4:dev2
```

- [ ] **Step 3: Upgrade the release to the new images**

```bash
helm upgrade htr charts/htrflow-batch -n htr-batch --reuse-values \
  --set reconciler.image=127.0.0.1:30500/htrflow-reconciler:dev2 \
  --set viewer.image=127.0.0.1:30500/uv4:dev2
kubectl -n htr-batch rollout status deploy/uv4-viewer --timeout=120s
```

- [ ] **Step 4: Trigger a tick and verify status.json**

```bash
kubectl -n htr-batch create job --from=cronjob/htr-reconciler htr-reconciler-verify \
  && kubectl -n htr-batch wait --for=condition=complete job/htr-reconciler-verify --timeout=120s
curl -s http://127.0.0.1:30900/htr-results/status/status.json | python3 -m json.tool | head -40
```

Expected: `campaigns_repo_url` present, campaign has `pipeline_steps` (3
entries for local-v2-gpu), volume `htr-demo-examples` has `pages_total: 2`.

- [ ] **Step 5: Verify the page in a real browser**

Morgan browses `http://localhost:30800/` (SSH forward). Expected: repo URL in
header, `0/1 volumes · 0/2 pages` (or `1/1 · 2/2` if the GPU job finished),
campaign expanded without a click, pending cards dashed + labeled `planned`,
steps line under the campaign name.

- [ ] **Step 6: Clean up the verify job**

```bash
kubectl -n htr-batch delete job htr-reconciler-verify --ignore-not-found
```

---

## Self-review notes

- Spec coverage: repo URL (T3/T5), page counts (T2/T3/T4/T5), planned
  visibility (T5), schema compatibility (T4 tests both directions), rollout
  new-tags rule (T6). No gaps found.
- `_p3_manifest` helper is defined in Task 2's test and reused in Task 3's —
  tasks share `test_tick.py`, executed in order.
- `pagesLabel` accepts any `{pages_done, pages_total}` object, so volume
  entries pass through it unchanged in Task 5.
