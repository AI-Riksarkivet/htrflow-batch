#!/usr/bin/env sh
# Build or serve the documentation site from a staged copy of docs/ WITHOUT
# docs/features/: the stories are the backlog's product view (mirrored to
# Azure DevOps), not site content (Morgan, 2026-09-07). The generator has no
# exclude option, so the staging is the exclusion. Output lands in ./site.
set -eu
cmd=${1:-build}
[ $# -gt 0 ] && shift
stage=.docs-site
rm -rf "$stage" site
mkdir -p "$stage"
cp -R docs "$stage/docs"
rm -rf "$stage/docs/features"
cp zensical.toml "$stage/zensical.toml"
cd "$stage"
${ZENSICAL:-uvx zensical} "$cmd" "$@"
[ "$cmd" = build ] && mv site ../site
