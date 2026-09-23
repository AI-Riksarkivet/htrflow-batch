import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { PageStat } from "$lib/run.js";
import PagesTable from "./PagesTable.svelte";

function page(overrides: Partial<PageStat> = {}): PageStat {
  return { id: "0001", status: "ok", seconds: 1.2, ...overrides };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PagesTable alto column", () => {
  test("no alto URL: the cell has neither link nor button", () => {
    render(PagesTable, { pages: [page()] });
    expect(screen.queryByRole("link", { name: "view" })).toBeNull();
    expect(screen.queryByRole("button", { name: "download" })).toBeNull();
  });

  test("view links to /alto?src=<encoded ALTO URL>", () => {
    render(PagesTable, {
      pages: [page({ alto: "https://bucket/v1/vol/alto/0001.xml" })],
    });
    expect(screen.getByRole("link", { name: "view" })).toHaveAttribute(
      "href",
      "/alto?src=https%3A%2F%2Fbucket%2Fv1%2Fvol%2Falto%2F0001.xml",
    );
  });

  test("download fetches the ALTO XML and saves it as <page>.xml via an object URL", async () => {
    const xml = "<alto/>";
    const fetchMock = vi.fn(
      async () =>
        new Response(xml, {
          status: 200,
          headers: { "content-type": "application/xml" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn((_: Blob) => "blob:fake");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const saved: { name: string; href: string }[] = [];
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        saved.push({ name: this.download, href: this.href });
      });

    render(PagesTable, {
      pages: [page({ alto: "https://bucket/v1/vol/alto/0001.xml" })],
    });
    await fireEvent.click(screen.getByRole("button", { name: "download" }));
    await vi.waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));

    expect(fetchMock).toHaveBeenCalledWith(
      "https://bucket/v1/vol/alto/0001.xml",
    );
    expect(saved).toEqual([{ name: "0001.xml", href: "blob:fake" }]);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(await createObjectURL.mock.calls[0]?.[0].text()).toBe(xml);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake");
    expect(screen.queryByRole("alert")).toBeNull();

    clickSpy.mockRestore();
  });

  test("a failed download surfaces one sentence, not a thrown error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 404 })),
    );
    render(PagesTable, {
      pages: [
        page({ id: "0002", alto: "https://bucket/v1/vol/alto/0002.xml" }),
      ],
    });
    await fireEvent.click(screen.getByRole("button", { name: "download" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      'Could not download 0002.xml: HTTP 404. Try "view" and save from there instead.',
    );
  });
});
