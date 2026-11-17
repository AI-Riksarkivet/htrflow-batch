# A campaigns API: people reach the cluster through a service, not a Kubernetes login

Status: design, 2026-11-17. Proposed, not approved, not planned, nothing built.

## Why

Every write to the cluster is made today by `htrflow-campaigns` running as
whoever holds a kubeconfig: `apply`, `retry`, `pipeline add` and the pause
that `apply` syncs. In practice that is one kubeconfig on a campaigns host,
driven over SSH by scripts that move files in a folder and run
`apply --prune`. The other path is the Argo CD hook.

The owner's ruling (2026-11-17) is the direction and nothing more: **people
do not talk to the cluster directly, they go through an API that runs in
it.** The owner did not say why. What follows is this note's reading of what
the direct path costs, taken from the code and from how production has been
run, and it is the reading the design is built on:

1. **A Kubernetes login per person, or one shared by all.** Neither gives a
   person an identity. `applied-by` is the name of whoever was logged in on
   the host.
2. **The checks are advice.** `validate` and `render` run on the person's
   machine. A kubeconfig that may create Jobs may create one the converter
   never rendered.
3. **The logic is as old as each install.** An old CLI's prune deletes
   retries and added pipelines. The Lease marks (`prunes-retries`,
   `keeps-added-pipelines`) and `--no-old-apply` exist only to guard against
   that.
4. **The folder is the truth.** A stale file in the campaigns folder starts
   a campaign on the next apply, and a file missing from it cancels one
   under `--prune`.

## What is decided

| Question | Ruling | Whose |
|---|---|---|
| Who talks to the Kubernetes API? | A service in the cluster. People do not. | the owner |
| Is it part of `htrflow-web`? | No, a second service. `htrflow-web` stays read-only. | the owner |
| What does it offer? | Pipelines and campaigns as things that are added, started, paused, resumed, retried and removed one at a time. No folder upload, no prune. | the owner (approach B below) |
| Can it remove? | Yes, campaigns and pipelines. | the owner |
| Can it list? | Yes: the read commands are part of it. | the owner |
| Sign-in | The organisation's Keycloak. | the owner |
| Does `export` go through it? | No. `export` never touches the Kubernetes API and stays as it is. | this note, shown to the owner |
| Does `remove` delete results? | No. Nothing here deletes from the results store. | this note, shown to the owner |
| One service or one per namespace? | One per namespace, installed by that namespace's chart release. | this note, following the htrflow-train team API |
| Does it keep state? | No. Every answer is read from Jobs, ConfigMaps, Kueue and the results store at request time. | this note |
| Whose is `converter.yaml`? | The chart's, whole. The service reads the one the chart renders; nobody uploads one. | this note |
| Direct mode | Kept, unchanged: `apply`, `retry` and `pipeline add` with a kubeconfig, and the Argo CD hook. The new commands exist only through the service. | this note |
| Changing a running campaign | Not through the service in this cut. More volumes are a new campaign. | this note, **open for the owner** |

## Goals

- A person signs in once and can add and remove pipelines, start, pause,
  resume, retry and remove campaigns, and list and inspect both, with no
  Kubernetes role and no store key.
- A campaign reaches the cluster only as objects the converter rendered, in
  the service, from a file it accepted.
- One version does the writing. An old CLI still produces current objects,
  because it renders nothing.
- `applied-by` names the signed-in person, taken from the token.
- Running and queued campaigns do not notice when the service is down.
- An operator with a kubeconfig can still do everything without it.

## Non-goals

- `export`. It reads results through the results proxy and writes to the
  publishing share from the person's machine.
- Deleting or moving anything in the results store.
- Buttons on the web page. The page keeps its store login, and
  `htrflow-web` changes in one place only: how it tells a paused campaign
  from a queued one (see Reads).
- The shared pool's upload and job API of the multi-tenant design (T03,
  T04). This service is one namespace's door, for its own team.
