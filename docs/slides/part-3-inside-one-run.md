---
marp: true
theme: riksarkivet
paginate: false
lang: en
---

<!-- _class: lead cover -->
<!-- _footer: "Enheten för AI-labb och datatjänster" -->

# Inside one run

## The container, the pod and what it may do, what the wrapper does with a page, and what "failed" means

<!--
Part 1 drew the pod's lifecycle in one picture. This part opens it: the
loop, the fetch, the two files, the bucket contract, resume, verify, the
exit codes, and the one bug that taught us most. This is the deck for the
people who develop htrflow, because every design choice here is a reaction
to something htrflow does.
-->

---

# Why there is a wrapper at all

<div class="cols">
<div>

**What the htrflow CLI does with a folder.** It submits every page to a thread pool and never collects the futures. A page whose thread throws simply vanishes; the process still exits 0.

**So exit 0 proves nothing.** On one folder you would notice a missing file. On ten thousand volumes, nobody would.

</div>
<div>

**What the wrapper does instead.** It imports htrflow as a library, builds the pipeline once, and runs each page itself, in order, keeping every page's outcome. After the loop it lists the bucket and checks the outcome again.

**One rule to remember:** *done* means *verified* means `manifest.json` exists. An exit code is never trusted on its own.

</div>
</div>

<p class="note">The library API is not a stability contract the way the CLI is, so the wrapper is tested against the exact htrflow in the image, and the image is pinned by digest. That pin is load-bearing.</p>

<!--
This slide is the whole justification for the layer. If htrflow's CLI
collected its futures and failed on a lost page, half of this deck would be
unnecessary -- and that is a legitimate thing to fix upstream one day.
-->

---

# The container — htrflow plus a thin layer

<div class="cols">
<div>

<table class="plain">
<tr><td><strong>top</strong></td><td>the wrapper package — installed from a lock file, with hashes</td></tr>
<tr><td></td><td>httpx and boto3 — fetching pages, writing to S3</td></tr>
<tr><td><strong>base</strong></td><td>the upstream htrflow image, pinned by digest — Python, PyTorch, htrflow</td></tr>
</table>

<p class="filename">what the pipeline file points at</p>

```
docker.io/riksarkivet/htrflow-batch@sha256:cb30d0…
```

</div>
<div>

**One image, built on htrflow's own.** Nothing about htrflow is rebuilt or patched; the layer adds the wrapper and the two libraries it needs to fetch pages and write to S3.

**No models inside.** Weights live in the cache the warm-up fills, so one image serves every pipeline and the image stays the size of the code.

**It says where it came from.** The htrflow base revision is a label and an environment variable, so every ALTO can name it. Each release is signed, with a build provenance record and a software bill of materials.

**One rule to remember:** a pipeline names the image by digest, so the code that runs is exactly the code that was reviewed.

</div>
</div>

<!--
The transformers line is a build argument of this image: a model saved by
one major line of transformers needs that line, so a campaigns repo can
carry pipelines pinning images of both lines. Part 5 returns to that.
-->

---

# One pod, drawn

```
campaign pod — one volume, one GPU, then gone
│
├─ init container   warmup-wait   waits for this pipeline's model marker
├─ container        wrapper       fetch · htrflow · upload · verify
│
├─ /campaign     read-only    volumes.txt — which volume this index is
├─ /config       read-only    the pipeline's steps
├─ /data         read-only    the model cache the warm-up filled
├─ /secrets/s3   read-only    the bucket credentials, as a file
└─ /work         in memory    pages, outputs, HOME, TMPDIR — the only writable place
```

<div class="cols">
<div>

**Everything a run needs is mounted, nothing is baked in:** which volume, which recipe, which models, where to write. So the same image runs any campaign.

</div>
<div>

**Nothing it writes outlives it** except what it uploaded to the bucket: `/work` is memory, and it is released with the pod.

</div>
</div>

