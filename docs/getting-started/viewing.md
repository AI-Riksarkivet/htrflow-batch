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

Every page's ALTO XML is under the results URL, behind the login, at
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

Log in with an account on the results store; the
[deploy page](deploy.md#the-results-bucket-stays-private) says which fields
your store takes. The session lasts `results.sessionHours` (8 by default).
**Log out** in the header ends it in this browser.

Without a session, or once it has expired:

- The **campaign browser**, the **run viewer** (`/log`) and `/alto` go to
  the login page, and back to where you were after you log in.
- The **viewer** (`/uv.html`) asks for a manifest under the site's own
  `/results/` before it loads it, and shows **Log in to open this volume.**
  with a link to the login page that comes back to the same volume. A
  manifest from anywhere else loads as before.
- A result URL opened directly answers `401` with a short JSON body.

What you see is what the store lets your account read:

- A volume on a campaign card whose progress file the store refuses your
  account shows **Your account may not read this volume.** where its
  progress would be, and its id does not open the viewer. The card asks
  again every few seconds, so a permission granted on the store shows on a
  later refresh.
- The viewer shows **Your account may not read this volume.** instead of
  loading a refused manifest.
- The run viewer and `/alto` show **Your account may not read this file.**
  for a log or ALTO file the store refuses.

A login the proxy refuses says why: the store did not accept the user name
or password; the page is not on the site's own address (the proxy in front
does not pass the browser's host on); too many failed attempts, per address
(wait a minute) or per user (wait five minutes), or too many logins at once;
the user name or password is too long or malformed; or what did not answer
(the store, or the results service itself).

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
