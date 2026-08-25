# Campaign Table Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the volume card grid with a rask-styled table per campaign, backed by new per-volume `updated` and `failure_log` fields from the reconciler.

**Architecture:** The reconciler's `done_volumes` already HEADs every manifest — it now returns id→mtime instead of a set, feeding a per-volume `updated`. Failure-log URLs are emitted for retry/needs-attention volumes, and the needs-attention path gains the log upload the retry path already had. The SPA re-renders campaigns as header + steps line + volume table, styled with rask's oklch token palette as plain CSS (light + dark).

**Tech Stack:** Python 3.13/pydantic/pytest (packages/reconciler), SvelteKit 5 runes + zod/vitest via bun (frontend/).

**Spec:** `docs/superpowers/specs/2026-08-25-campaign-table-redesign-design.md`

## Global Constraints

- Branch: `feat/campaign-browser-visibility` in `/home/morgan/htrflow-batch`.
- Python: `uv run --all-packages pytest -q` fully green; `uvx ruff check packages/reconciler` clean; imports at top of file.
- Frontend: `cd frontend && bun run test && bun run check && bun run build` all green.
- No new dependencies either side. Conventional commits, no co-author trailers. TDD.

---

### Task 1: done_volumes mtimes + per-volume `updated` (reconciler)

**Files:**
- Modify: `packages/reconciler/src/htrflow_reconciler/s3.py` (done_volumes, ~line 81)
- Modify: `packages/reconciler/src/htrflow_reconciler/status.py` (derive annotation only)
- Modify: `packages/reconciler/src/htrflow_reconciler/main.py` (volume entry)
- Test: `packages/reconciler/tests/test_bucket.py`, `packages/reconciler/tests/test_tick.py`

**Interfaces:**
- Produces: `Bucket.done_volumes(pipeline_id) -> dict[str, str]` (volume id → ISO-8601 mtime, UTC, `%Y-%m-%dT%H:%M:%SZ`); volume entries gain `"updated": str | None`.
- `derive`'s `done` parameter annotation widens to `Mapping[str, str] | AbstractSet[str]` (`from collections.abc import Mapping, Set as AbstractSet`) — membership (`in`) is all it uses; existing tests keep passing sets.

- [ ] **Step 1: Write the failing tests**

Read `packages/reconciler/tests/test_bucket.py` first and follow its stub
style for the boto3 client. Add:

```python
def test_done_volumes_returns_manifest_mtimes():
    # stub client: one volume prefix, head_object returns LastModified
    # datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
    ...existing stub pattern...
    done = bucket.done_volumes("demo-v1")
    assert done == {"R0000001": "2026-08-25T10:00:00Z"}
```

In `test_tick.py`, update `FakeBucket.done_volumes` to
`return {v: "2026-08-25T10:00:00Z" for v in self._done}` and add:

```python
def test_done_volume_carries_updated(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["updated"] == "2026-08-25T10:00:00Z"
    assert byid["R0000002"]["updated"] is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest packages/reconciler/tests/test_bucket.py packages/reconciler/tests/test_tick.py -q` → FAIL (set vs dict / KeyError updated)

- [ ] **Step 3: Implement**

`s3.py` `done_volumes` — HEAD directly (keep `exists()` untouched for other callers):

```python
    def done_volumes(self, pipeline_id: str) -> dict[str, str]:
        """Volume id -> manifest.json LastModified (ISO-8601 UTC) under
        ``<pipeline>/``. A manifest is the wrapper's completion marker; its
        mtime is when the volume finished publishing. Still HEAD-only.
        """
        done: dict[str, str] = {}
        paginator = self.c.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self.bucket, Prefix=f"{pipeline_id}/", Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []):
                vid = cp["Prefix"].rstrip("/").split("/", 1)[1]
                try:
                    head = self.c.head_object(
                        Bucket=self.bucket, Key=manifest_key(pipeline_id, vid)
                    )
                except ClientError as e:
                    code = str(e.response.get("Error", {}).get("Code", ""))
                    if code in _MISSING_CODES:
                        continue
                    raise
                done[vid] = head["LastModified"].astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
        return done
```

