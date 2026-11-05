# Campaign Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `htrflow-campaigns export` writes a finished campaign's volumes to a publishing folder: one folder per volume, one full-resolution ALTO per image, empty ALTO where there is no text, whole volumes only, filtered on predicted quality.

**Architecture:** Four new modules in the converter package, each with one job — page rules (pure), ALTO rewriting (pure, stdlib XML), HTTP reads (results proxy login + IIIF `info.json`), and the per-volume export with its swap and report — wired into the existing argparse CLI. All network access goes through one injectable `httpx.Client`, so every test runs on `httpx.MockTransport`.

**Tech Stack:** Python ≥3.12, `httpx` (new converter dependency), `xml.etree.ElementTree`, `argparse`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-campaign-export-design.md`

## Global Constraints

- Output layout: `<target>/<volume id>/<volume id>_<nnnnn>.xml`, the number taken from the page's own source image id (five digits), never from canvas order.
- Every volume folder holds exactly `manifest.json`'s `pages` files; no partial volume ever carries the real folder name.
- Quality filter: `--min-quality` default **0.75**; `--no-quality-filter` turns it off. Mean over pages that have a `quality`, that is not exactly `0`, and that are not in `--empty-pages`. No such page → volume skipped ("no scores") while the filter is on. Below the threshold → skipped.
- `--empty-pages` default `1`; 1-based positions, commas and ranges (`1,3-4`); `none` empties nothing; positions past a volume's end are ignored.
- Empty ALTO: `Description` with only `MeasurementUnit` (`pixel`) and `sourceImageInformation/fileName`; no `Processing`, no `PC`, no `ReadingOrder`; `Page` at native size, `PHYSICAL_IMG_NR` = page number, `ID` = `_<page key>`; empty `PrintSpace` `HPOS=0 VPOS=0` at page size. It must match the spec's R0001203 example and validate against ALTO 4.4.
- Pages with text keep htrflow's `Description`; `fileName` becomes the published stem and `PHYSICAL_IMG_NR` the page number on every page.
- Scaling: `sx = native_w / Page@WIDTH`, `sy = native_h / Page@HEIGHT`; every `HPOS`/`WIDTH` × `sx`, `VPOS`/`HEIGHT` × `sy`, every `Polygon@POINTS` pair × `(sx, sy)`; rounded to integers; the `Page` ends exactly at native size. Aspect ratios differing by more than 1 % fail the volume.
- Results are read only through the results proxy: `POST {results_url}/_login` (JSON body `{"username", "password"}`; `Origin` = the results URL's `scheme://host`), then `GET {results_url}/{namespace}/{pipeline}/{quote(volume id)}/…`. `results_url` and `namespace` come from the repo's `converter.yaml`; `--results-url` overrides. Password from a no-echo prompt or `HTRFLOW_PASSWORD`, never an argument; user from `--user` or `HTRFLOW_USER`.
- Proxy answers: `404` on `manifest.json` = "not finished"; `403` = skipped, "your account may not read this volume"; `401` mid-run = log in again once and retry, then stop the run.
- Report: one row per campaign volume, columns `volume, outcome, pages, pages_with_text, pages_empty_filled, pages_empty_chosen, quality_mean, quality_pages, reason`; printed as a table and written as CSV to `--report` (default `./export-<campaign>.csv`), never into the target. Outcomes: `exported`, `below threshold`, `not finished`, `no scores`, `forbidden`, `failed`.
- Exit status non-zero iff any volume's outcome is `failed` or `forbidden`, or the run stopped (session lost, target unusable).
- One new dependency at most (`httpx>=0.27`, the range the wrapper uses). No lxml, no S3 client, no htrflow.
- Converter LOC budget (`scripts/loc-budget.sh`, currently 5085) is raised in the last task with a comment in the file's style.
- No person's name in any doc or comment; docs say "the publishing side", not a system name.

## Review Focus

