# View results

The web front serves the campaign browser at `/`, which links every volume
into the Universal Viewer at `/uv.html`: the page image, clickable line
outlines, and the ALTO text beside it.

## The viewer URL

```
<web-front-url>/uv.html#?manifest=<results-url>/<namespace>/<pipeline>/<volume>/iiif.json
```

`<results-url>` is the chart's `resultsUrl`. A volume's
`manifest.json` carries the same `iiif.json` URL as `viewer_url`. The
wrapper rewrites `iiif.json` every 10 pages, so a volume opens before it
has finished.

## A page's ALTO

Every page's ALTO XML is public at
`<results-url>/<namespace>/<pipeline>/<volume>/alto/<page>.xml`. The
run viewer (`/log`, a volume's **log** link) lists each page with two
links in its **alto** column:

- **view** opens `/alto?src=<ALTO URL>`: the page's lines in reading order,
  each tinted by its word confidence (`WC`), with a legend of the cutoffs. A
  **raw XML** button shows the source with IDs, bounding boxes and
  hyphenation; **raw** opens the untouched file. `/alto` reads only files
  under the results base, so a link cannot make it show someone else's
  text.
- **download** saves the XML as `<page>.xml`.

## Predicted page quality

A volume run with a
[`QualityPrediction` step](../reference/campaign-yaml.md#predicted-page-quality)
has a score from 0 to 1 for each page. Without the step, none of the
following appears.

- The run viewer's per-page table has a **quality** column. Select its
  header to sort the pages lowest score first, with unscored pages last;
  select it again to return to page order.
- In the Universal Viewer, the text panel shows the page's **Predicted
  quality** above its text. It reads the score from the page's ALTO.
- The **More information** panel lists **Predicted quality** for the page,
  and for the volume its mean, lowest score and pages scored. Both come
  from `iiif.json`.

Where each score is stored is in the
[S3 layout](../reference/s3-layout.md#manifestjson-completion-marker).

## Exposing the web front

A browser needs one address, the **web front**, Service `htrflow-web`, on
NodePort `web.nodePort` (default 30800) or behind an ingress controller. It
serves the campaign browser, the viewer and, under `/results`, every result
file: manifests, ALTO, progress and run logs, read from the bucket by the
results proxy. Page images are not among them; they come from the IIIF
source. Who may reach the address is set in
[Deploy → Web front access](deploy.md#web-front-access).

## Logging in

Opening the campaign browser, the viewer or any result URL without a session
shows the login page. Log in with an account on the results store; the
[deploy page](deploy.md#the-results-bucket-stays-private) says which fields
your store takes. The session lasts `results.sessionHours` (8 by default).
What you see is what the store lets your account read: a volume it refuses
shows "your account may not read this volume". Log out from the link in the
header. A login that is refused says whether the account, the store or the
number of attempts is the reason.

Keep these in mind:

- **Choose a stable results URL before real campaigns.** It is written into
  every `iiif.json` and `manifest.json` and never rewritten. The chart's
  `resultsUrl` (`https://<web front host>/results`) must equal
  `converter.yaml`'s `results_url`: the run viewer and `/alto` refuse
  addresses outside the chart's base.
- **Volumes published under another results URL must be run again** to open
  in the viewer through the proxy, since their manifests still carry the old
  address.
- **Behind port forwarding, the base is what the browser sees.** Forward the
  web front's port; the results come through it.
- **Campaign-file URLs are fetched by the campaign pod**, not your browser:
  they must resolve in-cluster and be inside `network.iiifCidrs`.
