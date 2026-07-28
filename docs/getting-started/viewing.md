# Viewing Results

Results are served through the Riksarkivet `universalviewer4` fork, which
renders IIIF Presentation 3 manifests with ALTO text overlays (canvas
`seeAlso`), including clickable per-line outlines on the page image.

## URL scheme

The viewer takes its manifest as a URL fragment:

```
http://<viewer-host>/uv.html#?manifest=<url to iiif.json>
```

For example, the PoC's mock volume:

```
http://localhost:30800/uv.html#?manifest=http://localhost:30900/htr-results/demo-v1/mock-vol/iiif.json
```

Requesting `/` on the viewer 302-redirects (a *relative* redirect — nginx
runs with `absolute_redirect off`, otherwise a NodePort URL gets dropped
from the Location header) to `uv.html#?manifest=...` using
`viewer.defaultManifest` from the chart values, so you can bookmark just
the viewer's root URL once a default manifest is configured.

## Reaching the viewer over ssh (PoC / bare-k3s)

On the bare-k3s PoC host, the viewer and RustFS are exposed as NodePorts
(30800 and 30900) that aren't routable from a laptop directly, and the
node's own hostname resolves IPv6-only (see [Prerequisites](index.md)) so
tunnelling straight to the hostname doesn't work either. Tunnel both ports
to the node's pinned IPv4 address instead:

```bash
ssh -L 30800:10.16.51.53:30800 -L 30900:10.16.51.53:30900 <ssh-host>
```

`<ssh-host>` is any machine you can ssh to that reaches 10.16.51.53 (e.g.
your coder host); the `-L` targets resolve on the far side. Both ports are
required — the viewer page comes from 30800 but the manifest, images and
ALTO come from 30900.

Then open `http://localhost:30800/` in a browser on your laptop.

## The localhost-URL caveat

Because the tunnel maps both NodePorts to `localhost` on your laptop, the
demo `iiif.json` and the viewer's redirect target must themselves be built
with `http://localhost:{30900,30800}` URLs (not the node's real IP) — a
manifest built with the node's IP would be unreachable from inside the
tunnel. This is a **PoC-only artifact** of ssh port-forwarding: production
deployments need a real, browser-reachable `PUBLIC_RESULTS_BASE` behind an
ingress rather than a tunnel, so this rewriting doesn't apply once you're
past the bare-k3s replay path (see [Deploy](deploy.md#production-shaped-install)).
