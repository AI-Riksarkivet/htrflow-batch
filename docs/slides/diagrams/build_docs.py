"""The hand-drawn diagrams on the documentation site. Run from the repository
root:

    python3 docs/slides/diagrams/build_docs.py

The site's text column is about 690 px wide, so these canvases are 1000 wide
(shown at about 0.7, like the slides) and never more than three columns across.
The output goes to docs/assets/diagrams, which the site publishes.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, CARD_H, GAP, MAGENTA, tall_height
from seqkit import Steps

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "diagrams") + os.sep
os.makedirs(OUT, exist_ok=True)

W = 1000
COLW = (904 - 2 * 56) / 3
C = [48 + i * (COLW + 56) for i in range(3)]
MID = [x + COLW / 2 for x in C]
ROWGAP = 72


def L(name):
    return "lucide-" + name


def tall_row(d, y, items, arrows=True, reverse=False):
    """Tall cards in columns; items are (col, title, sub, icon, {opts})."""
    h = max(tall_height(it[2]) for it in items)
    boxes = {}
    for col, t, s, ic, *o in items:
        boxes[col] = d.tall(C[col], y + (h - tall_height(s)) / 2, COLW, t, s, ic, **(o[0] if o else {}))
    if arrows:
        cols = sorted(boxes)
        pairs = zip(cols, cols[1:])
        for a, b in pairs:
            if reverse:
                d.arrow([(C[b], y + h / 2), (C[a] + COLW + GAP, y + h / 2)])
            else:
                d.arrow([(C[a] + COLW, y + h / 2), (C[b] - GAP, y + h / 2)])
    return h


def group_around(d, cols, y, h, label, icon=None, outside=False, inner=False):
    x0, x1 = C[min(cols)] - 24, C[max(cols)] + COLW + 24
    d.group(x0, y, x1 - x0, h, label, icon, outside=outside, inner=inner)


# ---------------------------------------------------------------- the whole platform, for the site's front page
d = Diagram(W, 1496)
H1 = tall_height("x")
d.group(24, 16, 952, 56 + H1 + 24, "In git", L("git-branch"))
tall_row(d, 72, [(0, "You", "open a pull request", L("user")),
                 (1, "Campaigns repo", "campaigns · pipelines", L("git-pull-request")),
                 (2, "Converter", "checks, renders in CI", L("file-check"))])
g2 = 16 + 56 + H1 + 24 + 64
group_around(d, [2], g2, 56 + H1 + 24, "Delivery", L("send"))
d.tall(C[2], g2 + 56, COLW, "Apply", "Argo CD or the CLI", "argo", logo=True)
d.arrow([(MID[2], 72 + H1), (MID[2], g2 + 56 - GAP)])
g3 = g2 + 56 + H1 + 24 + 64
r1 = g3 + 56
d.group(24, g3, 952, 56 + H1 + ROWGAP + H1 + 24, "In the cluster", "k8s-node")
tall_row(d, r1, [(0, "Kyverno", "checks every object", "kyverno", {"logo": True}),
                 (1, "Kueue", "waits for GPUs", "kueue", {"logo": True})])
d.tall(C[2], r1, COLW, "Warm-up", "fills the model cache", L("hard-drive-download"))
lane = g3 - 32
d.arrow([(MID[2], g2 + 56 + H1), (MID[2], lane), (MID[0] + 70, lane), (MID[0] + 70, r1 - GAP)])
r2 = r1 + H1 + ROWGAP
d.tall(C[1], r2, COLW, "Pods", "one per volume", "k8s-pod", logo=True, strong=True)
d.tall(C[2], r2, COLW, "Web front", "status · viewer", L("layout-dashboard"))
d.arrow([(MID[1] - 40, r1 + H1), (MID[1] - 40, r2 - GAP)])
d.arrow([(MID[2] - 60, r1 + H1), (MID[2] - 60, r2 - 36), (MID[1] + 60, r2 - 36), (MID[1] + 60, r2 - GAP)], label="models", at=((MID[2] - 60 + MID[1] + 60) / 2, r2 - 36))
g4 = g3 + 56 + H1 + ROWGAP + H1 + 24 + 64
r3 = g4 + 56
group_around(d, [0], g4, 56 + H1 + 24, "Storage", L("database"))
group_around(d, [1, 2], g4, 56 + H1 + 24, "Outside", L("globe"), outside=True)
d.tall(C[0], r3, COLW, "S3 bucket", "ALTO · PAGE", L("database"))
d.tall(C[1], r3, COLW, "IIIF servers", "the page images", L("images"), outside=True)
d.tall(C[2], r3, COLW, "Browser", "anyone reading", L("globe"), outside=True)
lane = g4 - 32
d.arrow([(MID[1] - 60, r2 + H1), (MID[1] - 60, lane), (MID[0] + 50, lane), (MID[0] + 50, r3 - GAP)], label="results", at=((MID[0] + 50 + MID[1] - 60) / 2, lane))
d.arrow([(MID[1] + 40, r3), (MID[1] + 40, r2 + H1 + GAP)], label="pages", at=(MID[1] + 40, lane))
d.arrow([(MID[2], r3), (MID[2], r2 + H1 + GAP)], label="reads", at=(MID[2], lane))
d.save(OUT + "overview.svg")

# ---------------------------------------------------------------- architecture: the map (the overview, with the detail)
H2 = tall_height("a\nb")
d = Diagram(W, 16 + (56 + H2 + 24) * 2 + 64 * 3 + (56 + H2 + ROWGAP + H2 + 24) + (56 + H2 + 24) + 16)
d.group(24, 16, 952, 56 + H2 + 24, "Campaigns repo", L("git-branch"))
tall_row(d, 72, [(0, "Campaign files", "campaigns, pipelines,\nconverter.yaml", L("file-text")),
                 (1, "Converter in CI", "validates pull requests,\nrenders on main", L("file-check")),
                 (2, "rendered/", "what apply sends,\ncommitted to git", L("file-code"))])
g2 = 16 + 56 + H2 + 24 + 64
group_around(d, [2], g2, 56 + H2 + 24, "Delivery", L("send"))
d.tall(C[2], g2 + 56, COLW, "Apply", "Argo CD, or\nhtrflow-campaigns apply", "argo", logo=True)
d.arrow([(MID[2], 72 + H2), (MID[2], g2 + 56 - GAP)])
g3 = g2 + 56 + H2 + 24 + 64
r1, r2 = g3 + 56, g3 + 56 + H2 + ROWGAP
d.group(24, g3, 952, 56 + H2 + ROWGAP + H2 + 24, "Kubernetes cluster", "k8s-node")
tall_row(d, r1, [(0, "Kyverno", "checks every Job, Pod\nand pipeline ConfigMap", "kyverno", {"logo": True}),
                 (1, "Kueue", "LocalQueue, then the\nClusterQueue's quota", "kueue", {"logo": True})])
d.tall(C[2], r1, COLW, "Warm-up Job", "one per pipeline, on\nCPU, not queued", L("hard-drive-download"))
d.arrow([(MID[2], g2 + 56 + H2), (MID[2], g3 - 32), (MID[0] + 110, g3 - 32), (MID[0] + 110, r1 - GAP)])
d.tall(C[1], r2, COLW, "Campaign pods", "an Indexed Job,\none pod per volume", "k8s-pod", logo=True, strong=True)
d.tall(C[2], r2, COLW, "Web front", "status page, read API\nand the viewer", L("layout-dashboard"))
d.arrow([(MID[1] - 40, r1 + H2), (MID[1] - 40, r2 - GAP)], label="admits", at=(MID[1] - 40, r1 + H2 + ROWGAP / 2))
d.arrow([(MID[2] - 60, r1 + H2), (MID[2] - 60, r2 - 36), (MID[1] + 60, r2 - 36), (MID[1] + 60, r2 - GAP)], dashed=True)
d.arrow([(C[2], r2 + H2 / 2), (C[1] + COLW + GAP, r2 + H2 / 2)])
g4 = g3 + 56 + H2 + ROWGAP + H2 + 24 + 64
r3 = g4 + 56
group_around(d, [0], g4, 56 + H2 + 24, "Storage", L("database"))
group_around(d, [1, 2], g4, 56 + H2 + 24, "Outside", L("globe"), outside=True)
d.tall(C[0], r3, COLW, "S3 bucket", "PAGE, ALTO, progress,\nrun log, manifest.json", L("database"))
d.tall(C[1], r3, COLW, "IIIF servers", "width-capped\nimage GETs", L("images"), outside=True)
d.tall(C[2], r3, COLW, "Browser", "the status page\nand the viewer", L("globe"), outside=True)
lane = g4 - 32
d.arrow([(MID[1] - 60, r2 + H2), (MID[1] - 60, lane), (MID[0] + 50, lane), (MID[0] + 50, r3 - GAP)], label="results", at=((MID[0] + 50 + MID[1] - 60) / 2, lane))
d.arrow([(MID[1] + 40, r3), (MID[1] + 40, r2 + H2 + GAP)], label="pages", at=(MID[1] + 40, lane))
d.arrow([(MID[2], r3), (MID[2], r2 + H2 + GAP)], label="reads", at=(MID[2], lane))
d.save(OUT + "architecture.svg")

# ---------------------------------------------------------------- architecture: htrflow in a pod
H1 = tall_height("x")
top_h = H1
ga = 16 + top_h + 64                      # the pod panel
ia = ga + 56                              # the init container panel
ib = ia + 56 + H1 + 24 + 24               # the container panel
ra, rb = ib + 56, ib + 56 + H1 + ROWGAP
d = Diagram(W, rb + H1 + 24 + 24 + 16)
d.tall(C[0], 16, COLW, "IIIF server", "the page images", L("images"), outside=True)
d.tall(C[1], 16, COLW, "Model cache", "read-only, shared", L("database"))
d.tall(C[2], 16, COLW, "S3 bucket", "PAGE and ALTO", L("database"))
d.group(24, ga, 952, rb + H1 + 24 + 24 - ga, "Pod, one volume", "k8s-pod")
group_around(d, [1], ia, 56 + H1 + 24, "Init container", inner=True)
d.tall(C[1], ia + 56, COLW, "Wait for models", "until the cache is ready", L("hard-drive-download"))
d.group(48, ib, 904, rb + H1 + 24 - ib, "Container", inner=True)
tall_row(d, ra, [(0, "Fetch the page", "skips pages done", L("download")),
                 (1, "htrflow", "runs your pipeline", L("scroll-text"), {"strong": True}),
                 (2, "Upload", "PAGE, then ALTO", L("upload"))])
tall_row(d, rb, [(0, "Exit", "the GPU is free", L("power")),
                 (1, "Publish", "manifest.json last", L("cloud-upload")),
                 (2, "Verify", "every page counted", L("search-check"))], reverse=True)
d.arrow([(MID[0] + 100, 16 + H1), (MID[0] + 100, ra - GAP)], label="pages", at=(MID[0] + 100, ga - 32))
d.arrow([(MID[1] + 60, 16 + H1), (MID[1] + 60, ia + 56 - GAP)], label="marker", at=(MID[1] + 60, ga - 32))
d.arrow([(MID[1], ia + 56 + H1), (MID[1], ra - GAP)], dot=False)
d.arrow([(MID[2], ra), (MID[2], 16 + H1 + GAP)], label="uploads", at=(MID[2], ga - 32))
d.arrow([(MID[2] - 50, ra + H1), (MID[2] - 50, ra + H1 + 36), (MID[0], ra + H1 + 36), (MID[0], ra + H1 + GAP)], label="next page", at=(MID[1], ra + H1 + 36))
d.arrow([(MID[2] + 60, ra + H1), (MID[2] + 60, rb - GAP)], dot=False)
d.save(OUT + "htrflow-in-a-pod.svg")

# ---------------------------------------------------------------- campaigns: from a file to results
d = Diagram(W, 4 * H2 + 3 * ROWGAP + 48)
ys = [24 + i * (H2 + ROWGAP) for i in range(4)]
tall_row(d, ys[0], [(0, "Campaigns repo", "campaigns, pipelines,\nconverter.yaml", L("git-branch")),
                    (1, "Converter in CI", "PR: validate\nmain: render", L("file-check")),
                    (2, "rendered/", "committed to git", L("file-code"))])
tall_row(d, ys[1], [(0, "Kueue", "queue-name label:\nwaits for GPUs", "kueue", {"logo": True}),
                    (1, "Campaign Job", "an Indexed Job and\nits ConfigMaps", "k8s-job", {"logo": True}),
                    (2, "Apply", "Argo CD, or\nhtrflow-campaigns apply", "argo", {"logo": True})], reverse=True)
d.arrow([(MID[2], ys[0] + H2), (MID[2], ys[1] - GAP)])
d.tall(C[0], ys[2], COLW, "Wrapper pods", "one per volume,\nup to the window", "k8s-pod", logo=True, strong=True)
d.tall(C[2], ys[2], COLW, "htrflow-web", "reads Jobs and Pods,\nserves the viewer", L("layout-dashboard"))
d.arrow([(MID[0], ys[1] + H2), (MID[0], ys[2] - GAP)], label="admits", at=(MID[0], ys[1] + H2 + ROWGAP / 2))
d.arrow([(MID[2], ys[2]), (MID[2], ys[2] - 36), (MID[1], ys[2] - 36), (MID[1], ys[1] + H2 + GAP)])
d.tall(C[0], ys[3], COLW, "S3 results bucket", "page/, alto/, progress,\nmanifest.json, run log", L("database"))
d.tall(C[2], ys[3], COLW, "Browser", "the status page\nand the viewer", L("globe"), outside=True)
d.arrow([(MID[0], ys[2] + H2), (MID[0], ys[3] - GAP)])
d.arrow([(MID[2] - 60, ys[2] + H2), (MID[2] - 60, ys[3] - 36), (MID[0] + 60, ys[3] - 36), (MID[0] + 60, ys[3] - GAP)])
d.arrow([(MID[2] + 40, ys[3]), (MID[2] + 40, ys[2] + H2 + GAP)])
d.arrow([(C[2], ys[3] + H2 / 2), (C[0] + COLW + GAP, ys[3] + H2 / 2)])
d.save(OUT + "campaigns.svg")

# ---------------------------------------------------------------- page flow: one page, from image to transcription
H2 = tall_height("a\nb")
gp = 16 + H2 + 64
ra, rb = gp + 56, gp + 56 + H2 + ROWGAP
gb = rb + H2 + 24 + 64
d = Diagram(W, gb + H2 + 16)
d.tall(C[1], 16, COLW, "IIIF server", "the manifest and\nthe page image", L("images"), outside=True)
d.group(24, gp, 952, rb + H2 + 24 - gp, "Inside the wrapper pod, for one page", "k8s-pod")
tall_row(d, ra, [(0, "PageRef", "index 1, name 0001,\nwidth-capped URL", L("tag")),
                 (1, "Fetch", "signature and size\nchecked, on tmpfs", L("download")),
                 (2, "htrflow", "regions, then lines,\nthen their text", L("scroll-text"), {"strong": True})])
tall_row(d, rb, [(0, "Upload", "PAGE, then ALTO,\nthen both unlinked", L("upload")),
                 (1, "ALTO stamped", "the htrflow-batch\nProcessing block", L("file-check")),
                 (2, "Two files", "page/0001.xml\nalto/0001.xml", L("file-code"))], reverse=True)
d.arrow([(MID[1], 16 + H2), (MID[1], ra - GAP)], label="GET", at=(MID[1], gp - 32))
d.arrow([(MID[2], ra + H2), (MID[2], rb - GAP)])
d.tall(C[0], gb, COLW, "S3 bucket", "page/0001.xml,\nalto/0001.xml", L("database"))
d.card(C[1], gb + (H2 - CARD_H) / 2, COLW * 2 + 56, "After the last page", "iiif.json, pipeline.yaml, manifest.json last", L("flag"), outside=True)
d.arrow([(MID[0], rb + H2), (MID[0], gb - GAP)], label="each page", at=(MID[0], gb - 32))
d.save(OUT + "page-flow.svg")

# ---------------------------------------------------------------- wrapper: the model cache and the warm-up
d = Diagram(W, 2 * H2 + ROWGAP + 48)
ys = [24, 24 + H2 + ROWGAP]
tall_row(d, ys[0], [(0, "Pipeline file", "a new pipelines/\nfile in git", L("file-code")),
                    (1, "Warm-up Job", "fills the cache,\nthen writes a marker", L("hard-drive-download")),
                    (2, "Model cache", "one shared disk,\na marker per pipeline", L("database"))])
d.tall(C[1], ys[1], COLW, "Campaign pod", "offline, the cache\nmounted read-only", "k8s-pod", logo=True, strong=True)
d.tall(C[2], ys[1], COLW, "warmup-wait", "init container:\nwaits for the marker", L("clock"))
d.arrow([(MID[2] + 50, ys[0] + H2), (MID[2] + 50, ys[1] - GAP)])
d.arrow([(MID[2] - 50, ys[0] + H2), (MID[2] - 50, ys[1] - 36), (MID[1], ys[1] - 36), (MID[1], ys[1] - GAP)], label="read-only", at=((MID[2] - 50 + MID[1]) / 2, ys[1] - 36))
d.arrow([(C[2], ys[1] + H2 / 2), (C[1] + COLW + GAP, ys[1] + H2 / 2)])
d.save(OUT + "warmup.svg")

# ---------------------------------------------------------------- queueing: the objects
GH = 56 + H2 + 24
d = Diagram(W, 16 + 3 * GH + 2 * 64 + 16)
gy = [16, 16 + GH + 64, 16 + 2 * (GH + 64)]
group_around(d, [0, 2], gy[0], GH, "Chart — values.queue", L("file-code"))
tall_row(d, gy[0] + 56, [(0, "LocalQueue", "in the release\nnamespace", L("door-open")),
                         (1, "ClusterQueue", "nominalQuota per\ncovered resource", L("layers")),
                         (2, "ResourceFlavor", "no nodeLabels:\nany node", L("tag"))])
group_around(d, [0], gy[1], GH, "Converter", L("file-check"))
group_around(d, [1], gy[1], GH, "Kueue", "kueue")
d.tall(C[0], gy[1] + 56, COLW, "Campaign Job", "Indexed, completions\n= volumes", "k8s-job", logo=True, strong=True)
d.tall(C[1], gy[1] + 56, COLW, "Workload", "made for the Job;\nunsuspends it", "kueue", logo=True)
d.arrow([(C[0] + COLW, gy[1] + 56 + H2 / 2 - 24), (C[1] - GAP, gy[1] + 56 + H2 / 2 - 24)])
d.arrow([(C[1], gy[1] + 56 + H2 / 2 + 24), (C[0] + COLW + GAP, gy[1] + 56 + H2 / 2 + 24)])
d.arrow([(MID[0] + 60, gy[1] + 56), (MID[0] + 60, gy[0] + 56 + H2 + GAP)], label="queue-name label", at=(MID[0] + 60, gy[1] - 32))
d.arrow([(MID[1], gy[1] + 56), (MID[1], gy[0] + 56 + H2 + GAP)], label="quota reserved", at=(MID[1], gy[1] - 32))
group_around(d, [0], gy[2], GH, "Kubernetes", "k8s-node")
d.tall(C[0], gy[2] + 56, COLW, "Pods", "one per index, up\nto parallelism", "k8s-pod", logo=True)
d.arrow([(MID[0] + 60, gy[1] + 56 + H2), (MID[0] + 60, gy[2] + 56 - GAP)], label="Job controller", at=(MID[0] + 60, gy[2] - 32))
d.save(OUT + "queue-objects.svg")

# ---------------------------------------------------------------- queueing: a campaign's life
d = Diagram(W, 3 * H2 + 2 * ROWGAP + 48)
ys = [24 + i * (H2 + ROWGAP) for i in range(3)]
tall_row(d, ys[0], [(0, "Rendered", "the Job manifest,\ncommitted to git", L("file-code")),
                    (1, "Applied", "suspended by the\nwebhook; a Workload", "k8s-job", {"logo": True}),
                    (2, "Queued", "waiting, counted in\npendingWorkloads", L("clock"))])
tall_row(d, ys[1], [(1, "Done", "Job Complete,\nquota released", L("circle-check")),
                    (2, "Running", "admitted when quota\nis free", L("play"), {"strong": True})], reverse=True)
d.tall(C[0], ys[1], COLW, "Failed", "failed or partly;\nWorkload Finished", L("circle-x"), bad=True)
d.tall(C[2], ys[2], COLW, "Paused", "suspend: true in git;\nremove it to queue", L("pause"))
d.arrow([(MID[2], ys[0] + H2), (MID[2], ys[1] - GAP)])
d.arrow([(MID[2] - 80, ys[1] + H2), (MID[2] - 80, ys[2] - 36), (MID[0], ys[2] - 36), (MID[0], ys[1] + H2 + GAP)])
d.arrow([(MID[2] + 40, ys[1] + H2), (MID[2] + 40, ys[2] - GAP)])
d.arrow([(C[2] + COLW, ys[2] + H2 / 2), (976, ys[2] + H2 / 2), (976, ys[0] + H2 / 2), (C[2] + COLW + GAP, ys[0] + H2 / 2)])
d.save(OUT + "campaign-lifecycle.svg")

# ---------------------------------------------------------------- failure handling: one index
d = Diagram(W, 2 * H2 + ROWGAP + 48 + CARD_H + 48)
ys = [24, 24 + H2 + ROWGAP]
tall_row(d, ys[0], [(0, "Queued", "applied, suspended\nuntil Kueue admits", L("clock")),
                    (1, "Running", "a pod for the index,\non a GPU", "k8s-pod", {"logo": True, "strong": True}),
                    (2, "Done", "verify passed,\nmanifest.json in S3", L("circle-check"))])
d.tall(C[0], ys[1], COLW, "Retry", "exit 1 or 143,\nretries left", L("rotate-ccw"))
d.tall(C[2], ys[1], COLW, "Index failed", "exit 13, or no\nretries left", L("circle-x"), bad=True)
lane = ys[1] - 36
d.arrow([(MID[1] - 60, ys[0] + H2), (MID[1] - 60, lane), (MID[0], lane), (MID[0], ys[1] - GAP)])
d.arrow([(C[0] + COLW, ys[1] + H2 / 2), (MID[1], ys[1] + H2 / 2), (MID[1], ys[0] + H2 + GAP)], label="replaced, resumes", at=(MID[1], ys[1] + 40))
d.arrow([(MID[1] + 60, ys[0] + H2), (MID[1] + 60, lane), (MID[2], lane), (MID[2], ys[1] - GAP)])
d.card(48, ys[1] + H2 + 48, 904, "Pod disruption", "a drain or preemption: the pod is replaced, no retry is charged", L("power"), outside=True)
d.save(OUT + "index-failure.svg")


# ---------------------------------------------------------------- sequences, as numbered steps
A = {
    "ci": ("Campaigns CI", L("git-branch"), False), "apply": ("Apply", "argo", False),
    "api": ("API server", L("server"), False), "kueue": ("Kueue", "kueue", False),
    "webhook": ("Kueue webhook", "kueue", False), "ctrl": ("Kueue controller", "kueue", False),
    "jobc": ("Job controller", L("repeat"), False), "sched": ("Scheduler", L("calendar-clock"), False),
    "kubelet": ("kubelet", "k8s-node", False), "pod": ("Pod, index i", "k8s-pod", False),
    "wrapper": ("Wrapper pod", "k8s-pod", False), "iiif": ("IIIF server", L("images"), True),
    "s3": ("Results bucket", L("database"), False), "log": ("Run log in S3", L("scroll-text"), False),
    "readapi": ("Read API", L("layout-dashboard"), False), "browser": ("Log view", L("globe"), True),
    "warmer": ("Warmer", L("hard-drive-download"), False), "alluxio": ("Alluxio workers", L("layers"), False),
    "shim": ("iiif-shim", L("server"), False), "origin": ("IIIF origin", L("images"), True),
    "fuse": ("Pod via FUSE", "k8s-pod", False),
}

Steps(W, A, [
    ("msg", "ci", "ci", "htrflow-campaigns render; rendered/ is committed"),
    ("msg", "apply", "api", "apply the campaign Job: Indexed, completions = N, with the queue-name label"),
    ("msg", "kueue", "kueue", "the webhook suspends the Job, and its Workload is queued"),
    ("msg", "kueue", "api", "quota is free: unsuspend the Job, up to parallelism"),
    ("msg", "api", "pod", "a pod for index i: one GPU, a tmpfs workdir, the model cache read-only"),
    ("msg", "pod", "iiif", "fetch the IIIF manifest for line i of volumes.txt"),
    ("msg", "pod", "s3", "list page/ and alto/ — the resume check"),
    ("msg", "pod", "pod", "load the models once, while the first pages download"),
    ("loop", "Streaming — downloader, consumer and uploader at once", [
        ("msg", "pod", "iiif", "fetch page N+k, a few pages ahead, width-capped"),
        ("msg", "pod", "pod", "run the pipeline on page N as soon as it is downloaded"),
        ("msg", "pod", "s3", "upload page N−1's PAGE, then its ALTO, as soon as htrflow wrote them"),
        ("msg", "pod", "s3", "progress.json after every page; the run log every 15 s"),
        ("msg", "pod", "pod", "delete page N−1's image and XML from tmpfs"),
    ]),
    ("msg", "pod", "pod", "verify every page is uploaded, skipped or recorded as failed"),
    ("msg", "pod", "s3", "upload iiif.json, pipeline.yaml, then manifest.json last — the completion marker"),
    ("msg", "pod", "api", "exit 0: index i joins completedIndexes"),
], "One campaign, in order", L("list-ordered")).draw(OUT + "seq-campaign.svg")

Steps(W, A, [
    ("msg", "apply", "api", "server-side apply the Indexed Job, with its queue-name label"),
    ("msg", "api", "webhook", "AdmissionReview: CREATE batch/v1 jobs"),
    ("reply", "webhook", "api", "patch spec.suspend to true"),
    ("msg", "ctrl", "api", "create the Workload, owned by the Job, labelled job-uid"),
    ("note", "The Workload's podSets main count is the Job's parallelism."),
    ("msg", "ctrl", "ctrl", "order the queue: BestEffortFIFO"),
    ("msg", "ctrl", "api", "reserve quota: QuotaReserved, then Admitted"),
    ("msg", "ctrl", "api", "patch the Job's spec.suspend to false"),
    ("msg", "jobc", "api", "create one pod per index, up to parallelism"),
    ("msg", "api", "sched", "an unscheduled pod"),
    ("msg", "sched", "api", "bind it to a node with a free nvidia.com/gpu"),
    ("msg", "api", "kubelet", "pod assigned"),
    ("msg", "kubelet", "kubelet", "run the init container warmup-wait, then the wrapper"),
    ("msg", "kubelet", "api", "pod succeeded"),
    ("msg", "jobc", "api", "record the index in status.completedIndexes"),
    ("msg", "jobc", "api", "Job condition Complete once every index is done"),
    ("msg", "ctrl", "api", "Workload condition Finished; quota released"),
], "The admission cycle", "kueue").draw(OUT + "seq-admission.svg")

Steps(W, A, [
    ("msg", "kueue", "jobc", "Workload QuotaReserved and Admitted; Job unsuspended"),
    ("msg", "jobc", "kubelet", "a pod for index i (event SuccessfulCreate)"),
    ("msg", "kubelet", "kubelet", "the init container warmup-wait reads the marker on the cache"),
    ("msg", "wrapper", "s3", "run log claimed at start, then shipped again every 15 s"),
    ("loop", "Each page", [
        ("msg", "wrapper", "s3", "page XML, then ALTO XML — the ALTO carries the provenance block"),
        ("msg", "wrapper", "s3", "progress.json every page, iiif.json every 10th"),
    ]),
    ("note", "The read API polls progress.json here too, on its own path to the bucket, not the browser's."),
    ("msg", "wrapper", "s3", "iiif.json, pipeline.yaml, then manifest.json last"),
    ("msg", "wrapper", "kubelet", "exit 0"),
    ("msg", "kubelet", "jobc", "the container's exit code"),
    ("msg", "jobc", "jobc", "index i added to completedIndexes"),
    ("note", "On failure the wrapper writes the termination message first, then exits 13 (FailIndex), or 1 or 143 (retried)."),
    ("note", "Nothing above writes the campaign's status ConfigMap: the read API copies the phase and counts there when a request sees something new, and apply copies them off the live Job, so they outlive it."),
], "One index, in order", L("list-ordered")).draw(OUT + "seq-signals-index.svg")

Steps(W, A, [
    ("msg", "wrapper", "log", "PUT: claim the key at start"),
    ("loop", "Every LOG_SHIP_SECONDS (15 s), if the buffer changed", [
        ("msg", "wrapper", "log", "PUT the whole buffer"),
    ]),
    ("msg", "browser", "readapi", "GET the campaign detail: per-index state and logUrl"),
    ("msg", "browser", "log", "GET the log every 15 s, ETag-revalidated, until the terminal line"),
    ("msg", "wrapper", "log", "PUT once more on exit: the complete log, on SIGTERM too"),
], "The live run log", L("scroll-text")).draw(OUT + "seq-run-log.svg")

Steps(W, A, [
    ("msg", "ci", "api", "apply the campaign Indexed Job, suspended"),
    ("msg", "warmer", "api", "read the queue order and the volume lists"),
    ("msg", "warmer", "api", "create DataLoads for the next volumes"),
    ("msg", "alluxio", "shim", "GET the volume index page"),
    ("msg", "shim", "origin", "fetch the IIIF manifest, cached in the shim"),
    ("msg", "alluxio", "shim", "GET each page"),
    ("msg", "shim", "origin", "width-capped image GETs"),
    ("note", "The volume's blocks sit in the memory tier on the GPU nodes."),
    ("msg", "kueue", "api", "quota is free: unsuspend the Job"),
    ("msg", "api", "fuse", "schedule the pod, preferring nodes that hold the blocks"),
    ("msg", "fuse", "s3", "list existing outputs to resume"),
    ("msg", "fuse", "fuse", "the inputs are the pages minus those done"),
    ("msg", "fuse", "alluxio", "htrflow reads pages through FUSE"),
    ("reply", "alluxio", "fuse", "a warm read from node-local memory"),
    ("msg", "fuse", "alluxio", "a read of a page never prefetched"),
    ("msg", "alluxio", "shim", "read-through GET"),
    ("msg", "shim", "origin", "fetch from the origin"),
    ("reply", "alluxio", "fuse", "bytes served, and cached"),
    ("msg", "fuse", "fuse", "verify the outputs match the inputs"),
    ("msg", "fuse", "s3", "upload ALTO and PAGE per page, manifest.json last"),
], "One campaign, with the cache layer", L("list-ordered")).draw(OUT + "seq-cache-layer.svg")

# ---------------------------------------------------------------- roadmap: the cache layer
H2 = tall_height("a\nb")
gc = 16 + H2 + 64
rows = [gc + 56 + i * (H2 + ROWGAP) for i in range(4)]
d = Diagram(W, rows[3] + H2 + 24 + 16)
d.tall(C[1], 16, COLW, "Campaigns CI", "applies the campaign\nIndexed Job", L("git-branch"))
d.group(24, gc, C[1] + COLW, rows[3] + H2 + 24 - gc, "Kubernetes cluster", "k8s-node")
tall_row(d, rows[0], [(0, "Warmer", "reads queue order,\ncreates DataLoads", L("hard-drive-download")),
                      (1, "Kueue", "admits when quota\nis free", "kueue", {"logo": True})], arrows=False)
d.tall(C[0], rows[1], COLW, "DataLoad", "one per warmed\nvolume", L("download"))
d.tall(C[1], rows[1], COLW, "Campaign pod", "wrapper and htrflow,\nno download stage", "k8s-pod", logo=True, strong=True)
d.tall(C[0], rows[2], COLW, "AlluxioRuntime", "memory tier, then\ndisk, on GPU nodes", L("layers"))
d.tall(C[1], rows[2], COLW, "Dataset PVC", "iiif-volumes, FUSE,\nread-only", L("hard-drive-download"))
d.tall(C[0], rows[3], COLW, "iiif-shim", "stateless; the width\nis in the path", L("server"))
group_around(d, [2], rows[1] - 56, 56 + H2 + 24, "Storage", L("database"))
d.tall(C[2], rows[1], COLW, "Results bucket", "ALTO, PAGE,\nmanifest.json", L("database"))
group_around(d, [2], rows[3] - 56, 56 + H2 + 24, "Outside", L("globe"), outside=True)
d.tall(C[2], rows[3], COLW, "IIIF origin", "manifests and\nimages", L("images"), outside=True)
d.arrow([(MID[1], 16 + H2), (MID[1], rows[0] - GAP)], label="Job, suspended", at=(MID[1], gc - 32))
d.arrow([(C[0] + COLW, rows[0] + H2 / 2), (C[1] - GAP, rows[0] + H2 / 2)], label="reads", at=(C[0] + COLW + 28, rows[0] + H2 / 2 - 30))
d.arrow([(MID[0], rows[0] + H2), (MID[0], rows[1] - GAP)])
d.arrow([(MID[1], rows[0] + H2), (MID[1], rows[1] - GAP)], dashed=True)
d.arrow([(C[1] + COLW, rows[1] + H2 / 2), (C[2] - GAP, rows[1] + H2 / 2)])
d.arrow([(MID[0], rows[1] + H2), (MID[0], rows[2] - GAP)])
d.arrow([(MID[1], rows[1] + H2), (MID[1], rows[2] - GAP)], label="reads", at=(MID[1], rows[1] + H2 + ROWGAP / 2))
d.arrow([(C[1], rows[2] + H2 / 2), (C[0] + COLW + GAP, rows[2] + H2 / 2)])
d.arrow([(MID[0], rows[2] + H2), (MID[0], rows[3] - GAP)], label="read-through", at=(MID[0], rows[2] + H2 + ROWGAP / 2))
d.arrow([(C[0] + COLW, rows[3] + H2 / 2), (C[2] - GAP, rows[3] + H2 / 2)], label="width-capped GETs", at=(MID[1], rows[3] + H2 / 2))
d.save(OUT + "cache-layer.svg")
print("docs diagrams written")