<!--
Pages are width-capped and processed one at a time, so the tmpfs holds a
window of pages and never the volume; it is counted against the pod's
memory limit, which is what bounds it.
-->

---

# What the pod may do

<table class="plain">
<tr><td><strong>Not root</strong></td><td>runs as an ordinary user, set both in the image and in the pod, so neither side can regress alone</td></tr>
<tr><td><strong>No privileges</strong></td><td>every Linux capability dropped, privilege escalation refused, the runtime's default system-call filter on</td></tr>
<tr><td><strong>A read-only filesystem</strong></td><td>the image cannot be written to; the only writable place is the in-memory <code>/work</code></td></tr>
<tr><td><strong>No cluster identity</strong></td><td>no service-account token is mounted, so the pod cannot talk to the Kubernetes API at all</td></tr>
<tr><td><strong>Secrets as files</strong></td><td>the S3 credentials are a read-only file, never an environment variable a crash dump or a child process would carry</td></tr>
<tr><td><strong>Cannot poison the models</strong></td><td>the model cache is mounted read-only; only the warm-up pod writes it, so a bad page cannot change the weights every later run loads</td></tr>
</table>

<div class="cols">
<div>

**Pod Security *restricted*.** Every pod the platform runs meets Kubernetes' strictest built-in profile, and the namespace warns on any regression.

</div>
<div>

**Not a sandbox.** The GPU reaches the pod through the container runtime's GPU hooks; a kernel-isolating runtime with GPU support is the next step if one is ever needed.

</div>
</div>

<!--
Why so strict for a transcription job: whoever can write the campaigns repo
chooses the image and the models, Hub weights are pickled Python that runs
code when loaded, and every page comes from a URL someone typed. The pod is
built on the assumption that any of those may be hostile.
-->

---

# What the pod may reach

<p class="filename">the namespace denies all traffic by default; each kind of pod gets its own short list</p>

<table class="plain">
<tr><td></td><td><strong>may reach</strong></td><td><strong>may not reach</strong></td></tr>
<tr><td><strong>campaign pod</strong></td><td>the IIIF servers the platform names · the results bucket</td><td>Hugging Face Hub · the internet · the Kubernetes API · other pods</td></tr>
<tr><td><strong>warm-up pod</strong></td><td>the public internet on HTTPS, for the Hub</td><td>the results bucket · the Kubernetes API · anything in the cluster · private and link-local addresses</td></tr>
<tr><td><strong>web front</strong></td><td>the Kubernetes API, to read campaigns · the bucket, to read progress</td><td>the IIIF servers · the Hub · the internet</td></tr>
</table>

<div class="cols">
<div>

**A campaign pod reaches two things:** the IIIF servers the platform names, and the bucket. No Hub, no API server, no other pod, no wider internet — so a hostile model has nowhere to send what it reads.

</div>
<div>

**The warm-up pod is the opposite:** it may reach the internet, because the Hub has no fixed address, but not the bucket, not the cluster, and not the private or link-local ranges. It holds no campaign data and no S3 credentials.

</div>
</div>

**One rule to remember:** the pod that can reach the internet holds nothing worth stealing; the pod that holds the credentials cannot reach the internet.

<!--
Images hosted somewhere other than the IIIF origin need their range added to
the platform's allow-list, or the page fetch is refused like any other
address. A new pod's network rules can take a moment to apply on some
network plugins; the wrapper's first network act is the allowed manifest
fetch, which keeps that window small.
-->

---

# Three roles, one loop

```mermaid h:200
flowchart LR
  I["IIIF server"]
  D["downloader pool<br/>12 in flight, at most<br/>64 pages ahead"]
  T[("tmpfs<br/>/work — 2 GiB, in memory")]
  C["consumer<br/>one thread: the GPU<br/>serialises the work anyway"]
  U["uploader<br/>PAGE, then ALTO,<br/>then delete the page's files"]
  B[("bucket")]
  I --> D --> T --> C --> U --> B
```

