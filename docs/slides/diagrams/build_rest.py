"""The remaining hand-drawn diagrams across the decks (build.py holds the
four in part 1's opening). Run from the repository root:

    python3 docs/slides/diagrams/build_rest.py

then embed the font into each generated SVG with scripts/slides_embed_font.py.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, MAGENTA, MUTED

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets") + os.sep
M = {"color": MAGENTA}


def L(name):
    return "lucide-" + name


# ---------------------------------------------------------------- part 1: the Workload
d = Diagram(1280, 230)
p = d.row(40, 150, [
    ("campaign Job", "window 2\neach pod 1 GPU", "k8s-job"),
    ("Workload", "asks for 2 GPUs\nat once", "kueue"),
    ("LocalQueue", "waits in line", L("door-open"), M),
    ("ClusterQueue", "2 GPUs free?", L("layers"), M),
    ("admitted", "the Job's pods start", L("play"), {**M, "strong": True}),
])
d.text((p[0][0] + p[0][1] + p[1][0]) / 2, 30, "Kueue makes", 14)
d.save(OUT + "p1-workload.svg")

# ---------------------------------------------------------------- part 1: Kyverno admission
d = Diagram(1280, 330)
d.vbox(20, 90, 220, 150, "apply", "sends an object", L("send"), MAGENTA)
d.vbox(290, 90, 220, 150, "API server", "Kubernetes", L("server"), MAGENTA)
d.vbox(560, 90, 240, 150, "Kyverno", "checks the policies", "kyverno", strong=True)
d.vbox(900, 10, 300, 140, "accepted", "stored and run", L("circle-check"), MAGENTA)
d.vbox(900, 180, 300, 140, "refused", "one sentence why", L("circle-x"), MAGENTA, bad=True)
d.arrow([(240, 165), (288, 165)])
d.arrow([(510, 165), (558, 165)])
d.arrow([(800, 145), (850, 145), (850, 80), (898, 80)], label="passes", lx=846, ly=70, anchor="end")
d.arrow([(800, 185), (850, 185), (850, 250), (898, 250)], label="breaks a rule", lx=846, ly=275, anchor="end")
d.save(OUT + "p1-admission.svg")

# ---------------------------------------------------------------- part 1: Kueue + Kyverno flow
d = Diagram(1280, 360)
top = 150
items = [("Job created", "paused by Kueue", "k8s-job"),
         ("Kyverno", "checks the Job", "kyverno", {"strong": True}),
         ("Workload", "waits in the queue", "kueue"),
         ("window fits?", "free GPUs", L("layers"), M),
         ("admitted", "Job unpaused", L("play"), M),
         ("Kyverno", "checks each pod", "kyverno", {"strong": True}),
         ("pods run", None, "k8s-pod")]
p = d.row(top, 150, items, gap=26)
kx, kw = p[1]
d.vbox(kx - 10, 10, kw + 20, 100, "refused", "never stored", L("circle-x"), MAGENTA, bad=True, isize=30)
d.arrow([(kx + kw / 2, top), (kx + kw / 2, 112)])
wx, ww = p[2]; qx, qw = p[3]
d.arrow([(qx + qw / 2, top + 150), (qx + qw / 2, top + 185), (wx + ww / 2, top + 185), (wx + ww / 2, top + 152)],
        dashed=True, label="not yet", lx=(wx + qx + qw) / 2 + 20, ly=top + 205)
d.save(OUT + "p1-kueue-flow.svg")

# ---------------------------------------------------------------- part 1: htrflow in a pod
d = Diagram(1280, 520)
d.group(20, 10, 1240, 150, "get ready")
d.row(45, 105, [("wait for the models", "in the cache", L("hard-drive-download"), M),
                ("read the manifest", "or the image list", L("file-text"), M),
                ("resume", "skip pages already done", L("list-checks"), M)], x0=45, total=1190, gap=60, isize=30)
d.group(20, 185, 1240, 150, "page by page")
p = d.row(220, 105, [("fetch the page", None, L("download"), M),
                     ("htrflow runs your pipeline", None, L("scroll-text"), {**M, "strong": True}),
                     ("upload ALTO and PAGE", "to the bucket", L("upload"), M)], x0=45, total=1190, gap=60, isize=30)
fx, fw = p[0]; ux, uw = p[2]
d.arrow([(ux + uw / 2, 325), (ux + uw / 2, 330), (fx + fw / 2, 330), (fx + fw / 2, 327)], dashed=True)
d.text((fx + ux + uw) / 2, 350, "next page", 14)
d.group(20, 360, 1240, 150, "finish")
d.row(395, 105, [("verify every page", "done or recorded", L("search-check"), M),
                 ("publish", "viewer manifest, manifest.json", L("cloud-upload"), M),
                 ("exit", "the GPU is free", L("power"), M)], x0=45, total=1190, gap=60, isize=30)
d.arrow([(640, 160), (640, 183)])
d.arrow([(640, 335), (640, 358)])
d.save(OUT + "p1-pod.svg")

# ---------------------------------------------------------------- part 1: one Job, four indexes
d = Diagram(1000, 330)
d.vbox(300, 10, 400, 120, "Job", "completions = 4 · parallelism = 2", "k8s-job")
for i, ref in enumerate(["R0001203", "R0001204", "R0001205", "R0001206"]):
    x = 10 + i * 250
    d.vbox(x, 190, 230, 130, f"index {i}", ref, "k8s-pod")
    d.arrow([(500, 130), (500, 160), (x + 115, 160), (x + 115, 188)])
d.save(OUT + "p1-job-indexes.svg")

# ---------------------------------------------------------------- part 1: window waves
d = Diagram(1280, 230)
for w in range(3):
    gx = 20 + w * 430
    d.group(gx, 10, 390, 210, f"wave {w + 1}")
    for k in range(2):
        d.vbox(gx + 20 + k * 180, 55, 170, 145, "pod", f"R000{1203 + w * 2 + k}", "k8s-pod")
    if w:
        d.arrow([(gx - 40, 115), (gx - 2, 115)])
d.save(OUT + "p1-window.svg")

# ---------------------------------------------------------------- part 1: not enough GPUs
d = Diagram(1280, 380)
d.group(640, 10, 620, 360, "the cluster — 4 GPUs")
d.vbox(670, 60, 560, 130, "campaign A · window 2", "running on 2 GPUs", L("play"), MAGENTA)
d.vbox(670, 220, 560, 130, "2 GPUs free", None, L("microchip"), MAGENTA)
d.vbox(20, 30, 520, 150, "campaign B · window 4", "needs 4 — waits, reads Queued", L("clock"), MAGENTA)
d.vbox(20, 210, 520, 150, "campaign C · window 2", "needs 2 — starts", L("play"), MAGENTA, strong=True)
d.arrow([(540, 105), (600, 105), (600, 270), (668, 270)], dashed=True, label="not enough", lx=590, ly=95, anchor="end")
d.arrow([(540, 300), (668, 300)])
d.save(OUT + "p1-not-enough.svg")

# ---------------------------------------------------------------- part 1: priority
d = Diagram(1280, 230)
d.row(30, 170, [
    ("waiting", "A bulk 09:00 · B bulk 09:30\nC interactive 10:00", L("list-ordered"), M),
    ("Kueue admits", "C, then A, then B", "kueue", {"strong": True}),
    ("running: D", "bulk — untouched\nnothing is evicted", L("play"), M),
], gap=60)
d.save(OUT + "p1-priority.svg")

# ---------------------------------------------------------------- part 1: git flow
d = Diagram(1280, 200)
d.row(20, 160, [
    ("you edit", "campaigns/demo.yaml", L("pencil"), M),
    ("validate", "locally", L("file-check"), M),
    ("pull request", "CI checks again", L("git-pull-request"), M),
    ("merge", "rendered/ committed", L("git-merge"), M),
    ("apply", "cluster objects", "argo"),
    ("status page", "and the viewer", L("layout-dashboard"), M),
], gap=26)
d.save(OUT + "p1-git-flow.svg")

# ---------------------------------------------------------------- part 1: follow one campaign
d = Diagram(1280, 200)
d.row(20, 160, [
    ("merged", "1 volume · window 1", L("git-merge"), M),
    ("Queued", "quota full", L("clock"), M),
    ("Running", "137 / 638 pages", L("play"), M),
    ("Done", "637 ok, 1 failed", L("circle-check"), M),
    ("viewer", "the whole volume", L("book-open"), M),
], gap=34)
d.save(OUT + "p1-follow.svg")

# ---------------------------------------------------------------- part 2: the pull request
d = Diagram(1280, 200)
d.row(20, 160, [
    ("edit", "kyrkobocker-1.yaml", L("pencil"), M),
    ("validate", "locally", L("file-check"), M),
    ("pull request", "CI: validate + policies", L("git-pull-request"), M),
    ("review", "a colleague reads it", L("users"), M),
    ("merge", "CI commits rendered/", L("git-merge"), M),
], gap=34)
d.save(OUT + "p2-pull-request.svg")

# ---------------------------------------------------------------- part 2: apply
d = Diagram(1280, 200)
d.row(20, 160, [
    ("render again", "append-only,\npipelines unchanged", L("refresh-cw"), M),
    ("write records", "each campaign's,\nbefore anything is sent", L("file-pen"), M),
    ("apply pipelines", "ConfigMap +\nwarm-up Job", L("file-code"), M),
    ("apply campaigns", "skip finished,\nunchanged ones", L("send"), M),
    ("pause states", "on each Kueue\nWorkload", L("pause"), M),
], gap=34)
d.save(OUT + "p2-apply.svg")

# ---------------------------------------------------------------- part 3: three roles
d = Diagram(1280, 210)
d.row(20, 170, [
    ("IIIF server", "the pages", L("images"), {"color": MUTED, "external": True}),
    ("downloader", "12 in flight,\n64 pages ahead", L("download"), M),
    ("tmpfs", "/work, in memory", L("memory-stick"), M),
    ("consumer", "one thread —\nthe GPU serialises", L("microchip"), {**M, "strong": True}),
    ("uploader", "PAGE, then ALTO,\nthen delete", L("upload"), M),
    ("bucket", None, L("database"), M),
], gap=26)
d.save(OUT + "p3-loop.svg")

# ---------------------------------------------------------------- part 3: verify
d = Diagram(1280, 330)
d.vbox(20, 90, 300, 150, "list the bucket", "page/ and alto/", L("list-checks"), MAGENTA)
d.vbox(390, 90, 360, 150, "every page accounted for?", "uploaded, skipped or failed", L("search-check"), MAGENTA, strong=True)
d.vbox(870, 10, 390, 140, "publish", "iiif.json, pipeline.yaml,\nmanifest.json last", L("cloud-upload"), MAGENTA)
d.vbox(870, 180, 390, 140, "fail the volume", "with the page list", L("circle-x"), MAGENTA, bad=True)
d.arrow([(320, 165), (388, 165)])
d.arrow([(750, 145), (810, 145), (810, 80), (868, 80)], label="yes", lx=806, ly=70, anchor="end")
d.arrow([(750, 185), (810, 185), (810, 250), (868, 250)], label="no", lx=806, ly=275, anchor="end")
d.save(OUT + "p3-verify.svg")

# ---------------------------------------------------------------- part 3: exit codes
d = Diagram(1280, 360)
d.vbox(490, 10, 300, 110, "the pod runs", None, "k8s-pod")
codes = [("exit 0", "done — failed\npages recorded", L("circle-check")),
         ("exit 1", "transient — retried up to 3×,\nresuming from the bucket", L("rotate-ccw")),
         ("exit 143", "a drain or the deadline —\nretried like exit 1", L("power")),
         ("exit 13", "permanent — fails at once,\nnever retried", L("octagon-x"))]
w = (1240 - 3 * 26) / 4
for i, (t, sub, ic) in enumerate(codes):
    x = 20 + i * (w + 26)
    d.vbox(x, 190, w, 160, t, sub, ic, MAGENTA, bad=(t == "exit 13"))
    d.arrow([(640, 120), (640, 155), (x + w / 2, 155), (x + w / 2, 188)])
d.save(OUT + "p3-exit.svg")

# ---------------------------------------------------------------- part 5: how a model reaches the GPU
d = Diagram(1280, 360)
d.vbox(20, 110, 220, 140, "new pipeline", "merged and applied", L("git-pull-request"), MAGENTA)
d.vbox(300, 200, 240, 150, "model hub", "Hugging Face", L("cloud-download"), MUTED, external=True)
d.vbox(300, 10, 240, 150, "warm-up Job", "CPU, outside the queue", L("hard-drive-download"), MAGENTA, strong=True)
d.vbox(640, 10, 260, 150, "model cache", "one shared disk", L("database"), MAGENTA)
d.vbox(640, 200, 260, 150, "marker file", "pipeline ready", L("flag"), MAGENTA)
d.vbox(1000, 105, 260, 150, "campaign pods", "offline, read-only", "k8s-pod")
d.arrow([(240, 180), (270, 180), (270, 85), (298, 85)])
d.arrow([(420, 200), (420, 162)], label="weights", lx=432, ly=186, anchor="start")
d.arrow([(540, 85), (638, 85)])
d.arrow([(540, 120), (590, 120), (590, 275), (638, 275)])
d.arrow([(900, 85), (950, 85), (950, 150), (998, 150)], label="weights", lx=945, ly=75, anchor="end")
d.arrow([(900, 275), (950, 275), (950, 210), (998, 210)], label="unblocks", lx=945, ly=300, anchor="end")
d.save(OUT + "p5-model-path.svg")

print("rest written")