- **Target not mounted:** `--to` pointing at a path that does not exist (an unmounted share) must refuse before reading anything, never create a local folder — test in Task 5.
- **A leftover temp folder** from a crashed run (`<target>/.<id>.tmp-*`) is removed when that volume is exported again and never counted as a volume — test in Task 4.
- **Session expiring mid-campaign** (a `401` on the 200th file) logs in once more and continues; a second `401` stops the run with a clear message — test in Task 3.
- **Both `POINTS` spellings** ALTO allows — `"x,y x,y"` and `"x y x y"` — scale correctly and keep their spelling — test in Task 2.
- **An `ok` page whose ALTO is gone** (`404` on `alto/<key>.xml`) fails its volume ("page 0002: ALTO missing for an ok page"): the wrapper keeps an ALTO for every `ok`/`skipped` page, so a missing one means the bucket changed under the export — test in Task 4. `manifest.json` is read again before the swap; gone or different fails the volume ("volume changed during export").

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/converter/src/htrflow_converter/export_pages.py` | Pure page rules: page sets, file numbers from source URLs, `info.json` URLs, the quality mean |
| `packages/converter/src/htrflow_converter/export_alto.py` | Pure ALTO rewriting: scale a page, build an empty page |
| `packages/converter/src/htrflow_converter/export_http.py` | Results-proxy session and IIIF size reads over one `httpx.Client` |
| `packages/converter/src/htrflow_converter/export.py` | One volume end to end, the swap, the run over a campaign, the report |
| `packages/converter/src/htrflow_converter/cli.py` | The `export` subparser |
| `packages/converter/tests/test_export_*.py` | One test file per module |
| `docs/reference/cli.md` | An `export` section |

---

### Task 1: Page rules

**Files:**
- Create: `packages/converter/src/htrflow_converter/export_pages.py`
- Test: `packages/converter/tests/test_export_pages.py`

**Interfaces:**
- Produces:
  - `class PageRuleError(ValueError)`
  - `parse_page_set(text: str) -> frozenset[int]`
  - `page_number(source_url: str, volume_id: str) -> int` — raises `PageRuleError`
  - `info_url(source_url: str) -> str` — raises `PageRuleError`
  - `quality_mean(results: dict[str, dict], empty_keys: set[str]) -> tuple[float | None, int]` — `(mean, pages counted)`, `(None, 0)` when nothing counts

- [ ] **Step 1: Write the failing tests**

```python
RA = "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/full/2500,/0/default.jpg"

def test_page_sets():
    assert parse_page_set("1") == {1}
    assert parse_page_set("1,3-4") == {1, 3, 4}
    assert parse_page_set("none") == frozenset()
    for bad in ("0", "a", "4-2", "", "1,,2"):
        with pytest.raises(PageRuleError):
            parse_page_set(bad)

def test_number_comes_from_the_source_image_id():
    assert page_number(RA, "R0001203") == 1
    assert page_number(RA.replace("_00001", "_00417"), "R0001203") == 417

def test_a_source_that_is_not_this_volume_is_refused():
    for url in (RA.replace("R0001203_", "R0009999_"),          # another volume
                RA.replace("_00001", "_0001"),                  # four digits
                "https://example.org/scan.jpg"):                # no image id
        with pytest.raises(PageRuleError):
            page_number(url, "R0001203")

def test_info_url_is_the_image_service_base():
    assert info_url(RA) == "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/info.json"
    with pytest.raises(PageRuleError):
        info_url("https://example.org/scan.jpg")

