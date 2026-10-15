#!/usr/bin/env bash
# Which images a change can reach: reads changed paths (one per line) on
# stdin, prints `wrapper=true|false` and `web=true|false` for $GITHUB_OUTPUT.
# ci.yml's wrapper and web scans build their image from scratch (~9 and ~3
# minutes, no cache on a runner), so a push to main that touches nothing an
# image is built from skips its scan. Each list is wide on purpose: all of
# .docker/, .dagger/ and .github/, every pyproject.toml and the workspace
# lock count for both, so a path this script does not know is never what
# turns a scan off. The weekly security workflow scans the published images
# whatever changed, which is where a new advisory against an unchanged
# image shows up.
set -euo pipefail

shared='^(\.docker/|\.dagger/|\.github/|scripts/image-inputs-changed\.sh$|uv\.lock$|pyproject\.toml$|packages/[^/]+/pyproject\.toml$)'
wrapper="$shared|^packages/wrapper/"
web="$shared|^packages/web/|^frontend/"

paths=$(cat)
hit() { printf '%s\n' "$paths" | grep -qE "$1" && echo true || echo false; }
echo "wrapper=$(hit "$wrapper")"
echo "web=$(hit "$web")"
