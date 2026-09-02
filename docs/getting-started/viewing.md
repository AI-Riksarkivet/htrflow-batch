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
