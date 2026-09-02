# Wrapper simplifications — `packages/wrapper` on `b63-indexed` (2026-09-02)

Handoff for an implementing agent. Companion to
`2026-09-02-wrapper-audit.md`; that audit's seven findings are already
applied (commits `3e75c14` … `a05e89b`), so line numbers below are against
HEAD `a05e89b`. Scope: `packages/wrapper/src/htrflow_batch/`. Baseline:
`uv run --all-packages pytest -q packages/wrapper` green.

Ground rules: no behaviour change — every existing test keeps passing
unmodified unless the item says otherwise, and the env contract, exit
codes, S3 keys and log lines stay byte-identical. Work in this worktree.
One item per commit, subject ends in `(B63)`, no Co-Authored-By trailer.
`uv`, never pip. Run the verification block after each commit.

Items are ordered by value. Do 1–5; 6–9 are optional; 10–11 are listed so
they are not "simplified" by mistake.

---

## 1. One S3 put path in `ResultStore`

`put_json`, `put_json_at`, `put_bytes`, `put_text` and `put_run_log`
(`store.py:98–146`) are five copies of the same `put_object` call differing
only in key, body, content type and which boto client is used.

Do: add `_put(self, key: str, body: bytes, content_type: str, client=None)`
and make the five public methods one-line callers. Keep their names and
signatures; `main.py`, `publish.py` and the tests call them.

Guard: existing `test_store.py` and `test_publish.py`.

## 2. One place that joins `S3_PREFIX`

The bucket-root key under the prefix is built twice:

- `main.py:384` — `prefix = f"{cfg.s3_prefix}/" if cfg.s3_prefix else ""`
  in `_synthetic_source`
- `store.py:110` — `full_key = f"{self.cfg.s3_prefix}/{key}" if ... else key`
  in `put_json_at`

next to `Config.volume_prefix` (`config.py:83`), which does the same join
for the per-volume case.

Do: add `Config.root_key(self, rel: str) -> str` (returns `rel` when the
prefix is empty, else `f"{s3_prefix}/{rel}"`). Use it in both places;
`_synthetic_source` then builds `manifest_id` from `cfg.root_key(key)`.

Guard: `test_main.py` IMAGES tests (they assert the `sources/` key and the
manifest id with and without `S3_PREFIX`), `test_store.py`.

## 3. Let pydantic parse the env in `Config.from_env`

`config.py:23` defines `_bool`; `config.py:60–80` hand-converts every
optional field with `int(...)`/`float(...)`/`_bool(...)` and repeats each
default as a string beside the class-level default.

Pydantic v2 already coerces `"64"` → `int`, `"15"` → `float`, and
`"true"/"1"/"yes"/"on"/"t"/"y"` → `True` (and the negatives → `False`);
verified locally on this workspace's pydantic. A bad value raises
`ValidationError`, which subclasses `ValueError`, so `main._main` still
classifies it permanent (exit 13).

Do: keep the required-field check and the exactly-one-of check as they are.
For the optional fields, build `kwargs` from a table of `(attr, ENV)` pairs
and pass the raw string only when the env var is set, so the class default
is the single source of truth. Keep `s3_prefix`'s `.strip("/")`. Delete
`_bool`.

Note: pydantic's bool set is a superset of the old `_bool` set (`t`, `y`
also accepted; previously they read as `False`). Nothing in the repo sets
those; treat as acceptable and say so in the commit body.

Guard: `test_config.py`. Add one case: `RESUME=off` → `False`,
`LOOKAHEAD_PAGES=abc` → `ConfigError`/`ValueError`.

## 4. One htrflow exception map for driver and warm-up

`driver.load_pipeline` (`driver.py:32–42`) translates htrflow's `KeyError`
(unknown step) and `NotImplementedError` (unknown model class) into
`ValueError("bad pipeline config…")`. `warmup.PERMANENT_ERRORS`
(`warmup.py:28–33`) lists the same two types again so the two agree by
convention only.

Do: in `driver.py`, extract `build_pipeline(pipeline_path) -> Pipeline`
that does the YAML pre-check, the `from_config` call with its `TypeError`
fallback, and the `KeyError`/`NotImplementedError` → `ValueError`
translation. `load_pipeline` calls it and then appends the Export steps.
`warmup._load` calls `build_pipeline` and `PERMANENT_ERRORS` shrinks to
`(ValueError, yaml.YAMLError)`.

