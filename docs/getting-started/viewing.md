# View results

The web front serves the campaign browser at `/`, which links every volume
into the Universal Viewer at `/uv.html`: the page image, clickable line
outlines, and the ALTO text beside it.

## The viewer URL

```
<web-front-url>/uv.html#?manifest=<results-base-url>/<namespace>/<pipeline>/<volume>/iiif.json
```

`<results-base-url>` is the chart's `publicResultsBase`. A volume's
`manifest.json` carries the same `iiif.json` URL as `viewer_url`. The
wrapper rewrites `iiif.json` every 10 pages, so a volume opens before it
has finished.

## A page's ALTO

Every page's ALTO XML is public at
`<results-base-url>/<namespace>/<pipeline>/<volume>/alto/<page>.xml`. The
run viewer (`/log`, a volume's **log** link) lists each page with two
links in its **alto** column:

- **view** opens `/alto?src=<ALTO URL>`: the page's lines in reading order,
  each tinted by its word confidence (`WC`), with a legend of the cutoffs. A
  **raw XML** button shows the source with IDs, bounding boxes and
  hyphenation; **raw** opens the untouched file. `/alto` reads only files
  under the results base, so a link cannot make it show someone else's
  text.
- **download** saves the XML as `<page>.xml`.

## Exposing the web front

A browser needs two addresses:

- **The web front**, Service `htrflow-web`, on NodePort `web.nodePort`
  (default 30800) or behind an ingress controller. It has no
  authentication; who may reach it is set in
  [Deploy → Web front access](deploy.md#web-front-access).
- **The results base URL**, for manifests, page images, ALTO and run logs,
  fetched straight from the bucket. Its CORS rule must allow the web front's
  origin ([Deploy → Prepare the bucket](deploy.md#3-prepare-the-bucket)).

Keep these in mind:

- **Choose a stable results base before real campaigns.** It is written into
  every `iiif.json` and `manifest.json` and never rewritten. The chart's
  `publicResultsBase` must equal `converter.yaml`'s `public_results_base`:
  the run viewer and `/alto` refuse addresses outside the chart's base.
- **Behind port forwarding, the base is what the browser sees.** Forward
  the web front's port and the bucket's port together.
- **The read API reads progress from inside the cluster.** When
  `publicResultsBase` does not resolve from a pod, set
  `web.internalResultsBase` to an in-cluster address of the bucket.
  Otherwise the only symptom is a campaign browser that never shows a
  running volume's progress.
- **Campaign-file URLs are fetched by the campaign pod**, not your browser:
  they must resolve in-cluster and be inside `network.iiifCidrs`.
