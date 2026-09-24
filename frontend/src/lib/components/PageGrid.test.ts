import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, test } from "vitest";
import type { PageStat } from "$lib/run.js";
import PageGrid from "./PageGrid.svelte";

const pages: PageStat[] = ["0001", "0002", "0003", "0004"].map((id, i) => ({
  id,
  status: i === 2 ? "failed" : "ok",
  seconds: i + 1,
}));

// The grid is one tab stop, and the arrow keys, Home and End walk the pages
// inside it (a roving tabindex): a volume of six hundred pages must not be
// six hundred presses of Tab to get past.
describe("PageGrid keyboard navigation", () => {
  function cells(): HTMLButtonElement[] {
    return screen.getAllByRole("button") as HTMLButtonElement[];
  }

  /** The one cell Tab lands on, and the one that has the focus. */
  function stop(): { tabbable: string[]; focused: string | null } {
    return {
      tabbable: cells()
        .filter((c) => c.tabIndex === 0)
        .map((c) => c.getAttribute("aria-label") ?? ""),
      focused: document.activeElement?.getAttribute("aria-label") ?? null,
    };
  }

  const at = (n: number) => {
    const label = `page ${pages[n]?.id} · ${pages[n]?.seconds.toFixed(1)} s · ${pages[n]?.status}`;
    return { tabbable: [label], focused: label };
  };

  async function press(key: string): Promise<boolean> {
    return fireEvent.keyDown(document.activeElement as HTMLElement, { key });
  }

  test("one tab stop, walked with the arrows, Home and End, never past either end", async () => {
    render(PageGrid, { pages, max: 4 });
    expect(cells().filter((c) => c.tabIndex === 0)).toHaveLength(1);
    cells()[0]?.focus();
    expect(stop()).toEqual(at(0));

    await press("ArrowRight");
    expect(stop()).toEqual(at(1));
    await press("ArrowRight");
    expect(stop()).toEqual(at(2));
    await press("ArrowLeft");
    expect(stop()).toEqual(at(1));
    await press("End");
    expect(stop()).toEqual(at(3));
    await press("ArrowRight");
    expect(stop()).toEqual(at(3));
    await press("Home");
    expect(stop()).toEqual(at(0));
    await press("ArrowLeft");
    expect(stop()).toEqual(at(0));
  });

  test("the keys it walks with are its own; any other is left to the page", async () => {
    render(PageGrid, { pages, max: 4 });
    cells()[1]?.focus();
    // fireEvent returns false when the handler called preventDefault.
    expect(await press("ArrowRight")).toBe(false);
    expect(await press("Tab")).toBe(true);
    expect(await press("ArrowDown")).toBe(true);
    expect(stop()).toEqual(at(2));
  });

  test("the focused page is read out", async () => {
    const { container } = render(PageGrid, { pages, max: 4 });
    cells()[0]?.focus();
    await press("End");
    expect(container.querySelector(".readout")).toHaveTextContent(
      "page 0004 · 4.0 s · ok",
    );
  });
});