Guard: `test_driver.py`, `test_warmup.py` (the tests that raise `KeyError`
and `NotImplementedError` from the injected loader must still exit 13 —
they inject `load`, so they bypass the driver; keep them, they now pin the
contract at the warm-up boundary). `test_driver_real.py` (htrflow marker)
is the level-0 check inside the image.

## 5. One ALTO reader in `publish.alto_dims`

`publish.py:22–54` has two branches — local file this run wrote, or the
stored object for a skipped page — each with its own `try`/`except`.

Do: resolve the bytes first (`local.read_bytes()` if a local ALTO exists,
else `store.get_bytes(...)` when the page is in `uploaded`, else skip),
then parse once with `parse_alto_dims_bytes`. Keep the warning on a store
read error and the silent skip on a parse error. `viewer.parse_alto_dims`
(path variant) then has no production caller: delete it and update the
three calls in `test_viewer.py:15`, `:22`, `:37` to read the bytes and call
`parse_alto_dims_bytes`. That is the one test edit this document allows.

Guard: `test_publish.py` (resumed volume keeps a complete viewer manifest;
unparsable ALTO is left out, not fatal).

---

## 6. `PageStream`: partial instead of a packed tuple (optional)

`stream.py:84` packs `(dest, client, retries, backoff, max_bytes, stop)` and
`stream.py:107` splats it on every submit. A `functools.partial(fetch_page,
dest_dir=dest, client=client, retries=retries, backoff=backoff,
max_bytes=max_bytes, stop=stop)` built once in `__init__` reads better.
`_stop` is also stored separately for `_fill`; keep that.

Guard: `test_stream.py`.

## 7. `PageStream.error` (optional)

`stream.py:82` / `:112`: set by `_abandon`, read only by
`test_stream.py:168`. The run path relies on the `log.error` in `_abandon`
and on the verify gate. Either drop the attribute and have that test assert
on `caplog`, or keep it and say why in the class docstring. Do not silently
leave it half-used.

## 8. `fetched = PageStream` alias (optional)

`stream.py:143`. Two names for one class; docs and tests say `fetched()`,
`main.py:298` calls `fetched(`. Pick one. If dropping the alias, rename the
call in `main.py` and the six uses in `test_stream.py`, and the mentions in
`docs/reference/wrapper.md` and `docs/how-it-works/wrapper.md`.

## 9. Shared canvas walk in `iiif` (optional, last)

`_image_url` (`iiif.py:152`) and `painting_body` (`iiif.py:172`) both walk
the P3 (`items[].items[].body`) and P2 (`images[].resource`) structures. A
generator `_bodies(canvas)` yielding `(body_dict, service_id)` in order
would leave one walk. This is the least mechanical item and the only one
that touches manifest parsing after the audit hardened it; the
`ManifestError` wrapping in `pages_from_manifest` must keep catching the
same exception types. Do it last or skip it.

Guard: `test_iiif.py` (P2 string label, service-less body, junk canvas,
non-http body id) and `test_viewer.py`.

---

## 10. Keep: `RedactingFormatter`

`logship.py:36`. Redundant with the redaction now done in
`LogCapture._append` for the *shipped* log, but it still redacts the lines
that reach `kubectl logs` through the wrapper's own handler. Not a
simplification target.

## 11. Keep: the `from_config` compatibility fallback

`driver.py:35` (`except TypeError:` → pass the parsed dict). Dead against
the pinned base image, but removing it is a functionality decision about
which htrflow builds the wrapper supports, not a simplification. Leave it
unless that decision is made explicitly (then `test_driver_real.py` is the
guard).

---

## Verification after every commit

```bash
cd /home/morgan/htrflow-batch/.worktrees/b63-indexed
uv run --all-packages pytest -q packages/wrapper
make typecheck
uv run --no-sync ruff check packages/wrapper
uv run --no-sync ruff format --check packages/wrapper
```

Expected: 177 passed (plus any cases added above), 1 skipped; ty and ruff
clean. Push the branch after each reviewed commit.
