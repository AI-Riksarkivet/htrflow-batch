import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import AltoPage from "./+page.svelte";

const XML = `<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout>
    <Page ID="page_1" WIDTH="100" HEIGHT="100">
      <PrintSpace>
        <TextBlock ID="block_1">
          <TextLine ID="line_1">
            <String CONTENT="Confident" WC="0.97"/>
          </TextLine>
          <TextLine ID="line_2">
            <String CONTENT="Shaky" WC="0.4"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>`;

function setSrc(url: string | null): void {
  window.history.replaceState(
    null,
    "",
    url === null ? "/alto" : `/alto?src=${encodeURIComponent(url)}`,
  );
}

function fetchOk(body: string): typeof fetch {
  return vi.fn(
    async () =>
      new Response(body, {
        status: 200,
        headers: { "content-type": "application/xml" },
      }),
  ) as typeof fetch;
}

beforeEach(() => {
  setSrc("https://bucket/v1/vol/alto/0001.xml");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("/alto", () => {
  test("renders each line's text in reading order, tinted by confidence", async () => {
    vi.stubGlobal("fetch", fetchOk(XML));
    render(AltoPage);

    const confident = await screen.findByText("Confident");
    const shaky = await screen.findByText("Shaky");
    expect(confident.className).toContain("high"); // WC 0.97
    expect(shaky.className).toContain("low"); // WC 0.4
    expect(screen.getByRole("heading")).toHaveTextContent("ALTO · 0001");
  });

  test("the legend names the confidence buckets", async () => {
    vi.stubGlobal("fetch", fetchOk(XML));
    render(AltoPage);
    await screen.findByText("Confident");
    expect(screen.getByText(/high \(≥0\.9\)/)).toBeInTheDocument();
    expect(screen.getByText(/medium \(0\.7–0\.9\)/)).toBeInTheDocument();
    expect(screen.getByText(/low \(<0\.7\)/)).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  test("the raw-XML toggle shows the pretty-printed source and back again", async () => {
    vi.stubGlobal("fetch", fetchOk(XML));
    render(AltoPage);
    await screen.findByText("Confident");
    expect(screen.queryByText(/<alto/)).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "raw XML" }));
    expect(screen.getByText(/<alto/)).toBeInTheDocument();
    expect(screen.queryByText("Confident")).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "text" }));
    expect(await screen.findByText("Confident")).toBeInTheDocument();
  });

  test("a fetch failure: one sentence, 'unreachable'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    render(AltoPage);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the ALTO file: Failed to fetch. Check that the raw link still works.",
    );
  });

  test("a non-XML response: one sentence, 'not XML'", async () => {
    vi.stubGlobal("fetch", fetchOk("this is not xml <<<"));
    render(AltoPage);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This file is not valid XML — it cannot be an ALTO page.",
    );
  });

  test("valid XML with no TextLine: one sentence, 'no text lines'", async () => {
    vi.stubGlobal("fetch", fetchOk("<alto><Layout/></alto>"));
    render(AltoPage);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This ALTO file has no text lines to show. Open the raw XML to inspect it.",
    );
    // the raw toggle is still available: the fetch succeeded
    expect(screen.getByRole("button", { name: "raw XML" })).toBeInTheDocument();
  });

  test("no src param at all", async () => {
    setSrc(null);
    vi.stubGlobal("fetch", fetchOk(XML));
    render(AltoPage);
    expect(screen.getByRole("alert")).toHaveTextContent("No ALTO URL given");
  });

  test("a non-http(s) src is refused before any fetch", async () => {
    setSrc("javascript:alert(1)");
    const fetchMock = fetchOk(XML);
    vi.stubGlobal("fetch", fetchMock);
    render(AltoPage);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "must be an absolute http(s) URL",
    );
  });
});
