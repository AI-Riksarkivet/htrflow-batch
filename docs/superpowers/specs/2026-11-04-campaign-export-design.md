# Campaign export to a publishing folder — design

## Intent

A finished campaign is published by copying its ALTO into a shared
publishing folder (an NFS share), one folder per volume. The publishing
side takes **whole volumes only**: a volume folder must hold exactly one
ALTO file per image of the volume, so the number of ALTO files equals the
volume's page count. Today nothing produces that folder; this design adds
a command that does.

The export also decides **what is good enough to publish**: a volume
whose predicted quality is too low is not exported at all, and chosen
pages (for example the cover, page 1) are published without text.

## Goals

- One command exports one campaign to a target folder on a mounted share.
- Output layout, per volume:

  ```
  <target>/<bildid>/<bildid>_<00004>.xml
  ```

  The folder is the volume id from the campaign file. Campaigns that are
  exported list their volumes by image-series id ("bildid", e.g.
  `30003043`, `R0001203`); the image server names each image
  `<bildid>_<nnnnn>`. Each file is named after **its page's own source
  image id**, read from that page's `page_sources` URL — not assumed from
  the canvas position. A volume where any page's source image is not
  `<volume id>_<five digits>`, or where two pages carry the same number,
  fails (reported) rather than being written under guessed names. (For
  RA's manifests the number equals the canvas position, the naming the
  image cache already uses; the check is what makes that safe to rely on.)
- **Complete volumes**: every page of the volume gets a file. A page with no
  ALTO in the results bucket (a failed page) gets an **empty ALTO**.
- **Quality filter (per volume)**: average the predicted quality over the
  volume's pages, leaving out pages scored exactly 0, pages with no score,
  and pages excluded by the page filter below. A volume whose average is
  below the threshold is not exported. A volume at or above it is exported
  complete.
- **Page filter**: the **first page** of every volume (by default; other
  positions can be chosen) is written as empty ALTO, whatever its
  transcription was.
- **Full-resolution coordinates**: every exported ALTO is in the pixel space
  of the original scan (see "Scaling" below).
- **Overwrite**: re-exporting replaces a volume's folder; nothing from an
  earlier export survives in it. The share is emptied by the publishing
  side as it ingests, so a folder is usually not there at all. A volume
  this run skips is left untouched on the share (it is not deleted); the
  publishing side only picks up complete volume folders.
- A **report** at the end: one row per volume of the campaign.

## Non-goals

- No web UI, no in-cluster job: a CLI run by a person on a workstation that
  has the share mounted.
- No PAGE XML, images, IIIF manifests or metadata files in the export —
  ALTO only.
- No fixing of the transcription itself. In particular, htrflow writes word
  boxes relative to their region (a known htrflow bug, tracked separately);
  the export scales what is there and does not repair it.
- No partial volumes, ever, and no per-page quality filtering (a low page
  inside a good volume is published as transcribed).

## Anti-goals (failure even if it "works")

- A volume folder on the share with fewer ALTO files than the volume has
  pages, or with stale files from an earlier export mixed in.
- A half-written volume visible on the share (a crash or Ctrl-C mid-volume
  must leave either the old folder or the new one, never a mix).
- ALTO whose coordinates do not match the published scan — a wrong scale
  is worse than no export, so a page whose real size cannot be read stops
  that volume rather than being written at the wrong size.
- A volume that is still running, or never ran, exported from whatever
  happens to be in the bucket.

## Context: what the results bucket already holds

Facts from the code, the [S3 layout](../../reference/s3-layout.md) and a
real volume on the dev cluster:

- A campaign file names **exactly one** pipeline (`pipeline:`), and lists its
  volumes by id. A volume's results live under
  `<namespace>/<pipeline>/<volume>/`.