- Changing a running campaign (appending volumes, changing its window).
- A `logs` command. The run log stays where it is read today, on the web
  page.
- Building or pushing images.

## Anti-goals

- **A switch that turns the token check off.** The dev cluster gets a real
  identity provider instead (see Testing).
- **A database, a queue or any file the service must not lose.**
- **A CRD or a controller.** Ruled out for this repository before, and this
  design does not reopen it: the service acts when called and at no other
  time.
- **A second copy of the logic.** Render, retry and the rows of the read API
  each exist once. The service calls the code that is there.
- **Rights outside its namespace**, beyond the two named Kueue reads the
  chart already grants.
- **A person's token forwarded** to Kubernetes, the store or anywhere else.

## Approaches

**A. Today's commands behind HTTP.** `apply` uploads the campaigns folder
and the service validates, renders, applies and prunes; `retry` and
`pipeline add` become calls. People change nothing in how they work, and it
mirrors the htrflow-train team API. But the folder stays the truth, so
prune stays: a stale file still starts a campaign, and two people with
different folders prune each other.

**B. Pipelines and campaigns, one at a time (chosen by the owner).** No
folder and no prune through the service; the cluster is the truth and a
campaign ends by being removed by name. It matches how production is run
(one script per campaign to start, pause and remove) and the earlier ruling
that `pipeline add` and `retry` need no repo. It costs new verbs and new
documentation, and a campaigns repo does not map onto it.

Dropped before these:

- **A proxy that forwards Kubernetes calls with the person's identity.**
  The checks stay on the client and an old CLI stays dangerous.
- **Git as the only door.** Refused by the owner on 2026-11-10 for new
  pipelines ("no repo").
- **Writes inside `htrflow-web`.** It is read-only by design, and a test
  holds it to that; a pod browsers reach should not be able to delete Jobs.

## The shape

```text
 a person's machine              one namespace                         outside it

 htrflow-campaigns  --HTTPS and--> campaigns API (one pod)
                      a token       checks the token          ----->  Kubernetes API
                                    validates and renders             (this namespace)
                                    applies, removes, reads   ----->  results store (read only)

 htrflow-campaigns login  <-- device sign-in -->  Keycloak  <-- signing keys -- campaigns API

 a browser          --------------> htrflow-web, results proxy   (unchanged)
 htrflow-campaigns export --------> results proxy                (unchanged)
```

## What a person types

```text
htrflow-campaigns login --server https://<host>/<path>
htrflow-campaigns whoami

htrflow-campaigns pipeline add    pipelines/high-prio-v5.yaml
htrflow-campaigns pipeline list
htrflow-campaigns pipeline show   high-prio-v5
htrflow-campaigns pipeline remove high-prio-v5

htrflow-campaigns campaign start  campaigns/low-prio.yaml
htrflow-campaigns campaign list
htrflow-campaigns campaign show   low-prio [--failed]
htrflow-campaigns campaign pause  low-prio
htrflow-campaigns campaign resume low-prio
htrflow-campaigns campaign retry  low-prio [--pipeline ID] [--include-permanent]
htrflow-campaigns campaign remove low-prio
```

```text
$ htrflow-campaigns campaign list
CAMPAIGN      PART  PIPELINE       STATE     DONE  FAILED  TOTAL  STARTED BY  STARTED
low-prio         -  high-prio-v5   running    412       3    958  <user>      2026-11-10 09:14
medium-prio      1  high-prio-v5   paused    1630     370   2000  <user>      2026-10-22 15:02
medium-prio      2  high-prio-v5   queued       0       0    653  <user>      2026-10-22 15:02
printed-1        -  printed-v2     finished    30       0     30  <user>      2026-10-21 11:40
```

A row is a Job, as on the web page: a campaign split into parts has one row
per part. The commands take the campaign's name and act on every part.

- Every command that changes something takes `--dry-run`, which prints what
  would be done and changes nothing.
