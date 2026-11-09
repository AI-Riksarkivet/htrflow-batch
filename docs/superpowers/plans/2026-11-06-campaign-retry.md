# Campaign Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `htrflow-campaigns retry <campaign> --namespace <ns>` starts a new campaign Job, directly in the cluster, holding only the original campaign's unfinished volumes, with the original Job's settings.

**Architecture:** A new module `retry.py` in the converter finds unfinished volumes (campaign ConfigMap's `volumes.txt` minus volumes with `manifest.json`, read through the results proxy like `export`), classifies each from its run log, and builds the retry ConfigMap + Job from a copy of the original Job. `apply` gains two stamps: the rendered Job spec on each campaign ConfigMap (so a retry works after the Job's TTL) and a mark on the apply Lease (so `retry` can tell the last prune knew about retries). `Cluster.prune` keeps a retry while its original campaign is rendered.

**Tech Stack:** Python 3.12, kubernetes client, httpx, pytest, uv workspace (`uv run --no-sync`), ruff, ty.

**Spec:** `docs/superpowers/specs/2026-11-06-campaign-retry-design.md`

## Global Constraints

- Same pipeline, same image digest, same Job settings as the original; only name, volume list and their counts change.
- The original campaign's Job, ConfigMap, status ConfigMap and results are never written.
- "Done" means `<namespace>/<pipeline>/<volume>/manifest.json` exists. Nothing else.
- Run log key: `<namespace>/status/logs/<pipeline>/<volume>.txt`; terminal lines contain `permanent failure in ` or `transient failure in `.
- Permanent failures skipped unless `--include-permanent`; every original volume is done, retried, or named as skipped.
- Names: `<root>-retry-<n>`, the whole name within `render.split_stem` budget (`_SPLIT_STEM`), parts `-part<i>` via `render.split`.
- New label/annotation keys under `htrflow.riksarkivet.se/`: label `retry-of`, annotation `job-spec`, Lease annotation `prunes-retries`.
- Exit codes: 0 started, dry run, or nothing to retry; 1 refused.
- No names of people in docs or code comments; internal system names stay out of general docs.
- Before any push: `make ci` (ty + locked ruff over the whole repo + tests).
- End-to-end test only on the local k3s test cluster (ns `htr-batch`), volumes only `images:` URLs to the `Riksarkivet/htr_demo` Space's `.gradio_cache/examples/` pinned to a commit. No IIIF. Never prune the namespace from a partial repo.

## Review Focus

1. **A split original (several `-partN` Jobs)**: every part's volumes are considered, the retry's `retry-of` names one part, prune keeps it. Test in Task 4 and Task 2.
2. **A volume id with a `/` or a leading `.`** (unsafe as a key segment): it is not fetched and is counted as retry with reason "id not readable as a results path", never silently dropped. Test in Task 3.
3. **The results proxy answering 401 mid-run / 403 / 5xx**: `Results` re-logs in on 401; a 403 or 5xx on any volume stops the command with exit 1 before anything is created, not a retry built from partial knowledge. Test in Task 3.
4. **An older converter applies between retry and the next prune**: `retry` refuses when the Lease lacks `prunes-retries`, and a retry passing the Lease through never sets the mark itself. Test in Task 1 and Task 4.
5. **Retrying a retry**: names the root, numbers on from the highest existing retry, and copies the retry's own stamped spec. Test in Task 4.

---

### Task 1: Lease mark and Job-spec stamp in `apply`

**Files:**
- Modify: `packages/converter/src/htrflow_converter/cluster.py` (`Cluster.lease`, new `Cluster.lease_marks`)
- Modify: `packages/converter/src/htrflow_converter/render.py` (constants)
- Modify: `packages/converter/src/htrflow_converter/cli.py` (`_apply`: lease call near line 1326, stamp loop near line 1424)
- Test: `packages/converter/tests/test_apply.py`

