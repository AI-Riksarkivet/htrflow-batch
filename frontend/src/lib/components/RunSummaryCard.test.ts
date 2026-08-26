import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { describe, expect, test } from "vitest";
import type { RunManifest } from "$lib/run.js";
import RunSummaryCard from "./RunSummaryCard.svelte";

function manifest(n: number, failedIds: string[] = []): RunManifest {
  const results: RunManifest["results"] = {};
  for (let i = 1; i <= n; i++) {
    const id = String(i).padStart(4, "0");
    results[id] = failedIds.includes(id)
      ? { status: "failed", seconds: 0.3, error: `boom ${id}` }
      : { status: "ok", seconds: i };
  }
  return {
    volume: "R1",
    pipeline_id: "p",
    htrflow_version: "0.2.6",
    image_digest: "reg/img@sha256:abcdef0123456789",
    pages: n,
    results,
  };
}

describe("RunSummaryCard", () => {
  test("summary strip shows counts, timing and the failed pages with errors", () => {
    render(RunSummaryCard, { manifest: manifest(6, ["0003"]) });
    expect(screen.getByText("5 ok")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    const failed = screen.getByRole("region", { name: "failed pages" });
    expect(within(failed).getByText("boom 0003")).toBeInTheDocument();
    // slowest strip lists pages with the biggest seconds first
    expect(screen.getByText("slowest pages").parentElement?.textContent).toMatch(
      /0006.*0005.*0004.*0002.*0001/s,
    );
  });

  test("one focusable cell per page with id and seconds in the label", () => {
    render(RunSummaryCard, { manifest: manifest(4) });
    const grid = screen.getByRole("group", { name: /pages/ });
    const cells = within(grid).getAllByRole("button");
    expect(cells).toHaveLength(4);
    expect(cells[1]).toHaveAttribute("aria-label", "page 0002 · 2.0 s · ok");
    // roving tabindex: exactly one tab stop
    expect(cells.filter((c) => c.tabIndex === 0)).toHaveLength(1);
  });

  test("the full table is behind details and pages by 100", async () => {
    render(RunSummaryCard, { manifest: manifest(250) });
    const details = screen.getByText("all 250 pages").closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    const table = screen.getByRole("table", { name: /per-page results/i });
    expect(within(table).getAllByRole("row")).toHaveLength(101); // header + 100
    expect(screen.getByText("1–100 of 250")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "next" }));
    expect(screen.getByText("101–200 of 250")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "next" }));
    expect(screen.getByText("201–250 of 250")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "next" })).toBeDisabled();
  });
});
