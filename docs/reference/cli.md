# htrflow-campaigns CLI

`htrflow-campaigns` is the converter's command line. It reads a campaigns
repo, checks it, renders it to Kubernetes objects and applies them. The
file formats it reads are in [Campaign & Pipeline YAML](campaign-yaml.md).
What a render produces, field by field, is in
[Rendered objects](rendered.md).

Source: [`packages/converter/src/htrflow_converter/cli.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/cli.py).

## Commands

| Command | What it does |
|---|---|
| `init <dir> [--force] [--ci github\|azure]` | Writes a new campaigns repo from the template, with GitHub Actions (default) or Azure Pipelines CI. |
| `validate <repo> [--rendered]` | Checks `converter.yaml`, `campaigns/` and `pipelines/`, and prints one line per problem. `--rendered` also refuses unless the committed `rendered/` is exactly what this checkout renders. |
| `render <repo> --out <dir>` | Writes the rendered objects to `<dir>`. Renders nothing at all if there is one problem. |
| `apply <repo> [flags]` | Renders into a temporary directory (or `--out`), then applies to the cluster. See below. |

`make campaigns-apply DIR=<repo>` runs
`uv run htrflow-campaigns apply <repo> --out <repo>/rendered`. `PRUNE=1`
adds `--prune`, and `ALLOW_EMPTY=1` adds `--allow-empty`.

| `apply` flag | Meaning |
|---|---|
| `--prune` | Delete the converter-labelled objects this render did not produce. Opt-in. See [Prune](#prune-cancelling-a-campaign). |
| `--allow-empty` | Let `--prune` run on a render with no campaigns, which cancels every campaign in the namespace. |
| `--namespace <ns>` | The namespace this apply is meant for. Refused, with nothing applied, unless `converter.yaml` names the same one. |
| `--pause-wait <s>` | How long to wait for a new paused campaign's Kueue Workload (default 10). |
| `--dry-run` | Render and print `would apply: <Kind>/<name>` lines. Opens no connection. |
| `--out <dir>` | Render here instead of a temporary directory. |

`apply` talks to the API server through the official Kubernetes client,
with server-side apply and no `kubectl`. It authenticates from
`$KUBECONFIG` or, in a pod, from the mounted ServiceAccount token. The chart
renders a suitable ServiceAccount behind `apply.rbac.enabled`
([Chart Values](chart.md#apply-identity-apply)).

## What `apply` does, in order

1. **Render** the repo. A render that does not pass stops here.
2. **Check the namespace** against `--namespace`, and refuse an empty
   render under `--prune` without `--allow-empty`.
3. **Take the Lease** `htrflow-campaigns-apply` for the whole run
   ([One apply at a time](#one-apply-at-a-time)).
4. **Check against the live cluster**, before anything is sent. Any of
   these stops the whole apply with exit `1`:
    - a campaign whose live ConfigMap has a different volume list, pipeline
      or image
    - a pipeline whose live steps differ while a campaign Job that has not
      ended still mounts it
    - a live, unpaused campaign Job whose pod count the render would change
      ([Changing a window](#changing-a-running-campaigns-window))
5. **Record check.** For each campaign, read the live Job and write the
   ending it shows into the campaign's status record. A campaign that is
   finished and whose volume list has not moved is left alone
   ([Finished campaigns](#finished-campaigns-are-not-run-again)).
6. **Volume check.** Hold back a campaign that shares a volume with another
   campaign on the same pipeline that is still running, or starting in this
   apply.
7. **Dry-run each campaign Job** (`dryRun=All`, admission webhooks
   included). A campaign whose Job would be refused keeps its ConfigMap
   unchanged too.
8. **Apply pipelines**: each pipeline's ConfigMap and warm-up Job. They go
   first because a campaign Job mounts its pipeline's ConfigMap.
9. **Apply campaigns**: each campaign's ConfigMap, then its Job.
10. **Pause sync**: set each campaign's Kueue Workload `spec.active` from
    git ([Pausing](#pausing)). This reaches campaign Jobs the API server
    refused too.
11. **Prune**, with `--prune` only.

The pause sync runs before the prune on purpose. A missing pause burns GPU
time now, so a prune problem must never stop it.

Every action prints one line: `applied: <Kind>/<name>`, `replaced: …`,
`pruned: …`, or a sentence on stderr for a refusal.

## `rendered/`

```
rendered/
  pipelines/<id>.yaml     # ConfigMap htr-pipeline-<id> + Job htr-warmup-<id>
  campaigns/<name>.yaml   # ConfigMap campaign-<name> + the campaign's Indexed Job
  sync.yaml               # ConfigMap htrflow-campaigns-render: the render's digest
```

The campaigns repo's CI renders this on `main` and commits it. Never edit
it by hand.

- **Only `htrflow-campaigns apply` applies it.** Never `kubectl apply` it,
  and never let Argo CD apply it. A finished campaign's Job is deleted at
  its TTL while its file stays in `rendered/`, and a tool that makes the
  cluster match the directory creates that Job again and runs every volume
  again. It would also skip every check above.
- **Every object is an Argo CD Skip hook.** Each object under `pipelines/`
  and `campaigns/` carries `argocd.argoproj.io/hook: Skip`. An Application
  that syncs `rendered/` applies, heals and prunes none of them.
- **The committed `rendered/` is the record.** `--out` says only where a
  render is written. `validate` and `render` always hold the repo against
  its own committed `rendered/`, so rendering into a fresh directory does
  not make a running campaign new.
- **`render` removes files it did not produce.** Deleting
  `campaigns/<name>.yaml` deletes `rendered/campaigns/<name>.yaml` too.
  `render` writes the whole render beside `--out` and moves it in only when
  it is complete. Anything in `--out` other than `pipelines/`, `campaigns/`
  and `sync.yaml` is left alone.
- **The static shape is in the skeletons.** The four objects are real YAML
  under
  [`manifests/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/packages/converter/src/htrflow_converter/manifests),
  with placeholders that `render.py` fills in.

