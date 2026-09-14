# General documentation — design

Status: direction and page map agreed with the product owner 2026-09-14.
Documentation only; goes straight to `main` under the docs rule (strict site
build as the gate).

## 1. Problem

The published site reads as a project diary for one deployment rather than
documentation of a system:

- **Site-bound.** Pages name one node's hardware, one cluster flavour, one
  institution's IIIF host and manifest template, personal home-directory
  paths, and `localhost` / loopback registry addresses — including in the
  install instructions.
- **History-bound.** Story and task identifiers, dates, "this replaced X"
  notes and a status changelog are woven through user-facing pages; the
  landing page's status section is a changelog.
- **Version-bound.** Chart, wrapper, Kueue, Kyverno and Kubernetes versions
  are written into prose and go stale on the next release.
- **Audiences mixed.** Run logs, the test log, audits and the decision log
  share the navigation with Getting Started; "How it Works" is twelve pages
  with no reading order.
- **Needless warnings.** Campaigns-repo branch protection is repeated on seven
  pages, once as a red "user action" box. It is ordinary GitOps hygiene, not a
  property of htrflow-batch.

## 2. Goal

The site documents htrflow-batch for any operator, on any conformant
Kubernetes cluster with NVIDIA GPU nodes, with any IIIF source and any
S3-compatible bucket. Project history stays in git but leaves the site.

## 3. Writing rules (every site page and the root README)

1. **Present tense, the system as it is.** No story/task/decision identifiers
   (`B63`, `D14`, `Task 22`, `X2`), no dates, no "used to", "since", "was
   replaced by", "Phase 1/2", "PoC".
2. **No version numbers.** Point at the file that holds the version
   (`Makefile` `KUEUE_VERSION`, `Chart.yaml`, `pyproject.toml`) instead.
