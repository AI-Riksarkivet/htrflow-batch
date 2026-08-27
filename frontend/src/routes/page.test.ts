import { render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { DEFAULT_STATUS_URL, RELOAD_MS } from "$lib/config.js";
import CampaignsPage from "./+page.svelte";

const volume = {
  id: "R1",
  status: "done",
  attempts: 0,
  pages_done: 3,
  pages_total: 3,
  error: null,
  viewer_manifest: "http://bucket/p/R1/iiif.json",
  run_manifest: null,
  source_manifest: "http://iiif/R1/manifest",
  thumbnail: null,
  updated: "2026-08-26T08:54:43Z",
  failure_log: null,
  run_log: null,
  terminal: null,
};

const doc = {
  generated_at: new Date().toISOString(),
  tick_seconds: 300,
  campaigns_repo_url: "https://git.example/campaigns",
  warnings: [],
  tick_summary: {
    seconds: 4.06,
    s3_calls: 12,
    validations: 3,
    submitted: 1,
    retried: 0,
  },
  campaigns: [
    {
      name: "demo",
      pipeline: "demo-v1",
      pipeline_steps: null,
      pipeline_yaml: null,
      error: null,
      totals: { done: 1, total: 2, pages_done: 3, pages_total: 3 },
      volumes: [volume, { ...volume, id: "R2", attempts: "many" }],
      orphans: [],
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("/ campaign page", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    delete window.STATUS_URL;
  });

  test("fetches window.STATUS_URL, shows the tick summary, degrades one bad volume", async () => {
    window.STATUS_URL = "http://elsewhere/status.json";
    const fetchMock = vi.fn(async () => jsonResponse(doc));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://elsewhere/status.json",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(screen.getByText(/last tick 4\.1 s · 12 S3 calls/)).toHaveClass(
      "tick",
    );
    expect(
      screen.getByRole("link", { name: "https://git.example/campaigns" }),
    ).toBeInTheDocument();
    // the good row renders, the bad one is an error row, the page stands
    expect(screen.getByText("R1")).toBeInTheDocument();
    expect(screen.getByText("R2")).toBeInTheDocument();
    expect(
      screen.getByText(/status\.json: demo\/R2: attempts/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("falls back to the default URL and keeps the last good document through a failed poll", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ ...doc, tick_summary: null }))
      .mockResolvedValueOnce(jsonResponse("gone", 503));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenLastCalledWith(
      DEFAULT_STATUS_URL,
      expect.anything(),
    );
    expect(screen.queryByText(/last tick/)).toBeNull(); // no summary → no line
    expect(screen.getByText("R1")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Cannot load status: HTTP 503 — showing the last good document/,
    );
    expect(screen.getByText("R1")).toBeInTheDocument();
  });
});
