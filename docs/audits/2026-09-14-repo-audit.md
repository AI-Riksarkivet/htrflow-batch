# Repository audit — 2026-09-14 (before the first archive-scale run)

## 0. Scope

**Code:** `htrflow-batch` at `main`, commit `18a7324` — after B76 (the status
record that outlives a reaped Job) and B77 (`apply` refuses a pipeline edit
under a live campaign, and exits 3 when some objects are refused). Five angles,
one reviewer each, each in its own read-only worktree:

| Angle | Covers | Finding prefix |
|---|---|---|
| Wrapper | `packages/wrapper`, the streaming driver, resume, publish, log shipping | `W` |
| Converter | `packages/converter`: parse, validate, render, apply, prune — including B77 | `C` |
| Web + frontend | `packages/web` (read API, projection, kube client) and `frontend/` | `F`, and the `T`/`D` ids that landed on that code |
| Chart, policy, CI | `charts/`, Kyverno policies, `.github/workflows`, the dagger gates | `D`, `T` |
| Docs + tests | `docs/`, `README.md`, and the test suites as documentation of intent | plain numbers `1`–`20`, `T` |

**Method:** read-only. No reviewer changed a file, and none used a cluster.
Every finding had to name a **concrete failure scenario** — the operator or
reader who acts on the claim, and what happens to them — verified against the
code, not against another document. A finding that could only be phrased as "this
could be clearer" was dropped. Findings were then handed out one branch per
angle for a fix round; this file records every one of them and where it went.

**Dispositions.** "fixed in round 2026-09-14" means the fix is on the named
branch, not yet on `main`. "open" means no fix was attempted this round and the
finding needs a story.

**Previous audit:** [2026-09-07](2026-09-07-repo-audit.md) (after B63), whose
stories are in [2026-09-07-audit-stories.md](2026-09-07-audit-stories.md).

---

## 1. Wrapper (`W`)

Branch `audit-wrapper`. The reviewer's own texts are in that branch's commit
bodies; the four landed so far are `fb67ac6` (W1), `06876bf` (W2), `c89bd62`
(W3), `61f69b4` (W4).

| Id | Severity | File | Defect | Disposition |
|---|---|---|---|---|
| W1 | — | `packages/wrapper/src/htrflow_batch/driver.py` | a pipeline construction that fails part-way leaves the steps it already built standing, holding their models | fixed in round 2026-09-14 (branch `audit-wrapper`) |
| W2 | — | `packages/wrapper/src/htrflow_batch/driver.py` | the dict pipeline-config fallback is taken for any failure of the path call, not only for a refusal | fixed in round 2026-09-14 (branch `audit-wrapper`) |
| W3 | — | `packages/wrapper/src/htrflow_batch/` (resume/verify) | a previous run's objects answer for a page that failed in this run | fixed in round 2026-09-14 (branch `audit-wrapper`) |
| W4 | — | `packages/wrapper/src/htrflow_batch/main.py`, `logship.py` | the SIGTERM grace period is spent on three status PUTs instead of on the final log ship | fixed in round 2026-09-14 (branch `audit-wrapper`) |
| W5–W9, W11–W17 | — | see branch `audit-wrapper` | see branch `audit-wrapper` | fixed in round 2026-09-14 (branch `audit-wrapper`) |
| W10 | — | see branch `audit-wrapper` | documentation only — no code change | fixed in round 2026-09-14 (branch `audit-docs`) |
| W18 | — | see branch `audit-wrapper` | a CI/test-harness defect, not a wrapper one | fixed in round 2026-09-14 (branch `audit-deploy`) |

## 2. Converter (`C`), including B77

Branch `audit-converter`. Commits so far: `7e2969d`, `adf4be6`, `8e71c0d`,
`abc1b6b`, `8fb0f64`.

| Id | Severity | File | Defect | Disposition |
|---|---|---|---|---|
| C1–C9, C11, C13–C15 | — | see branch `audit-converter` | see branch `audit-converter` | fixed in round 2026-09-14 (branch `audit-converter`) |
| C10 | — | see branch `audit-converter` | see branch `audit-converter` | fixed in round 2026-09-14 (branch `audit-converter`) |
| C12 | — | see branch `audit-converter` | see branch `audit-converter` | open → story B86 / B78 / B80 / new |
| C16 | — | see branch `audit-converter` | see branch `audit-converter` | open → story B86 / B78 / B80 / new |

