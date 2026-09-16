#!/usr/bin/env bash
# Build every deck under docs/slides/ into site/slides/ as HTML, PDF and PPTX.
#
# Both tools below drive a headless Chromium, and neither downloads one here.
# Point them at a browser you already have:
#
#   CHROME_PATH                 the browser Marp renders with
#   PUPPETEER_EXECUTABLE_PATH   the browser the Mermaid CLI renders with
#
# Setting either one sets the other. Both renderers run with Chromium's
# sandbox off, because it needs user namespaces this host may not allow.
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
# Fonts are bundled, not fetched: docs/slides/theme/fonts holds Open Sans as
# woff2 (inlined in the theme) and as ttf (what fontconfig reads). This script
# points fontconfig at that directory for the run, because Mermaid MEASURES
# every label with the font the browser resolves, and a fallback would size
# each node box against the wrong metrics. Each rendered SVG then gets the
# regular face embedded as a data URI, so the committed file draws the same
# outside this build as inside it.
#
# Every deck at the top of docs/slides is PUBLISHED: the documentation
# workflow runs this script after the site build, and the site's
# Presentations page links each deck's HTML and PDF. So the decks meet the
# site's content rules and are linted the same way before anything renders.
# Superseded decks live in docs/slides/archive and are neither linted nor
# built. The Marp sources themselves stay out of the site pages
# (scripts/docs-site.sh stages docs/ without them).
#
#   SLIDES_FORMATS   which files to write per deck (default: html pdf pptx)
#   MERMAID          "render" (default) re-renders an SVG older than its
#                    fence; "skip" never renders and fails on a missing SVG,
#                    for CI, where a checkout's mtimes say nothing about
#                    which file is newer and the committed SVGs are the truth
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/.."

SLIDES_DIR=docs/slides
ASSETS_DIR="$SLIDES_DIR/assets"
THEME="$SLIDES_DIR/theme/riksarkivet.css"
MERMAID_CONFIG="$SLIDES_DIR/theme/mermaid.json"
OUT_DIR=site/slides

MARP=${MARP:-"bunx --yes @marp-team/marp-cli@4.5.1"}
MMDC=${MMDC:-"bunx --yes @mermaid-js/mermaid-cli@11.17.0"}
SLIDES_FORMATS=${SLIDES_FORMATS:-"html pdf pptx"}
MERMAID=${MERMAID:-render}

# One trap for everything this run creates, set before the first of it, so
# a failed lint, a missing diagram or a Marp error leaves nothing behind.
LINT_DIR="" PUPPETEER_CONFIG="" FONT_CONF="" FONT_CACHE="" build=""
trap 'rm -rf "$LINT_DIR" "$PUPPETEER_CONFIG" "$FONT_CONF" "$FONT_CACHE"; [ -z "$build" ] || rm -f "$build"' EXIT

