---
type: Product Backlog Item
id: 2848
parent: 2800
title: Run the whole thing locally and on the GPU proof-of-concept node
---

# B07 · Run the whole thing locally and on the GPU proof-of-concept node

**Story.** As a developer joining the project, I want to run the complete
system on my laptop without a cluster, and then on the team's GPU node with
real models, so that I can test a change end to end in minutes and we have
proof that the design works on real hardware before asking for a production
cluster.

## Why it matters

A batch system you can only test in production is a batch system nobody
dares change. And before asking for production resources we needed to show
— with numbers — that streaming, queueing, resume and the status page all
work on a real GPU.

## What this delivers

- **A docker-compose stack**: local S3, fixture pages, the wrapper and the
  viewer. `make compose-up`, no cluster.
- **A single-node k3s recipe** for the team's arm64 GPU node, including a
  GPU image build that works on that architecture and a local git server
  for the campaigns repo.
- **A test log** of what has been validated on the node: single volumes,
  several concurrent volumes, a 480-page volume driven end to end by the
  reconciler, kill-and-resume, hardened pods with zero GPU stall recorded.

## Done when

- [ ] `make install && make test && make compose-up` works on a clean
      machine and produces a viewable transcribed volume.
- [ ] The k3s walk-through goes from empty node to a running campaign.
- [ ] The test log records the runs above with their measured timings.