Verified from that branch's commit bodies, for the record: `apply` without
`--out` rendered into a temporary directory, so the append-only and split-shape
rules never ran for the one command that reaches a cluster (`7e2969d`);
`--prune` over an empty render deletes every managed object in the namespace,
and a repo with no `converter.yaml` silently defaulted the namespace
(`adf4be6`); a paused campaign whose Job was refused went on running and was
reported as exit 3 rather than as an unenforced pause (`8e71c0d`);
`source_template` errors surfaced as a traceback about a file the author never
opened (`abc1b6b`); `priority:` is rendered straight into a label value and was
never checked against the label grammar (`8fb0f64`).

## 3. Web + frontend (`F`, `T`, `D16`/`D17`)

Branch `audit-web`. Commits so far: `58b31e9`, `457c6fb`.

| Id | Severity | File | Defect | Disposition |
|---|---|---|---|---|
| F1–F20 | — | see branch `audit-web` | see branch `audit-web` | fixed in round 2026-09-14 (branch `audit-web`) |
| T1, T2 | — | see branch `audit-web` | see branch `audit-web` | fixed in round 2026-09-14 (branch `audit-web`) |
| T6–T12 | — | see branch `audit-web` | see branch `audit-web` | fixed in round 2026-09-14 (branch `audit-web`) |
| D16, D17 | — | see branch `audit-web` | landed on the read API's code rather than on the chart | fixed in round 2026-09-14 (branch `audit-web`) |

Verified from that branch: a failed cluster read answered without the security
headers the rest of the API carries, and is now a 502 that has them
(`58b31e9`); the test doubles for the Kubernetes reader had drifted from the
real `Reader`'s shape, so the suite could pass over a client that no longer
existed (`457c6fb`).

## 4. Chart, policy, CI (`D`, `T`)

Branch `audit-deploy`. Commits so far: `1f89d37`, `3939c93`, `a52ac7c`.

| Id | Severity | File | Defect | Disposition |
|---|---|---|---|---|
| D1 | blocking | `charts/htrflow-batch/templates/web.yaml` | the web Role's `create`/`patch` on `configmaps` necessarily covers every ConfigMap in the namespace, `htr-pipeline-<id>` included — the immutable pipeline ConfigMap a campaign Job mounts. Overwriting one makes the next campaign load weights of an attacker's choosing, from the one pod browsers reach and that has no authentication of its own | fixed in round 2026-09-14 (branch `audit-deploy`): a Kyverno ClusterPolicy matched on the web ServiceAccount denies any ConfigMap create/update not named `campaign-<name>-status` |
| D2–D8 | — | see branch `audit-deploy` | see branch `audit-deploy` | fixed in round 2026-09-14 (branch `audit-deploy`) |
| D9 | — | `docs/how-it-works/security.md` | the pod-security section still called the read API a read-only client | fixed in round 2026-09-14 (branch `audit-deploy`, commit `a52ac7c`) — the same claim as docs finding 2 below, found independently by both angles; the two fixes overlap and must be merged as one paragraph |
| D10, D13, D14, D18 | — | see branch `audit-deploy` | see branch `audit-deploy` | fixed in round 2026-09-14 (branch `audit-deploy`) |
| D11, D12, D15 | — | see branch `audit-deploy` | see branch `audit-deploy` | open → story B86 / B78 / B80 / new |
| T3 | — | see branch `audit-deploy` | see branch `audit-deploy` | fixed in round 2026-09-14 (branch `audit-deploy`) |
| T4, T5 | — | `.dagger/`, `packages/converter/tests/test_render.py`, `test_apply.py` | the CI pytest image had neither kubeconform nor git, so the manifest-validation and commit-provenance tests skipped in CI while the suite reported green — 688/3 skipped in the container against 690/1 on a host was the only trace | fixed in round 2026-09-14 (branch `audit-deploy`, commit `1f89d37`) |
| T13, T14 | — | see branch `audit-deploy` | see branch `audit-deploy` | open → story B86 / B78 / B80 / new |

## 5. Docs and tests (`1`–`20`)

Branch `audit-docs`. Every finding below was verified against the code named in
its row before it was written, and every one is fixed on that branch. Line
numbers are omitted because they moved during the fix; the section is named
instead.

