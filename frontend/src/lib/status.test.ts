import { describe, expect, it } from "vitest";
import { statusDocSchema } from "./status.js";

const volume = {
  id: "v1",
  status: "pending",
  attempts: 0,
  pages_done: null,
  pages_total: null,
  error: null,
  viewer_manifest: null,
  source_manifest: "http://s/m.json",
  thumbnail: null,
};

const oldDoc = {
  generated_at: "2026-08-25T10:00:00Z",
  tick_seconds: 300,
  warnings: [],
  campaigns: [
    {
      name: "c",
      pipeline: "p",
      error: null,
      totals: { done: 0, total: 1 },
      volumes: [volume],
    },
  ],
};

describe("statusDocSchema", () => {
  it("parses a pre-visibility document (missing new fields)", () => {
    const doc = statusDocSchema.parse(oldDoc);
    expect(doc.campaigns_repo_url).toBeNull();
    const campaign = doc.campaigns[0];
    expect(campaign).toBeDefined();
    if (campaign) {
      expect(campaign.pipeline_steps).toBeNull();
      expect(campaign.totals.pages_total).toBeNull();
    }
  });

  it("parses a new document with the visibility fields", () => {
    const doc = statusDocSchema.parse({
      ...oldDoc,
      campaigns_repo_url: "git://example/campaigns",
      campaigns: [
        {
          ...oldDoc.campaigns[0],
          pipeline_steps: ["Segmentation: yolo (weights)"],
          totals: { done: 0, total: 1, pages_done: 0, pages_total: 2 },
        },
      ],
    });
    expect(doc.campaigns_repo_url).toBe("git://example/campaigns");
    const campaign = doc.campaigns[0];
    expect(campaign).toBeDefined();
    if (campaign) {
      expect(campaign.totals.pages_total).toBe(2);
    }
  });
});
