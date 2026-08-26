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

  test("an unrecognised status renders the neutral unknown chip", () => {
    const campaign = campaignFrom([{ ...volume, status: "paused" }]);
    render(CampaignCard, { campaign });
    const chip = screen.getByText("unknown");
    expect(chip.closest(".status")).toHaveClass("unknown");
    // and no error text is invented for it
    expect(screen.queryByText(/invalid status entry/)).toBeNull();
  });
});
