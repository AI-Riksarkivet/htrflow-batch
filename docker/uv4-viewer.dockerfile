# UV4 viewer (Riksarkivet universalviewer4 fork) as a static nginx site.
# Build context = the universalviewer4 repo root (dist/ must exist, see below):
#   cd ~/universalviewer4
#   NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt npm install && npm run build
#   docker build -f ~/htrflow-batch/docker/uv4-viewer.dockerfile -t 127.0.0.1:30500/uv4:v1 .
# (NODE_EXTRA_CA_CERTS needed on RA hosts: firewall TLS interception vs node's bundled CAs)
FROM nginx:alpine
COPY dist /usr/share/nginx/html
