# Architecture

```mermaid
flowchart LR
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
            DLP["downloader pool<br/>threads, bounded lookahead"]
            PQ[("page queue<br/>on tmpfs")]
            CONS["consumer thread<br/>pipeline.run(page)<br/>models loaded ONCE"]
            UPL["uploader<br/>ships PAGE then ALTO as written,<br/>rolling-deletes source image"]
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
    git -->|"Argo CD / kubectl apply<br/>(suspend: true)"| LQ
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

The read API (`packages/web`) is a sixth, passive piece: read-only RBAC on
Jobs/Pods/ConfigMaps, no state of its own, serving the status page's
`GET /api/v1/jobs` — it derives everything from the live Job, nothing is
cached ([Campaigns](campaigns.md#the-read-api-and-status-page)).

## Job lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant G as campaigns repo CI
    participant Ar as Argo CD / kubectl apply
    participant K8s as kube-apiserver
    participant Q as Kueue
    participant P as GPU pod (streaming driver), index i
    participant I as IIIF origin
    participant S3 as S3 results

    G->>G: htrflow-campaigns render -> rendered/ (committed)
    Ar->>K8s: apply Job <campaign> (completionMode: Indexed,<br/>completions=N, suspend: true, queue-name label)
    Q->>Q: workload queued (FIFO)
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
        P->>P: delete page N−1's image from tmpfs (rolling cleanup)
    end
    P->>P: VERIFY page/ + alto/ == page list (D8)
    P->>S3: upload iiif.json, pipeline.yaml, then manifest.json LAST (completion marker, incl. timings)
    P->>K8s: exit 0 → index i in completedIndexes
```

See [The Wrapper](wrapper.md) for the streaming driver's downloader/consumer/
uploader roles and the `gpu_stall_seconds` instrumentation this diagram's
loop produces, [Campaigns (Indexed Jobs)](campaigns.md) for the render →
apply flow, and [Failure Handling](failure-handling.md) for what happens off
the happy path.