LINT_DIR=$(mktemp -d -t slides-lint-XXXXXX)
cp "$SLIDES_DIR"/*.md "$LINT_DIR"/
python3 "$SCRIPT_DIR/docs_lint.py" "$LINT_DIR"

if [ -n "${CHROME_PATH:-}" ] && [ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ]; then
  export PUPPETEER_EXECUTABLE_PATH="$CHROME_PATH"
fi
if [ -n "${PUPPETEER_EXECUTABLE_PATH:-}" ] && [ -z "${CHROME_PATH:-}" ]; then
  export CHROME_PATH="$PUPPETEER_EXECUTABLE_PATH"
fi

# Chromium's sandbox needs unprivileged user namespaces, which many hosts now
# restrict. Both renderers open only files from this checkout, so both are run
# with the sandbox off: CHROME_NO_SANDBOX for Marp, and --no-sandbox in the
# Puppeteer config for the Mermaid CLI. Override CHROME_NO_SANDBOX to keep it.
export CHROME_NO_SANDBOX=${CHROME_NO_SANDBOX:-true}

# Puppeteer refuses some binaries unless the path arrives through its own
# config file, so write one and pass it with -p.
PUPPETEER_CONFIG=$(mktemp -t puppeteer-XXXXXX.json)
FONT_CONF=$(mktemp -t slides-fonts-XXXXXX.conf)
FONT_CACHE=$(mktemp -d -t slides-fontcache-XXXXXX)
printf '{"executablePath": "%s", "args": ["--no-sandbox"]}\n' \
  "${PUPPETEER_EXECUTABLE_PATH:-}" >"$PUPPETEER_CONFIG"

# Both renderers resolve fonts through fontconfig, never through the theme's
# data URIs. Hand them a config that keeps the system one and adds the
# bundled faces: nothing is installed into the user's home, and the file is
# written fresh on every run, so this is idempotent and leaves nothing behind.
FONTS_DIR="$PWD/$SLIDES_DIR/theme/fonts"
cat >"$FONT_CONF" <<XML
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>$FONTS_DIR</dir>
  <cachedir>$FONT_CACHE</cachedir>
</fontconfig>
XML
export FONTCONFIG_FILE="$FONT_CONF"

mkdir -p "$OUT_DIR" "$ASSETS_DIR"

for deck in "$SLIDES_DIR"/*.md; do
  [ -e "$deck" ] || { echo "no decks in $SLIDES_DIR"; exit 0; }
  name=$(basename "$deck" .md)
  build="$SLIDES_DIR/.$name.build.md"

  # Split the deck into mermaid sources plus a copy that points at the SVGs.
  # The build copy stays in docs/slides so its relative asset paths hold.
  python3 - "$deck" "$build" "$ASSETS_DIR" "$name" "$MERMAID" <<'PY'
import pathlib, re, sys

src, build, assets, name, mode = sys.argv[1:6]
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
        if mode == "skip":
            # CI renders nothing, so a fence that no longer matches its
            # committed .mmd would publish the old diagram without a word.
            sys.exit(f"{mmd.name} does not match its fence in {pathlib.Path(src).name}: run scripts/slides.sh locally and commit the diagram")
        mmd.write_text(body, encoding="utf-8")
    return f"![{directive}](assets/{name}-{count}.svg)"


pathlib.Path(build).write_text(fence.sub(take, text), encoding="utf-8")
print(f"{name}: {count} mermaid diagram(s)")
PY

  for mmd in "$ASSETS_DIR/$name"-*.mmd; do
    [ -e "$mmd" ] || continue
    svg="${mmd%.mmd}.svg"
    if [ "$MERMAID" = skip ]; then
      [ -f "$svg" ] || { echo "missing $(basename "$svg"): run scripts/slides.sh locally and commit it"; exit 1; }
      continue
    fi
    if [ -f "$svg" ] && [ "$svg" -nt "$mmd" ]; then
      echo "  $(basename "$svg") up to date"
      continue
    fi
    echo "  rendering $(basename "$svg")"
    $MMDC -p "$PUPPETEER_CONFIG" -c "$MERMAID_CONFIG" \
      -i "$mmd" -o "$svg" -b transparent >/dev/null
    python3 "$SCRIPT_DIR/slides_embed_font.py" \
      "$svg" "$FONTS_DIR/OpenSans-Regular.woff2"
  done

  # --html allows raw HTML in the markdown (the column and table helpers).
  # The output format comes from -o, and needs a flag of its own only where
  # Marp asks for one — passing --html as a format would collide with it.
  for fmt in $SLIDES_FORMATS; do
    echo "  $OUT_DIR/$name.$fmt"
    case "$fmt" in
      html) flag="" ;;
      *) flag="--$fmt" ;;
    esac
    # shellcheck disable=SC2086  # $flag is one optional word, or none
    $MARP --no-stdin --theme "$THEME" --html --allow-local-files \
      $flag -o "$OUT_DIR/$name.$fmt" "$build" >/dev/null
  done

  # The HTML refers to its diagrams and pictures as assets/<file>, relative
  # to itself; the PDF and PPTX embed them. So the files an HTML deck names
  # go beside it, and nothing else from assets/ is published.
  case " $SLIDES_FORMATS " in
    *" html "*)
      mkdir -p "$OUT_DIR/assets"
      { grep -o 'assets/[A-Za-z0-9._-]*' "$OUT_DIR/$name.html" || true; } | sort -u |
        while read -r ref; do cp "$SLIDES_DIR/$ref" "$OUT_DIR/$ref"; done
      ;;
  esac

  rm -f "$build"
  build=""
done

echo "decks in $OUT_DIR"
