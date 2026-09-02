# Wrapper audit — `packages/wrapper` on `b63-indexed` (2026-09-02)

Handoff for an implementing agent. Scope: the batch-Job container
(`packages/wrapper/src/htrflow_batch/`), `.docker/htrflow-batch.dockerfile`,
the Job template
`packages/converter/src/htrflow_converter/manifests/campaign-job.yaml`, and
`docs/reference/wrapper.md`. Baseline at audit time: commit `2588c24`,
`uv run --all-packages pytest -q packages/wrapper` → 177 passed, 1 skipped.

Work in this worktree (`.worktrees/b63-indexed`), not the main checkout.
One logical step per commit, each with its regression test, tests green
before every commit. Branch conventions: commit subjects end in `(B63)`.
Do not add a Co-Authored-By trailer. Use `uv`, never pip.

Overall the wrapper is sound: stage machine, permanent/transient exit
split, byte caps on untrusted fetches, PAGE-before-ALTO upload order,
pinned image inputs, a regression test per historical bug. The items below
are ranked by severity. Items 1–3 are the ones to fix; 4–7 are decisions or
smaller hardening.

---

## 1. Resume never converges for tokenised image URLs — CONFIRMED, fix first

**Defect.** `publish.run_manifest` stores each page's source URL *redacted*
(`page_sources: {name: redact_url(p.image_url)}`), but `main._changed_sources`
compares that stored value against the *raw* `p.image_url`. Any URL that
`redact_url` changes — a query string (`?token=…`), an uppercase host, an
explicit default port, an unencoded space — makes every done page look
"changed", so each Kubernetes retry reprocesses the whole volume. This is
exactly the private-IIIF case the S6 redaction was added for.

- `packages/wrapper/src/htrflow_batch/main.py:372` —
  `sources[p.name] != p.image_url`
- `packages/wrapper/src/htrflow_batch/publish.py:110` —
  `"page_sources": {p.name: redact_url(p.image_url) for p in pages}`

**Reproduction (run from repo root):**

```python
from htrflow_batch.main import _changed_sources
from htrflow_batch.iiif import PageRef, redact_url
class S:
    def get_json_or_none(self, k):
        return {"page_sources": {"0001": redact_url(url)}}
url = "https://iiif.example/private/p1/full/2500,/0/default.jpg?token=SECRET"
pages = [PageRef(index=1, name="0001", image_url=url, canvas={})]
print(_changed_sources(S(), pages, {"0001"}))   # prints {'0001'}; must be set()
```

**Fix.** In `_changed_sources`, compare `sources[p.name] != redact_url(p.image_url)`.
Keep `page_sources` redacted (the bucket is public).

**Test.** Add to `packages/wrapper/tests/test_main.py`, next to
`test_resume_reprocesses_pages_whose_source_changed`: a done page whose
current image URL carries `?token=SECRET` and whose stored `page_sources`
entry is the redacted form must stay skipped (`calls == []`,
`results["0001"]["status"] == "skipped"`). The existing test passes only
because it uses clean URLs; leave it in place.

**Docs.** `docs/reference/wrapper.md` (RESUME row and "Completion contract")
already describe the intended behaviour; no change needed beyond the code.

---

## 2. Why a page failed is never recorded — fix second

**Defect.** `stream.consume` records fetch/processing/upload errors only in
`stats.results[name].error`. Nothing is logged at that point. `_verify`
raises `RuntimeError(f"verify failed: missing={missing} failed={failed}")`
with page *names* only, and a run with failed pages never publishes
`manifest.json`, the one place the error text would have gone. The operator
sees `verify failed: failed=['0042']` and nothing about the cause.

- `packages/wrapper/src/htrflow_batch/stream.py:157` (fetch failure),
  `:163` (process failure), `:170` and `:173` (upload failures)
- `packages/wrapper/src/htrflow_batch/main.py:319` (verify message)

**Fix.**
- In `consume`, `log.warning("page %s failed: %s", name, error)` on each
  failure path (the root handler's `RedactingFormatter` strips URL secrets;
  do not log the raw `item.page.image_url` separately).
- In `_verify`, include the error text for the failed pages, bounded (for
  example the first 10, each truncated to ~200 chars) so the termination
  log stays well under its 3500-char field cap in `_terminate`.

**Test.** `packages/wrapper/tests/test_stream.py`: a fetch failure and a
process failure each produce one warning containing the page name and the
error (use `caplog`). `packages/wrapper/tests/test_main.py`: the termination
log written for a verify failure contains the failed page's error text, and
a `?token=` in that error is redacted.

---

## 3. SIGTERM cleanup can outlive the pod's grace period

**Defect.** On SIGTERM, `LogCapture.finish` joins the shipping thread
(up to 30 s), then waits for `_upload_lock` and does a final PUT through
`ResultStore._log_client` (connect 5 s, read 30 s, 2 attempts ≈ 70 s worst
case). The Job template sets no `terminationGracePeriodSeconds`, so the
default 30 s applies: on a slow S3 the pod is SIGKILLed before the final
run-log ship and before the `os._exit(143)` the docs promise.

- `packages/wrapper/src/htrflow_batch/logship.py:215-220`
- `packages/wrapper/src/htrflow_batch/store.py:40-46`
- `packages/converter/src/htrflow_converter/manifests/campaign-job.yaml`
  (pod spec, no grace period set)