## With Argo CD

Argo CD syncs one object and runs the command as a hook:

- **The Application's source** is the campaigns repo, with
  `directory.recurse: true`, `directory.include` set to
  `{rendered/sync.yaml,argocd/*.yaml}`, and automated sync on.
- **`rendered/sync.yaml`** is a ConfigMap holding a digest of the render. A
  render that changes anything changes it, so the Application goes
  OutOfSync and syncs. Without it, an Application whose other objects are
  all Skip hooks is never OutOfSync. It carries no converter label, so a
  prune leaves it alone.
- **The hook** is a `PostSync` Job that runs `htrflow-campaigns apply
  --prune` on a checkout of the campaigns repo
  ([The hook manifest](#the-hook-manifest)).

A refresh, a self-heal or a re-sync with no new render changes nothing.
Cancelling is the command's `--prune`: Argo CD never applied a campaign
object, so it has none to prune.

!!! warning "An Application that already applied `rendered/` itself"

    Such an Application tracks every campaign object it applied. Once a
    render makes them Skip hooks, its next sync with pruning on may delete
    them. Turn its pruning off, or delete it with
    `argocd app delete --cascade=false`, before the first such render
    reaches it.

### The hook manifest

`htrflow-campaigns init` writes it as `argocd/apply.yaml`
([in the template](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/template/argocd/apply.yaml)).
It is a `PostSync` Job on the `htrflow-campaigns` image with three steps:

1. An init container clones the campaigns repo over HTTPS with dulwich
   (pure-Python git; the image has no git binary and no shell).
2. A second init container runs `htrflow-campaigns validate --rendered
   /repo`.
3. The main container runs `htrflow-campaigns apply --prune --namespace
   <the hook's namespace> /repo`.

What to set:

| Setting | Value |
|---|---|
| `REPO_URL`, `REPO_BRANCH` | The clone step's env: the campaigns repo's HTTPS URL and the branch CI renders on. |
| `HTRFLOW_APPLIED_BY` | The apply container's env: `argocd-hook/<application>`. It becomes the record's `applied-by`; without it the record says `unknown`. |
| Secret `htrflow-campaigns-git`, key `token` | A read-only token for the repo, in the release namespace: `kubectl -n <namespace> create secret generic htrflow-campaigns-git --from-literal=token=<token>`. |
| Chart `apply.rbac.enabled=true` | The ServiceAccount the Job runs as. |
| Chart `apply.gitCidrs` (and `apply.gitPorts` if not 443) | The egress rule to the git host, by address. The hook's default-deny NetworkPolicy opens only DNS and the API server. |

**Why `validate --rendered`.** The hook clones the tracked branch, not the
revision Argo CD synced, because a hook Job cannot reliably learn that
revision. So the check renders the checkout and refuses it unless the
result matches the committed `rendered/`, compared as parsed objects. A
commit merged after CI's last render fails the hook and applies nothing.
CI's render commit for it then changes `sync.yaml` and starts the next
sync. The check also fails when CI's `CONVERTER_REF` and the hook's image
render different objects, so keep them on the same converter release.

**Exit codes in a hook.** The hook fails the sync on any non-zero exit.
Exit `3` still changed the cluster, and its summary line names what is left
to fix. Read the summary line rather than the code
([Exit codes](#exit-codes)).

## Pausing

`suspend: true` in a campaign file renders `spec.suspend: true` on its Job.
But Kueue owns `spec.suspend` on a Job it has admitted and undoes any edit
within seconds. So the apply enforces the pause on the Workload instead
([Queueing → Pause](../how-it-works/queueing.md#pause)):

- **Every campaign's Workload gets `spec.active`**: `false` for a
  suspended campaign, `true` otherwise. The patch is idempotent.
- **Deactivating** evicts the campaign's pods and keeps every finished
  index. `kubectl get job` then shows `suspend: true`.
- **Reactivating** continues at the next unfinished index.
- **`active: true` is written for every unpaused campaign.** A Workload
  Kueue deactivated on its own (a requeue limit, `maximumExecutionTimeSeconds`)
  is re-admitted at the next apply. Nothing stays paused unless git says so.
- **A new paused campaign has no Workload yet** when its Job is created.
  The apply waits up to `--pause-wait` seconds for it, and exits `1` if it
  never appears: re-run the apply. An unpaused campaign whose Workload is
  missing is skipped, since a Workload that does not exist is not admitted.
- **Resuming a campaign Kueue never admitted** keeps its Job suspended
  through the apply, under a second field manager
  (`htrflow-campaigns-suspend`), until Kueue admits it.
- **A campaign Job the API server refused still has its pause synced.** A
  converter upgrade can make every live campaign Job refused, and a paused
  campaign must still stop.

With Argo CD, a merged `suspend: true` takes effect on the sync its render
triggers.

!!! warning "A campaign committed as `suspend: true` still runs one pod for a few seconds"

    Kueue creates, admits and unsuspends the Job in the same second it is
    created, before the apply can reach it. One pod starts and is deleted a
    few seconds later. Nothing is written for that volume, and the index
    runs again when the campaign resumes.

    Rendering a paused Job without the Kueue queue label, so Kueue never
    sees it, does not work. It strands an admitted Workload that keeps the
    campaign's quota, and Kueue refuses to put the label back on resume.

## Prune: cancelling a campaign

Deleting a campaign file removes its rendered file, but the objects stay in
the cluster until an apply prunes them:

- **`apply --prune`** lists every Job and ConfigMap in the namespace that
  carries the converter's `managed-by=converter` label and deletes the ones
  this render did not produce. Every object the converter renders carries
  that label. So does the status record, which a prune removes together
  with its campaign.
- **It is opt-in.** Without `--prune`, a deleted campaign's Job simply
  stays. The Argo CD hook always passes it.
- **Never prune from a partial checkout.** Anything missing from the render
  is deleted.
- **An empty render is refused.** A render with no campaigns at all under
  `--prune` looks like an empty `campaigns/`, a mistyped directory or a
  checkout that never happened. Pass `--allow-empty` when retiring the last
  campaign is what you mean. `--dry-run` still shows what such a prune
  would delete.
- **A deleted pipeline file** takes its ConfigMap and warm-up Job with it.

Results in S3 are never touched by a prune, or by anything else here.

## One apply at a time

Two applies at once, such as the Argo CD hook and a hand-run apply, would
interleave. A prune from the older checkout would delete what the newer one
just created. So:

- `apply` holds the `coordination.k8s.io` Lease `htrflow-campaigns-apply`
  in the namespace for its whole run, and releases it however it ends,
  SIGTERM included.
- A second apply while the Lease is held sends nothing, names the holder
  and exits `1`. Re-run it once the first is done.
- The holder renews the Lease as it goes. A Lease not renewed for ten
  minutes belongs to an apply that died, and the next apply takes it over.
- An apply that cannot renew, or finds the Lease taken over, stops and
  exits `1`, since another apply may be running.
- Both the renewal times and the takeover judgement use the API server's
  clock, never the local machine's.

`--namespace` guards the other side: the chart's policies match the release
namespace only, so an apply from a kubeconfig with wider rights must not
put Jobs anywhere else. The Argo CD hook passes its own namespace.

## Checks `apply` makes against the cluster

`validate` and `render` hold a repo against its committed `rendered/`. That
can be missing or behind the cluster, so `apply` checks the live objects as
well.

### Append-only and immutable recipes

| What `apply` finds | What happens |
|---|---|
| A campaign's live ConfigMap has a different volume list, pipeline or image | `campaign <name> is in the cluster with different …`, nothing is applied, exit `1`. |
| A pipeline's live steps differ, and a campaign Job that has not ended mounts it | `pipeline <id> is in the cluster with different steps and campaigns … still run it`, nothing is applied, exit `1`. Steps are compared parsed. |
| A ConfigMap it may not read | Stops the same way: a check that cannot be made has not passed. |

The rules themselves are in
[Campaign & Pipeline YAML → Immutability](campaign-yaml.md#immutability).

### Changing a running campaign's window

Kueue compares an admitted Job's pod count (the smaller of `parallelism`
and the volume count) with its Workload. When they differ, it stops every
running pod and queues the campaign again. So:

- `validate` and `render` print a `warning:` when a window change would
  change a rendered, unpaused campaign's pod count, and go on.
- `apply` refuses the change for a live, unpaused campaign Job: `campaign
  <name> runs <n> pods at a time and would now run <m> … nothing was
  applied`, exit `1`.

The safe way: pause the campaign, change the window once the pause is
applied, then resume it. A paused campaign's Workload is updated in place.

### Two campaigns on one volume

Results are keyed by pipeline and volume, so two campaigns on one pipeline
must not run the same volume at once. `apply` compares each campaign's
volume ids with those of every other campaign on the same pipeline whose
Job has not ended, and with campaigns earlier in the same apply. A campaign
that shares one is left as it was, named with the other campaign and the
volumes, and the apply exits `3`. Once the other campaign has ended, the
next apply sends it. If the running campaigns cannot be read, each new
campaign is held back the same way.

Listing a failed volume again in a new campaign is how it runs again, and
that is fine once the old campaign is over.

### Finished campaigns are not run again

`apply` reads each campaign's live Job and its status record
(`campaign-<name>-status`) before it sends anything:

- **A live Job decides.** While the Job exists, it alone says whether the
  campaign is over, and its ending is written into the record.
- **With no Job, the record decides.** A campaign the record says is
  `Succeeded`, `Failed` or `PartiallyFailed`, whose `volumes.txt` has not
  moved, is left alone: `campaign kyrk finished <date>, unchanged, left
  alone (120/120 volumes)`.
- **A record counts only if it names this campaign's Job.** `apply` stamps
  the uid of the Job it created on the campaign ConfigMap (`job-uid`). A
  record about another Job, or about a Job never created, is not believed:
  `apply` says so and applies the campaign. A ConfigMap from before the
  stamp existed carries no uid, and its record is believed.
- **A Job or record that cannot be read** leaves that campaign exactly as
  it is, named, with exit `3`.
- **There is no `--force`.** A campaign that should run again is a new
  campaign file.

What the record holds is in
[Campaigns → The record](../how-it-works/campaigns.md#the-record-a-campaign-leaves).

## When the API server refuses an object

`apply` sends each object on its own. **One refusal is one object's
problem**: it is named on stderr, everything else is still applied, and a
summary line lists what was left unchanged. A campaign's ConfigMap and Job
are one change: if the Job's dry run is refused, the ConfigMap is kept as
it was too.

```
Job htr-warmup-demo: the pod template changed and a Job's pod template is immutable once the Job exists — a pipeline id is a permanent name for a recipe, so a changed recipe is a new pipeline file, and a Job that has to change is deleted and created again
2 of 6 objects were refused by the API server and are unchanged: ConfigMap/campaign-kyrk, Job/kyrk — the other 4 were applied (exit 3)
```

A pause sync that did not reach a Workload has its own closing line:
`the pause sync did not reach the Kueue Workload of Job/kyrk; see above
(exit 1)`.

A Job's pod template is fixed once the Job exists. Two different changes
move it: a recipe edit, and a converter upgrade or `converter.yaml` setting
that reaches every Job (the RuntimeClass, the Hub token's env). They get
different answers:

- **A warm-up Job is replaced.** It holds no state beyond its marker on the
  cache PVC, so `apply` deletes it, creates it again and prints
  `replaced: Job/htr-warmup-<id> — its pod template changed …`.
    - A warm-up that is **running** is left alone and reported, since
      deleting it would kill the download. If the change was to the recipe,
      re-run the apply once the old warm-up finishes, before the new
      recipe's campaigns run out of `warmup_wait_seconds`.
    - A warm-up that has **failed** is replaced even with an unchanged
      template, since a failed Job never runs again: `replaced:
      Job/htr-warmup-<id> — it had failed …`.
- **A campaign Job is never replaced.** Its completed indexes are the
  campaign, and deleting it would start every volume over. It is reported,
  left as it is, and the apply exits `3`. Its pause still follows git.

## Exit codes

The codes are a precedence, highest first: `1` beats `3` beats `0`.

| Exit | Meaning |
|---|---|
| `1` | A pause is **not enforced**, whatever else was applied: a paused campaign's Workload never appeared, the Workload patch failed, or its Job was refused and could not be read. Or nothing reached the cluster: no credentials, an unreachable API server, a render that did not pass, every object refused, the Lease held by another apply, a `--namespace` mismatch, or a live-cluster check that failed. Or the API server stopped answering part-way, or the Lease was lost: the line names the object the apply stopped at, and a re-run finishes the job. |
| `3` | Some objects were refused and are unchanged, a prune could not delete some, a campaign's Job or record could not be read, or a campaign was held back for sharing a volume. Everything else was applied, and every pause holds. The summary line names each one. |
| `0` | Everything was applied. |

`validate` and `render` exit non-zero on any problem, with one line per
problem ([When something is wrong](campaign-yaml.md#when-something-is-wrong)).
