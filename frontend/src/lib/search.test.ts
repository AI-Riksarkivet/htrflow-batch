import { describe, expect, it } from "vitest";
import {
  altoUrl,
  buildParams,
  buildQ,
  escapeToken,
  PAGE_ROWS,
  pageCount,
  pageOf,
  parseAnswer,
  QuerySyntaxError,
  snippetFor,
  type Hit,
} from "./search.js";

describe("escapeToken", () => {
  it("leaves an ordinary word alone", () => {
    expect(escapeToken("helsa")).toBe("helsa");
  });
  it("escapes the parser's special characters", () => {
    expect(escapeToken("(AND)")).toBe("\\(AND\\)");
    expect(escapeToken("a:b")).toBe("a\\:b");
    expect(escapeToken('"quoted"')).toBe('\\"quoted\\"');
  });
});

describe("buildQ", () => {
  it("searches one short token exactly", () => {
    expect(buildQ("och")).toBe("content_txt:och");
  });
  it("fuzzes a token long enough for an HTR misreading", () => {
    expect(buildQ("helsa")).toBe("content_txt:helsa~1");
  });
  it("ANDs the tokens of a multi-word search", () => {
    expect(buildQ("god helsa")).toBe("content_txt:(god helsa~1)");
  });
  it("trims and collapses whitespace", () => {
    expect(buildQ("  bref   eder ")).toBe("content_txt:(bref~1 eder~1)");
  });
});

describe("buildParams", () => {
  it("carries the query, paging and highlighting", () => {
    const p = buildParams({ words: "helsa", pipelines: [], start: 0 });
    expect(p.get("q")).toBe("content_txt:helsa~1");
    expect(p.get("rows")).toBe(String(PAGE_ROWS));
    expect(p.get("start")).toBe("0");
    expect(p.get("hl")).toBe("on");
    expect(p.get("facet.field")).toBe("pipeline_s");
  });
  it("turns every selected pipeline into its own fq", () => {
    const p = buildParams({
      words: "x",
      pipelines: ["a-v1", "b-v2"],
      start: 40,
    });
    expect(p.getAll("fq")).toEqual(['pipeline_s:"a-v1"', 'pipeline_s:"b-v2"']);
    expect(p.get("start")).toBe("40");
  });
});

const ANSWER = {
  response: {
    numFound: 3,
    docs: [
      { id: "p/v1/0001", pipeline_s: "p", volume_s: "v1", page_s: "0001" },
      { id: "p/v2/0001", pipeline_s: "p", volume_s: "v2", page_s: "0001" },
    ],
  },
  highlighting: { "p/v1/0001": { content_txt: ["a <em>helsa</em> hit"] } },
  facet_counts: { facet_fields: { pipeline_s: ["p-v1", 2, "p-v2", 1] } },
};

describe("parseAnswer", () => {
  it("reads hits, highlighting and the pipeline facet", () => {
    const a = parseAnswer(200, ANSWER);
    expect(a.numFound).toBe(3);
    expect(a.hits).toHaveLength(2);
    expect(a.pipelines).toEqual([
      { value: "p-v1", count: 2 },
      { value: "p-v2", count: 1 },
    ]);
  });
  it("accepts an answer without facets or highlighting", () => {
    const a = parseAnswer(200, { response: { numFound: 0, docs: [] } });
    expect(a.pipelines).toEqual([]);
    expect(a.highlighting).toEqual({});
  });
  it("names a parser refusal so the page can hint instead of blame", () => {
    expect(() => parseAnswer(400, { error: { msg: "SyntaxError" } })).toThrow(
      QuerySyntaxError,
    );
  });
  it("refuses a shape it does not know", () => {
    expect(() => parseAnswer(200, { response: { numFound: "lots" } })).toThrow(
      /unexpected/,
    );
  });
});

describe("snippetFor", () => {
  const hit: Hit = { id: "p/v1/0001" };
  it("takes the highlighter's first fragment", () => {
    const a = parseAnswer(200, ANSWER);
    expect(snippetFor(hit, a.highlighting)).toBe("a <em>helsa</em> hit");
  });
  it("is empty when nothing was highlighted", () => {
    expect(snippetFor(hit, {})).toBeNull();
  });
});

describe("altoUrl", () => {
  const hit: Hit = {
    id: "x",
    pipeline_s: "p-v2",
    volume_s: "v",
    page_s: "0001",
  };
  it("builds the viewer's src inside the results base", () => {
    expect(altoUrl("http://front/results", hit)).toBe(
      "http://front/results/htr-batch/p-v2/v/alto/0001.xml",
    );
  });
  it("takes a base that already ends in a slash", () => {
    expect(altoUrl("http://front/results/", hit)).toBe(
      "http://front/results/htr-batch/p-v2/v/alto/0001.xml",
    );
  });
  it("is nothing without the parts to build it from", () => {
    expect(altoUrl("", hit)).toBeNull();
    expect(altoUrl("http://front/results", { id: "x" })).toBeNull();
  });
});

describe("paging", () => {
  it("counts pages, at least one", () => {
    expect(pageCount(0)).toBe(1);
    expect(pageCount(PAGE_ROWS)).toBe(1);
    expect(pageCount(PAGE_ROWS + 1)).toBe(2);
  });
  it("knows which page an offset is on", () => {
    expect(pageOf(0)).toBe(1);
    expect(pageOf(PAGE_ROWS)).toBe(2);
  });
});