- `remove` (both kinds) prints what it would delete and asks; `--yes`
  answers for a script.
- `--json` on the read commands prints what the service sent.
- **Two commands exist in both modes: `retry` and `pipeline add`.** The
  rule is `--namespace`. Given, the command runs directly with a
  kubeconfig, exactly as today (both require the flag today, so every
  existing script keeps its meaning). Left out, it goes through the
  service, and with no server known it is refused with both ways named.
  `--like` and `--user` belong to the direct form only. `campaign retry`
  is a second spelling of the service form.
- Every other new command exists only through the service.
- `validate` and `render` stay offline commands on a folder, as today.

## The interface

HTTPS and JSON under `/v1`. Every call needs a token but two: the health
check, and the sign-in details `login` has to read before it has one.

| Call | What it does | Command |
|---|---|---|
| `GET /v1/login` | no token: the issuer, the client id, the audience, the server's version and the oldest client it accepts. Nothing in it is secret | `login` |
| `GET /v1/whoami` | user, groups, namespace, queue, server version | `whoami` |
| `GET /v1/pipelines` | every pipeline in the namespace: id, image, size, warm-up state, whether campaigns use it | `pipeline list` |
| `GET /v1/pipelines/{id}` | the same row, plus the pipeline file as stored | `pipeline show` |
| `PUT /v1/pipelines/{id}` | body: a pipeline file. Validates, renders the ConfigMap and warm-up Job, applies them | `pipeline add` |
| `DELETE /v1/pipelines/{id}` | removes the ConfigMap and warm-up Job | `pipeline remove` |
| `GET /v1/campaigns` | the rows `htrflow-web` serves at `/api/v1/jobs` | `campaign list` |
| `GET /v1/campaigns/{name}` | the detail `htrflow-web` serves, for each part of the campaign: volumes, states, reasons | `campaign show` |
| `PUT /v1/campaigns/{name}` | body: a campaign file. Validates, renders, applies | `campaign start` |
| `POST /v1/campaigns/{name}/pause` | stops admitting its pods | `campaign pause` |
| `POST /v1/campaigns/{name}/resume` | admits them again | `campaign resume` |
| `POST /v1/campaigns/{name}/retry` | starts a new campaign of the volumes not done | `campaign retry` |
| `DELETE /v1/campaigns/{name}` | removes the campaign's Jobs and records | `campaign remove` |
| `GET /healthz` | liveness, no token | |

- **`dry_run=true`** on every write returns the preview and changes nothing.
- **A body is text, never a path.** The id of a pipeline and the name of a
  campaign come from the URL, as they come from the file's name today. The
  size of a body is capped by a chart value.
- **Nothing in a request names a namespace, a queue, a Secret, a bucket or
  an endpoint.** Those are the service's configuration.
- **Errors** are one sentence saying what was refused and one saying what to
  do, in a JSON body beside the HTTP status, and the CLI prints them as it
  prints its own. The CLI keeps its exit codes.
- **Versions.** `whoami` returns the server's version. The server refuses a
  client older than a minimum it names, with both versions in the sentence.

## What each write does

Each write takes the Lease `apply` takes today
(`htrflow-campaigns-apply`), so two writes, or a write and a direct apply,
never interleave. Taking it replaces the Lease's marks, so the service
writes back the marks it found, as `retry` does today: it neither wipes
them nor vouches for an install it has not seen. Each write first sends its objects as a server-side dry
run, so an admission refusal (Kyverno, quota, an immutable field) is
reported before anything exists.

**`pipeline add`.** The file is validated exactly as a file in a repo is.
The two objects come from `render.pipeline_objects` and the service's
`converter.yaml`. That is why `--like` is not needed: the settings `--like`
copies from another warm-up are in the service's own configuration.

- The pipeline file as sent is stored on the pipeline's ConfigMap. A
  campaign started later needs all of it (its deadline and time-to-live
  overrides are in no object today), and the service keeps nothing itself.
  Where on the ConfigMap is the builder's; the key rules in the chart's
  policies must allow it.