3. **No specific hardware, node, cluster distribution or site.** No GPU model
   or generation, CPU architecture, emulation notes, cluster distribution,
   institution host or address, home-directory path, or loopback registry.
   Requirements are stated generally ("an NVIDIA GPU supported by the htrflow
   image", "your node's architecture").
4. **Placeholders** are `<angle-bracketed>` and named for what they are:
   `<registry>`, `<iiif-manifest-url>`, `<results-base-url>`, `<namespace>`.
5. **Reasons stay next to what they explain**, in a sentence or two. No page
   depends on the decision log.
6. **Say it once.** A requirement or trust statement lives on one page and is
   linked, not repeated. The campaigns repo's trust statement lives in
   How it Works → Security, once, framed as the trust model (write access
   selects the image and models that run with the bucket's write credentials;
   the enforcing controls are the chart's Kyverno policies and digest pins).
7. No person's name anywhere; roles only.

## 4. What leaves the site

Stays in git at its current path — story paths are mirrored to Azure DevOps,
and code comments, stories and READMEs link these files — but is removed from
the navigation and from the staged build:

| Path | Why it stays at its path |
|---|---|
| `docs/features/` | already excluded; Azure DevOps mapping |
| `docs/superpowers/` | specs and plans; linked from stories |
| `docs/audits/` | findings are referenced by stories |
| `docs/how-it-works/decision-log.md` | linked from stories B67, B85 |
| `docs/development/e2e-indexed-jobs.md` | linked from `render.py`, the devstack README, stories B13, B63 |
| `docs/development/test-log.md` | linked from `packages/wrapper/README.md` |

Deviation from the page map as first presented: these three files were to move
under `docs/history/`. Moving them would break links from code, READMEs and
stories, so they are excluded by path in `scripts/docs-site.sh` instead.

`scripts/docs-site.sh` removes all six from the staged copy. Any remaining
link from a site page into them then fails the strict build.

## 5. Site map

Source pages on the left; every source page is accounted for.

### Home

| Target | Sources | Content |
|---|---|---|
| `index.md` | `index.md` | What it does, one diagram of how it fits together, where to start, licence. The not-ready notice stays, without a version. No status section. |

### Getting Started

| Target | Sources | Content |
|---|---|---|
| `getting-started/index.md` — Prerequisites | same | Cluster with Kueue and Kyverno; NVIDIA GPU nodes with the device plugin; S3-compatible bucket; registry; a IIIF source. |
| `getting-started/deploy.md` — Deploy | `deploy.md` minus the replay section | Install, required values, bucket policy and CORS, hardening, upgrading. |
| `getting-started/try-it.md` — Try it (new) | `deploy.md` replay section, `run-a-volume.md` compose section, the README's first-transcription block | The compose smoke stack (no cluster), then a dev cluster with `charts/htrflow-devstack`. |
| `getting-started/campaigns.md` — Run a campaign | same | Create the repo, pin a digest, render and apply, add work, watch. No danger box. |
| `getting-started/viewing.md` — View results | same | URL scheme, reading ALTO, exposing the web front generally. |
| — (removed) | `run-a-volume.md` | Job contract, env vars and exit codes go to Reference → Wrapper; compose goes to Try it. |

### How it Works

| Target | Sources | Content |
|---|---|---|
| `how-it-works/architecture.md` | same | The map, component boundaries, Job lifecycle, reading order. |
| `how-it-works/campaigns.md` | `campaigns.md`, `rendering-example.md` | Repo, converter, what is rendered (the worked example as a section), immutability, retries, trade-offs stated generally. |
| `how-it-works/queueing.md` | `queueing.md`, `kueue.md` | One page: topology, admission, pause, the window, preemption and cohorts, who owns which field, operator's reading. |
| `how-it-works/wrapper.md` | `wrapper.md`, `memory-budget.md` | Streaming driver, stages, provenance, model cache, pipeline configs, memory bounds. Kueue topology and the Job template are linked, not repeated. |
| `how-it-works/page-flow.md` | same | One page from IIIF GET to upload. |
| `how-it-works/failure-handling.md` | same | Invariants, exit codes, retries, warm-up failures, what a person is told. |
| `how-it-works/signals.md` | `signals.md`, `live-run-log.md` | Events and signals, with the live run log as a section. |
| `how-it-works/security.md` | `development/security.md` | Trust model (including the campaigns-repo statement), bucket policy, pod posture, NetworkPolicy, cache PVC ownership, devstack caveats. |

### Reference

| Target | Sources | Content |
|---|---|---|
| `reference/index.md` | same | Pages and packages. |
| `reference/configuration.md` | generated from `scripts/config_reference.md` + models + `values.yaml` | Edit the sidecar only, then `make config-reference`. |
| `reference/campaign-yaml.md` | same | Formats and rules; no "where these rules used to live" notes. |
| `reference/wrapper.md` | `reference/wrapper.md`, `run-a-volume.md` (Job contract, `volumes.txt`, env, exit codes) | The wrapper's full contract. |
| `reference/chart.md` | same | Current values only; the "Removed in …" sections are dropped (the chart README's changelog already holds them). |
| `reference/s3-layout.md` | same | Keys, `manifest.json`, `progress.json`, live status. |
| `reference/frontend.md` | same | Campaign browser. |

### Roadmap

| Target | Sources | Content |
|---|---|---|
| `roadmap/index.md` | `evolution.md`, `open-items.md` | What is open and what could come next, without identifiers or dates. |
| `roadmap/phase-2-cache.md` → `roadmap/cache-layer.md` | `phase-2-cache.md` | The evidence-gated cache-layer proposal, general. |

### Development

| Target | Sources | Content |
|---|---|---|
| `development/index.md` — Setup | same | Workspace setup, acceptance levels. |
| `development/testing.md` | same | Levels and how to run them; no links into the test log. |
| `development/ci.md` | same | Dagger functions, Makefile targets, workflows, how pins are managed (not what they are). |
| `development/releasing.md` | `development/deployment.md` | Building and publishing images; chart releases point at the chart README. |
| `development/dev-cluster.md` | `development/local-k3s.md` | A single-node dev cluster: `.env`, building the GPU image for the node's architecture, the in-cluster registry, applying a campaigns repo, reaching the web front, general gotchas. |
| `development/licenses.md` | same | Unchanged apart from the rules. |

29 pages, from 45 in the navigation today.

## 6. Root README

Same rules. Keeps the two diagrams and the repository table. The
first-transcription block stops prescribing a cluster distribution and image
version and links Try it. "Where things stand" loses versions and the audit
link. The site links follow the new page names.

## 7. Enforcement

`scripts/docs-lint.sh` greps the staged site copy and `README.md` and exits
non-zero with `path:line: rule` for each hit:

| Rule | Pattern (extended regex, case-insensitive where it matters) |
|---|---|
| ids | `\b[BDTUCXGI][0-9]{1,3}\b`, `\bS[0-9]{2}\b`, `\bTask [0-9]+`, `\bPhase [12]\b`, `\bPoC\b` |
| dates | `\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b` |
| versions | `\bv?[0-9]+\.[0-9]+\.[0-9]+\b`, `\bv[0-9]+\.[0-9]+\b` |
| hardware | `GB10`, `\bAda\b`, `Blackwell`, `\b[AHL][0-9]{1,3}\b` GPU names, `arm64`, `amd64`, `aarch64`, `x86_64`, `qemu`, `binfmt`, `k3s` |
| site | `arkis`, `lbiiif`, `riksarkivet\.se`, `/home/`, `127\.0\.0\.1`, `localhost`, `192\.121\.` |

`scripts/docs-lint.allow` holds `path:regex` exceptions, each with a comment
saying why. The only planned entries are `localhost` on
`getting-started/try-it.md` (the compose stack runs on the reader's own
machine), and the code defaults in §9 on `reference/configuration.md` and
`reference/chart.md` (the IIIF CIDR, the manifest template, the pod/service
CIDRs), removed when §9 lands.

Wiring: `docs-site.sh` runs the lint on the staged copy before building;
`.github/workflows/docs.yml` builds with `--strict`. The lint is wired in by
the last commit of the work, when it passes, so the published site builds
throughout.

## 8. Delivery

- A worktree off `org/main`; one page or one merge per commit; each commit
  gated locally on `scripts/docs-site.sh build --clean --strict` and pushed to
  `main`.
- Order: exclusions and navigation first (so removed pages stop being built),
  then Getting Started, How it Works, Reference, Roadmap, Development, README,
  and finally the lint wiring.
- Mermaid rules: no `;` in any message, label or `%%` comment; no
  angle-bracket tokens in sequence-diagram text; flowcharts `TB`.
- `make config-reference` after editing the sidecar, and
  `packages/*/tests/test_chart_agreement.py` must still pass.

## 9. Out of scope — follow-ups

Code, so branch and review rather than straight to `main`:

- `packages/converter/src/htrflow_converter/models.py:283` — `source_template`
  defaults to one institution's manifest template; make it required or a
  neutral placeholder.
- `charts/htrflow-batch/values.yaml:152-157` — `network.iiifCidrs` defaults to
  one institution's IIIF address, and its comment names the host.
- `charts/htrflow-batch/values.yaml` — `network.clusterCidrs` defaults to one
  distribution's pod and service ranges.
- `packages/wrapper/src/htrflow_batch/iiif.py:130` — the comment names one
  server; describe the behaviour instead.

Documentation, later:

- `charts/htrflow-batch/README.md`, `charts/htrflow-devstack/README.md` and
  the package READMEs under the same rules.

## 10. Done when

- `scripts/docs-site.sh build --clean --strict` is clean with the lint wired in.
- The navigation holds exactly the 29 pages in §5; none of the §4 paths is in
  the built site.
- `grep` of the built site for the §7 patterns returns only the allow-listed
  lines.
- `make config-reference` leaves the tree unchanged and the chart-agreement
  test passes.
