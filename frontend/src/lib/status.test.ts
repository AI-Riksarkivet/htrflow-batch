import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  formatIssues,
  parseStatusDoc,
  statusDocSchema,
  volumeStatusSchema,
} from "./status.js";

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

describe("volumeStatusSchema", () => {
  it("maps a status this build does not know to unknown", () => {
    expect(volumeStatusSchema.parse("paused")).toBe("unknown");
    expect(volumeStatusSchema.parse(undefined)).toBe("unknown");
    expect(volumeStatusSchema.parse("done")).toBe("done");
  });
});

describe("formatIssues", () => {
  it("renders path and lower-cased message without a ZodError dump", () => {
    const r = z
      .object({ volumes: z.array(z.object({ attempts: z.number() })) })
      .safeParse({ volumes: [{ attempts: "3" }] });
    expect(r.success).toBe(false);
    if (!r.success) {
      expect(formatIssues(r.error.issues)).toBe(
        "volumes[0].attempts: expected number, received string",
      );
    }
  });
});

describe("parseStatusDoc", () => {
  it("degrades a bad volume to an error row and keeps the rest", () => {
    const { doc, problems } = parseStatusDoc({
      ...oldDoc,
      campaigns: [
        {
          ...oldDoc.campaigns[0],
          volumes: [volume, { ...volume, id: "bad", attempts: "3" }],
        },
      ],
    });
    expect(doc).not.toBeNull();
    const vols = doc?.campaigns[0]?.volumes ?? [];
    expect(vols).toHaveLength(2);
    expect(vols[0]?.status).toBe("pending");
    expect(vols[1]).toMatchObject({
      id: "bad",
      status: "unknown",
      error: "invalid status entry: attempts: expected number, received string",
    });
    expect(problems).toEqual(["c/bad: attempts: expected number, received string"]);
  });

  it("degrades a bad campaign to a broken card and keeps the rest", () => {
    const { doc, problems } = parseStatusDoc({
      ...oldDoc,
      campaigns: [{ name: "broken", volumes: [] }, oldDoc.campaigns[0]],
    });
    expect(doc?.campaigns).toHaveLength(2);
    expect(doc?.campaigns[0]?.name).toBe("broken");
    expect(doc?.campaigns[0]?.error).toMatch(/^invalid status entry: /);
    expect(doc?.campaigns[1]?.volumes).toHaveLength(1);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(/^broken: /);
  });

  it("names an entry without a usable id by position", () => {
    const { doc } = parseStatusDoc({
      ...oldDoc,
      campaigns: [{ ...oldDoc.campaigns[0], volumes: [42] }],
    });
    expect(doc?.campaigns[0]?.volumes[0]?.id).toBe("volume #1");
  });

  it("returns no doc when the envelope is unusable", () => {
    const { doc, problems } = parseStatusDoc({ campaigns: "nope" });
    expect(doc).toBeNull();
    expect(problems[0]).toContain("generated_at: required");
  });
});
