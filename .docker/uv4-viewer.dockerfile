# UV4 viewer (Riksarkivet universalviewer4 fork) as a static nginx site.
# Build context = the universalviewer4 repo root (dist/ must exist, see below):
#   cd ~/universalviewer4
#   NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt npm install && npm run build
#   docker build -f ~/htrflow-batch/.docker/uv4-viewer.dockerfile -t 127.0.0.1:30500/uv4:v1 .
# (NODE_EXTRA_CA_CERTS needed on RA hosts: firewall TLS interception vs node's bundled CAs)
#
# v2: apply uv4-uv-html.patch (.docker/) to src/uv.html before building — the stock
# page never fetches uv-iiif-config.json (fetch commented out), so textRightPanelEnabled
# stays false and the ALTO transcription panel never shows. The panel additionally
# requires the manifest to declare a IIIF search service (see DESIGN.md D19 notes).
FROM nginx:alpine
COPY dist /usr/share/nginx/html
