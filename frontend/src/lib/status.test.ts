import { describe, expect, it } from "vitest";
import { z } from "zod";
import sample from "../../static/status.sample.json";
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
    expect(problems).toEqual([
      "c/bad: attempts: expected number, received string",
    ]);
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

  it("refuses non-http(s) URLs: null field plus a warning line", () => {
    const { doc, problems } = parseStatusDoc({
      ...oldDoc,
      campaigns: [
        {
          ...oldDoc.campaigns[0],
          volumes: [
            {
              ...volume,
              source_manifest: "javascript:alert(1)",
              thumbnail: "data:image/png;base64,AAAA",
              run_log: "http://ok/log.txt",
            },
          ],
        },
      ],
    });
    const v = doc?.campaigns[0]?.volumes[0];
    expect(v?.source_manifest).toBeNull();
    expect(v?.thumbnail).toBeNull();
    expect(v?.run_log).toBe("http://ok/log.txt");
    expect(v?.invalid).toBeUndefined();
    expect(problems).toEqual([
      "c/v1: source_manifest is not an http(s) URL, ignored",
      "c/v1: thumbnail is not an http(s) URL, ignored",
    ]);
  });

  it("knows the reconciler's deleting status (a retry in progress)", () => {
    expect(volumeStatusSchema.parse("deleting")).toBe("deleting");
  });

  it("carries terminal and tick_summary when present, tolerates their absence", () => {
    const withNew = parseStatusDoc({
      ...oldDoc,
      tick_summary: { seconds: 4.1, s3_calls: 12 },
      campaigns: [
        {
          ...oldDoc.campaigns[0],
          volumes: [
            { ...volume, status: "needs-attention", terminal: "exit-13" },
          ],
        },
      ],
    });
    expect(withNew.doc?.tick_summary).toEqual({ seconds: 4.1, s3_calls: 12 });
    expect(withNew.doc?.campaigns[0]?.volumes[0]?.terminal).toBe("exit-13");

    const without = parseStatusDoc(oldDoc);
    expect(without.doc?.tick_summary).toBeNull();
    expect(without.doc?.campaigns[0]?.volumes[0]?.terminal).toBeNull();

    const junk = parseStatusDoc({ ...oldDoc, tick_summary: "later" });
    expect(junk.doc).not.toBeNull();
    expect(junk.doc?.tick_summary).toBeNull();
  });

  it("the dev fixture is the full current shape: parses clean, every status known", () => {
    const { doc, problems } = parseStatusDoc(sample);
    expect(problems).toEqual([]);
    expect(doc).not.toBeNull();
    expect(doc?.tick_summary).not.toBeNull();
    const volumes = doc?.campaigns.flatMap((c) => c.volumes) ?? [];
    expect(volumes.length).toBeGreaterThan(5);
    expect(volumes.map((v) => v.status)).not.toContain("unknown");
    expect(volumes.some((v) => v.terminal !== null)).toBe(true);
    expect(doc?.campaigns.some((c) => c.error !== null)).toBe(true);
    // every key the reconciler writes is present on every raw volume
    const keys = Object.keys(volume).concat("terminal").sort();
    for (const c of sample.campaigns) {
      for (const v of c.volumes) expect(Object.keys(v).sort()).toEqual(keys);
    }
  });

  it("returns no doc when the envelope is unusable", () => {
    const { doc, problems } = parseStatusDoc({ campaigns: "nope" });
    expect(doc).toBeNull();
    expect(problems[0]).toContain("generated_at: required");
  });
});
