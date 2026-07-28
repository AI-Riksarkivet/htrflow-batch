# Architecture

```mermaid
flowchart LR
    subgraph submitter["operator workstation"]
        CLI["htrq CLI<br/>submit / status / logs / retry"]
    end

    subgraph cluster["Kubernetes cluster"]
        subgraph queueing["Kueue"]
            LQ["LocalQueue htr-batch"] --> CQ["ClusterQueue htr-batch-cq<br/>quota: N × nvidia.com/gpu<br/>flavor: gpu-ada"]
        end

        subgraph job["GPU Job — one per volume: streaming driver (D16)"]
            DLP["downloader pool<br/>async, bounded lookahead"]
            PQ[("page queue<br/>on tmpfs")]
            CONS["consumer thread<br/>pipeline.run(page)<br/>models loaded ONCE"]
            UPL["uploader thread<br/>ships each ALTO as written,<br/>rolling-deletes source image"]
            DLP --> PQ --> CONS --> UPL
        end

        LQ -.->|admits when quota free| job
    end

    IIIF["lbiiif.riksarkivet.se<br/>(IIIF image server)"]
    S3[("S3 / HCP<br/>results bucket")]

    CLI -->|"kubectl apply Job (suspend: true)"| LQ
    DLP -->|"width-capped GETs (WAN)"| IIIF
    UPL -->|"ALTO/PAGE per page,<br/>manifest.json LAST"| S3
```

Four pieces, each boring on purpose:

| Piece | Owns | Explicitly does not own |
|---|---|---|
| **Kueue** | admission, GPU quota, queue order | anything about HTR or data |
| **k8s Job** | lifecycle: retries, deadlines, completion status | queueing (starts `suspend: true`) |
| **wrapper (streaming driver)** | I/O (IIIF in, S3 out), page queue, resume, **output verification**, provenance; drives htrflow in-process | HTR logic |
| **htrflow** | HTR | everything else — unmodified package, driven as a library |

## Job lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant U as htrq CLI
    participant K8s as kube-apiserver
    participant Q as Kueue
    participant P as GPU pod (streaming driver)
    participant I as IIIF origin
    participant S3 as S3 results

    U->>K8s: apply Job htr-<slug>-<hash> (suspend: true, queue-name label)
    Q->>Q: workload queued (FIFO within priority)
    Q->>K8s: quota free → unsuspend Job
    K8s->>P: schedule pod (1 GPU, tmpfs workdir)
    P->>P: Pipeline.from_config() — models load ONCE
    P->>I: fetch IIIF manifest
    P->>S3: list existing outputs (resume check)
    loop streaming — downloader ∥ consumer ∥ uploader run concurrently
        P->>I: fetch page N+k (bounded lookahead, width-capped)
        P->>P: pipeline.run(page N) the moment page N is downloaded
        P->>S3: upload page N−1's ALTO/PAGE the moment htrflow wrote it
        P->>P: delete page N−1's image from tmpfs (rolling cleanup)
    end
    P->>P: VERIFY per-page results + uploads == page list (D8)
    P->>S3: upload manifest.json LAST (completion marker, incl. timings)
    P->>K8s: exit 0 → Job Complete
```

See [The Wrapper](wrapper.md) for the streaming driver's downloader/consumer/
uploader roles and the `gpu_stall_seconds` instrumentation this diagram's
loop produces, and [Failure Handling](failure-handling.md) for what happens
off the happy path.