- `manifest.json` is written last, only when the volume is done. It carries
  `pages` (the canvas count), `results` per page key (`"0001"`, …) with
  `status` (`ok`/`failed`/`skipped`) and, for a pipeline with a
  `QualityPrediction` step, `quality` (0–1) on each scored page, and
  `page_sources` (each page's source image URL, credentials stripped).
- `alto/<page>.xml` exists for every page that is `ok` (or skipped by resume
  because an earlier run produced it). A `failed` page has none.
- Page keys are the 1-based canvas position, zero-padded to four digits
  (`f"{i:04d}"`, so a volume past 9999 pages gets wider keys).
- htrflow's ALTO is ALTO v4 (`alto-4-4.xsd`): `Description`
  (`MeasurementUnit` pixel, `sourceImageInformation/fileName` = the page
  key, `Processing` entries for htrflow and htrflow-batch), then
  `Layout/Page` with `WIDTH`, `HEIGHT`, `PHYSICAL_IMG_NR`, `ID`, and `PC`
  (the page's predicted quality) when scored, then `PrintSpace` with
  `TextBlock`/`TextLine`/`String`, `Shape/Polygon@POINTS`, and a
  `ReadingOrder`.
- **The ALTO is not at the scan's size.** The wrapper asks the image server
  for at most `MAX_IMAGE_WIDTH` (2500 px) and htrflow writes coordinates in
  the pixels it received. Measured: a page of R0001203 has
  `<Page WIDTH="2500" HEIGHT="1769">` while the scan is 3494 × 2472; image
  `30003043_00004` is 5268 px wide natively. The project's own viewer hides
  this by sizing its canvas to the ALTO; the publishing viewer draws ALTO
  over the full-resolution scan.

## The command

A new subcommand of the existing person-facing CLI (my call):

```
htrflow-campaigns export <campaign-file> --to <target-dir>
    [--min-quality 0.75 | --no-quality-filter] [--empty-pages 1]
    [--results-url URL] [--user NAME] [--report FILE] [--dry-run]
```

- `<campaign-file>` is `campaigns/<name>.yaml` in the person's campaigns
  repo checkout. Its `pipeline:` and `volumes:` say what to read; the
  namespace (the key prefix) is `namespace:` in the same repo's
  `converter.yaml`, as `render` reads it.
- `--min-quality` defaults to **0.75**, so every export filters on quality
  unless told otherwise. A volume with no scored pages at all is
  **skipped** and reported as such (it cannot be shown to meet the
  threshold). `--no-quality-filter` turns the filter off entirely, for a
  campaign whose pipeline has no quality step.
- `--empty-pages` defaults to `1`: the first page of every volume is
  published empty. It takes 1-based page positions, comma-separated, and
  ranges (`1-2`); `--empty-pages none` empties no page.
  Positions past a volume's end are ignored for that volume.
- `--dry-run` reads everything and prints the report, writes nothing.
- The results address is `results_url` in the same repo's `converter.yaml`
  (required there, e.g. `https://<web front host>/results`);
  `--results-url` overrides it.
- **Results are read through the results proxy**, with the same login a
  person uses in the browser — no store keys on the workstation and no
  direct route to the bucket needed. The CLI logs in with
  `POST {results_url}/_login` (a JSON body `{"username", "password"}`, as the browser's login page sends it; the `Origin`
  header set to the site's own origin, which the proxy requires), keeps the
  `htr_session` cookie, and reads files with `GET {results_url}/<key>`
  ([results proxy login design](2026-09-30-results-proxy-login-design.md),
  sections 2–3). The password is asked for at the prompt (never echoed,
  never a command-line argument) or taken from an environment variable for
  scripted runs. The export only ever asks for keys it can name —
  `<namespace>/<pipeline>/<volume>/manifest.json` and
  `…/alto/<page>.xml` — so the proxy's no-listing rule costs nothing. No
  server change is needed. The proxy's answers map to outcomes: `404` on
  `manifest.json` is "not finished"; `403` is "your account may not read
  this volume" (reported, the volume skipped); `401` mid-run means the
  session expired — log in again once and retry, then stop.

## Per volume

1. **Read `manifest.json`.** None: skip, reported "not finished". Its
   `pages`, `results` and `page_sources` drive everything below.
2. **Quality filter.** Compute the mean as defined in Goals. Below the
   threshold: skip, reported with the mean.
3. **Real page sizes.** For every page (ok and failed alike) read the scan's
   native `width`/`height` from the image service's `info.json`, derived
   from that page's `page_sources` URL. Any page whose size cannot be read
   (unreachable, no service, no size) fails the volume: skipped, reported
   with the page and the reason. Nothing for it is written.
4. **Build every page** into a temporary folder beside the target
   (`<target>/.<bildid>.tmp-…`, same filesystem):
   - a page with an ALTO and not in `--empty-pages`: the ALTO **scaled**;
   - a page in `--empty-pages`, and a page with no ALTO: an **empty ALTO**
     (below), built the same way for both — nothing of the page's own
     ALTO, if it has one, is carried over.
   In every file, `sourceImageInformation/fileName` is set to the
   published file stem (`<bildid>_<00004>`).
5. **Check** the temporary folder holds exactly `pages` files, one per
   page, with the names from the source image ids, each well-formed
   ALTO v4.
6. **Swap in**: replace `<target>/<bildid>` with the temporary folder (move
   the old one aside, rename the new one in, delete the old one). A
   directory cannot be renamed over a non-empty one, so there is a moment
   with no `<bildid>` folder; that is acceptable (the publishing side
   picks up only complete folders), and a half-written folder never
   carries the real name.

A failure in one volume is reported and the export moves on to the next;
the exit status is non-zero if any volume failed for a reason other than
the quality filter, "not finished", or no scores.

## Scaling

The scan the publishing viewer shows is the image RA's IIIF server
serves, so its `info.json` `width`/`height` is the target size.

Per page, `sx = native_width / ALTO WIDTH`, `sy = native_height / ALTO
HEIGHT`. Every `HPOS`, `WIDTH` is multiplied by `sx`, every `VPOS`, `HEIGHT`
by `sy`, and every `Polygon@POINTS` pair by `(sx, sy)`, on every element that
has them (`Page`, `PrintSpace`, `TextBlock`, `TextLine`, `String`, …).
Results are rounded to integers. The `Page` ends up exactly at the native
size. A page already at native size is copied unchanged.

The aspect ratios of the ALTO and the scan should agree to within rounding;
a page where they do not (by more than ~1%) means the ALTO was made from a
different image, and fails the volume like an unreadable size.

## Empty ALTO

A valid ALTO v4 document that says nothing about processing: a
`Description` with only `MeasurementUnit` (`pixel`) and
`sourceImageInformation/fileName` (the published stem) — **no
`Processing` entries**, since no model's output is on the page — and
`Layout/Page` with `WIDTH`/`HEIGHT` = the scan's native size,
`PHYSICAL_IMG_NR` = the page position, `ID` as htrflow writes it
(`_<page key>`), and an empty `PrintSpace` covering the whole page
(`HPOS=0 VPOS=0 WIDTH HEIGHT` = the page). No `PC`: an empty page carries no
quality claim. No `ReadingOrder`.

For R0001203 page 1 (scan 3494 × 2472), checked valid against the official
ALTO 4.4 schema:

```xml
<?xml version='1.0' encoding='UTF-8'?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:schemaLocation="http://www.loc.gov/standards/alto/ns-v4# http://www.loc.gov/standards/alto/v4/alto-4-4.xsd">
  <Description>
    <MeasurementUnit>pixel</MeasurementUnit>
    <sourceImageInformation>
      <fileName>R0001203_00001</fileName>
    </sourceImageInformation>
  </Description>
  <Layout>
    <Page WIDTH="3494" HEIGHT="2472" PHYSICAL_IMG_NR="1" ID="_0001">
      <PrintSpace HPOS="0" VPOS="0" WIDTH="3494" HEIGHT="2472"/>
    </Page>
  </Layout>
</alto>
```

Pages with text keep htrflow's `Description` (its `Processing` entries
included) and get `PHYSICAL_IMG_NR` = the page position as well.

## Report

Printed as a table, and written as CSV to the current directory (or
`--report <file>`), never into the target folder: one row per volume in the
campaign file, with `volume`, `outcome` (exported / below threshold / not
finished / no scores / failed), `pages`, `pages_with_text`,
`pages_empty_filled` (no ALTO), `pages_empty_chosen` (`--empty-pages`),
`quality_mean`, `quality_pages` (pages in the mean), and `reason`.

## Left to the builder

- Module layout inside the converter package, and the HTTP client for the
  proxy and the image server — one new dependency at most (httpx, as the
  wrapper and web front already use), nothing heavy (no htrflow, no torch,
  no S3 client).
- XML handling library (lxml or the standard library), as long as
  namespaces and the declaration survive and output is well-formed.
- Concurrency for the `info.json` reads and the volume loop, and retries.
- Exact CLI help text, the table format, and the temporary-folder naming.
- Tests: unit tests for scaling, emptying, the quality mean, page-range
  parsing and the swap; one end-to-end test against a fake bucket and a
  fake image server producing a complete folder.

## Calls made in this design (not from the requester)

- The command is `htrflow-campaigns export`, reading the campaign file.
- Results are read only through the results proxy, with the browser login.
- File names come from each page's source image id, checked against the
  volume id.
- Excluded pages also leave the quality average.
- Unscored pages leave the average; a threshold with no scored pages at all
  skips the volume.
- Image size comes from each page's `info.json`, for every page.
- One failing volume does not stop the others.
- The aspect-ratio guard, and dropping `PC` from empty pages.
- `PHYSICAL_IMG_NR` set to the page position on every page (htrflow writes 0).
- `fileName` rewritten to the published stem.
