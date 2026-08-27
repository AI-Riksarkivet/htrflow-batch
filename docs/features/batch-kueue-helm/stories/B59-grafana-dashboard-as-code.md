---
type: Product Backlog Item
id: 2877
parent: 2800
title: Grafana dashboard as code
---

# B59 · Grafana dashboard as code

**Story.** As the product owner and the operator, I want one Grafana dashboard — campaign overview, reconciler health, throughput and GPU stall, failures — installed by the chart in every environment, so that "how is the campaign going?" is a URL and the dashboard cannot drift between DEV and prod.

## Why it matters

A dashboard built by hand in one Grafana is lost on the next cluster; one shipped as code goes through the same promotion path (B34) as the system it observes.

## What this delivers

- Dashboard JSON in the chart (ConfigMap with the Grafana sidecar label, or the Grafana Operator CRD, whichever the platform provides) with four rows: campaign overview · reconciler health · throughput & GPU stall · failures.
- Panels use only metrics from B40, Kueue and DCGM, so the dashboard works anywhere those are scraped.

## Done when

- [ ] The dashboard shows a running and a finished campaign on DEV without manual queries.
- [ ] Installed by the chart; identical in DEV and staging after a promotion.
