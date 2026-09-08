# Architecture

This page is the map: the two pictures of the system, the one-line job
description of each piece, and where to read the detail.

```mermaid
%% Top-to-bottom so the site renders it readable at page width. The
%% streaming driver keeps its left-to-right row inside its own box.
flowchart TB
    subgraph git["campaigns repo (git)"]
        CAMP["campaigns/*.yaml<br/>pipelines/*.yaml<br/>converter.yaml"]
    end

    subgraph ci["campaigns repo CI"]
        CONV["converter<br/>htrflow-campaigns render"]
    end

    subgraph cluster["Kubernetes cluster"]
        subgraph queueing["Kueue"]
            LQ["LocalQueue htr-batch"] --> CQ["ClusterQueue htr-batch-cq<br/>quota: N × nvidia.com/gpu"]
        end

        subgraph job["Indexed Job — one per campaign, one index per volume: streaming driver (D16)"]
            direction LR
            DLP["downloader pool<br/>threads, bounded lookahead"]
            PQ[("page queue<br/>on tmpfs")]
            CONS["consumer thread<br/>pipeline.run(page)<br/>models loaded ONCE"]
            UPL["uploader<br/>ships PAGE then ALTO as written,<br/>rolling-deletes image + outputs"]
            DLP --> PQ --> CONS --> UPL
        end

        WARM["warm-up Job (CPU)<br/>fills the model cache"]
        API["read API :8081<br/>GET /api/v1/jobs"]
        LQ -.->|admits when quota free| job
        API -->|"list/get, read-only RBAC"| job
    end

    IIIF["lbiiif.riksarkivet.se<br/>(IIIF image server)"]
    S3[("S3<br/>results bucket")]
    BROWSER["browser<br/>campaign browser + UV4"]

    CAMP -->|"PR: validate"| CONV
    CONV -->|"main: render, commit rendered/"| git
    git -->|"Argo CD / htrflow-campaigns apply"| LQ
    CONV -.->|"rendered once per pipeline"| WARM
    DLP -->|"width-capped GETs (WAN)"| IIIF
    UPL -->|"PAGE/ALTO per page, run log,<br/>manifest.json LAST"| S3
    BROWSER -->|"GET /api/v1/jobs"| API
    BROWSER -->|"iiif.json, ALTO, run log"| S3
```

Five pieces, each boring on purpose:

| Piece | Owns | Explicitly does not own |
|---|---|---|
| **campaigns repo** | desired state: which volumes, which pipeline (image digest + steps) | anything that runs |
| **converter** | a pure function from campaign YAML to Kubernetes manifests (`packages/converter`), run in the campaigns repo's own CI | the cluster, S3, retries, anything at runtime |
| **Kueue** | admission, GPU quota, queue order | anything about HTR or data |
| **Indexed Job** | lifecycle for a whole campaign: per-index retries (`backoffLimitPerIndex`), disruption absorption, the exit-13 verdict per index, progress (`completedIndexes`/`failedIndexes`) | HTR, the results themselves |
| **wrapper (streaming driver)** | I/O (IIIF in, S3 out), page queue, resume, **output verification**, provenance, the live log; drives htrflow in-process | HTR logic |
| **htrflow** | HTR | everything else — unmodified package, driven as a library |

The web front (`packages/web`) is a sixth, passive piece: a read-only
projection of live Job/Pod/ConfigMap state that also serves the status page
and Universal Viewer, with no state of its own
([Campaigns](campaigns.md#the-web-front-and-status-page)).

## Job lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant G as campaigns repo CI
    participant Ar as Argo CD / htrflow-campaigns apply
    participant K8s as kube-apiserver
    participant Q as Kueue
    participant P as GPU pod (streaming driver), index i
    participant I as IIIF origin
    participant S3 as S3 results

    G->>G: htrflow-campaigns render -> rendered/ (committed)
    Ar->>K8s: apply the campaign Job (completionMode: Indexed,<br/>completions=N, queue-name label)
    Q->>Q: webhook suspends the Job, workload queued (FIFO)
    Q->>K8s: quota free → unsuspend Job (up to `parallelism`)
    K8s->>P: schedule pod for index i (1 GPU, tmpfs workdir, read-only model cache)
    P->>I: fetch IIIF manifest for volumes.txt line i
    P->>S3: list page/ + alto/ (resume check)
    P->>P: Pipeline.from_config() — models load ONCE, overlapping the first downloads
    loop streaming — downloader ∥ consumer ∥ uploader run concurrently
        P->>I: fetch page N+k (bounded lookahead, width-capped)
        P->>P: pipeline.run(page N) the moment page N is downloaded
        P->>S3: upload page N−1's PAGE then ALTO the moment htrflow wrote them
        P->>S3: ship the run log (every 15 s)
        P->>P: delete page N−1's image and XML from tmpfs (rolling cleanup)
    end
    P->>P: VERIFY page/ + alto/ == page list (D8)
    P->>S3: upload iiif.json, pipeline.yaml, then manifest.json LAST (completion marker, incl. timings)
    P->>K8s: exit 0 → index i in completedIndexes
```

## Read next

| Page | Answers |
|---|---|
| [Queueing (Kueue)](queueing.md) | When does a campaign actually run? What the chart renders, how admission works, why one campaign owns a one-GPU queue to the end |
| [The Wrapper](wrapper.md) | The streaming driver, the Job template, the model cache and the pipeline configs |
| [From image to transcription](page-flow.md) | One page: the IIIF GET, the htrflow steps, the two XML files, the upload order |
| [Campaigns (Indexed Jobs)](campaigns.md) | The campaigns repo, what the converter renders, the bucket layout, the accepted trade-offs |
| [Events and signals](signals.md) | Everything the system emits, who reads it, and what survives the Job's TTL |
| [Failure Handling](failure-handling.md) | Exit codes, retries, the pod deadline, and what a person is told |
| [Memory Budget](memory-budget.md) | Why tmpfs is the limit that matters and what is bounded by what |
| [Live Run Log](live-run-log.md) | How the browser follows a running volume with nothing writing status |
| [Decision Log](decision-log.md) | Every decision, dated, with what superseded it |
