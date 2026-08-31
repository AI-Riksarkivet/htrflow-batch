# Features & Stories

This section is the product view of `htrflow-batch`: **what** we set out to
deliver, **why** it matters, and **whether it is done** — written for a
product owner, not for someone deploying the system. The technical pages
(How it Works, Reference, Development) are linked from each story for the
curious.

## How this maps to Azure DevOps

The folder layout mirrors the backlog in the **AI-labbet** project:

```
docs/features/
├── index.md                       ← this page (Epic: HTRflow HT26, #2769)
├── batch-kueue-helm/
│   ├── feature.md                 ← Feature #2800 "Batch using Kueue+Helm chart"
│   └── stories/
│       ├── B01-streaming-wrapper.md   ← one Product Backlog Item each
│       └── …
├── uv4-linux/
│   ├── feature.md                 ← Feature #2801 "UV4 linux"
│   └── stories/U01-…              ← one Product Backlog Item each
└── search-solr/
    ├── feature.md                 ← Feature #2811 "Solr"
    └── stories/S02-…
```

- One **folder** = one Azure **Feature**. `feature.md` carries its id and parent (the epic).
- Story ids (`B06`, `U03`) are **stable**: never reused, never renumbered.
  A new story takes the next free number; the feature page's tables give
  the reading order.
- One **story file** = one Azure **Product Backlog Item**. The file is the
  source of truth for the story text; the Azure item carries the same title
  and links back here. When the item is created its id goes into the file's
  front matter (`id:`), so the mapping works in both directions.
- **State and owner live in Azure only** (Scrum states New → Approved →
  Committed → Done; assignee). The file carries identity and content,
  never status. Every story starts as New in Azure — including the ones
  already built in the repository — and becomes Done only when the product
  owner has walked its "Done when" list and accepted it. The tables on each
  feature page separate *implemented, awaiting acceptance* from *not
  started* so that review can be planned, but that is a reading aid, not a
  state.

Every story file has the same four parts:

| Part | Answers |
|---|---|
| **Story** | Who wants this and what they get out of it |
| **Why it matters** | The problem it removes, in plain words |
| **What this delivers** | The visible result — what someone can see, run or rely on |
| **Done when** | Acceptance criteria — the checklist the PO signs off on |

## Tooling

- `python3 scripts/stories/wire.py` — regenerates the story tables, the epic
  table above and the docs nav from the ordered id lists in the script.
  A new story is one new file plus its id in a list there.
- `python3 scripts/stories/azure_sync.py story <file>` — creates the Azure
  item (writes the id back) or updates it; `… feature <feature.md>` updates
  a Feature's description. Needs a PAT in `~/.azdo_pat`.

## Definition of done — applies to every story

A story is accepted only when, in addition to its own "Done when" list:

- the documentation page that describes the changed behaviour is updated in
  the same pull request (there is no separate "update the docs" story —
  the docs CI gate, B58, is what catches a page left behind);
- if the change adds or removes a connection between components, the
  diagram on that page is updated (conventions in B38);
- the change was reviewed by someone other than the author and went in
  through a pull request on a protected branch.

## Epic: HTRflow HT26 (#2769)

The autumn-2026 epic for the HTR pipeline. The two features documented here
are the ones delivered by this repository:

| Feature | Azure | What it is, in one sentence | Stories |
|---|---|---|---|
| [Batch using Kueue+Helm chart](batch-kueue-helm/feature.md) | #2800 | Transcribe whole archive volumes unattended on the GPU cluster, with results appearing in S3 as they are produced and a web page that shows progress | 57 (19 built, 38 not started) |
| [Campaigns status page](campaign-status-page/feature.md) | #2923 | The read-only status page for the data scientists running campaigns — every campaign and volume, live logs, links into the viewer, at archive scale | 10 (3 built, 7 not started) |
| [UV4 linux](uv4-linux/feature.md) | #2801 | The Riksarkivet Universal Viewer built and run on Linux so anyone can open a transcribed volume from S3 in the browser, with the text next to the page image | 9 (4 built, 5 not started) |
| [Search — Solr](search-solr/feature.md) | #2811 | Make every transcribed line findable across all volumes: a Solr index fed automatically from the results bucket, a search service, and a search page that opens the hit in the viewer with the line highlighted | 13 draft, unreviewed — not in Azure yet |
| [ATR as a Service (ATRaaS)](atr-as-a-service/feature.md) | #2831 | The batch system as a free, registration-based service for public sector and universities: organisations, a public API, uploads, quotas, retention and a thin web UI on top of htrflow-batch | 18 (0 built, 18 not started) |

The remaining feature under the epic — *Quality prediction step* (#2770)
— is tracked in the `htrflow` repository; where a story here depends on it,
it says so.

## A few words the stories use

- **Volume** — one archival unit (a bound book, a box of documents) as a
  sequence of page images. A volume is the unit of work: one volume in, one
  transcribed volume out.
- **IIIF** — the standard way Riksarkivet serves page images over the web.
  The batch system reads pages from it; the viewer displays them from it.
- **ALTO** — the XML format for a transcribed page: the text plus where each
  line sits on the image. This is what the viewer overlays on the page.
- **Campaign** — a named list of volumes to transcribe with a given model
  pipeline. Declared in a small git repo; the system picks it up by itself.
- **GitOps / pull request** — running the system by editing files in git:
  a change is proposed as a *pull request*, reviewed by a colleague, merged
  into the protected `main` branch, and the cluster then makes itself match.
  The git history is the audit trail; undoing a change is reverting a commit.
- **S3** — the object store where all results live. Nothing is stored in a
  database; the files in S3 *are* the record.
- **Kueue / Helm** — the queueing layer that hands out GPUs fairly, and the
  packaging format that installs the whole system on a cluster in one command.
- **SLSA** — *Supply-chain Levels for Software Artifacts*: a graded standard
  for proving that a piece of software was built by a trusted process from
  known source. Higher level = stronger proof.
- **Argo CD** — the tool that keeps a cluster equal to what a git repo
  says should run there (GitOps for the platform itself). Here it installs
  and updates the batch system in each environment from a deployment repo.
- **Kargo** — the promotion layer on top of Argo CD: it packages a release
  as *Freight* (exact image digests + chart version), moves it through
  *Stages* — dev → staging → prod — only after each stage's checks pass,
  and requires an approval for production.
- **Pull-through cache** — a registry of our own that fetches an image
  from Docker Hub or GitHub the first time it is asked for and serves its
  own copy afterwards, so the cluster depends on one source we control.
- **ModelPack** — a CNCF standard for storing a machine-learning model in
  a container registry as an OCI artifact, so weights get the same digest,
  signature and access control as container images.
- **NIS2** — the EU cybersecurity directive (2022/2555), Swedish
  *cybersäkerhetslagen* from 2026. Public administration is in scope, so it
  applies to Riksarkivet: management is accountable for risk measures,
  supply-chain security and vulnerability handling are required, and
  significant incidents must be reported within 24/72 hours. The SLSA,
  SBOM and Kyverno stories exist largely because of it.
- **SBOM** — *Software Bill of Materials*: the machine-readable list of
  every package inside a container image, attached to the image, so "do we
  run the vulnerable version?" is a query rather than an investigation.
- **Kyverno / policy as code** — a Kubernetes component that enforces rules
  written as files in the repo — here: "no container starts unless its image
  was signed by our CI".