<div class="cols">
<div>

**The downloader runs ahead** of the GPU, but never more than the lookahead window, so tmpfs holds a window of pages and never the volume.

**The consumer is one thread**, because the GPU would serialise the work anyway. It runs `pipeline.run(document)` on each page the moment that page is on disk.

</div>
<div>

**The uploader ships each page's two files** the moment htrflow writes them, then deletes the image and both files from tmpfs.

**The effect:** the GPU idles for about one page's download; results appear in the bucket while the run goes; a 600-page volume never needs 600 pages of disk.

</div>
</div>

<!--
The two knobs that shape the loop are DOWNLOAD_CONCURRENCY (12) and
LOOKAHEAD_PAGES (64). One slow or retrying fetch at the head of the window
stalls the consumer -- the window is ordered, because the GPU processes in
order. That is a known limit, not a bug.
-->

---

# Fetching one page — campaign data is untrusted

<div class="cols">
<div>

**The URL is width-capped:** `<service>/full/<width>,/0/default.jpg`. The width form, not best-fit, because every compliant server supports it; `max` when the canvas is already narrower, because level-1 servers refuse upscaling; a 400 anyway retries once with `max` before the page fails.

**A canvas with no image service** is fetched at native size, bounded only by the byte cap.

</div>
<div>

**The body is checked before it is kept.** A textual content type is refused. The first chunk must start with a known raster signature. An empty or oversized body is refused — 64 MiB per image, 16 MiB per manifest.

**Why:** a login page served with a 200 would otherwise be saved as the image and waste a whole attempt inside htrflow. Every URL came from a file someone edited in git.

</div>
</div>

**One rule to remember:** the width cap is part of the fetched URL, so the stored results always match the configuration that made them.

<!--
The image lands in /work/input on the memory-backed emptyDir. Under a
read-only root filesystem, that same tmpfs is also HOME, TMPDIR and where
YOLO writes its config -- there is nowhere else to write.
-->

---

# What htrflow does to it, and the two files

<div class="cols">
<div>

**Your steps, then two exports.** The wrapper appends `Export` steps for PAGE XML and ALTO to the `steps:` you wrote, and refuses a pipeline that already has one. Region, line, text: the recipe is yours, unchanged.

**Both files are parsed as XML before anything is uploaded.** A malformed file is a page failure, not a corrupt object in the bucket.

</div>
<div>

**A second provenance block in every ALTO.** htrflow writes its own `<Processing>` block: its version, the steps, each model's commit hash. It cannot know about the layer above it, so the wrapper appends `<Processing ID="htrflow-batch">` with the image digest, the htrflow base revision and the wrapper's own version.

**PAGE XML is not stamped.** The same facts are in the volume's `manifest.json`.

</div>
</div>

**One rule to remember:** a year from now, an ALTO answers "which model, which image, which wrapper" by itself.

<!--
ALTO allows any number of Processing blocks in that position, so the file
stays schema-valid and htrflow's own block stays as written. If the stamp
cannot parse an ALTO, the page fails -- the same as a page missing a format.
-->

---

# Upload, then delete — the bucket's contract

```
<namespace>/<pipeline>/<volume>/
  page/0001.xml        uploaded FIRST
  alto/0001.xml        uploaded second — its presence means "page 0001 is complete"
  progress.json        rewritten after every page: done, failed, last error, stage
  iiif.json            the viewer manifest, republished every ten pages
  pipeline.yaml        the steps this run used
  manifest.json        written LAST — its presence means "the volume is complete"
status/logs/<pipeline>/<volume>.txt     the run's own log, shipped every 15 s
```

<div class="cols">
<div>

**PAGE before ALTO, always.** A crash between the two leaves a PAGE without its ALTO, which resume redoes — never the reverse. So an ALTO is proof the page is whole.

**Keys are deterministic and overwritten blindly.** A retry writes the same keys again; nothing needs cleaning up first.