| Id | Severity | File (section) | Defect | Disposition |
|---|---|---|---|---|
| 1 | high | `docs/how-it-works/signals.md` (opening, signals table, first sequence diagram); `docs/reference/chart.md` (Web front); `docs/reference/s3-layout.md` (Live status) | "Nothing in this system publishes a campaign status document." Two writers do: the read API (`projection.status_configmap` + `kube.apply_configmap`) on any request that observes something new, and `htrflow-campaigns apply` (`render.status_configmap`) from the live Job. A reader reasoning about RBAC, retention or "what survives the TTL" from this sentence gets all three wrong | fixed in round 2026-09-14 (branch `audit-docs`) |
| 2 | high | `docs/reference/chart.md` (Web front); `docs/how-it-works/security.md` (Service account tokens) | the web Role is called read-only. `templates/web.yaml` grants `create` and `patch` on `configmaps`, and RBAC cannot narrow them to a name pattern. An operator reading either page would not think to look for the write, nor to police it | fixed in round 2026-09-14 (branch `audit-docs`) — see D9: the deploy angle rewrote the same paragraph, and the two must be merged |
| 3 | medium | `docs/how-it-works/queueing.md` (Known limits) | the Kueue API version "can differ from the version the chart renders". It *does*: `cluster.py` pins the older beta version, `templates/kueue.yaml` renders the newer. Written as a hypothetical, nobody looks for the symptom — a campaign git says is paused that keeps running | fixed in round 2026-09-14 (branch `audit-docs`) |
| 4 | medium | `docs/how-it-works/queueing.md` (what Kueue sees); `docs/how-it-works/signals.md` (known limits); `docs/reference/wrapper.md` (the Job sample, `ttlSecondsAfterFinished: 86400`) | the Job's TTL is given as a day. `ConverterConfig.ttl_seconds_after_finished` defaults to a week, and a pipeline may set its own. An operator sizing a retention window, or waiting for a Job to disappear, is out by a factor of seven | fixed in round 2026-09-14 (branch `audit-docs`) |
| 5 | high | `docs/roadmap/index.md` (Finished campaigns after the TTL); `docs/how-it-works/queueing.md` (Known limits) | "A campaign reaped by its TTL runs again." Since B76, `apply` writes the terminal record off the live Job and then leaves a finished, unchanged campaign alone (`cli._record_and_decide`, `cli._finished`). The page predicts a GPU bill that does not happen, and hides the case that does: an appended volume list past the TTL | fixed in round 2026-09-14 (branch `audit-docs`) |
| 6 | medium | `docs/how-it-works/signals.md` (known limits) | past the TTL the read API "answers 404". It serves the stored record (`app._reaped_detail`, `projection.record_summary`); the 404 is for a campaign with no record at all. An operator would not think to look at the status page for a finished campaign | fixed in round 2026-09-14 (branch `audit-docs`) |
| 7 | high | `docs/reference/wrapper.md` (index example, "Reading the file yourself"); `docs/how-it-works/campaigns.md` (ConfigMap sample) | `images:` URLs shown comma-joined. `models.split_image_urls` and `Config.image_urls` split on **whitespace**, and `Volume.source_line` joins with a space. A hand-written `volumes.txt` copied from these examples gives the wrapper one unresolvable URL | fixed in round 2026-09-14 (branch `audit-docs`) |
| 8 | medium | `docs/reference/s3-layout.md` (progress.json fields); `docs/reference/wrapper.md` (Stages) | the `progress.json` stage list carries `config`, which it can never hold — the tracker is built after that stage, so a `ConfigError` leaves no `progress.json` at all — and omits `failed`, which `main` writes on every non-success exit. Someone debugging a volume with no progress file is told to expect one | fixed in round 2026-09-14 (branch `audit-docs`) |
| 9 | medium | `docs/how-it-works/security.md` (Read-only root filesystem) | the tmpfs workdir is described as being "in campaign and warm-up pods". `warmup-job.yaml`'s `work` volume is a plain `emptyDir` with a size limit; only `campaign-job.yaml`'s carries `medium: Memory`. An operator sizing warm-up memory, or explaining a full node disk, is looking in the wrong place | fixed in round 2026-09-14 (branch `audit-docs`) |
| 10 | high | `docs/how-it-works/security.md` (NetworkPolicy table) | the warm-up pod is listed as unable to reach S3. Its policy allows `0.0.0.0/0` on 443 minus the pod, service and node ranges — an S3 endpoint on the public internet is reachable. What keeps it out of the bucket is holding no credential. The table overstates the isolation a reader is asked to rely on | fixed in round 2026-09-14 (branch `audit-docs`) |
| 11 | low | `docs/how-it-works/signals.md` (signals table); `docs/reference/s3-layout.md` (Live status) | `progress.json` is "cached a few seconds". `progress.RUNNING_TTL` is 5 s and `DONE_TTL` is an hour. Someone watching a finished volume for a change waits an hour for it | fixed in round 2026-09-14 (branch `audit-docs`) |
| 12 | medium | `docs/getting-started/campaigns.md` (From a kubeconfig) | "This one command does four things." It does five: before anything is sent it reads each live Job, writes the status record from it, and skips a campaign the record says finished unchanged. The step that decides whether a campaign runs at all was missing from the list | fixed in round 2026-09-14 (branch `audit-docs`) |
| 13 | low | `docs/reference/frontend.md` (Failures block) | the callout is described as showing `JobDetail.failures`. `CampaignCard` renders `unseenFailures`: with the table open it drops every failure already on a loaded page, and the heading becomes "failures not shown below". A reader expecting the full list when the table is open does not get it | fixed in round 2026-09-14 (branch `audit-docs`) |
| 14 | low | `README.md` (Developing) | `make ci` is said to run "budgets". It runs two dagger gates; `scripts/loc-budget.sh` is a separate CI step. A contributor trusting `make ci` green pushes a budget failure | fixed in round 2026-09-14 (branch `audit-docs`) |
| 15 | medium | `docs/roadmap/index.md` (Durable failure history) | a failed volume is said to be remembered only by the run log, which the next attempt overwrites. The status record keeps up to 50 failed ids with a sentence each (`projection._MAX_FAILURES`). The roadmap proposes building something that partly exists | fixed in round 2026-09-14 (branch `audit-docs`) |
| 16 | medium | `docs/how-it-works/campaigns.md` (Architecture diagram) | the read API's edge points at Kueue. It reads Jobs, Pods and ConfigMaps and writes the status one; it never talks to Kueue. `architecture.md` already draws it at the Job | fixed in round 2026-09-14 (branch `audit-docs`) |
| 17 | medium | `docs/how-it-works/campaigns.md` (The record a campaign leaves) | the table says both writers write `failedVolumes`. Only the read API's **detail** route does — `projection.status_record` takes `failures` only from that route, and the list route and `apply` omit the field rather than send an empty one over it. An operator wondering why a record has counts but no ids is told it cannot happen | fixed in round 2026-09-14 (branch `audit-docs`) |
| 18 | medium | `docs/reference/wrapper.md` (Warm-up entrypoint) | the warm-up env table omits `HF_TOKEN`, which `render.py` adds from `hf_token_secret` and `warmup.py` reads. Someone debugging a gated-model 404 has no list entry telling them the token is a warm-up-only variable | fixed in round 2026-09-14 (branch `audit-docs`) |
| 19 | low | `docs/reference/frontend.md` (Warm-up chip) | "`failed` and `missing` also push the card's left accent to the failed colour". `missing` does so only while the phase is not `Succeeded` — deliberately, so an old pipeline with no warm-up Job does not paint a finished campaign red | fixed in round 2026-09-14 (branch `audit-docs`) |
| 20 | medium | `docs/reference/campaign-yaml.md` (Campaign file) | `priority:` is called "a Kueue PriorityClass name". It is rendered into `kueue.x-k8s.io/priority-class`, which names a **WorkloadPriorityClass**; `how-it-works/campaigns.md` already says so. Two reference pages disagreeing is how an operator creates the wrong object | fixed in round 2026-09-14 (branch `audit-docs`) |

