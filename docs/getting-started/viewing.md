# View results

The web front serves the Universal Viewer at `/uv.html`. It renders IIIF
Presentation 3 manifests with ALTO text overlays, including clickable
per-line outlines on the page image.

## URL scheme

The viewer takes its manifest as a URL fragment. It is served from the same
origin as the campaign browser and the read API:

```
<web-front-url>/uv.html#?manifest=<results-base-url>/<namespace>/<pipeline>/<volume>/iiif.json
```

`<results-base-url>` is the release's `publicResultsBase`. A volume's
`manifest.json` carries the same `iiif.json` URL as `viewer_url`. The wrapper
rewrites `iiif.json` every 10 pages while it runs, so a volume opens in the
viewer before it is finished.

Requesting `/` serves the [campaign browser](campaigns.md#5-watch-it), which
links into the viewer for each volume.

## Reading a page's ALTO

Every page's ALTO XML is public alongside the manifest, at
`<results-base-url>/<namespace>/<pipeline>/<volume>/alto/<page>.xml`. Once a
volume is expanded, the run viewer's per-page table (`/log?…`) links to it in
an **alto** column:

- **view** opens `/alto?src=<url of the page's ALTO XML>`. It is a text render
  of the page's lines in reading order, each tinted by its `WC` (word
  confidence) in four bands; a legend line above the text names the cutoffs.
  - A **raw XML** button next to the theme toggle swaps the text for the
    pretty-printed source, to show markup the text view leaves out: `ID`s,
    bounding boxes, and `HYP`/`SUBS_CONTENT` hyphenation detail.
  - A **raw** link opens the untouched file.
  - If a page's ALTO cannot be read, is not XML, or has no text at all, the
    view says so in one sentence.
  - It reads only a file under the results base URL, like the run viewer:
    `/alto?src=` with any other address is refused before anything is
    fetched, so a link cannot make the page show someone else's text.
- **download** saves the same XML as `<page>.xml`.

## Exposing the web front

A browser needs two addresses:

- **The web front**, for the campaign browser, the viewers and
  `/api/v1/…`: Service `htrflow-web`, on NodePort `web.nodePort` (default
  30800) or behind an ingress controller. It has no authentication of its
  own; who may reach it is set in
  [Deploy → Web front ingress](deploy.md#web-front-ingress). Put an
  authenticating proxy in front if the campaign list should not be public.
- **The results base URL** (`publicResultsBase`), for manifests, page
  images, ALTO and run logs, fetched straight from the bucket. The bucket's
  CORS rule must allow the web front's origin
  ([Deploy](deploy.md#s3-secret-bucket-policy-and-cors)), and the web front's
  pages may fetch from nowhere else.

How the pieces use these URLs:

- **Choose a stable base before real campaigns.** It is written into every
  `iiif.json` and `manifest.json` and never rewritten. The chart's
  `publicResultsBase` must equal `converter.yaml`'s `public_results_base`:
  the run viewer and `/alto` refuse addresses outside the chart's base, so
  runs published under an old base lose their logs and ALTO views (the
  viewer still opens them while the old address answers).
- **Behind port forwarding, the base is what the browser sees.** Forward the
  web front's port and the bucket's port together.
- **The read API reads progress from inside the cluster.** Set
  **`web.internalResultsBase`** to an in-cluster address of the bucket
  whenever `publicResultsBase` does not resolve from a pod. Unset, it
  defaults to `publicResultsBase`; wrong, the only symptom is a campaign
  browser that never shows a running volume's progress.
- **Campaign-file URLs are fetched by the campaign pod**, not your browser:
  they must resolve in-cluster and be inside `network.iiifCidrs`.
