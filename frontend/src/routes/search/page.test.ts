import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, test, vi } from "vitest";
import SearchPage from "./+page.svelte";

const ANSWER = {
  response: {
    numFound: 1,
    docs: [
      {
        id: "htrdemo-swe-single-v2/451511_1512_01/0001",
        pipeline_s: "htrdemo-swe-single-v2",
        volume_s: "451511_1512_01",
        page_s: "0001",
        image_url_s: "https://hf.example/scan.jpg",
        viewer_url_s: "http://front/results/htr-batch/iiif.json",
      },
    ],
  },
  highlighting: {
    "htrdemo-swe-single-v2/451511_1512_01/0001": {
      content_txt: ["en god <em>helsa</em> intill"],
    },
  },
  facet_counts: {
    facet_fields: {
      pipeline_s: ["htrdemo-swe-single-v2", 1, "htrdemo-swe-single-v1", 1],
    },
  },
};

function fetchWith(body: unknown, status = 200): typeof fetch {
  return vi.fn(
    async () =>
      new Response(typeof body === "string" ? body : JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  ) as typeof fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/search");
});

describe("/search", () => {
  test("a submitted search asks select and shows the hit", async () => {
    const f = fetchWith(ANSWER);
    vi.stubGlobal("fetch", f);
    render(SearchPage);
    await fireEvent.input(
      screen.getByLabelText(/search the transcribed pages/i),
      {
        target: { value: "helsa" },
      },
    );
    await fireEvent.submit(
      screen.getByLabelText(/search the transcribed pages/i).closest("form")!,
    );
    await screen.findByText("451511_1512_01");
    const call = (f as ReturnType<typeof vi.fn>).mock.calls.find((c) =>
      String(c[0]).includes("/search/select?"),
    );
    expect(call).toBeDefined();
    const url = call![0] as string;
    expect(url).toContain("/search/select?");
    expect(url).toContain("q=content_txt%3Ahelsa%7E1");
    expect(screen.getByText(/1 page$/)).toBeTruthy();
    expect(screen.getByText(/en god/)).toBeTruthy();
    // The facets the answer counted are offered.
    expect(screen.getByText("htrdemo-swe-single-v1")).toBeTruthy();
  });

  test("the address bar keeps the search bookmarkable", async () => {
    vi.stubGlobal("fetch", fetchWith(ANSWER));
    render(SearchPage);
    await fireEvent.input(
      screen.getByLabelText(/search the transcribed pages/i),
      {
        target: { value: "bref" },
      },
    );
    await fireEvent.submit(
      screen.getByLabelText(/search the transcribed pages/i).closest("form")!,
    );
    await waitFor(() => expect(window.location.search).toBe("?q=bref"));
  });

  test("a facet click re-searches with an fq", async () => {
    const f = fetchWith(ANSWER);
    vi.stubGlobal("fetch", f);
    render(SearchPage);
    await fireEvent.input(
      screen.getByLabelText(/search the transcribed pages/i),
      {
        target: { value: "helsa" },
      },
    );
    await fireEvent.submit(
      screen.getByLabelText(/search the transcribed pages/i).closest("form")!,
    );
    await screen.findAllByText("htrdemo-swe-single-v2");
    await fireEvent.click(
      screen.getByRole("checkbox", { name: /htrdemo-swe-single-v2/ }),
    );
    await waitFor(() => {
      const urls = (f as ReturnType<typeof vi.fn>).mock.calls.map(
        (c) => c[0] as string,
      );
      expect(
        urls.some((u) =>
          u.includes("fq=pipeline_s%3A%22htrdemo-swe-single-v2%22"),
        ),
      ).toBe(true);
    });
  });

  test("a query Solr's parser refused is a hint, not a stack trace", async () => {
    vi.stubGlobal("fetch", fetchWith({ error: { msg: "SyntaxError" } }, 400));
    render(SearchPage);
    await fireEvent.input(
      screen.getByLabelText(/search the transcribed pages/i),
      {
        target: { value: "(" },
      },
    );
    await fireEvent.submit(
      screen.getByLabelText(/search the transcribed pages/i).closest("form")!,
    );
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toMatch(
      /could not be parsed/,
    );
  });

  test("a 502 from the proxy is one sentence in a banner", async () => {
    vi.stubGlobal(
      "fetch",
      fetchWith(
        JSON.stringify({ detail: "the search service did not answer" }),
        502,
      ),
    );
    render(SearchPage);
    await fireEvent.input(
      screen.getByLabelText(/search the transcribed pages/i),
      {
        target: { value: "helsa" },
      },
    );
    await fireEvent.submit(
      screen.getByLabelText(/search the transcribed pages/i).closest("form")!,
    );
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain(
      "the search service did not answer",
    );
  });

  test("no search route on this deployment is said plainly", async () => {
    vi.stubGlobal("fetch", fetchWith("not found", 404));
    render(SearchPage);
    await fireEvent.input(
      screen.getByLabelText(/search the transcribed pages/i),
      {
        target: { value: "helsa" },
      },
    );
    await fireEvent.submit(
      screen.getByLabelText(/search the transcribed pages/i).closest("form")!,
    );
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toMatch(/no search index/);
  });

  test("opened with ?q= it searches on its own", async () => {
    window.history.replaceState(null, "", "/search?q=helsa");
    const f = fetchWith(ANSWER);
    vi.stubGlobal("fetch", f);
    render(SearchPage);
    await screen.findByText("451511_1512_01");
    expect((f as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
      0,
    );
  });
});
