---
type: Product Backlog Item
id: 2876
parent: 2800
title: Metrics exporter — the batch system's numbers in Prometheus format
---

# B40 · Metrics exporter — the batch system's numbers in Prometheus format

**Story.** As the operator on call, I want the batch system to expose its numbers at a `/metrics` endpoint that Prometheus scrapes — alongside Kueue's and the GPUs' own metrics — so that queue depth, throughput, failures and GPU idle time are time series rather than files in a bucket.

## Why it matters

Everything the system knows is written to S3 (`status.json` per tick, `manifest.json` per volume) but nothing trends or alerts on it, and the archive-scale question (B16) — what fraction of wall-clock the GPU spent waiting — is "a script over the bucket". The reconciler is a CronJob and cannot be scraped, so a small always-on exporter reads the same files the browser reads.

## What this delivers

- An exporter Deployment in the chart exposing, per pipeline and campaign: volumes by state, attempts, orphans; reconciler tick duration, age of last success, errors, submissions, warm-ups pending; from completed manifests: pages, pages/hour, median/p95 page seconds, `gpu_stall_seconds` / wall seconds, bytes fetched, failure reasons by exit code.
- A `ServiceMonitor` rendered by the chart and a network-policy rule allowing the monitoring namespace to scrape; Kueue and DCGM (GPU) metrics confirmed scraped in the same Prometheus so one query can join them.
- The dashboard (B59) and alert rules (B60) are separate stories.

## Done when

- [ ] `/metrics` is scraped on DEV; the B16 figure (aggregate GPU stall fraction) is a PromQL query, not a script.
- [ ] Kueue queue depth and GPU utilisation are queryable next to the batch metrics.