</div>
<div>

**The pipeline id is in the key.** A better recipe writes beside the old results, never over them.

**Timeouts are short.** Ten seconds to connect, sixty to read, three retries — so a dead bucket cannot pin a GPU for hours.

</div>
</div>

<!--
The bucket is the only durable state in the whole system. How durable the
results are is the bucket's own replication and backup story.
-->

---

# Resume — why a restart costs one page

<div class="cols">
<div>

**Before the first fetch, the pod lists the bucket.** `page/` and `alto/`, and the previous run's `manifest.json` if there is one.

**A page is skipped when** both its files exist *and* its source image URL has not changed since the earlier run recorded it. Everything else is fetched and run.

**So a retry of a long volume costs minutes, not hours.** Kubernetes restarts the pod, the pod lists, it carries on at the first page without an ALTO.

</div>
<div>

**Resumed on a newer image?** Pages already done stay as they are: their ALTOs name the image that made them, `manifest.json` names the image that finished the volume. Each file is right about itself.

**Same volumes under a new campaign name?** Same rule: resume finds the ALTOs and transcribes nothing — it still takes a queue slot and a model load, and rewrites `manifest.json`.

</div>
</div>

**One rule to remember:** resume compares the bucket to the manifest, never the other way round. The bucket is the truth.

<!--
The source-URL check is what makes resume safe when a campaign's images:
list is corrected: a page whose URL changed is redone even though an ALTO
exists for its number.
-->

---

# Verify, then publish

```mermaid w:980
flowchart LR
  L["list page/ and alto/<br/>in the bucket"]
  Q["every page uploaded,<br/>skipped, or recorded as failed?"]
  P["publish: iiif.json, pipeline.yaml,<br/>then manifest.json — last"]
  F["otherwise: fail the volume<br/>with the page list in the message"]
  L --> Q -->|"yes"| P
  Q -->|"no"| F
```

<div class="cols">
<div>

**`manifest.json` is the marker and the record:** each page's source URL and outcome, `pages_ok` and `pages_failed`, the pipeline and its hash, the image digest, timings, and the viewer URL.

</div>
<div>

**A failed page is recorded, not hidden.** It is named in `manifest.json`, counted, and shown on the card — and the volume still completes, because failing it would leave the good pages in the bucket with no marker to open them.

</div>
</div>

<!--
Two cases fail the volume, and both are things a retry can converge on or
that need a person: a page that never landed, and a run where nothing at all
came out. The termination message lists the pages in each case.
-->

---

# Failed is not missing

<table class="plain">
<tr><td><strong>failed page</strong></td><td>Fetched or transcribed and it went wrong: a 404, a refused body, a dead model thread. <strong>Recorded</strong> in <code>manifest.json</code>, counted in <code>pages_failed</code>, named on the card. <strong>The volume completes.</strong> The same page would fail the same way on every retry, so retrying the volume for it would only cost GPU time.</td></tr>
<tr><td><strong>missing page</strong></td><td>Neither uploaded nor recorded — an upload that never landed. An inconsistency a retry converges on. <strong>The volume fails</strong> with a transient error naming the pages; Kubernetes retries the index, resume redoes only those pages.</td></tr>
<tr><td><strong>nothing came out</strong></td><td>Every page the run processed failed and nothing was resumed. That is a broken model or a dead GPU, not the pages. <strong>The volume fails</strong> and says so — "check the model and the GPU".</td></tr>
</table>

**One rule to remember:** *failed* is a fact about a page; *missing* is a bug to retry.

<!--
This distinction is the one people get wrong first. "Partially succeeded"
on the campaign card is the failed-page case: every volume finished, some
pages are recorded as failed. A missing page never reaches the card as
"missing" -- it becomes a retry, and then either an ALTO or a failed page.
-->

---

# Exit codes, and what each one costs