- The same recipe sent again changes nothing and says so.
- Another recipe under an id that is taken is refused, as today: an id
  names one recipe for good.
- A pipeline already in the cluster without its stored file (from a folder
  apply or the older `pipeline add`) is taken over by sending its file
  once: same recipe, the file is stored, nothing else moves.
- The objects carry the `added` label, so a direct `apply --prune` keeps
  them, as it keeps an added pipeline today.

**`pipeline remove`.** Refused while any campaign on the pipeline is in the
cluster, running or finished, naming them. Otherwise the ConfigMap and the
warm-up Job go. The model cache on the volume is not touched.

**`campaign start`.** The campaign file is validated as today (volume ids,
priority among the configured classes, window within the cluster's). Its
pipeline is read back from the cluster; a pipeline that is not there, or
has no stored file, is refused with the `pipeline add` to run. The objects
come from `render.campaign_objects`.

- A name that is in the cluster, live or as a finished record, is refused:
  remove it or choose another name. This replaces "finished campaigns are
  not run again" and append-only volumes with one rule.
- The check for two campaigns on one volume stays.
- `suspend: true` in the file starts it paused, as today.
- `applied-by` is the token's user name and `applied-at` the service's
  clock. Neither is read from the request.
- The objects carry a label a direct `apply --prune` keeps. Whether that is
  the `added` label or a new one is the builder's.

**`campaign pause` and `campaign resume`.** The patch `apply`'s pause sync
makes today (`Workload.spec.active`), on every part of the campaign. The
state lives on the Workloads and nowhere else. Pausing a paused campaign,
or a finished one, says so and changes nothing.

**`campaign retry`.** What `retry` does today, with the same refusals, the
same names (`<name>-retry-<n>`), `--pipeline` and `--include-permanent`.
One difference: the service finds the volumes that are not done by reading
the results store itself, with the namespace's store Secret, so the person
gives no results login. The preview (how many, grouped by reason) is the
`dry_run` answer.

**`campaign remove`.** Deletes every part's Job (and with it its pods),
the campaign's ConfigMaps and its status record, whether it is running or
finished. Results stay. Retries of it are campaigns of their own and stay;
the answer names them.

## Reads

The rows come from the code `htrflow-web` already runs
(`htrflow_web.projection` over the same Kubernetes reads), so the page and
the command line cannot disagree. Four things are not simply reuse:

- **The unit is the Job.** The page's rows and detail are per Job, and
  there is no per-campaign total to reuse. The service adds none: `list`
  prints the rows as they are, and `show` prints each part's detail in
  turn (`--failed` runs through all parts).
- **The service writes the status record, as `apply` does.** A campaign
  whose Job has been removed by its time-to-live is listed only from its
  status record. Today two things write that record: the page, while
  someone has it open, and every `apply`. Through the service nobody
  applies, so the service writes it on `campaign list`, on
  `campaign show` and before every write to a campaign, with the
  converter's own function. A campaign that finishes and is removed by its
  time-to-live with no page view and no call in between leaves no record.
  That gap exists today and is not closed here.
- **Paused is read from the Workload, in one place.** The page calls a
  suspended Job with nothing done "Queued" and one with something done
  "Paused", which is a guess. After `campaign pause` a person has to see
  "paused". The projection gains the one fact that decides it
  (`Workload.spec.active`), and both the page and the service use it. For
  `htrflow-web` that is one more read, and `list` on Workloads in its
  Role.
- **Page counts come from the store with the namespace's Secret.** The
  page reads each volume's progress through the results proxy with the
  viewer's own login. The service has no such login, so its store reader
  feeds the detail rows as well as `retry`. A group member therefore sees
  counts and failure reasons, never result files, whatever their store
  account may read.

`pipeline list` has no projection today; it is new, and small: the
pipeline ConfigMaps, their warm-up Jobs, and which campaigns name them.

## Identity

The same rules as the htrflow-train team API, so the two can share a
sign-in later.

- **The token.** A signed access token from the organisation's OpenID
  Connect provider (Keycloak), carrying an audience and the person's group
  names. The service checks the signature against the provider's published
  keys, the issuer, the audience and the expiry, with a fixed list of
  asymmetric algorithms. Keys are cached and fetched again, at a limited
  rate, when a token names one the cache has not seen.
- **Who may use it.** The chart sets one group name. A token whose groups
  include it may make every call. A valid token without it is refused with
  the group's name in the sentence. There is one role in this cut: reading
  and removing need the same group.
- **The claims.** The user name is `preferred_username` and the groups are
  the `groups` claim, a list of names. Both claim names are chart values
  with those defaults.
- **`login`.** The CLI reads `GET /v1/login` from the server it is given,
  then runs the device sign-in: it prints a link and a code and waits, so
  it works over SSH. The token and its refresh token are stored
  in the user's configuration directory, readable by the user only. The
  server address is stored beside them, or read from `HTRFLOW_API_URL`.
- **If Keycloak is down**, nobody can sign in, and tokens the service can
  still verify from cached keys work until they expire.

Needed from whoever runs Keycloak: one public client with the device grant
and no secret, a mapper for the audience, a mapper for group names, and the
group. Whether that client is shared with htrflow-train is theirs and the
owner's to say; the service only needs an issuer, an audience and a group,
all chart values.

## What the service may do

- **Kubernetes.** The ServiceAccount and Role the chart has under
  `apply.rbac` (Jobs and ConfigMaps in the namespace, list and patch on
  Workloads, the one Lease, the named queue reads), plus `get` and `list`
  on pods for the rows. Turning the service on turns `apply.rbac` on. The
  chart's `rbac-scope` policy holds this identity to its own objects, as it
  does today.
- **The results store.** It mounts the namespace's store Secret and only
  ever reads with it: the per-volume files `retry` and the rows need
  (`manifest.json`, `progress.json`, the run log's outcome).
- **Network.** In from the ingress controller only. Out to the Kubernetes
  API, the results store, Keycloak's key endpoint and DNS.
- **The pod** meets what the namespace demands of every pod: the pod
  security level, an image by digest from an allowed repository.
- **One replica.** It holds nothing, so a restart loses nothing; the Lease
  is what orders writes.

## Configuration

`converter.yaml` holds only what the cluster decides: the queue, the
window, sizes and flavors, the data volume, the runtime class, placement,
the source template, the store Secret, the Hub token's Secret, the results
address. Through the service none of it is a person's to send.

- The chart renders it into a ConfigMap from values and the service mounts
  it. The values that exist already (`queue.name`, `s3.existingSecret`,
  `modelCache.name`, `resultsUrl`) are used where they are; the rest come
  in under one new block. The test that holds a repo's `converter.yaml` to
  the chart has nothing to hold for the service.
- New chart values: on/off, the image, the address path, the issuer, the
  audience, the group, the body size cap.
- **The address is the web front's Ingress, with TLS, and nothing else.**
  The Service is ClusterIP and has no NodePort, because a token must not
  travel unencrypted. The chart refuses to turn the service on without
  `web.ingress.enabled` and a TLS Secret. The path is a chart value and
  collides with nothing the web front serves (`/api/v1`, `/results`, the
  page's own routes).
- **The CLI refuses a server address that is not HTTPS**, with one
  exception: a loopback address, which is how the dev cluster is reached
  (`kubectl port-forward` to the Service).

## Beside a direct apply

Direct mode is not changed, and the two models can meet in one namespace.

- A direct `apply --prune` from this release on keeps what the service
  made, by label.
- An install older than this release prunes it, exactly as an old install
  prunes retries today. The service cannot refuse on an old client's
  behalf and does not read the Lease marks. Where the service is on, the
  Argo CD hook's image and any operator's install move to this release
  first; the documentation says so.
- A campaign a folder apply started can be listed, paused, resumed, retried
  and removed through the service. If its file is still in a folder, the
  next direct apply starts or pauses it again as the file says. That is the
  operator's to keep straight.
- The scripts that drive a campaigns host over SSH have nothing left to do
  once the service is on.

## Where the code goes

- **The service** is a new workspace package beside `converter`, `web` and
  `wrapper`. It depends on `htrflow-converter` (models, render, retry,
  cluster) and `htrflow-web` (projection and its reader), and adds the
  token check, the routes and a store reader for `retry`. It uses FastAPI
  and uvicorn, as `htrflow-web` does.
- **The image.** The `htrflow-campaigns` image gains the service as a
  second entry point, so no new repository has to be published. It is
  pinned by digest in the chart like the others.
- **The client** is in the converter's CLI: `login`, `whoami`, the
  `campaign` group, and `pipeline list`, `show` and `remove`, over `httpx`,
  which the package already has.
- **Line budgets.** The new package gets one in `scripts/loc-budget.sh`,
  and the converter's is raised for the client. Both numbers are set when
  the plan is written, with the reason in the script as usual.
- **Fixes the service makes necessary.** The converter accepts volume ids
  containing `..` that the wrapper refuses. With the service as the gate,
  `validate` refuses them.

## Testing

- **Unit tests** run the routes against the fake cluster the converter's
  tests use, with tokens signed by a key made in the test and served as the
  provider's keys. Covered at least: a missing, expired, wrongly signed,
  wrong-audience and wrong-group token; a symmetric algorithm; each
  refusal named under "What each write does".
- **On the dev cluster** the devstack chart gains a Keycloak with one
  imported realm (the client, the group, a test user), behind the same
  switch as the devstack's other insecure defaults. The service is reached
  by port-forward. One thing is not known and is step 1's to find out: a
  token's issuer has to read the same from the developer's machine and
  from the service's pod, and the devstack has no ingress controller. A
  fixed host name for Keycloak may do; if not, the devstack gains an
  Ingress with TLS. The whole path is run there: `login`, add a pipeline, start a campaign on the demo example
  images, list, pause, resume, retry, remove.
- **`make ci`** passes before any push.

## Order of work

1. A spike on the dev cluster: Keycloak in the devstack, the token check,
   `login` and `whoami`, and the issuer question under Testing settled.
2. The read calls and commands.
3. `pipeline add` and `pipeline remove`, with the stored pipeline file.
4. `campaign start` and `campaign remove`.
5. `campaign pause` and `campaign resume`.
6. `campaign retry` with the store reader.
7. The chart: Deployment, Service, ingress path, NetworkPolicy, the
   rendered `converter.yaml`, the added reads in both Roles.
8. Documentation: a getting-started page for the commands, the CLI and
   chart references, and the security page's account of who may write.
9. A release, then the first cluster: the Keycloak client and group, the
   hook and operators' installs moved to the release, the SSH scripts
   retired.

## Left to the builder

- Package and Deployment names, and the default address path.
- Where on the pipeline ConfigMap the file is stored, and the policy change
  that allows it.
- The label that marks service-made campaigns for a direct prune.
- The body size cap's default.
- How the read rows are shared with `htrflow-web` without moving its code
  more than needed.
- The exact JSON of each answer, beyond "the rows `htrflow-web` serves".

## Open questions for the owner

1. **Changing a running campaign.** Direct `apply` can append volumes and
   change a window. This cut offers neither through the service. Is that
   acceptable, or is one of them needed from the start?
2. **The Keycloak client.** One shared with htrflow-train, or one of its
   own? And who is asked for it.
3. **One role or two.** Everyone in the group may remove. Is a read-only
   group wanted?
4. **`logs` from the command line.** Left out; the web page has the run
   log. Wanted in this cut?