def test_quality_mean_leaves_out_zero_unscored_and_emptied_pages():
    results = {"0001": {"status": "ok", "quality": 0.2},   # emptied below
               "0002": {"status": "ok", "quality": 0.9},
               "0003": {"status": "ok", "quality": 0.0},   # QP 0
               "0004": {"status": "failed"},               # unscored
               "0005": {"status": "skipped", "quality": 0.7}}
    assert quality_mean(results, {"0001"}) == (pytest.approx(0.8), 2)
    assert quality_mean({"0001": {"status": "failed"}}, set()) == (None, 0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_export_pages.py -q`
Expected: FAIL, `ModuleNotFoundError: htrflow_converter.export_pages`

- [ ] **Step 3: Implement the four functions in `export_pages.py`**

`page_number`: the image identifier is the URL path's segment before the four IIIF request segments (`region/size/rotation/quality.format`), URL-decoded; it must end in `<volume_id>_<exactly five digits>`, preceded by start, `!`, `/` or `:`. `info_url`: that identifier's URL with `/info.json`. A URL with fewer than five path segments after the host is not an image request → `PageRuleError`.

- [ ] **Step 4: Run to verify they pass** — same command, Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/converter/src/htrflow_converter/export_pages.py packages/converter/tests/test_export_pages.py
git commit -m "feat(export): page rules — page sets, file numbers from source ids, the quality mean"
```

---

### Task 2: ALTO rewriting

**Files:**
- Create: `packages/converter/src/htrflow_converter/export_alto.py`
- Test: `packages/converter/tests/test_export_alto.py`, fixture `packages/converter/tests/fixtures/export/alto-2500.xml` (a small ALTO v4 in htrflow's shape — `Description` with two `Processing` entries, `Page WIDTH="2500" HEIGHT="1769" PHYSICAL_IMG_NR="0" ID="_0001" PC="0.87"`, a `PrintSpace` with one `TextBlock`/`TextLine`/`String` carrying `HPOS/VPOS/WIDTH/HEIGHT`, a `Shape/Polygon POINTS` in `x,y` spelling, another in `x y` spelling, and a `ReadingOrder`; invented text, no RA content)

**Interfaces:**
- Produces:
  - `class AltoMismatch(ValueError)`
  - `ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"`
  - `scale_alto(xml: bytes, native: tuple[int, int], stem: str, number: int) -> bytes`
  - `empty_alto(native: tuple[int, int], stem: str, number: int, page_key: str) -> bytes`

- [ ] **Step 1: Write the failing tests**

```python
def test_empty_alto_is_the_spec_example():
    xml = empty_alto((3494, 2472), "R0001203_00001", 1, "0001")
    root = ET.fromstring(xml)
    assert [e.tag.split("}")[1] for e in root.find(f"{{{ALTO_NS}}}Description")] == \
        ["MeasurementUnit", "sourceImageInformation"]
    page = root.find(f".//{{{ALTO_NS}}}Page")
    assert page.attrib == {"WIDTH": "3494", "HEIGHT": "2472", "PHYSICAL_IMG_NR": "1", "ID": "_0001"}
    ps = page.find(f"{{{ALTO_NS}}}PrintSpace")
    assert ps.attrib == {"HPOS": "0", "VPOS": "0", "WIDTH": "3494", "HEIGHT": "2472"} and len(ps) == 0
    assert root.find(f".//{{{ALTO_NS}}}fileName").text == "R0001203_00001"
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    assert b"xsi:schemaLocation" in xml and b"alto-4-4.xsd" in xml

def test_scaling_reaches_the_native_size_on_every_coordinate():
    out = ET.fromstring(scale_alto(FIXTURE.read_bytes(), (3494, 2472), "R0001203_00001", 1))
    page = out.find(f".//{{{ALTO_NS}}}Page")
    assert (page.get("WIDTH"), page.get("HEIGHT"), page.get("PHYSICAL_IMG_NR")) == ("3494", "2472", "1")
    assert page.get("PC") == "0.87"                       # a page with text keeps its score
    s = out.find(f".//{{{ALTO_NS}}}String")
    assert int(s.get("HPOS")) == round(<fixture HPOS> * 3494 / 2500)   # fill from the fixture
    assert out.find(f".//{{{ALTO_NS}}}fileName").text == "R0001203_00001"
    assert out.find(f".//{{{ALTO_NS}}}Processing") is not None

def test_both_points_spellings_scale_and_keep_their_spelling():
    out = scale_alto(FIXTURE.read_bytes(), (5000, 3538), "X_00001", 1)   # exactly ×2
    comma, space = [p.get("POINTS") for p in ET.fromstring(out).iter(f"{{{ALTO_NS}}}Polygon")]
    assert comma == "<fixture comma points, each doubled>"
    assert space == "<fixture space points, each doubled>"

def test_a_page_already_native_comes_out_unchanged_but_named():
    out = scale_alto(FIXTURE.read_bytes(), (2500, 1769), "X_00001", 1)
    assert ET.fromstring(out).find(f".//{{{ALTO_NS}}}String").get("HPOS") == "<fixture HPOS>"

def test_a_different_shape_is_refused():
    with pytest.raises(AltoMismatch):
        scale_alto(FIXTURE.read_bytes(), (2472, 3494), "X_00001", 1)   # rotated
```

Plus: the empty ALTO for the spec's R0001203 example validates against ALTO 4.4 — a test that is skipped unless the schema files sit in `packages/converter/tests/fixtures/export/xsd/` (they are not committed; Task 6 runs it once by hand).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_export_alto.py -q`
Expected: FAIL, module missing

- [ ] **Step 3: Implement in `export_alto.py`**

Stdlib `ElementTree`, with `register_namespace("", ALTO_NS)` and the `xsi` prefix registered so output keeps a default namespace and `xsi:schemaLocation`. Serialise with `xml_declaration=True, encoding="UTF-8"` and `ET.indent`. The aspect check: `abs((w/h) / (native_w/native_h) - 1) > 0.01` → `AltoMismatch`.

- [ ] **Step 4: Run to verify they pass** — same command, Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/converter/src/htrflow_converter/export_alto.py packages/converter/tests/test_export_alto.py packages/converter/tests/fixtures/export/alto-2500.xml
git commit -m "feat(export): scale an ALTO to its scan's size; build an empty ALTO"
```

---

### Task 3: Reading through the results proxy and the image server

**Files:**
- Modify: `packages/converter/pyproject.toml` (add `"httpx>=0.27"` with a one-line comment why), `uv.lock` (`uv lock`)
- Create: `packages/converter/src/htrflow_converter/export_http.py`
- Test: `packages/converter/tests/test_export_http.py`

**Interfaces:**
- Produces:
  - `class SessionLost(RuntimeError)`, `class Forbidden(RuntimeError)`, `class SizeUnreadable(RuntimeError)`
  - `class Results:` `__init__(self, results_url: str, user: str, password: str, client: httpx.Client)`; `login(self) -> None` (raises `SessionLost` on 401/429 with the proxy's `detail`); `get(self, key: str) -> bytes | None` (`None` on 404; `Forbidden` on 403; on 401 logs in once and retries, a second 401 → `SessionLost`)
  - `image_size(client: httpx.Client, url: str) -> tuple[int, int]` — `SizeUnreadable` on any HTTP error, non-JSON, or missing/non-positive `width`/`height`

- [ ] **Step 1: Write the failing tests** (all on `httpx.MockTransport`)

```python
def test_login_posts_the_form_with_the_sites_origin(): ...
    # request: POST https://htr.example/results/_login, form username/password,
    # header Origin == "https://htr.example"; answer 204 + Set-Cookie
def test_get_maps_404_to_none_and_403_to_forbidden(): ...
def test_an_expired_session_logs_in_once_more(): ...
    # GET answers 401 once, then 200 → bytes; exactly two _login POSTs in total
def test_a_second_401_stops_the_run(): ...
    # every GET 401 → SessionLost
def test_image_size_reads_info_json(): ...
    # {"width": 3494, "height": 2472} → (3494, 2472); 404, "not json", {"width": 0} → SizeUnreadable
```

Each test asserts on the recorded requests (method, URL, form body, `Origin`) and the return value or exception, as listed.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_export_http.py -q`
Expected: FAIL, module missing

- [ ] **Step 3: Add the dependency** — `uv add --package htrflow-converter "httpx>=0.27"`, then `uv sync`

- [ ] **Step 4: Implement `export_http.py`** — the client keeps cookies itself (the proxy sets `__Host-htr_session` on HTTPS); keys are joined under `results_url` exactly as given (callers quote the volume id).

- [ ] **Step 5: Run to verify they pass** — same command, Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/converter/pyproject.toml uv.lock packages/converter/src/htrflow_converter/export_http.py packages/converter/tests/test_export_http.py
git commit -m "feat(export): read results through the proxy's login and image sizes from info.json"
```

---

### Task 4: One volume, end to end

**Files:**
- Create: `packages/converter/src/htrflow_converter/export.py`
- Test: `packages/converter/tests/test_export_volume.py`

**Interfaces:**
- Consumes: Tasks 1–3 as declared.
- Produces:
  - `@dataclass(frozen=True) class Row:` `volume: str, outcome: str, pages: int = 0, pages_with_text: int = 0, pages_empty_filled: int = 0, pages_empty_chosen: int = 0, quality_mean: float | None = None, quality_pages: int = 0, reason: str = ""`
  - `@dataclass(frozen=True) class Options:` `target: Path, min_quality: float | None, empty_pages: frozenset[int]` (`min_quality=None` = filter off)
  - `export_volume(results: Results, client: httpx.Client, prefix: str, volume_id: str, opts: Options, dry_run: bool) -> Row` — `prefix` is `"{namespace}/{pipeline}"`
  - `swap_in(built: Path, final: Path) -> None`

Order inside `export_volume`, per the spec's "Per volume": manifest (`404` → `not finished`, `Forbidden` → `forbidden`) → quality (`no scores` / `below threshold`, with the mean in the row) → for every page: number from `page_sources` (`PageRuleError` → `failed`; a null `source_manifest` → `failed`, "not a whole volume (images: source)"; page numbers not exactly {1..`pages`} → `failed`, naming the first missing number), size from `info.json` (`SizeUnreadable` → `failed`, the page key in `reason`) → build files into `<target>/.<id>.tmp-<random>` (a page whose number is in `empty_pages` → `empty_alto`, counted `pages_empty_chosen`; `failed` status → `empty_alto`, counted `pages_empty_filled`; an `ok`/`skipped` page's ALTO `404` → `failed`; otherwise `scale_alto`, counted `pages_with_text`; `AltoMismatch` → `failed`) → check the count equals `pages` and the numbers are unique → read `manifest.json` again (gone or different → `failed`) → `swap_in`. On any `failed`, the temp folder is removed and the real folder is untouched. `dry_run` does everything but write and swap. Before building, remove any `<target>/.<id>.tmp-*` left by an earlier run.

- [ ] **Step 1: Write the failing tests** — a fake proxy + image server on one `MockTransport`, serving a three-page volume `V0000001` (page 1 ok, page 2 ok, page 3 failed; qualities 0.9, 0.8; images 5000×3538; ALTO = the Task 2 fixture), `tmp_path` as target:

```python
def test_a_good_volume_is_exported_complete():
    row = export_volume(..., Options(tmp_path, 0.75, frozenset({1})), dry_run=False)
    assert sorted(p.name for p in (tmp_path / "V0000001").iterdir()) == \
        ["V0000001_00001.xml", "V0000001_00002.xml", "V0000001_00003.xml"]
    assert (row.outcome, row.pages_with_text, row.pages_empty_chosen, row.pages_empty_filled) == \
        ("exported", 1, 1, 1)
    assert row.quality_mean == pytest.approx(0.8) and row.quality_pages == 1   # page 1 left out
    assert not list(tmp_path.glob(".*"))

def test_below_threshold_writes_nothing(): ...          # min_quality 0.95 → "below threshold", no folder
def test_no_manifest_is_not_finished(): ...
def test_a_403_is_forbidden(): ...
def test_no_scores_with_the_filter_on(): ...            # no "quality" anywhere → "no scores"
def test_no_scores_with_the_filter_off_exports(): ...   # min_quality None → "exported"
def test_an_unreadable_size_fails_and_keeps_the_old_folder():
    # target already holds V0000001/old.xml; page 2's info.json 500s
    # → outcome "failed", reason names "0002", old.xml still there, no temp folder left
def test_a_reexport_replaces_the_folder(): ...          # old.xml gone, three new files
def test_an_ok_page_without_its_alto_fails_its_volume(): ...  # ALTO 404 for page 2 → failed, old folder kept
def test_a_leftover_temp_folder_is_cleared(): ...       # .V0000001.tmp-old exists before → gone after
def test_dry_run_writes_nothing(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_export_volume.py -q`
Expected: FAIL, module missing

- [ ] **Step 3: Implement `Row`, `Options`, `export_volume`, `swap_in` in `export.py`** — `swap_in`: if `final` exists, rename it to `<target>/.<id>.old-<random>`, rename `built` to `final`, then delete the old one.

- [ ] **Step 4: Run to verify they pass** — same command, Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/converter/src/htrflow_converter/export.py packages/converter/tests/test_export_volume.py
git commit -m "feat(export): one volume — quality, sizes, pages, the check and the swap"
```

---

### Task 5: The command, the report and the docs

**Files:**
- Modify: `packages/converter/src/htrflow_converter/export.py` (add `run_export`, `write_report`)
- Modify: `packages/converter/src/htrflow_converter/cli.py` (subparser `export`, dispatch)
- Modify: `docs/reference/cli.md` (an `export` section after the command list, and its exit codes in "Exit codes")
- Modify: `scripts/loc-budget.sh` (converter budget raised to the measured count, with a comment naming the four modules)
- Test: `packages/converter/tests/test_export_cli.py`

**Interfaces:**
- Consumes: Task 4's `Row`, `Options`, `export_volume`; `parse.load`; `export_pages.parse_page_set`.
- Produces:
  - `run_export(campaign_file: Path, target: Path, *, results_url: str | None, user: str, password: str, opts: Options, report: Path, dry_run: bool, client: httpx.Client | None = None) -> int`
  - `write_report(rows: list[Row], path: Path) -> str` — writes the CSV, returns the printed table

CLI:

```
htrflow-campaigns export CAMPAIGN_FILE --to DIR
    [--min-quality 0.75 | --no-quality-filter] [--empty-pages 1]
    [--results-url URL] [--user NAME] [--report FILE] [--dry-run]
```

The repo is `CAMPAIGN_FILE.parent.parent`; `parse.load` validates it and the campaign is the one whose `name` is the file's stem. `--min-quality` and `--no-quality-filter` are mutually exclusive. The password comes from `HTRFLOW_PASSWORD` or `getpass.getpass()`.

- [ ] **Step 1: Write the failing tests** — a copy of `tests/fixtures/good` in `tmp_path` with one campaign of two volumes, the fake server from Task 4:

```python
def test_a_missing_target_is_refused_before_any_request():
    code = main(["export", str(campaign), "--to", str(tmp_path / "not-mounted")])
    assert code == 2 and requests == []                   # nothing asked of the server
    assert not (tmp_path / "not-mounted").exists()

def test_the_report_has_one_row_per_volume_and_stays_out_of_the_target():
    code = run_export(..., report=tmp_path / "r.csv")
    rows = list(csv.DictReader((tmp_path / "r.csv").open()))
    assert [r["volume"] for r in rows] == ["V0000001", "V0000002"]
    assert rows[0].keys() == {"volume", "outcome", "pages", "pages_with_text", "pages_empty_filled",
                              "pages_empty_chosen", "quality_mean", "quality_pages", "reason"}
    assert not list(target.glob("*.csv"))

def test_exit_status():                                    # below threshold + not finished → 0; one failed or forbidden → 1
def test_session_lost_stops_the_run_with_status_1(): ...
def test_the_defaults_are_075_and_page_1(): ...           # parsed args: min_quality 0.75, empty_pages {1}
def test_results_url_comes_from_converter_yaml(): ...     # the login request goes to its /_login
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_export_cli.py -q`
Expected: FAIL

- [ ] **Step 3: Implement `run_export`, `write_report` and the subparser**

- [ ] **Step 4: Run to verify they pass** — same command, Expected: PASS

- [ ] **Step 5: Docs and budget** — the `cli.md` section says what the export writes, the quality rule, the empty-page rule, scaling, the login, the report and the exit status, each in a sentence or two, linking the spec. Then `bash scripts/loc-budget.sh`, raise the converter line to the printed count with its comment.

- [ ] **Step 6: Whole-repo gate**

Run: `make ci`
Expected: exit 0 (ty, ruff check/format, the full pytest suite, the frontend and chart checks)

- [ ] **Step 7: Commit** (two commits: code + tests, then docs + budget)

```bash
git add packages/converter/src/htrflow_converter/export.py packages/converter/src/htrflow_converter/cli.py packages/converter/tests/test_export_cli.py
git commit -m "feat(export): htrflow-campaigns export — the campaign run, the report, exit status"
git add docs/reference/cli.md scripts/loc-budget.sh
git commit -m "docs(cli): the export command; converter budget for it"
```

---

### Task 6: A real volume, by hand

**Files:** none committed.

- [ ] **Step 1:** On the dev cluster, export the campaign that holds `R0001203` (`image-cache-test-v1`) through the local results proxy to a scratch folder, `--empty-pages 1`, `--no-quality-filter` (that pipeline has no quality step).
Expected: `R0001203/` with exactly 10 files `R0001203_00001.xml` … `R0001203_00010.xml`; the report row `exported`.
- [ ] **Step 2:** Compare `R0001203_00001.xml` with the spec's example byte for byte except indentation.
- [ ] **Step 3:** Validate all ten files against ALTO 4.4 (the schema and a local `xlink.xsd` stand-in in a scratch folder; LOC's `xlink.xsd` link returns 404).
Expected: all VALID.
- [ ] **Step 4:** For page 2, check `Page WIDTH/HEIGHT` equals its `info.json` and one `String`'s `HPOS` equals the original × `native_w / 2500`, rounded.
- [ ] **Step 5:** Re-run the same export: the folder is replaced, no `.R0001203.*` left behind.
