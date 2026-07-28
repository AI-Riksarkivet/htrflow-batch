# Failure Handling

```mermaid
stateDiagram-v2
    [*] --> Queued: htrq submit (suspend true)
    Queued --> Running: Kueue admits (quota free)
    Running --> Complete: verify passes → manifest.json in S3 → exit 0
    Running --> Retrying: transient exit (incl. verification gap)
    Retrying --> Running: backoffLimit not exhausted<br/>(resume skips done pages)
    Retrying --> Failed: backoffLimit exhausted
    Running --> Failed: exit 13 (permanent, FailJob)
    Running --> Failed: activeDeadlineSeconds
    Failed --> Queued: htrq retry
    Complete --> [*]
    note right of Retrying
        pod disruption (node drain)
        does not consume a retry
    end note
```

Invariants:

- **Complete ⇔ verified ⇔ `manifest.json` exists** (per pipeline id). Exit code
  alone is never trusted (see the [known upstream flaw](decision-log.md#known-upstream-flaw-the-design-must-absorb)).
- Retries converge: per-page overwrite + resume → a retry of a long volume
  costs minutes, not hours.
- A page that can't be fetched or transcribed after retries **fails the whole
  Job** — archival completeness over partial results.
- Failed Jobs stay inspectable 7 days; reason surfaced via termination-log.