(add `from datetime import timezone` to the imports.)

`main.py`: line 285 orphans becomes `sorted(done.keys() - claimed[pid])`;
the volume entry gains, after `"attempts"`:

```python
                    "updated": done.get(v.id),
```

`status.py`: widen `derive`'s `done` annotation as in Interfaces; no logic change.

- [ ] **Step 4: Full suite green** — `uv run --all-packages pytest -q` and `uvx ruff check packages/reconciler`

- [ ] **Step 5: Commit** — `feat(reconciler): per-volume updated from manifest mtimes`

---

### Task 2: failure_log emission + needs-attention upload (reconciler)

**Files:**
- Modify: `packages/reconciler/src/htrflow_reconciler/main.py` (volume loop)
- Test: `packages/reconciler/tests/test_tick.py`

**Interfaces:**
- Produces: volume entries gain `"failure_log": str | None` — public URL for status retry/needs-attention, else None. Needs-attention volumes with a still-existing Job get their logs uploaded to `keys.failure_log_key(pid, v.id)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_needs_attention_uploads_log_and_links_it(tmp_path):
    name = job_name("demo-v1", "R0000002")
    jobs = {name: JobState(active=False, failed=True, exit_code=13)}
    bucket, cluster = FakeBucket(), FakeCluster(jobs=jobs)
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "needs-attention"
    assert byid["R0000002"]["failure_log"] == (
        "http://pub/htr-results/status/failures/demo-v1/R0000002.txt"
    )
    assert bucket.written["status/failures/demo-v1/R0000002.txt"] == "boom traceback"
    assert byid["loose"]["failure_log"] is None
```

- [ ] **Step 2: Run to verify failure** — `-k needs_attention_uploads` → FAIL (KeyError failure_log)

- [ ] **Step 3: Implement** in `main.py`'s volume loop:

After the `elif st == "pending" and pid not in blocked:` branch add:

```python
            elif st == "needs-attention" and job_name(pid, v.id) in jobs:
                # The retry path uploads logs before deleting the Job; an
                # exit-13 (or capped) volume otherwise reaches its terminal
                # state with no uploaded evidence. Idempotent overwrite.
                bucket.put_text(
                    keys.failure_log_key(pid, v.id),
                    cluster.failed_job_logs(job_name(pid, v.id)),
                )
```

and in the volume entry, after `"updated"`:

```python
                    "failure_log": (
                        f"{cfg.public_results_base.rstrip('/')}/{keys.failure_log_key(pid, v.id)}"
                        if st in ("retry", "needs-attention")
                        else None
                    ),
```

- [ ] **Step 4: Full suite green** — as Task 1
- [ ] **Step 5: Commit** — `feat(reconciler): failure log links; upload logs for needs-attention jobs`

---

### Task 3: frontend schema + shortDate helper

**Files:**
- Modify: `frontend/src/lib/status.ts`, `frontend/src/lib/derive.ts`
- Test: `frontend/src/lib/status.test.ts`, `frontend/src/lib/derive.test.ts`

**Interfaces:**
- Produces: `volumeEntrySchema` gains `updated` and `failure_log` (`z.string().nullable().default(null)`); `shortDate(iso: string | null, timeZone?: string): string | null` in derive.ts.

- [ ] **Step 1: Failing tests** — status.test.ts: old-shape doc still parses with both new fields null; new-shape doc round-trips values. derive.test.ts:

```typescript
describe("shortDate", () => {
  test("formats an ISO timestamp", () => {
    expect(shortDate("2026-08-25T14:32:00Z", "UTC")).toBe("25 Aug, 14:32");
  });
  test("null and junk stay null", () => {
    expect(shortDate(null)).toBeNull();
    expect(shortDate("not-a-date")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && bun run test`

- [ ] **Step 3: Implement**

