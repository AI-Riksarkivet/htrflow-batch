# Viewing Results

Results are served through the Riksarkivet `universalviewer4` fork, which
renders IIIF Presentation 3 manifests with ALTO text overlays (canvas
`seeAlso`), including clickable per-line outlines on the page image.

## URL scheme

The viewer takes its manifest as a URL fragment, on the web front's port
(the same origin that serves the campaign browser and the read API):

```
http://<web-host>/uv.html#?manifest=<url to iiif.json>
```

For example, the PoC's mock volume:

```
http://localhost:30800/uv.html#?manifest=http://localhost:30900/htr-results/demo-v1/mock-vol/iiif.json
```

Requesting `/` serves the [campaign browser](campaigns.md#4-watch-it), which
links into the viewer per volume. (Through chart 0.3.0 a `defaultManifest`
value could 302 `/` into UV instead; it was deprecated and is gone in 0.4.0
— bookmark the `uv.html#?manifest=…` URL instead.)

## Reading a page's ALTO

Every page's ALTO XML is public alongside the manifest
(`<public_results_base>/<volume>/alto/<page>.xml`), and the run viewer's
per-page table (`/log?…`) links straight to it, once expanded, in an
**alto** column:

- **view** opens `/alto?src=<url to the page's ALTO XML>` — a text render of
  the page's lines, in reading order, each tinted by its `WC` (word
  confidence) in four bands (a legend line above the text names the
  cutoffs). A **raw XML** button next to the theme toggle swaps the text for
  the pretty-printed source, for a look at markup the text view leaves out
  (`ID`s, bounding boxes, `HYP`/`SUBS_CONTENT` hyphenation detail); a **raw**
  link opens the untouched file. A page whose ALTO can't be read, isn't XML,
  or has no text at all says so in one sentence.
- **download** fetches the same XML and saves it as `<page>.xml` — the
  results bucket is a different origin from the campaign browser, and a
  plain `<a download>` is silently ignored across origins, so this goes
  through `fetch` + `Blob` + a same-origin object URL instead. A failed
  download says so in one sentence rather than doing nothing.

## Reaching the web front over ssh (PoC / bare-k3s)

On the bare-k3s PoC host, the web front and RustFS are exposed as NodePorts
(30800 and 30900) that aren't routable from a laptop directly, and the
node's own hostname resolves IPv6-only (see [Prerequisites](index.md)) so
tunnelling straight to the hostname doesn't work either. Tunnel both ports
to the node's pinned IPv4 address instead:

```bash
ssh -L 30800:10.16.51.53:30800 -L 30900:10.16.51.53:30900 <ssh-host>
```

`<ssh-host>` is any machine you can ssh to that reaches 10.16.51.53 (e.g.
your coder host); the `-L` targets resolve on the far side. Both ports are
required — the page and the read API come from 30800 but the manifest,
images and ALTO come from 30900.

Then open `http://localhost:30800/` in a browser on your laptop.

## The localhost-URL caveat

Because the tunnel maps both NodePorts to `localhost` on your laptop, the
demo `iiif.json` must itself be built with `http://localhost:30900` URLs
(not the node's real IP) — a
manifest built with the node's IP would be unreachable from inside the
tunnel. This is a **PoC-only artifact** of ssh port-forwarding: production
deployments need a real, browser-reachable `PUBLIC_RESULTS_BASE` behind an
ingress rather than a tunnel, so this rewriting doesn't apply once you're
past the bare-k3s replay path (see [Deploy](deploy.md#production-shaped-install)).
