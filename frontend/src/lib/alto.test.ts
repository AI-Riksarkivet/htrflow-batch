import { describe, expect, test } from "vitest";
import { altoUrl, parseAlto, prettyXml } from "./alto.js";

// A default-namespaced ALTO with three lines: plain words with WC, a
// hyphenated fragment (SUBS_CONTENT + a following HYP) that must render as
// printed rather than as the reconstructed whole word, and a line with no
// WC on any of its String elements.
const XML = `<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout>
    <Page ID="page_1" WIDTH="1000" HEIGHT="1500">
      <PrintSpace>
        <TextBlock ID="block_1">
          <TextLine ID="line_1">
            <String CONTENT="Hello" WC="0.95"/>
            <SP/>
            <String CONTENT="world" WC="0.62"/>
          </TextLine>
          <TextLine ID="line_2">
            <String CONTENT="exam-" WC="0.88" SUBS_TYPE="HypPart1" SUBS_CONTENT="example"/>
            <HYP CONTENT="-"/>
          </TextLine>
          <TextLine ID="line_3">
            <String CONTENT="no" />
            <SP/>
            <String CONTENT="confidence" />
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>`;

describe("parseAlto", () => {
  test("joins a line's String CONTENT with spaces and averages its WC", () => {
    const page = parseAlto(XML);
    expect(page.lines[0]).toEqual({
      text: "Hello world",
      wc: (0.95 + 0.62) / 2,
    });
  });

  test("a hyphenated line renders as printed: CONTENT's trailing '-', not SUBS_CONTENT, and HYP contributes nothing extra", () => {
    const page = parseAlto(XML);
    expect(page.lines[1]).toEqual({ text: "exam-", wc: 0.88 });
  });

  test("a line with no WC on any String comes back with wc: null", () => {
    const page = parseAlto(XML);
    expect(page.lines[2]).toEqual({ text: "no confidence", wc: null });
  });

  test("throws on text that is not XML at all", () => {
    expect(() => parseAlto("not xml <<<")).toThrow();
  });

  test("valid XML with no TextLine anywhere yields no lines, not an error", () => {
    expect(parseAlto("<root><child/></root>")).toEqual({ lines: [] });
  });
});

describe("altoUrl", () => {
  test("swaps iiif.json for alto/<page>.xml alongside it", () => {
    expect(altoUrl("https://bucket/htr-results/v1/vol/iiif.json", "0007")).toBe(
      "https://bucket/htr-results/v1/vol/alto/0007.xml",
    );
  });
});

describe("prettyXml", () => {
  test("re-indents a collapsed document by nesting depth", () => {
    expect(prettyXml("<a><b/><c><d/></c></a>")).toBe(
      "<a>\n  <b/>\n  <c>\n    <d/>\n  </c>\n</a>",
    );
  });

  test("collapses existing whitespace before re-indenting, so already-pretty input is idempotent-looking", () => {
    expect(prettyXml("<a>\n   <b/>\n</a>")).toBe("<a>\n  <b/>\n</a>");
  });
});