```typescript
/** "25 Aug, 14:32" — viewer-local unless a timeZone is forced (tests use UTC). */
export function shortDate(
  iso: string | null,
  timeZone?: string,
): string | null {
  if (iso === null) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
}
```

(Confirm the exact separator `bun run test` reports — en-GB inserts `", "` between date and time; pin the assertion to the actual output.)

- [ ] **Step 4: Green + check** — `bun run test && bun run check`
- [ ] **Step 5: Commit** — `feat(frontend): updated/failure_log schema + shortDate helper`

---

### Task 4: table redesign + rask tokens (+page.svelte)

**Files:**
- Modify: `frontend/src/routes/+page.svelte` (full template + style rewrite; script's load/stale logic unchanged)

**Interfaces:** consumes `pagesLabel`, `shortDate`, `progress`, `viewerHref`, `isStale` and all schema fields. No downstream consumers.

- [ ] **Step 1: Rewrite the template.** Structure (keep `<script>` state/effects; drop the card grid entirely):

```svelte
<main>
  <header class="page">
    <h1>HTR Campaigns</h1>
    <div class="meta-block">
      {#if doc?.campaigns_repo_url}…repo link/code (as today, class "repo")…{/if}
      {#if doc}<p class="meta">generated {doc.generated_at}</p>{/if}
    </div>
  </header>
  …error / loading / stale banner / warnings as today…
  {#each doc.campaigns as c}
    <section class="campaign">
      <button class="camp" onclick={() => toggle(c.name)}>
        <span class="disclosure">{collapsed.has(c.name) ? "▸" : "▾"}</span>
        <span class="camp-name">{c.name}</span>
        {#if c.error !== null}<span class="chip needs-attention">broken</span>
        {:else}
          <span class="chip pipeline">{c.pipeline}</span>
          <progress max="100" value={progress(c.totals)}></progress>
          <span class="counts">
            {c.totals.done}/{c.totals.total} volumes
            {#if pagesLabel(c.totals) !== null}<span class="pages">· {pagesLabel(c.totals)}</span>{/if}
          </span>
        {/if}
      </button>
      {#if c.pipeline_steps?.length}<p class="steps">{c.pipeline_steps.join(" → ")}</p>{/if}
      {#if c.error !== null}<p class="notice error-row">{c.error}</p>{/if}
      {#if c.orphans.length > 0}<p class="notice warn-row">orphaned results (in bucket, not in git): {c.orphans.join(", ")}</p>{/if}
      {#if !collapsed.has(c.name) && c.error === null}
        <table class="volumes">
          <thead><tr><th></th><th>volume</th><th>status</th><th class="num">pages</th><th class="num">attempts</th><th>updated</th><th>links</th></tr></thead>
          <tbody>
            {#each c.volumes as v}
              <tr class:planned={v.status === "pending"}>
                <td class="thumb">{#if v.thumbnail !== null}<img src={v.thumbnail} alt="" loading="lazy" />{/if}</td>
                <td class="vid">{v.id}</td>
                <td><span class="status {v.status}"><span class="dot"></span>{v.status === "pending" ? "planned" : v.status}</span></td>
                <td class="num">{v.pages_total !== null || v.pages_done !== null ? `${v.pages_done ?? 0}/${v.pages_total ?? "?"}` : "—"}</td>
                <td class="num">{v.attempts > 0 ? v.attempts : "—"}</td>
                <td class="updated">{shortDate(v.updated) ?? "—"}</td>
                <td class="links">
                  {#if v.status === "done"}<a href={viewerHref(v)} target="_blank" rel="noopener">open</a>{/if}
                  <a class="secondary" href={v.source_manifest} target="_blank" rel="noopener">source</a>
                  {#if v.failure_log !== null}<a class="danger" href={v.failure_log} target="_blank" rel="noopener">log</a>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  {/each}
</main>
```

- [ ] **Step 2: Port the rask tokens** as the top of the `<style>` block — copy these values exactly:

```css
  main {
    --radius: 0.625rem;
    --background: oklch(0.985 0.004 80);
    --foreground: oklch(0.16 0.006 270);
    --card: oklch(0.993 0.002 80);
    --primary: oklch(0.37 0.19 250);
    --muted: oklch(0.955 0.006 260);
    --muted-foreground: oklch(0.45 0.012 260);
    --border: oklch(0.915 0.006 260);
    --success: oklch(0.65 0.2 145);
    --warning: oklch(0.75 0.18 75);
    --destructive: oklch(0.577 0.245 27.325);
  }
  @media (prefers-color-scheme: dark) {
    main {
      --background: oklch(0.13 0.006 270);
      --foreground: oklch(0.985 0 0);
      --card: oklch(0.17 0.008 270);
      --primary: oklch(0.68 0.16 250);
      --muted: oklch(0.22 0.008 270);
      --muted-foreground: oklch(0.65 0.01 260);
      --border: oklch(0.28 0.008 270);
    }
  }
```

Style rules to build from them (follow the mockup; use judgment for exact
values, keep it restrained): page/background/foreground on main; campaign
sections as cards (`--card`, `--border`, `--radius`, subtle padding);
table full-width, 13.5px, `border-collapse: collapse`, row separators
`1px solid var(--border)`, `tabular-nums` on `.num`; thumbnails 2.5rem
square, `object-fit: cover`, radius 4px; status dot (`0.5em` circle,
background per status: done→`--success`, running→`--primary`,
queued/retry→`--warning`, needs-attention/unreachable/unsupported→
`--destructive`, planned→`--muted-foreground`); planned rows at
`opacity: 0.65`; links in `--primary`, `.danger` in `--destructive`,
`.secondary` in `--muted-foreground`; `progress` accent-color
`--primary`; `.steps`/`.meta`/`.repo` in `--muted-foreground`. Remove all
now-unused card-grid CSS.

- [ ] **Step 3: Verify** — `cd frontend && bun run check && bun run test && bun run build` all green; then `bun run dev` is NOT needed (no component tests exist; live verification happens in Task 5).
- [ ] **Step 4: Commit** — `feat(frontend): rask-styled volume tables replace card grid`

---

### Task 5: rollout + live verification

**Files:** none (build/deploy)

- [ ] **Step 1:** From repo root: build+push `127.0.0.1:30500/htrflow-reconciler:dev4` (`.docker/htrflow-reconciler.dockerfile`).
- [ ] **Step 2:** `dagger call build-viewer --ca-bundle /etc/ssl/certs/ca-certificates.crt export --path /tmp/viewer3.tar`; `docker load`; tag the printed id `127.0.0.1:30500/uv4:dev3`; push.
- [ ] **Step 3:** `helm upgrade htr charts/htrflow-batch -n htr-batch --reuse-values --set reconciler.image=127.0.0.1:30500/htrflow-reconciler:dev4 --set viewer.image=127.0.0.1:30500/uv4:dev3` + `kubectl -n htr-batch rollout status deploy/uv4-viewer`.
- [ ] **Step 4:** Trigger a reconciler tick (create job from cronjob, wait). Verify `status/status.json`: every done volume has `updated` (ISO string); the `eng-modern-v1` campaign's volume (needs-attention) has a `failure_log` URL AND the log object exists (curl it, expect the tokenizer error text). Verify the served page: `curl http://127.0.0.1:30800/` is 200 and the new node chunk greps for `shortDate`-adjacent markers (e.g. "updated" header cell).
- [ ] **Step 5:** Clean up the verify job.

---

## Self-review notes

- Spec coverage: updated (T1), failure_log + upload fix (T2), schema (T3), table+tokens (T4), rollout (T5). Orphans set-op fix rides T1 (done becomes dict).
- The eng-modern-v1 needs-attention volume is real fixture data for T5's verification — its job may have TTL'd by rollout time; if `failure_log` upload finds no job, the URL is still emitted (status-based) and the log may 404: acceptable, note it in the report.
- shortDate separator pinned during T3 against actual ICU output.