**Fix (pick one, prefer the first).**
- Set `terminationGracePeriodSeconds` on the Job pod template to cover
  the worst case (120 s), regenerate the golden file
  `packages/converter/tests/golden/kyrk.job.yaml`, and note it in
  `docs/how-it-works/failure-handling.md`.
- Or shrink the budget: `finish()` joins with a short timeout and the log
  client uses `retries={"max_attempts": 1}`, so the SIGTERM path fits in
  30 s. Update the "Live run log" section of `docs/reference/wrapper.md`.

The converter's LOC budget for B63 is tight (see
`docs/features/` stories); the Job-template change is a one-line addition.

---

## 4. Decide: a missing cached model exits 13 (permanent) — PLAUSIBLE, verify in the image

`main._main` catches bare `ValueError` as permanent. Hugging Face's
`LocalEntryNotFoundError` subclasses both `FileNotFoundError` and
`ValueError`, so with `HF_HUB_OFFLINE=1` a model absent from `/data/hf`
would exit 13 → `FailIndex` → with `maxFailedIndexes: 1` the whole campaign
fails, instead of retrying after a re-warm. The comment in
`driver.load_pipeline` says an `OSError` from model construction must stay
transient; this exception is both.

- `packages/wrapper/src/htrflow_batch/main.py:296`

Verify inside the built image (`make test-driver-real` environment):
`python -c "from huggingface_hub.errors import LocalEntryNotFoundError as E; print(E.__mro__)"`.
If it is a `ValueError`, either catch it explicitly before the permanent
branch and classify it transient, or document the permanent classification
in `docs/reference/wrapper.md` (exit-code table) — and pin whichever with a
test that raises a `ValueError`+`OSError` subclass from the factory.

---

## 5. Junk manifest shapes are retried as transient

`iiif.pages_from_manifest` / `_image_url` assume dict/list shapes. A canvas
whose `items` is not a list, or an annotation whose `body` is a string,
raises `AttributeError`/`TypeError` → exit 1 → three retries of a condition
that cannot change. W11 fixed this for widths only.

- `packages/wrapper/src/htrflow_batch/iiif.py:153-168`, `:210`

Fix: wrap the per-canvas walk in `try/except (AttributeError, TypeError,
KeyError)` and raise `ManifestError(f"canvas {i} is malformed: …")`.
Test in `packages/wrapper/tests/test_iiif.py` with `items: "x"` and
`body: "https://…"`.

---

## 6. Redaction is narrower than the docs claim

Only the handler that `LogCapture.attach_logging` installs carries
`RedactingFormatter`. Unredacted paths into the world-readable run log:
bare `print()` output through the tee, handlers htrflow installs itself,
and a pre-existing root `StreamHandler` that `attach_logging` reuses
(early `return` at `logship.py:138` without setting the formatter).
`docs/reference/wrapper.md` says "Every URL in logs … is redacted".

- `packages/wrapper/src/htrflow_batch/logship.py:136-142`, `:51-54`

Fix: either apply `redact_urls` in `LogCapture._append` (covers all three
paths; costs a regex per write), or set `RedactingFormatter` on the reused
handler and narrow the doc sentence to "the wrapper's own log lines".
Test: a `print("https://h/x?token=S")` must not reach `capture.text()`
unredacted if the first option is chosen.

---

## 7. Smaller items (optional)

- **Manifest image URLs are not scheme-checked.** `check_http_url` runs for
  `IMAGES` only; a `file:`/`ftp:` body id from a manifest fails per page via
  httpx (`UnsupportedProtocol`) and is retried as transient. `iiif.py:159`,
  `:167`. Calling `check_http_url` in `pages_from_manifest` makes it
  permanent and explicit.
- **tmpfs worst case.** `LOOKAHEAD_PAGES` (64) × `FETCH_MAX_BYTES` (64 MiB)
  = 4 GiB against the 2 Gi emptyDir `sizeLimit`; eviction, not a clean
  failure. Realistic sized pages are ~1 MB, so this only bites service-less
  native-size images (already warned about in `fetch.py`'s docstring).
  Consider documenting the product bound in `docs/reference/wrapper.md`.
- **The stdout tee has no `buffer`.** `_Tee` (`logship.py:44`) subclasses
  `io.TextIOBase` without a `buffer` attribute; a library writing to
  `sys.stdout.buffer` raises `AttributeError`. Forward `buffer` to the
  original stream.

---

## Not findings (checked, fine)

Config parsing and fail-fast; `PageStream` lookahead/ordering and
`GeneratorExit` handling; `fetch_page` retry, 400→`max` fallback, byte cap
and partial-file unlink; `ResultStore` ordering, parse-before-PUT, bounded
boto timeouts; `_terminate` field-level truncation and redaction; the
`MAX_SECONDS` timer race via `state.terminating`; `viewer` label
normalisation; `synthetic` manifest; `warmup` classification; Dockerfile
pins and non-root user.

## Verification before claiming done

```bash
cd /home/morgan/htrflow-batch/.worktrees/b63-indexed
uv run --all-packages pytest -q packages/wrapper
make typecheck
uv run --no-sync ruff check packages/wrapper
uv run --all-packages pytest -q packages/converter   # if the Job template changed
```
