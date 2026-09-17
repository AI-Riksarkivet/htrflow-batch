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


def group_around(d, cols, y, h, label, icon=None, outside=False):
    x0, x1 = C[min(cols)] - 24, C[max(cols)] + COLW + 24
    d.group(x0, y, x1 - x0, h, label, icon, outside=outside)


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

# ---------------------------------------------------------------- architecture: the map
d = Diagram(W, 1296)
H2 = tall_height("a\nb")
d.group(24, 16, 952, 56 + H2 + 24, "Campaigns repo and delivery", L("git-branch"))
tall_row(d, 72, [(0, "Campaign files", "campaigns, pipelines,\nconverter.yaml", L("file-text")),
                 (1, "Converter in CI", "PR: validate\nmain: commit rendered/", L("file-check")),
                 (2, "Apply", "Argo CD, or\nhtrflow-campaigns apply", "argo", {"logo": True})])
g2 = 16 + 56 + H2 + 24 + 64
r1 = g2 + 56
r2 = r1 + H2 + ROWGAP
d.group(24, g2, 952, 56 + H2 + ROWGAP + H2 + 24, "Kubernetes cluster", "k8s-node")
tall_row(d, r1, [(0, "Kyverno", "every Job, Pod and\npipeline ConfigMap", "kyverno", {"logo": True}),
                 (1, "LocalQueue", "the campaign's line", L("door-open")),
                 (2, "ClusterQueue", "quota: N GPUs", L("layers"))])
d.arrow([(MID[2], 72 + H2), (MID[2], g2 - 32), (MID[0] + 80, g2 - 32), (MID[0] + 80, r1 - GAP)])
d.tall(C[0], r2, COLW, "Warm-up Job", "one per pipeline,\nnot queued", L("hard-drive-download"))
d.tall(C[1], r2, COLW, "Campaign pods", "an Indexed Job,\none pod per volume", "k8s-pod", logo=True, strong=True)
d.tall(C[2], r2, COLW, "Read API", "GET /api/v1/jobs\nread-only RBAC", L("layout-dashboard"))
d.arrow([(MID[0], r1 + H2), (MID[0], r2 - GAP)])
d.arrow([(MID[2] - 60, r1 + H2), (MID[2] - 60, r2 - 36), (MID[1], r2 - 36), (MID[1], r2 - GAP)], label="admits", at=((MID[2] - 60 + MID[1]) / 2, r2 - 36))
d.arrow([(C[0] + COLW, r2 + H2 / 2), (C[1] - GAP, r2 + H2 / 2)], dashed=True)
d.arrow([(C[2], r2 + H2 / 2), (C[1] + COLW + GAP, r2 + H2 / 2)])
g3 = g2 + 56 + H2 + ROWGAP + H2 + 24 + 64
r3 = g3 + 56
group_around(d, [0, 2], g3, 56 + H2 + 24, "Outside", L("globe"), outside=True)
d.tall(C[0], r3, COLW, "IIIF image server", "width-capped GETs", L("images"), outside=True)
d.tall(C[1], r3, COLW, "S3 results bucket", "PAGE, ALTO, progress,\nmanifest.json last", L("database"))
d.tall(C[2], r3, COLW, "Browser", "status page, and the\nviewer reading S3", L("globe"), outside=True)
lane = g3 - 32
d.arrow([(MID[1] - 60, r2 + H2), (MID[1] - 60, lane), (MID[0] + 50, lane), (MID[0] + 50, r3 - GAP)])
d.arrow([(MID[1] + 20, r2 + H2), (MID[1] + 20, r3 - GAP)])
d.arrow([(MID[2] - 60, r2 + H2), (MID[2] - 60, lane), (MID[1] + 80, lane), (MID[1] + 80, r3 - GAP)])
d.arrow([(MID[2] + 40, r3), (MID[2] + 40, r2 + H2 + GAP)])
d.arrow([(C[2], r3 + H2 / 2), (C[1] + COLW + GAP, r3 + H2 / 2)])
d.save(OUT + "architecture.svg")

# ---------------------------------------------------------------- architecture: inside one pod
d = Diagram(W, 318)
cw = (904 - 3 * 32) / 4
xs = [48 + i * (cw + 32) for i in range(4)]
d.group(24, 16, 952, 56 + H2 + 24, "Inside one pod — the streaming driver", "k8s-pod")
steps = [("Downloader", "pages ahead,\nwidth-capped", L("download"), {}),
         ("Page queue", "images on\ntmpfs", L("list-ordered"), {}),
         ("Consumer", "models loaded\nonce", L("scroll-text"), {"strong": True}),
         ("Uploader", "PAGE then ALTO,\nthen deletes", L("upload"), {})]
for i, (t, s, ic, o) in enumerate(steps):
    d.tall(xs[i], 72, cw, t, s, ic, **o)
    if i:
        d.arrow([(xs[i - 1] + cw, 72 + H2 / 2), (xs[i] - GAP, 72 + H2 / 2)])
d.save(OUT + "streaming-driver.svg")

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
d = Diagram(W, 3 * H2 + 2 * ROWGAP + 48)
ys = [24 + i * (H2 + ROWGAP) for i in range(3)]
tall_row(d, ys[0], [(0, "Manifest", "fetched, or built\nfrom image URLs", L("book-open")),
                    (1, "PageRef", "index 1, name 0001,\nwidth-capped URL", L("tag")),
                    (2, "Image on tmpfs", "input/0001.jpg,\nsignature checked", L("images"))])
tall_row(d, ys[1], [(0, "ALTO stamped", "the htrflow-batch\nProcessing block", L("file-check")),
                    (1, "XML on tmpfs", "page/0001.xml\nalto/0001.xml", L("file-code")),
                    (2, "htrflow runs", "regions, then lines,\nthen their text", L("scroll-text"), {"strong": True})], reverse=True)
d.arrow([(MID[2], ys[0] + H2), (MID[2], ys[1] - GAP)])
tall_row(d, ys[2], [(0, "Uploaded", "PAGE, then ALTO,\nthen unlinked", L("upload")),
                    (1, "Last page done", "iiif.json, pipeline.yaml,\nmanifest.json last", L("flag"))])
d.arrow([(MID[0], ys[1] + H2), (MID[0], ys[2] - GAP)])
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
print("docs diagrams written")
