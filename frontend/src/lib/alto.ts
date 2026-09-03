// The ALTO viewer's parsing (docs: wrapper/viewer.py builds the
// `<volume>/alto/<page>.xml` files this reads, one per page, alongside the
// `<volume>/iiif.json` whose `seeAlso` links to them). Namespace-agnostic:
// producers vary between a default namespace, a prefix, or none at all, so
// every lookup matches by local name via `getElementsByTagNameNS("*", …)`
// rather than assuming one.

export interface AltoLine {
  text: string;
  /** WC ("word confidence"), averaged over the line's String elements;
   * null when none carry one. */
  wc: number | null;
}

export interface AltoPage {
  lines: AltoLine[];
}

function localName(el: Element): string {
  return el.localName || el.tagName.replace(/^.*:/, "");
}

/**
 * Text and confidence for every TextLine, in document order (TextBlock then
 * TextLine then String — the order htrflow's own ALTO writer emits, which is
 * reading order for our output). A line's text is its String children's
 * `CONTENT`, joined with spaces; `SUBS_CONTENT` (the whole word a hyphenated
 * fragment reconstructs to) and `HYP` (the hyphen glyph, often redundant
 * with a trailing "-" already in `CONTENT`) are both left out, so a
 * hyphenated line renders exactly as printed on that line rather than the
 * editorially merged word. A line with no String children (or none with a
 * non-empty CONTENT) is left out of the result entirely.
 *
 * Throws when `xml` does not parse as XML at all; an XML document that
 * simply isn't ALTO (no TextLine anywhere) comes back as `{ lines: [] }` —
 * the caller decides what "no text lines" means for the reader.
 */
export function parseAlto(xml: string): AltoPage {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  if (doc.getElementsByTagName("parsererror").length > 0) {
    throw new Error("not valid XML");
  }
  const lines: AltoLine[] = [];
  const blocks = doc.documentElement.getElementsByTagNameNS("*", "TextBlock");
  for (const block of Array.from(blocks)) {
    const textLines = block.getElementsByTagNameNS("*", "TextLine");
    for (const line of Array.from(textLines)) {
      const words: string[] = [];
      const wcValues: number[] = [];
      for (const child of Array.from(line.children)) {
        if (localName(child) !== "String") continue;
        const content = child.getAttribute("CONTENT");
        if (content) words.push(content);
        const wc = Number(child.getAttribute("WC"));
        if (!Number.isNaN(wc) && child.hasAttribute("WC")) wcValues.push(wc);
      }
      if (words.length === 0) continue;
      const wc =
        wcValues.length > 0
          ? wcValues.reduce((a, b) => a + b, 0) / wcValues.length
          : null;
      lines.push({ text: words.join(" "), wc });
    }
  }
  return { lines };
}

/**
 * A page's ALTO URL from the manifest's `viewer_url`
 * (`<public_results_base>/<volume>/iiif.json`, publish.py) and its id: ALTO
 * lives at `<public_results_base>/<volume>/alto/<page>.xml`, a sibling
 * directory of the manifest — see `viewer.py`'s `seeAlso`.
 */
export function altoUrl(viewerUrl: string, page: string): string {
  const dir = viewerUrl.slice(0, viewerUrl.lastIndexOf("/"));
  return `${dir}/alto/${page}.xml`;
}

/**
 * A best-effort re-indent of an XML string for the raw-XML toggle: collapses
 * whitespace between tags, then re-indents by nesting depth. Good enough for
 * ALTO (attributes only, no mixed text content) — not a general XML
 * formatter.
 */
export function prettyXml(xml: string): string {
  const collapsed = xml.replace(/>\s+</g, "><").trim();
  let depth = 0;
  return collapsed
    .split(/(?<=>)(?=<)/)
    .map((tag) => {
      const closing = tag.startsWith("</");
      const selfClosing = /\/>$/.test(tag) || /^<[?!]/.test(tag);
      if (closing) depth = Math.max(0, depth - 1);
      const line = "  ".repeat(depth) + tag;
      if (!closing && !selfClosing) depth += 1;
      return line;
    })
    .join("\n");
}
