# Open Items

Discussion queue — things designed in or proposed but not yet confirmed on a
real cluster.

| # | Item | State |
|---|------|---|
| D6 | Output store: S3 (HCP) vs NFS | recommended S3, confirm |
| D9 | Resume-from-partial-results | designed into [the wrapper contract](../how-it-works/wrapper.md), confirm |
| D10 | Deterministic Job names as idempotency key | designed in, confirm |
| D11 | Provenance manifest contents | designed in, confirm |
| D12 | Structured failure via termination-log | designed in, confirm |
| D13 | Priority lanes (`htr-interactive` > `htr-bulk`) | proposed, confirm |
| D14 | Pod security + egress NetworkPolicy | proposed (see [Security](../development/security.md)), confirm |
| D15 | `htrq submit --dry-run` | proposed, confirm |
| — | `htrq` CLI itself (submit/status/logs/retry/report/pipeline deploy) | designed in [the wrapper doc](../how-it-works/wrapper.md#htrq-cli), no in-cluster components; not yet built as a standalone package |
| — | Target cluster for the PoC | **unresolved** |
| — | Quota N, memory numbers, width default | placeholders, tune on cluster |

## Active next step

The PoC has been validated end to end on a single volume and a handful of
concurrent volumes (see the [test log](../development/test-log.md)), but not
yet at archive scale. The active next step is an **archive-scale campaign**:
enough volumes, run for long enough, to produce a trustworthy aggregate
`gpu_stall_seconds / wall_seconds` figure from `htrq report` — the number that
actually gates [Phase 2](phase-2-cache.md). Everything else on this page
(priority lanes, NetworkPolicy, dry-run, the CLI itself) is useful but not
blocking that measurement.
