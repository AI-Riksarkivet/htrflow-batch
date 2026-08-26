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
  run_manifest: null,
  updated: null,
  failure_log: null,
  run_log: null,
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
      expect(campaign.pipeline_yaml).toBeNull();
      expect(campaign.totals.pages_total).toBeNull();
      const vol = campaign.volumes[0];
      expect(vol).toBeDefined();
      if (vol) {
        expect(vol.updated).toBeNull();
        expect(vol.failure_log).toBeNull();
        expect(vol.run_log).toBeNull();
      }
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
          pipeline_yaml: "steps:\n  - step: Segmentation\n",
          totals: { done: 0, total: 1, pages_done: 0, pages_total: 2 },
          volumes: [
            {
              ...volume,
              updated: "2026-08-25T14:32:00Z",
              failure_log: "http://example/logs/v1.log",
              run_log: "http://example/logs/v1-run.log",
            },
          ],
        },
      ],
    });
    expect(doc.campaigns_repo_url).toBe("git://example/campaigns");
    const campaign = doc.campaigns[0];
    expect(campaign).toBeDefined();
    if (campaign) {
      expect(campaign.pipeline_yaml).toBe("steps:\n  - step: Segmentation\n");
      expect(campaign.totals.pages_total).toBe(2);
      const vol = campaign.volumes[0];
      expect(vol).toBeDefined();
      if (vol) {
        expect(vol.updated).toBe("2026-08-25T14:32:00Z");
        expect(vol.failure_log).toBe("http://example/logs/v1.log");
        expect(vol.run_log).toBe("http://example/logs/v1-run.log");
      }
    }
  });
});