**Interfaces:**
- Produces: `render.RETRY_OF_LABEL = "htrflow.riksarkivet.se/retry-of"`, `render.JOB_SPEC_ANNOTATION = "htrflow.riksarkivet.se/job-spec"`, `cluster.PRUNES_RETRIES = "htrflow.riksarkivet.se/prunes-retries"`.
- Produces: `Cluster.lease(marks: dict[str, str] | None = None)` — the written Lease's `metadata.annotations` is exactly `marks or {}`.
- Produces: `Cluster.lease_marks() -> dict[str, str]` — the live Lease's annotations, `{}` when there is no Lease.

- [ ] **Step 1: Write the failing tests**

```python
def test_apply_marks_the_lease_as_pruning_retries(tmp_path, cluster):
    assert cli.main(["apply", str(_repo(tmp_path)), "--out", str(tmp_path / "r")]) == 0
    lease = cluster.leases[cluster_mod.LEASE]
    assert lease["metadata"]["annotations"] == {cluster_mod.PRUNES_RETRIES: "1"}

def test_apply_stamps_the_rendered_job_on_its_campaign_configmap(tmp_path, cluster):
    out = tmp_path / "r"
    assert cli.main(["apply", str(_repo(tmp_path)), "--out", str(out)]) == 0
    for job in (o for o in _objects(out / "campaigns") if o["kind"] == "Job"):
        cm = cluster.find("ConfigMap", f"campaign-{job['metadata']['name']}")
        assert json.loads(cm["metadata"]["annotations"][render.JOB_SPEC_ANNOTATION]) == job
```