**Test findings from this angle** (`T`) were routed to the angle that owns the
code: `T1`/`T2` and `T6`–`T12` to `audit-web`, `T3`–`T5` to `audit-deploy`,
`T13`/`T14` left open.

---

## 6. Open findings

Nothing on this list was fixed in the 2026-09-14 round. Each needs a story —
B86, B78 or B80 where one already covers the ground, otherwise a new one.

| Id | Angle | Disposition |
|---|---|---|
| C12 | converter | open → story B86 / B78 / B80 / new |
| C16 | converter | open → story B86 / B78 / B80 / new |
| D11 | chart/policy/CI | open → story B86 / B78 / B80 / new |
| D12 | chart/policy/CI | open → story B86 / B78 / B80 / new |
| D15 | chart/policy/CI | open → story B86 / B78 / B80 / new |
| T13 | tests | open → story B86 / B78 / B80 / new |
| T14 | tests | open → story B86 / B78 / B80 / new |

## 7. What the fix round must not lose

- **D1 and docs finding 2 touch the same paragraph.** The chart angle narrows
  the ConfigMap write with a Kyverno policy and rewrote
  `how-it-works/security.md` to say so (`a52ac7c`); the docs angle rewrote the
  same paragraph against `main`, where no such policy exists. Merge them into
  one paragraph that names the policy — do not take one side wholesale.
- **The docs angle deliberately described `main`, not the pending fixes.** Any
  page that describes a behaviour another branch is changing (the web Role's
  scope above, the converter's `priority:` validation, the empty-render prune)
  needs a second pass once those branches land.
