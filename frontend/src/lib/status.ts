// The status.json boundary: parse, don't validate. Shape mirrors the
// reconciler's emitted document (packages/reconciler/.../main.py).
import { z } from "zod";

export const volumeStatusSchema = z.enum([
  "done",
  "running",
  "queued",
  "retry",
  "needs-attention",
  "pending",
  "unreachable",
  "unsupported",
]);

export const volumeEntrySchema = z.object({
  id: z.string(),
  status: volumeStatusSchema,
  attempts: z.number(),
  pages_done: z.number().nullable(),
  pages_total: z.number().nullable(),
  error: z.string().nullable(),
  viewer_manifest: z.string().nullable(),
  source_manifest: z.string(),
  thumbnail: z.string().nullable(),
  updated: z.string().nullable().default(null),
  failure_log: z.string().nullable().default(null),
});

export const campaignEntrySchema = z.object({
  name: z.string(),
  pipeline: z.string().nullable(),
  pipeline_steps: z.array(z.string()).nullable().default(null),
  error: z.string().nullable(),
  totals: z.object({
    done: z.number(),
    total: z.number(),
    pages_done: z.number().nullable().default(null),
    pages_total: z.number().nullable().default(null),
  }),
  volumes: z.array(volumeEntrySchema),
  orphans: z.array(z.string()).default([]),
});

export const statusDocSchema = z.object({
  generated_at: z.string(),
  tick_seconds: z.number(),
  campaigns_repo_url: z.string().nullable().default(null),
  warnings: z.array(z.string()),
  campaigns: z.array(campaignEntrySchema),
});

export type VolumeStatus = z.infer<typeof volumeStatusSchema>;
export type VolumeEntry = z.infer<typeof volumeEntrySchema>;
export type CampaignEntry = z.infer<typeof campaignEntrySchema>;
export type StatusDoc = z.infer<typeof statusDocSchema>;
