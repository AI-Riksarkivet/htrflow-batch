#!/usr/bin/env bash
# Build every deck under docs/slides/ into site/slides/ as HTML, PDF and PPTX.
#
# Both tools below drive a headless Chromium, and neither downloads one here.
# Point them at a browser you already have:
#
#   CHROME_PATH                 the browser Marp renders with
#   PUPPETEER_EXECUTABLE_PATH   the browser the Mermaid CLI renders with
#
#   CHROME_PATH=/path/to/chrome PUPPETEER_EXECUTABLE_PATH=/path/to/chrome \
#     scripts/slides.sh
#
# Per deck, in order: each ```mermaid fence is written to
# docs/slides/assets/<deck>-<n>.mmd and rendered to an SVG beside it (skipped
# when the SVG is newer than the fence), the fence is replaced by an image
# reference in a temporary copy of the deck, and Marp renders that copy.
#
# A fence's info string carries an optional Marp image directive, so a deck
# controls how large its diagram is drawn:  ```mermaid h:420
#
# Fonts come from Google Fonts at render time, so the first build needs
# network access. Decks live in docs/slides and are NOT part of the
# documentation site (scripts/docs-site.sh stages docs/ without them).
set -euo pipefail

cd "$(dirname "$0")/.."

SLIDES_DIR=docs/slides
ASSETS_DIR="$SLIDES_DIR/assets"
THEME="$SLIDES_DIR/theme/riksarkivet.css"
MERMAID_CONFIG="$SLIDES_DIR/theme/mermaid.json"
OUT_DIR=site/slides

MARP=${MARP:-"bunx --yes @marp-team/marp-cli@4"}
MMDC=${MMDC:-"bunx --yes @mermaid-js/mermaid-cli"}

if [ -n "${CHROME_PATH:-}" ] && [ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ]; then
  export PUPPETEER_EXECUTABLE_PATH="$CHROME_PATH"
fi
if [ -n "${PUPPETEER_EXECUTABLE_PATH:-}" ] && [ -z "${CHROME_PATH:-}" ]; then
  export CHROME_PATH="$PUPPETEER_EXECUTABLE_PATH"
fi

# Puppeteer refuses some binaries unless the path arrives through its own
# config file, so write one and pass it with -p.
PUPPETEER_CONFIG=$(mktemp -t puppeteer-XXXXXX.json)
trap 'rm -f "$PUPPETEER_CONFIG"' EXIT
printf '{"executablePath": "%s", "args": ["--no-sandbox"]}\n' \
  "${PUPPETEER_EXECUTABLE_PATH:-}" >"$PUPPETEER_CONFIG"

mkdir -p "$OUT_DIR" "$ASSETS_DIR"

for deck in "$SLIDES_DIR"/*.md; do
  [ -e "$deck" ] || { echo "no decks in $SLIDES_DIR"; exit 0; }
  name=$(basename "$deck" .md)
  build="$SLIDES_DIR/.$name.build.md"

  # Split the deck into mermaid sources plus a copy that points at the SVGs.
  # The build copy stays in docs/slides so its relative asset paths hold.
  python3 - "$deck" "$build" "$ASSETS_DIR" "$name" <<'PY'
import pathlib, re, sys

src, build, assets, name = sys.argv[1:5]
text = pathlib.Path(src).read_text(encoding="utf-8")
fence = re.compile(r"^```mermaid[ \t]*(.*?)\n(.*?)^```[ \t]*$", re.M | re.S)
count = 0


def take(match: "re.Match[str]") -> str:
    global count
    count += 1
    directive = match.group(1).strip()
    mmd = pathlib.Path(assets) / f"{name}-{count}.mmd"
    body = match.group(2)
    if not mmd.exists() or mmd.read_text(encoding="utf-8") != body:
        mmd.write_text(body, encoding="utf-8")
    return f"![{directive}](assets/{name}-{count}.svg)"


pathlib.Path(build).write_text(fence.sub(take, text), encoding="utf-8")
print(f"{name}: {count} mermaid diagram(s)")
PY

  for mmd in "$ASSETS_DIR/$name"-*.mmd; do
    [ -e "$mmd" ] || continue
    svg="${mmd%.mmd}.svg"
    if [ -f "$svg" ] && [ "$svg" -nt "$mmd" ]; then
      echo "  $(basename "$svg") up to date"
      continue
    fi
    echo "  rendering $(basename "$svg")"
    $MMDC -p "$PUPPETEER_CONFIG" -c "$MERMAID_CONFIG" \
      -i "$mmd" -o "$svg" -b transparent >/dev/null
  done

  for fmt in html pdf pptx; do
    echo "  $OUT_DIR/$name.$fmt"
    $MARP --theme "$THEME" --html --allow-local-files \
      --"$fmt" -o "$OUT_DIR/$name.$fmt" "$build" >/dev/null
  done

  rm -f "$build"
done

echo "decks in $OUT_DIR"
