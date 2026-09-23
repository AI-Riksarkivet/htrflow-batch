# View results

The web front serves the Universal Viewer at `/uv.html`. It is the
`universalviewer4` fork of Universal Viewer, cloned and patched into the web
image when the image is built (`UV4_REPO` in `.docker/htrflow-web.dockerfile`).
It renders IIIF Presentation 3 manifests with ALTO text overlays (canvas
`seeAlso`), including clickable per-line outlines on the page image.

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
- **download** fetches the same XML and saves it as `<page>.xml`. The results
  bucket is a different origin from the campaign browser, and browsers
  silently ignore a plain `<a download>` across origins, so the download goes
  through `fetch`, a `Blob` and a same-origin object URL instead. A failed
  download says so in one sentence rather than doing nothing.

## Exposing the web front

A browser needs two addresses:

- **The web front**, for the campaign browser, the viewer, the run viewer
  and `/api/v1/…`. The chart exposes it as Service `htrflow-web` (port 8081)
  of type NodePort on `web.nodePort`, default 30800. An ingress or load
  balancer in front of that Service works the same way.
  `network.web.ingressCidrs` limits who may connect. The web front has no
  authentication of its own, so put an authenticating proxy in front of it
  if the campaign list should not be public.
- **The results base URL** (`publicResultsBase`), for manifests, page
  images, ALTO and run logs, which the browser fetches straight from the
  bucket. The bucket's CORS rule must allow the web front's origin
  ([Deploy](deploy.md#s3-secret-bucket-policy-and-cors)). The campaign
  browser's pages may fetch from nowhere else: the web front sends them a
  `connect-src` limited to its own origin and this base.

How the three sides use these URLs:

- **Published files keep the URL they were written with.** `publicResultsBase`
  is written into every `iiif.json` and `manifest.json` as the volume runs,
  and nothing rewrites those URLs afterwards. Choose a stable address that
  browsers can reach before running real campaigns.
- **Forwarded ports: the base is what the browser sees.** When you reach the
  cluster through port forwarding (`ssh -L`, `kubectl port-forward`),
  `publicResultsBase` must be the forwarded address as the browser sees it.
  Forward the web front's port and the bucket's port together.
- **Pods never resolve `publicResultsBase` themselves.** The wrapper writes
  through `S3_ENDPOINT` from the S3 Secret. The read API, however, reads each
  running volume's `progress.json` from the bucket itself. Set
  **`web.internalResultsBase`** to an address that reaches the bucket from
  inside the cluster whenever `publicResultsBase` does not. For example,
  `publicResultsBase` might only resolve on the browser's machine, or sit
  behind an ingress the pod cannot reach. Unset, it defaults to
  `publicResultsBase`, which is correct when the two are the same address.
  When they are not, the only symptom is a campaign browser that never shows
  a running volume's progress.
- **Campaign files are fetched from inside the cluster.** Any URL you put in
  a campaign file, such as a manifest, is fetched by the campaign pod, not by
  your browser. It must resolve from inside the cluster, and its address
  must be in `network.iiifCidrs`.