```mermaid h:200
flowchart TB
  R["the pod runs"]
  Z["exit 0<br/>done — failed<br/>pages recorded"]
  T["exit 1<br/>transient: retried up to 3×,<br/>resuming from the bucket"]
  K["exit 143<br/>SIGTERM: a drain or the<br/>pod deadline — retried like 1"]
  P["exit 13<br/>permanent: the index fails<br/>at once, never retried"]
  R --> Z & T & K & P
```

<div class="cols">
<div>

**13 is for what a retry cannot fix:** a bad manifest URL, a 404 on the manifest, an unknown step or model class, a missing setting. The wrapper writes `{stage, permanent: true, error}` as its last words and the Job marks the index failed.

**1 is for what might pass next time:** a 5xx, a network error, a model not yet in the cache, a missing page.

</div>
<div>

**A drain is nobody's fault.** A pod evicted by a node drain or preemption carries a `DisruptionTarget` condition, and the Job's failure policy ignores it: not counted, just restarted.

**The other indexes carry on regardless.** One bad volume never stops the rest; the Job ends *Complete*, or *Failed* with the exact indexes named.

</div>
</div>

<!--
Every exit path ends with the same three things: a structured termination
message (stage, permanent, error) that the campaign card turns into one
sentence, the last log ship, and a last progress.json.
-->

---

# The bug that taught us most — page 44

<div class="cols">
<div>

**What happened.** On a 638-page volume, page 44's segmentation produced a mask too small to become a polygon. htrflow put `None` in its polygon list, the `Inference` step's worker thread died on it — and `pipeline.run` waited on a future nobody would ever complete. A GPU reserved, a pod standing still until its deadline, then a retry onto the same page.

**Why htrflow did not notice.** Its steps hand each batch to a daemon thread. A thread that dies takes its exception with it.

</div>
<div>

**What the wrapper does now.** It runs each page's `pipeline.run` in a helper thread and checks every step's worker threads once a second. A step whose thread is gone raises `PipelineDead` — a *page* failure like any other.

**Then it rebuilds.** The dead pipeline's models are dropped, the CUDA cache emptied, a fresh pipeline built from the cache PVC. Page 45 runs. The volume completes: 637 ok, 1 failed, named.

</div>
</div>

**One rule to remember:** a page can kill a thread; it may not kill a volume.

<!--
This is the wrapper's clearest argument. It is not exit 1: the cause is in
the image, so the page would die the same way on every attempt, and the
volume would end as a failed index with no marker at all. The upstream bug
is filed against htrflow; the guard stays regardless, because a thread pool
that swallows exceptions is a class of bug, not one bug.
-->

---

# What a person is told

<div class="cols">
<div>

**On the campaign card,** one sentence built from the termination message: *"The IIIF manifest could not be read: manifest is not JSON. Fix the manifest URL in the campaign file — this volume will not be retried."* Stage, permanence and cause, in words.

**In the run log,** the same facts with their prefix:

```
ERROR permanent failure in setup: manifest is not JSON
      — a retry changes nothing — fix the campaign or pipeline file
ERROR transient failure in verify: verify failed: 1 missing, 0 failed
      missing=['0002'] — some pages produced no result; the retry redoes only those
WARNING page 0044 failed: PipelineDead("… worker thread died; the page
      is marked failed and the pipeline is rebuilt")
```

</div>
<div>

**What survives the Job.** The Job is reaped a week after it ends. Three things do not expire: `manifest.json` with every page's outcome, `progress.json` with the last count and sentence, and the run log under `status/logs/`. The card is rebuilt from those, and the viewer link keeps working.

**One rule to remember:** every failure the platform can name ends as a sentence a person can act on — never a stack trace on a card.

</div>
</div>

<!--
The run log is shipped every fifteen seconds while the run goes, and once
more as the last thing before exit, so a pod that died still leaves its
last minutes readable.
-->

---

<!-- _class: lead -->

# Any questions?