(`_objects` = `cli._objects`; compare against the rendered Job file, not the live one.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_apply.py -k "marks_the_lease or stamps_the_rendered_job" -v`
Expected: FAIL (`KeyError: 'annotations'` / missing `job-spec`).

- [ ] **Step 3: Implement**

`Cluster.lease(marks=None)` puts `marks or {}` in the body's `metadata.annotations`. `Cluster.lease_marks()` reads via `self._lease("read")`. In `_apply`: `cluster.lease({PRUNES_RETRIES: "1"})`; where campaign ConfigMap annotations get `prov`, also set `ann[render.JOB_SPEC_ANNOTATION] = json.dumps(job, sort_keys=True, separators=(",", ":"))`, `job` being the rendered Job of the same campaign name from `campaigns`. The rendered files in `rendered/` are not changed (B78: render stays pure).

- [ ] **Step 4: Run the converter suite**

Run: `uv run --no-sync pytest packages/converter/tests -q`
Expected: all pass (update golden/expectation tests only where they assert the exact ConfigMap annotations or Lease body, and say so in the commit).

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(converter): apply marks its Lease and stamps the rendered Job on the campaign record"
```

### Task 2: Prune keeps a retry with its original

**Files:**
- Modify: `packages/converter/src/htrflow_converter/cluster.py` (`Cluster.prune`, `_kept_status`)
- Test: `packages/converter/tests/test_apply.py`

**Interfaces:**
- Consumes: `render.RETRY_OF_LABEL` (Task 1).
- Produces: `Cluster.prune(rendered)` unchanged signature; keeps every labelled Job/ConfigMap whose `retry-of` label is `X` while `("ConfigMap", f"campaign-{X}")` is in `rendered`, and the status ConfigMap of each such kept campaign ConfigMap.

- [ ] **Step 1: Write the failing tests**

```python
def test_prune_keeps_a_retry_while_its_original_is_rendered(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "r"
    original = _first_campaign_job_name(repo)        # helper: name of the first rendered campaign Job
    retry = {CAMPAIGN_LABEL: "x-retry-1", render.RETRY_OF_LABEL: original}
    cluster.live += [_obj("Job", "x-retry-1", retry), _obj("ConfigMap", "campaign-x-retry-1", retry),
                     _obj("ConfigMap", "campaign-x-retry-1-status", retry)]
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 0
    assert {"x-retry-1", "campaign-x-retry-1", "campaign-x-retry-1-status"} <= _live_names(cluster)

def test_prune_takes_a_retry_with_its_original(tmp_path, cluster):
    # same seed; then delete the original's campaign file from the repo and apply --prune
    ...
    assert not {"x-retry-1", "campaign-x-retry-1", "campaign-x-retry-1-status"} & _live_names(cluster)
```

`_obj` is the file's existing helper that builds `{"kind", "metadata": {"name", "labels"}}`; give the seeded labels the converter's `managed-by` label too, as other prune tests do.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_apply.py -k "keeps_a_retry or takes_a_retry" -v`
Expected: first FAILS (retry pruned), second passes already (fine).

- [ ] **Step 3: Implement**

In `prune`, list both kinds first, then `kept = rendered | {(kind, name) of listed objects whose RETRY_OF_LABEL value v has ("ConfigMap", f"campaign-{v}") in rendered}`; run the existing delete loop and `_kept_status` against `kept`. A listing error for one kind keeps today's behaviour (problem recorded, that kind skipped).

- [ ] **Step 4: Run the converter suite** — `uv run --no-sync pytest packages/converter/tests -q`, all pass.

- [ ] **Step 5: Commit** — `git commit -am "feat(converter): prune keeps a retry campaign while its original is in git"`

### Task 3: Find and classify unfinished volumes

**Files:**
- Create: `packages/converter/src/htrflow_converter/retry.py`
- Test: `packages/converter/tests/test_retry_find.py`

**Interfaces:**
- Consumes: `export_http.Results` (`get(key) -> bytes | None`, raises `SessionLost`, `Forbidden`, `ResultsUnavailable`); `models.parse_source_line`.
- Produces:
  - `@dataclass(frozen=True) class Outcome: id: str; line: str; state: Literal["done", "retry", "permanent"]; reason: str` (`line` = the original `volumes.txt` line, unchanged).
  - `classify_log(text: str | None) -> tuple[bool, str]` — `(permanent, reason)`.
  - `reason_group(reason: str) -> str`.
  - `find_unfinished(results: Results, namespace: str, pipeline: str, lines: list[str], workers: int = 16) -> list[Outcome]` — one `Outcome` per line, same order.

- [ ] **Step 1: Write the failing tests**

```python
LOG_T = "2026-11-01 12:00:01 ERROR transient failure in fetch on the last attempt: [Errno 28] No space left on device: '/work/p/0003.jpg' — no retry follows: this was the index's last attempt\n"
LOG_P = "… ERROR permanent failure in manifest: canvas 4 has no image — a retry changes nothing — fix the campaign or pipeline file\n"

def test_classify_log():
    assert classify_log(None) == (False, "no run log: the volume never started")
    assert classify_log("INFO page 1 done\n") == (False, "no failure line in the run log")
    assert classify_log(LOG_T) == (False, "fetch: [Errno 28] No space left on device: '/work/p/0003.jpg'")
    assert classify_log(LOG_P) == (True, "manifest: canvas 4 has no image")
    assert classify_log(LOG_T + LOG_P)[0] is True          # the LAST failure line decides
    assert classify_log(LOG_P + LOG_T)[0] is False

def test_reason_group_folds_quoted_paths_and_caps_length():
    assert reason_group("fetch: [Errno 28] No space left on device: '/work/p/0003.jpg'") == \
           "fetch: [Errno 28] No space left on device: '…'"
    assert len(reason_group("x" * 500)) == 100

def test_find_unfinished(tmp_path):
    # httpx.MockTransport server (pattern: tests/test_export_cli.py Server):
    # A has manifest.json; B has no manifest and LOG_T; C has no manifest and LOG_P; D has neither
    outs = find_unfinished(results, "ns", "p1", ["A\timages:u", "B\timages:u", "C\timages:u", "D\timages:u"])
    assert [(o.id, o.state) for o in outs] == [("A", "done"), ("B", "retry"), ("C", "permanent"), ("D", "retry")]
    assert outs[1].line == "B\timages:u"

def test_an_unsafe_volume_id_is_retried_not_fetched(): ...
    # id "a/b": no request made for it; Outcome(state="retry", reason="id not readable as a results path")

def test_a_403_or_5xx_stops_the_search(): ...
    # Forbidden / ResultsUnavailable propagate out of find_unfinished
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_retry_find.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** in `retry.py`. `classify_log`: last line containing `permanent failure in ` or `transient failure in `; reason = text after `failure in `, minus ` on the last attempt`, cut at ` — `. `reason_group`: replace `'…'`/`"…"` quoted spans with `'…'`, then cap at 100 characters. `find_unfinished`: a `ThreadPoolExecutor(max_workers=workers)` over the volumes; the log is fetched only when `manifest.json` is absent; safety check = `export._safe_segment` (import it, don't copy it).

- [ ] **Step 4: Run** — `uv run --no-sync pytest packages/converter/tests/test_retry_find.py -v`, PASS.

- [ ] **Step 5: Commit** — `git add` both files; `git commit -m "feat(converter): find and classify a campaign's unfinished volumes"`

### Task 4: Build and start the retry

**Files:**
- Modify: `packages/converter/src/htrflow_converter/retry.py`
- Test: `packages/converter/tests/test_retry_build.py` (uses `FakeCluster` from `test_apply.py`; move it to `conftest.py`-importable `tests/fakes.py` if importing across test files is not already done, in its own commit)

**Interfaces:**
- Consumes: Task 1 constants and `Cluster.lease_marks` / `Cluster.lease(marks)`; `Cluster.get`, `Cluster.labelled`, `Cluster.apply(obj, dry_run=...)`; `render.split`, `render.split_stem`, `render.label_value`, `render.CAMPAIGN_LABEL`, `render._PIPELINE_LABEL`, `render._DIGEST_ANNOTATION`; `cli._provenance`-style keys (`_APPLIED_BY_ANNOTATION`, `_APPLIED_AT_ANNOTATION`).
- Produces:
  - `@dataclass class Original: root: str; parts: list[str]; pipeline: str; digest: str; lines: list[str]; source_job: dict` — `root` is the name the `retry-of` label carries (the first part's Job name; a retry's own `retry-of` when retrying a retry).
  - `load_original(cluster, campaign: str) -> Original` — raises `ClusterError` with one sentence when not found.
  - `refusal(cluster, original: Original, volume_ids: set[str]) -> str | None`.
  - `next_name(cluster, root: str) -> str`.
  - `retry_objects(original: Original, name: str, lines: list[str], skipped: list[str], prov: dict[str, str]) -> list[dict]` — ConfigMap, Job pairs in apply order.
  - `start(cluster, objects: list[dict]) -> None` — dry-runs each Job, then applies its ConfigMap, then the Job.

- [ ] **Step 1: Write the failing tests** (each against a `FakeCluster` seeded by applying the GOOD repo through `cli.main(["apply", ...])`, then marking the campaign Job finished with some indexes failed)

```python
def test_load_original_reads_every_part_and_the_stamped_job(...):
    o = load_original(cluster, "<split campaign file name>")
    assert o.parts == ["<stem>-part1", "<stem>-part2"] and len(o.lines) == <total>
    assert o.source_job == json.loads(<part1 ConfigMap job-spec annotation>)

def test_load_original_falls_back_to_the_live_job_without_a_stamp(...):
    # job-spec annotation removed; live Job carries controller-added fields
    o = load_original(cluster, name)
    tmpl = o.source_job["spec"]["template"]["metadata"].get("labels", {})
    assert "selector" not in o.source_job["spec"] and "controller-uid" not in tmpl
    assert not any(k.startswith("batch.kubernetes.io/") for k in tmpl)

def test_load_original_refuses_with_neither(...):
    # no stamp, Job reaped
    with pytest.raises(ClusterError, match="applied before retries existed and its Job is gone"): ...

def test_retry_objects(...):
    cm, job = retry_objects(o, "demo-retry-1", ["B\timages:u", "D\timages:u"], ["C"], prov)
    assert cm["data"] == {"volumes.txt": "B\timages:u\nD\timages:u\n", "skipped.txt": "C\n"}
    assert job["spec"]["completions"] == job["spec"]["maxFailedIndexes"] == 2
    assert job["spec"]["parallelism"] == min(o.source_job["spec"]["parallelism"], 2)
    assert job["metadata"]["labels"][render.RETRY_OF_LABEL] == o.root
    assert job["metadata"]["labels"][render.CAMPAIGN_LABEL] == "demo-retry-1"
    assert _campaign_volume(job)["configMap"]["name"] == "campaign-demo-retry-1"
    assert "suspend" not in job["spec"]
    assert _without(job, NAME_AND_COUNTS) == _without(o.source_job, NAME_AND_COUNTS)   # nothing else changed
    assert json.loads(cm["metadata"]["annotations"][render.JOB_SPEC_ANNOTATION]) == job

def test_next_name_numbers_on_and_fits_a_dns_label(...):
    assert next_name(cluster, "demo") == "demo-retry-1"
    # seed demo-retry-1 and demo-retry-3 with retry-of=demo → "demo-retry-4"
    assert len(next_name(cluster, "x" * 63)) <= render._SPLIT_STEM

def test_retrying_a_retry_names_the_root(...):
    # load_original(cluster, "demo-retry-1").root == "demo"

@pytest.mark.parametrize("state, sentence", [
    ("lease-unmarked", "the last apply in namespace"),           # Lease without prunes-retries
    ("original-running", "is still running"),
    ("retry-running", "is still running"),
    ("warmup-incomplete", "warm-up"),
    ("digest-moved", "no longer runs image"),
    ("volume-held", "is running volume"),
])
def test_refusal(state, sentence, ...): assert sentence in refusal(cluster, o, ids)

def test_start_sends_nothing_when_the_job_dry_run_is_refused(...):
    # FakeCluster refusing the Job's dry run → no ConfigMap written
```

- [ ] **Step 2: Run them to verify they fail** — `uv run --no-sync pytest packages/converter/tests/test_retry_build.py -v`, FAIL.

- [ ] **Step 3: Implement** in `retry.py`.
  - Parts = labelled ConfigMaps with `CAMPAIGN_LABEL == label_value(campaign)` and no `htrflow.riksarkivet.se/kind: status`, sorted by name; `lines` = their `volumes.txt` lines concatenated in part order.
  - `root` = the part's `RETRY_OF_LABEL` if set, else the first part's Job name.
  - Source job = the first part ConfigMap's `JOB_SPEC_ANNOTATION`; otherwise the live Job, stripped to `apiVersion, kind, metadata.{name, namespace, labels, annotations}, spec` with `spec.selector`, `spec.manualSelector`, every `batch.kubernetes.io/*` and `controller-uid` label (metadata and template), and `status` removed.
  - Refusals, in this order, each one sentence naming the object: Lease without `PRUNES_RETRIES` ("the last apply in namespace <ns> ran a converter whose prune does not know retries — upgrade the Argo CD hook's converter and let it apply once"); a part or an earlier retry of `root` whose Job has no `Complete`/`Failed` condition; Job `htr-warmup-<pipeline>` missing or not `Complete`; its container image ≠ `original.digest`; another running labelled Job on the same pipeline whose ConfigMap lists any of `volume_ids`.
  - `next_name`: highest `n` among labelled ConfigMaps with `RETRY_OF_LABEL == root` matching `-retry-(\d+)$`, plus one; `f"{root[:_SPLIT_STEM - len(suffix)]}{suffix}"`.
  - `retry_objects`: `render.split` over `Volume`s rebuilt from the lines (or split the lines with the same count/byte budgets); a single part keeps `name`, several get `-part<i>`. ConfigMap labels = the original part ConfigMap's, with `CAMPAIGN_LABEL` = `label_value(name)` and `RETRY_OF_LABEL` = `root`; annotations = image digest, `prov`, and `JOB_SPEC_ANNOTATION` of its Job.
  - `start`: `cluster.apply(job, dry_run=True)` for each Job first; then ConfigMap and Job per part.

- [ ] **Step 4: Run** — the new tests and `uv run --no-sync pytest packages/converter/tests -q`, all pass.

- [ ] **Step 5: Commit** — `git commit -m "feat(converter): build and start a retry campaign from the original Job"`

### Task 5: The `retry` subcommand

**Files:**
- Modify: `packages/converter/src/htrflow_converter/cli.py` (`main`, new `_retry(args) -> int`)
- Test: `packages/converter/tests/test_retry_cli.py`

**Interfaces:**
- Consumes: Tasks 3 and 4; the credential handling `_export` already has (user from `--user` / `HTRFLOW_USER` / prompt; password from `HTRFLOW_PASSWORD` / `getpass`). Results URL = `--results-url`, else the source Job's wrapper env `RESULTS_URL`.
- Produces: `htrflow-campaigns retry CAMPAIGN --namespace NS [--dry-run] [--include-permanent] [--user U] [--results-url URL]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_dry_run_prints_the_summary_and_creates_nothing(...):
    rc = cli.main(["retry", "demo", "--namespace", "ns", "--dry-run"])
    assert rc == 0 and _no_new_objects(cluster)
    assert capsys.readouterr().out == (
        "campaign demo: 4 volumes, 1 done, 3 not done\n"
        "  2  retry      no run log: the volume never started\n"
        "  1  permanent  manifest: canvas 4 has no image (skipped; --include-permanent takes it)\n"
        "would start: Job/demo-retry-1 with 2 volumes\n"
    )

def test_retry_starts_the_job_and_leaves_the_original_alone(...):
    before = _snapshot(cluster, ["demo", "campaign-demo", "campaign-demo-status"])
    assert cli.main(["retry", "demo", "--namespace", "ns"]) == 0
    assert "started: Job/demo-retry-1 with 2 volumes" in capsys.readouterr().out
    assert _snapshot(cluster, [...]) == before

def test_include_permanent_takes_them_too(...): ...   # 3 volumes, skipped.txt empty
def test_nothing_to_retry_exits_0_and_creates_nothing(...): ...  # "nothing to retry: all 4 volumes of demo are done"
def test_a_refusal_exits_1_with_its_sentence(...): ...
def test_a_results_error_exits_1_before_anything_is_created(...): ...
def test_retry_passes_the_lease_marks_through(...):   # an unmarked Lease stays unmarked after a refused retry; a marked one stays marked
```

- [ ] **Step 2: Run them to verify they fail** — `uv run --no-sync pytest packages/converter/tests/test_retry_cli.py -v`, FAIL.

- [ ] **Step 3: Implement `_retry`**: `prov` = `{_APPLIED_BY_ANNOTATION: _applied_by(), _APPLIED_AT_ANNOTATION: <UTC now, same format as _provenance>}` (no commit key: no repo is involved). Read `lease_marks()` → `load_original` → `find_unfinished` → print summary (groups by `(state, reason_group)`, largest first, the line format above) → `refusal` → `next_name` → `retry_objects` → print `would start:` and return on `--dry-run`, else `with cluster.lease(marks): start(...)` and print `started: Job/<name> with <n> volumes` per part. `ClusterError` and results errors print their sentence and return 1.

- [ ] **Step 4: Run** — `make ci`, all green.

- [ ] **Step 5: Commit** — `git commit -am "feat(converter): htrflow-campaigns retry"`

### Task 6: Docs

**Files:**
- Modify: `docs/getting-started/troubleshooting.md` ("Re-run a failed volume": the `retry` command first, the new-campaign-file way second)
- Modify: `docs/reference/cli.md` (a `retry` section: what it reads, the summary, refusals and their sentences, exit codes, prune keeps retries, "upgrade the Argo CD hook's converter before using retry")
- Modify: `docs/how-it-works/decision-log.md` (one row, 2026-11-06: retries run directly in the cluster, kept by prune with their original, settings copied)

- [ ] **Step 1:** Write the sections. No people's names; no internal system names.
- [ ] **Step 2:** Run `uv run --no-sync pytest packages/converter/tests/test_docs_lint.py -q` and the strict site build (`make docs-build` or what `ci.yml` runs) — both pass.
- [ ] **Step 3: Commit** — `git commit -am "docs: retry failed volumes with htrflow-campaigns retry"`

### Task 7: End-to-end on the local test cluster

No code. Local k3s, namespace `htr-batch`, kube context `default`. Never prod.

**Setup**

- [ ] Build the converter from this branch (`uv run --no-sync htrflow-campaigns --help` lists `retry`).
- [ ] Scratch campaigns repo in the session scratchpad: `htrflow-campaigns init`, then `converter.yaml` copied from `~/htr-test` (branch `local-k3s`) with `warmup_wait_seconds: 10` and `window: 1`; one pipeline `pipelines/retry-e2e-v1.yaml` = a copy of the live `htrdemo-eng-modern-v2` pipeline under the new id (image from Job `htr-warmup-htrdemo-eng-modern-v2`, steps from ConfigMap `htr-pipeline-htrdemo-eng-modern-v2`).
- [ ] Pick 5 example files from `Riksarkivet/htr_demo` pinned to one commit (`hf api` / the Space's file list); campaign `campaigns/retry-e2e.yaml`, volumes `images:https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/<commit>/.gradio_cache/examples/<file>`, plus a sixth volume naming an example file that does not exist.
- [ ] Results login for the read side: a store account that can read `htr-batch/`, via `HTRFLOW_USER` / `HTRFLOW_PASSWORD` exported in the shell, never written to a file.

**Run** (apply WITHOUT `--prune` throughout: the scratch repo does not hold the namespace's other campaigns)

- [ ] Apply the pipeline only (campaign file not yet present); wait for `htr-warmup-retry-e2e-v1` Complete.
- [ ] Add the campaign file, apply. With `window: 1`, watch `completedIndexes`; after two volumes are done, delete the marker `/data/retry-e2e-v1-<recipe sha>/warmup/retry-e2e-v1.done` (troubleshooting "The model cache" debug pod). The remaining indexes fail at `warmup-wait` (exit 13). Wait until the Job is `Failed`.
- [ ] Check the sixth volume's run log says `permanent failure`. If the wrapper reports a missing image as transient, use a cause it reports as permanent instead and note which.
- [ ] `retry retry-e2e --namespace htr-batch --dry-run`: summary = 6 volumes, 2 done, 3 retry (no run log), 1 permanent skipped; nothing created.
- [ ] Refusal: while the Job is still running (redo with a fresh campaign or check before it fails), `retry` exits 1 with "is still running".
- [ ] Fix: re-warm (delete the marker's Complete warm-up Job and apply). `retry retry-e2e --namespace htr-batch`: `Job/retry-e2e-retry-1` with 3 volumes; all three finish (`manifest.json` present); the original Job, `campaign-retry-e2e`, its status ConfigMap and the two original results are byte-identical to before (`kubectl get -o json` minus `resourceVersion`/`managedFields`, `curl -I` ETags).
- [ ] A second `retry` while `-retry-1` runs is refused; after it ends, `retry retry-e2e` says "nothing to retry" (permanent one skipped) and `--include-permanent --dry-run` names `retry-e2e-retry-2` with 1 volume.
- [ ] After TTL: set `ttl_seconds_after_finished: 60` on the pipeline file before the run, or delete the original Job by hand after it finishes; `retry --dry-run` still works (from the stamped spec).
- [ ] Prune, without collateral: in a Python shell against the live namespace, `Cluster("htr-batch").prune(R)` where `R` = every live labelled `(kind, name)` except the retry's objects and their status → nothing pruned. Then `R` minus the original's pair as well → exactly the original's and the retry's Jobs, ConfigMaps and status ConfigMaps pruned.

**Cleanup**

- [ ] Delete the test pipeline's warm-up Job and ConfigMap, the test results under `htr-batch/retry-e2e-v1/` and `htr-batch/status/logs/retry-e2e-v1/`, and the cache directory `/data/retry-e2e-v1-*`. Leave every other campaign alone. `kubectl -n htr-batch get jobs,cm -l htrflow.riksarkivet.se/managed-by=converter` shows exactly what was there before.
- [ ] Write the run's outcomes (commands, counts, anything that differed from this plan) into the PR description; no RA images or screenshots in git.
