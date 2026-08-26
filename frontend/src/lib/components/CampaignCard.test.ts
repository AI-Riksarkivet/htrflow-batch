import { render, screen, within } from "@testing-library/svelte";
import { describe, expect, test } from "vitest";
import { parseStatusDoc, type CampaignEntry } from "$lib/status.js";
import CampaignCard from "./CampaignCard.svelte";

const volume = {
  id: "R1",
  status: "done",
  attempts: 0,
  pages_done: 3,
  pages_total: 3,
  error: null,
  viewer_manifest: "http://pub/r1/iiif.json",
  source_manifest: "https://src/r1/manifest",
  thumbnail: null,
  run_manifest: null,
  updated: "2026-08-25T14:32:00Z",
  failure_log: null,
  run_log: "http://pub/logs/r1.txt",
};

function campaignFrom(volumes: unknown[]): CampaignEntry {
  const { doc } = parseStatusDoc({
    generated_at: "2026-08-25T10:00:00Z",
    tick_seconds: 300,
    warnings: [],
    campaigns: [
      {
        name: "demo",
        pipeline: "demo-v1",
        error: null,
        totals: { done: 1, total: volumes.length },
        volumes,
      },
    ],
  });
  const c = doc?.campaigns[0];
  if (c === undefined) throw new Error("fixture did not parse");
  return c;
}

describe("CampaignCard", () => {
  test("a bad volume degrades to an error row; the good row still renders", () => {
    const campaign = campaignFrom([volume, { ...volume, id: "R2", attempts: "x" }]);
    render(CampaignCard, { campaign });
    const rows = screen.getAllByRole("row").slice(1); // drop the header
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText("R1")).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText("done")).toBeInTheDocument();
    const bad = rows[1] as HTMLElement;
    expect(within(bad).getByText("R2")).toBeInTheDocument();
    expect(within(bad).getByText("unknown")).toBeInTheDocument();
    expect(within(bad).getByText(/invalid status entry: attempts/)).toBeInTheDocument();
  });

  test("javascript: URLs never reach an href or src", () => {
    const campaign = campaignFrom([
      {
        ...volume,
        source_manifest: "javascript:alert(1)",
        thumbnail: "javascript:alert(2)",
        failure_log: "javascript:alert(3)",
        viewer_manifest: "javascript:alert(4)",
      },
    ]);
    const { container } = render(CampaignCard, { campaign });
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs.some((h) => h?.startsWith("javascript:"))).toBe(false);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("invalid url")).toBeInTheDocument();
    // the run log is still reachable — only the refused fields are dropped
    expect(screen.getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      expect.stringContaining("log?log="),
    );
    expect(screen.queryByRole("link", { name: "open" })).toBeNull();
  });

  test("a degraded row has no links at all, not an 'invalid url' label", () => {
    const campaign = campaignFrom([{ ...volume, attempts: "x" }]);
    const { container } = render(CampaignCard, { campaign });
    expect(container.querySelectorAll("td.links a")).toHaveLength(0);
    expect(screen.queryByText("invalid url")).toBeNull();
  });

  test("an unrecognised status renders the neutral unknown chip", () => {
    const campaign = campaignFrom([{ ...volume, status: "paused" }]);
    render(CampaignCard, { campaign });
    const chip = screen.getByText("unknown");
    expect(chip.closest(".status")).toHaveClass("unknown");
    // and no error text is invented for it
    expect(screen.queryByText(/invalid status entry/)).toBeNull();
  });
});
