# UV4 viewer (Riksarkivet universalviewer4 fork) + the campaign browser SPA,
# as a static nginx site.
#
# Build context = the universalviewer4 repo root, with the SPA staged into it
# as campaign-app/. `make viewer-image` does both steps:
#   cd ~/universalviewer4
#   NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt npm install && npm run build
#   cp -r ~/htrflow-batch/frontend/dist ~/universalviewer4/campaign-app
#   docker build -f ~/htrflow-batch/.docker/uv4-viewer.dockerfile -t 127.0.0.1:30500/uv4:dev .
# (NODE_EXTRA_CA_CERTS needed on RA hosts: firewall TLS interception vs node's bundled CAs)
#
# v2: apply uv4-uv-html.patch (.docker/) to src/uv.html before building — the stock
# page never fetches uv-iiif-config.json (fetch commented out), so textRightPanelEnabled
# stays false and the ALTO transcription panel never shows. The panel additionally
# requires the manifest to declare a IIIF search service (see DESIGN.md D19 notes).
#
# v3: the campaign browser is the front door at /; UV keeps /uv.html.
#
# nginx-unprivileged runs as UID 101 and listens on 8080, not 80 — the chart's
# containerPort, Service targetPort and nginx `listen` all follow (NodePort 30800
# is unchanged).
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY dist /usr/share/nginx/html
# Campaign browser SPA (staged as campaign-app/ by `make viewer-image` or
# dagger BuildViewer) — overwrites UV's demo index.html; UV itself lives at
# /uv.html and is untouched.
COPY campaign-app /usr/share/nginx/html
