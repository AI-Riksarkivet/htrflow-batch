"""The remaining hand-drawn diagrams across the decks (build.py holds the
four in part 1's opening). Run from the repository root:

    python3 docs/slides/diagrams/build_rest.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, CARD_H, GAP, MAGENTA

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets") + os.sep
LOGO = {"logo": True}
OUTSIDE = {"outside": True}


def L(name):
    return "lucide-" + name


def fan(d, x, y, targets, lane):
    """One trunk from (x, y) down to `lane`, then an arrow into the top of
    each target box."""
    for tx, ty, tw, _ in targets:
        d.arrow([(x, y), (x, lane), (tx + tw / 2, lane), (tx + tw / 2, ty - GAP)], dot=False)
    d.back.append(f'<circle cx="{x}" cy="{y}" r="4.5" fill="{MAGENTA}"/>')


# ---------------------------------------------------------------- part 1: the Workload
d = Diagram(1600, 232)
d.row(24, [
    ("Campaign Job", "window 2\neach pod 1 GPU", "k8s-job", LOGO),
    ("Workload", "Kueue's copy,\nasks for 2 GPUs", "kueue", LOGO),
    ("LocalQueue", "waits in line", L("door-open")),
    ("ClusterQueue", "2 GPUs free?", L("layers")),
    ("Admitted", "the Job's pods start", L("play"), {"strong": True}),
])
d.save(OUT + "p1-workload.svg")

# ---------------------------------------------------------------- part 1: Kyverno admission
d = Diagram(1600, 336)
my = 120
d.card(24, my, 300, "Apply", "sends an object", "argo", logo=True)
d.card(396, my, 300, "API server", "Kubernetes", L("server"))
d.card(768, my, 300, "Kyverno", "checks the policies", "kyverno", logo=True, strong=True)
d.card(1212, 24, 364, "Accepted", "stored and run", L("circle-check"))
d.card(1212, 216, 364, "Refused", "one sentence why", L("circle-x"), bad=True)
d.arrow([(324, my + 48), (396 - GAP, my + 48)])
d.arrow([(696, my + 48), (768 - GAP, my + 48)])
d.arrow([(1068, my + 30), (1140, my + 30), (1140, 72), (1212 - GAP, 72)], label="passes", at=(1140, 110))
d.arrow([(1068, my + 66), (1140, my + 66), (1140, 264), (1212 - GAP, 264)], label="breaks a rule", at=(1140, 226))
d.save(OUT + "p1-admission.svg")

# ---------------------------------------------------------------- part 1: Kueue + Kyverno flow
d = Diagram(1600, 540)
y = 110
boxes = d.row(y, [
    ("Job", "created, paused", "k8s-job", {"logo": True, "n": 1}),
    ("Kyverno", "checks the Job", "kyverno", {"logo": True, "strong": True, "n": 2}),
    ("Kueue", "Workload waits", "kueue", {"logo": True, "n": 3}),
    ("Admitted", "Job unpaused", L("play"), {"n": 4}),
    ("Kyverno", "checks each pod", "kyverno", {"logo": True, "strong": True, "n": 5}),
    ("Pods run", "one per volume", "k8s-pod", {"logo": True, "n": 6}),
], gap=40)
bottom = y + boxes[0][3]
for i, sub in ((1, "never stored"), (4, "pod never starts")):
    bx, _, bw, _ = boxes[i]
    d.card(bx + bw / 2 - 160, 420, 320, "Refused", sub, L("circle-x"), bad=True)
    d.arrow([(bx + bw / 2, bottom), (bx + bw / 2, 420 - GAP)], label="breaks a rule", at=(bx + bw / 2, (bottom + 420) / 2))
kx = boxes[2][0] + boxes[2][2] / 2
d.arrow([(kx + 50, y), (kx + 50, 56), (kx - 50, 56), (kx - 50, y - GAP)])
d.pill(kx, 40, "until GPUs are free")
d.save(OUT + "p1-kueue-flow.svg")

# ---------------------------------------------------------------- part 1: Kueue's switch on the Job
d = Diagram(1600, 560)
gap = 140
w = (1552 - 3 * gap) / 4
x = [24 + i * (w + gap) for i in range(4)]
top = 24
states = [("Job created", "labelled with\nits queue", "k8s-job", {"logo": True}),
          ("Suspended", "set by Kueue's webhook:\nno pods yet", L("toggle-left"), {}),
          ("Running", "admitted: suspend off,\npods created", L("toggle-right"), {"strong": True}),
          ("Finished", "the quota is\nfree again", L("circle-check"), {})]
boxes = [d.tall(x[i], top, w, t, sub, ic, **o) for i, (t, sub, ic, o) in enumerate(states)]
h = boxes[0][3]
my = top + h / 2
d.arrow([(x[0] + w, my), (x[1] - GAP, my)])
d.arrow([(x[1] + w, my), (x[2] - GAP, my)], label="admitted", at=(x[1] + w + gap / 2, my))
d.arrow([(x[2] + w, my), (x[3] - GAP, my)], label="done", at=(x[2] + w + gap / 2, my))
py = 330
paused = d.tall(x[2], py, w, "Paused", "suspended again:\npods deleted", L("pause"))
d.arrow([(x[2] + w / 2, top + h), (x[2] + w / 2, py - GAP)], label="suspend: true in git", at=(x[2] + w / 2, (top + h + py) / 2))
d.arrow([(x[2], py + h / 2), (x[1] + w / 2, py + h / 2), (x[1] + w / 2, top + h + GAP)], label="resumed: queues again", at=((x[2] + x[1] + w / 2) / 2, py + h / 2))
d.save(OUT + "p1-suspend.svg")

# ---------------------------------------------------------------- part 1: htrflow in a pod
d = Diagram(1600, 760)
d.group(24, 176, 1232, 568, "Pod — one archival volume, one GPU", "k8s-pod")
# the init container
d.group(48, 232, 300, 256, "Init container", inner=True)
d.tall(72, 288, 252, "Wait for models", "until the cache is ready", L("hard-drive-download"))
# the main container: a loop over pages, then finish right to left
d.group(372, 232, 860, 488, "Container", inner=True)
cw = (860 - 48 - 2 * 48) / 3
c = [396 + i * (cw + 48) for i in range(3)]
mid = [x + cw / 2 for x in c]
ra, rb = 288, 520
d.tall(c[0], ra, cw, "Fetch the page", "skips pages done", L("download"))
d.tall(c[1], ra, cw, "htrflow", "runs your pipeline", L("scroll-text"), strong=True)
d.tall(c[2], ra, cw, "Upload", "PAGE, then ALTO", L("upload"))
d.tall(c[2], rb, cw, "Verify", "every page counted", L("search-check"))
d.tall(c[1], rb, cw, "Publish", "manifest.json last", L("cloud-upload"))
d.tall(c[0], rb, cw, "Exit", "the GPU is free", L("power"))
h = 176
d.arrow([(324, ra + h / 2), (c[0] - GAP, ra + h / 2)])
d.arrow([(c[0] + cw, ra + h / 2), (c[1] - GAP, ra + h / 2)])
d.arrow([(c[1] + cw, ra + h / 2), (c[2] - GAP, ra + h / 2)])
d.arrow([(mid[2] - 50, ra + h), (mid[2] - 50, 492), (mid[0], 492), (mid[0], ra + h + GAP)], label="next page", at=(mid[1], 492))
d.arrow([(mid[2] + 50, ra + h), (mid[2] + 50, rb - GAP)])
d.arrow([(c[2], rb + h / 2), (c[1] + cw + GAP, rb + h / 2)])
d.arrow([(c[1], rb + h / 2), (c[0] + cw + GAP, rb + h / 2)])
# what the pod reads, and where it writes
d.card(mid[0] - 260, 24, 300, "IIIF server", "the page images", L("images"), outside=True)
d.card(mid[1] - 40, 24, 300, "Model cache", "read-only, shared", L("database"))
d.card(1296, ra + h / 2 - CARD_H / 2, 280, "S3 bucket", "ALTO · PAGE", L("database"))
d.arrow([(mid[0] + 30, 120), (mid[0] + 30, ra - GAP)], label="pages", at=(mid[0] + 30, 148))
d.arrow([(mid[1], 120), (mid[1], ra - GAP)], label="weights", at=(mid[1], 148))
d.arrow([(c[2] + cw, ra + h / 2), (1296 - GAP, ra + h / 2)])
d.save(OUT + "p1-pod.svg")

# ---------------------------------------------------------------- part 1: one Job, four indexes
d = Diagram(760, 400)
d.card(24, 24, 712, "Job", "completions 4 · parallelism 2", "k8s-job", logo=True)
spine = 380
d.back.append(f'<circle cx="{spine}" cy="120" r="4.5" fill="{MAGENTA}"/>')
d.arrow([(spine, 120), (spine, 330)], head=False, dot=False)
pw = (712 - 64) / 2
for i, ref in enumerate(["R0001203", "R0001204", "R0001205", "R0001206"]):
    row, col = divmod(i, 2)
    py = 160 + row * (CARD_H + 24)
    px = 24 if col == 0 else spine + 32
    d.card(px, py, pw, f"Index {i}", ref, "k8s-pod", logo=True)
    if col == 0:
        d.arrow([(spine, py + 48), (px + pw + GAP, py + 48)], dot=False)
    else:
        d.arrow([(spine, py + 48), (px - GAP, py + 48)], dot=False)
d.save(OUT + "p1-job-indexes.svg")

# ---------------------------------------------------------------- part 1: window waves
d = Diagram(1600, 282)
gw = (1552 - 2 * 64) / 3
for w in range(3):
    gx = 24 + w * (gw + 64)
    d.group(gx, 16, gw, 250, f"Wave {w + 1}", L("layers"))
    tw = (gw - 48 - 24) / 2
    for k in range(2):
        d.tall(gx + 24 + k * (tw + 24), 72, tw, "Pod", f"R000{1203 + w * 2 + k}", "k8s-pod", logo=True)
    if w:
        d.arrow([(gx - 64, 141), (gx - GAP, 141)], dot=False)
d.save(OUT + "p1-window.svg")

# ---------------------------------------------------------------- part 1: not enough GPUs
d = Diagram(1600, 336)
r1, r2 = 72, 200
d.card(24, r1, 600, "Campaign B · window 4", "needs 4 — waits, reads Queued", L("clock"))
d.card(24, r2, 600, "Campaign C · window 2", "needs 2 — starts", L("play"), strong=True)
d.group(760, 16, 816, 304, "The cluster — 4 GPUs", L("microchip"))
d.card(784, r1, 768, "Campaign A · window 2", "running on 2 GPUs", L("play"))
d.card(784, r2, 768, "2 GPUs free", None, L("microchip"))
d.arrow([(624, r1 + 48), (692, r1 + 48), (692, r2 + 28), (784 - GAP, r2 + 28)], dashed=True, label="not enough", at=(692, 164))
d.arrow([(624, r2 + 64), (784 - GAP, r2 + 64)])
d.save(OUT + "p1-not-enough.svg")

# ---------------------------------------------------------------- part 1: priority
d = Diagram(1600, 252)
d.row(24, [
    ("Waiting", "A bulk 09:00 · B bulk 09:30\nC interactive 10:00", L("list-ordered")),
    ("Kueue admits", "C, then A, then B", "kueue", {"logo": True, "strong": True}),
    ("Running: D", "bulk — untouched\nnothing is evicted", L("play")),
], gap=96)
d.save(OUT + "p1-priority.svg")

# ---------------------------------------------------------------- part 1: git flow
d = Diagram(1600, 224)
d.row(24, [
    ("You edit", "the campaign file", L("pencil")),
    ("Validate", "locally", L("file-check")),
    ("Pull request", "CI checks again", L("git-pull-request")),
    ("Merge", "CI renders", L("git-merge")),
    ("Apply", "cluster objects", "argo", LOGO),
    ("Status page", "and the viewer", L("layout-dashboard")),
], gap=40)
d.save(OUT + "p1-git-flow.svg")

# ---------------------------------------------------------------- part 1: follow one campaign
d = Diagram(1600, 224)
d.row(24, [
    ("Merged", "1 volume · window 1", L("git-merge")),
    ("Queued", "quota full", L("clock")),
    ("Running", "137 / 638 pages", L("play"), {"strong": True}),
    ("Done", "637 ok, 1 failed", L("circle-check")),
    ("Viewer", "the whole volume", L("book-open")),
])
d.save(OUT + "p1-follow.svg")

# ---------------------------------------------------------------- part 2: the pull request
d = Diagram(1600, 224)
d.row(24, [
    ("Edit", "kyrkobocker-1.yaml", L("pencil")),
    ("Validate", "locally", L("file-check")),
    ("Pull request", "validate + policies", L("git-pull-request")),
    ("Review", "a colleague reads it", L("users")),
    ("Merge", "CI commits rendered/", L("git-merge")),
])
d.save(OUT + "p2-pull-request.svg")

# ---------------------------------------------------------------- part 2: apply
d = Diagram(1600, 252)
d.row(24, [
    ("Render again", "append-only,\npipelines unchanged", L("refresh-cw")),
    ("Write records", "one per campaign,\nbefore anything", L("file-pen")),
    ("Pipelines", "ConfigMap and\nwarm-up Job", L("file-code")),
    ("Campaigns", "skips finished,\nunchanged ones", L("send")),
    ("Pause states", "on each Kueue\nWorkload", L("pause")),
])
d.save(OUT + "p2-apply.svg")

# ---------------------------------------------------------------- part 3: three roles
d = Diagram(1600, 252)
d.row(24, [
    ("IIIF server", "the pages", L("images"), OUTSIDE),
    ("Downloader", "12 in flight,\n64 pages ahead", L("download")),
    ("tmpfs", "/work,\nin memory", L("memory-stick")),
    ("Consumer", "one thread —\nthe GPU serialises", L("microchip"), {"strong": True}),
    ("Uploader", "PAGE, then ALTO,\nthen delete", L("upload")),
    ("Bucket", "S3", L("database")),
], gap=40)
d.save(OUT + "p3-loop.svg")

# ---------------------------------------------------------------- part 3: verify
d = Diagram(1600, 336)
my = 120
d.card(24, my, 330, "List the bucket", "page/ and alto/", L("list-checks"))
d.card(426, my, 520, "Every page accounted for?", "uploaded, skipped or failed", L("search-check"), strong=True)
d.card(1136, 24, 440, "Publish", "manifest.json written last", L("cloud-upload"))
d.card(1136, 216, 440, "Fail the volume", "with the page list", L("circle-x"), bad=True)
d.arrow([(354, my + 48), (426 - GAP, my + 48)])
d.arrow([(946, my + 30), (1040, my + 30), (1040, 72), (1136 - GAP, 72)], label="yes", at=(1040, 110))
d.arrow([(946, my + 66), (1040, my + 66), (1040, 264), (1136 - GAP, 264)], label="no", at=(1040, 226))
d.save(OUT + "p3-verify.svg")

# ---------------------------------------------------------------- part 3: exit codes
d = Diagram(1600, 432)
d.card(640, 24, 320, "The pod runs", None, "k8s-pod", logo=True)
codes = [("Exit 0", "done — failed\npages recorded", L("circle-check"), {}),
         ("Exit 1", "transient — retried up to 3×,\nresuming from the bucket", L("rotate-ccw"), {}),
         ("Exit 143", "a drain or the deadline —\nretried like exit 1", L("power"), {}),
         ("Exit 13", "permanent — fails at once,\nnever retried", L("octagon-x"), {"bad": True})]
w = (1552 - 3 * 40) / 4
targets = []
for i, (t, s, ic, o) in enumerate(codes):
    targets.append(d.tall(24 + i * (w + 40), 200, w, t, s, ic, **o))
fan(d, 800, 24 + CARD_H, targets, 160)
d.save(OUT + "p3-exit.svg")

# ---------------------------------------------------------------- part 1: one pool, its flavors, their nodes
d = Diagram(1600, 600)
cw = (1552 - 56) / 2
col = [24, 24 + cw + 56]
mid = [x + cw / 2 for x in col]
top = d.tall(420, 24, 760, "ClusterQueue", "a100 quota: 8 GPU · 64 cores · 256 GB\nl4 quota: 4 GPU · 16 cores · 64 GB", L("layers"))
qb = 24 + top[3]
fy = qb + 82
for i, (name, product, gpu) in enumerate((("a100", "NVIDIA-A100-SXM4-80GB", "A100, 80 GB"), ("l4", "NVIDIA-L4", "L4, 24 GB"))):
    d.card(col[i], fy, cw, f"Flavor: {name}", f"nvidia.com/gpu.product={product}", L("tag"))
    nw = (cw - 24) / 2
    nodes = [(col[i] + k * (nw + 24), fy + 168, nw, CARD_H) for k in range(2)]
    for nx, ny, _, _ in nodes:
        d.card(nx, ny, nw, "Node", gpu, "k8s-node", logo=True)
    fan(d, mid[i], fy + CARD_H, nodes, fy + 132)
lane = qb + 40
d.arrow([(640, qb), (640, lane), (mid[0], lane), (mid[0], fy - GAP)], label="1st", at=((640 + mid[0]) / 2, lane))
d.arrow([(960, qb), (960, lane), (mid[1], lane), (mid[1], fy - GAP)], label="2nd", at=((960 + mid[1]) / 2, lane))
d.save(OUT + "p1-flavors.svg")

# ---------------------------------------------------------------- part 1: what one volume asks for
d = Diagram(1600, 424)
d.group(24, 16, 480, 392, "Size large — converter.yaml", L("file-code"))
d.card(48, 72, 432, "1 GPU", "on flavor a100", L("microchip"))
d.card(48, 184, 432, "8 CPU cores", "request and limit", L("cpu"))
d.card(48, 296, 432, "32 GB memory", "request and limit", L("memory-stick"))
wy = 212 - 88
d.tall(688, wy, 360, "Workload", "window 2:\n2 GPU · 16 cores · 64 GB", "kueue", logo=True, strong=True)
d.tall(1216, wy, 360, "ClusterQueue", "a100 quota:\n8 GPU · 64 cores · 256 GB", L("layers"))
d.arrow([(504, 212), (688 - GAP, 212)], label="× window", at=(596, 212))
d.arrow([(1048, 212), (1216 - GAP, 212)], label="requests", at=(1132, 212))
d.save(OUT + "p1-volume-resources.svg")

# ---------------------------------------------------------------- part 1: one team, one repo, one queue
d = Diagram(1600, 568)
gw = (1552 - 32) / 2
for i, team in enumerate(("transcription", "research")):
    gx = 24 + i * (gw + 32)
    d.group(gx, 16, gw, 312, f"Team: {team}", L("users"))
    d.card(gx + 24, 72, gw - 48, "Campaigns repo", "who may run what, reviewed", L("git-branch"))
    d.card(gx + 24, 208, gw - 48, "LocalQueue", "in the team's namespace", L("door-open"))
    d.arrow([(gx + gw / 2, 168), (gx + gw / 2, 208 - GAP)], dot=False)
d.group(24, 376, 1552, 176, "Cohort — the cluster's GPUs", L("layers"))
cw = 560
cx = [24 + gw / 2 - cw / 2, 24 + gw + 32 + gw / 2 - cw / 2]
for i, team in enumerate(("transcription", "research")):
    d.card(cx[i], 432, cw, f"ClusterQueue: {team}", "the team's budget", L("layers"))
    d.arrow([(24 + i * (gw + 32) + gw / 2, 304), (24 + i * (gw + 32) + gw / 2, 432 - GAP)])
d.arrow([(cx[0] + cw, 456), (cx[1] - GAP, 456)], dot=False)
d.arrow([(cx[1], 504), (cx[0] + cw + GAP, 504)], dot=False)
d.pill(800, 480, "lend idle GPUs")
d.save(OUT + "p1-teams.svg")

# ---------------------------------------------------------------- part 1: cohort, queues, flavors, nodes
d = Diagram(1600, 584)
cw = (1552 - 2 * 56) / 3
col = [24 + i * (cw + 56) for i in range(3)]
mid = [x + cw / 2 for x in col]
d.group(24, 16, 1552, 176, "Cohort — the archive", L("users"))
d.card(120, 72, 560, "ClusterQueue: transcription", "quota in a100 and l4", L("layers"))
d.card(920, 72, 560, "ClusterQueue: research", "quota in l4 and interruptible", L("layers"))
d.arrow([(680, 96), (920 - GAP, 96)], dot=False)
d.arrow([(920, 144), (680 + GAP, 144)], dot=False)
d.pill(800, 120, "lend idle quota")
fy, lane = 296, 244
for i, (name, label, having) in enumerate((("a100", "A100", "an A100"), ("l4", "L4", "an L4"), ("interruptible", "spot", "spot capacity"))):
    d.card(col[i], fy, cw, f"Flavor: {name}", f"nodes with {having}", L("tag"))
    nw = (cw - 24) / 2
    nodes = [(col[i] + k * (nw + 24), 464, nw, CARD_H) for k in range(2)]
    for nx, ny, _, _ in nodes:
        d.card(nx, ny, nw, "Node", label, "k8s-node", logo=True)
    fan(d, mid[i], fy + CARD_H, nodes, 428)
d.arrow([(mid[0], 168), (mid[0], fy - GAP)], label="1st", at=(mid[0], lane))
d.arrow([(600, 168), (600, lane), (mid[1] - 40, lane), (mid[1] - 40, fy - GAP)], label="2nd", at=(680, lane))
d.arrow([(1000, 168), (1000, lane), (mid[1] + 40, lane), (mid[1] + 40, fy - GAP)], label="1st", at=(920, lane))
d.arrow([(mid[2], 168), (mid[2], fy - GAP)], label="2nd", at=(mid[2], lane))
d.save(OUT + "p1-flavors-cohort.svg")

# ---------------------------------------------------------------- part 5: how a model reaches the GPU
d = Diagram(1600, 312)
cw = (1552 - 64 - 64 - 150) / 4
c = [24, 24 + cw + 64, 24 + 2 * (cw + 64), 1576 - cw]
r1, r2 = 24, 192
py = (r1 + r2) / 2
d.card(c[0], r1, cw, "New pipeline", "merged and applied", L("git-pull-request"))
d.card(c[1], r1, cw, "Warm-up Job", "runs on CPU", L("hard-drive-download"), strong=True)
d.card(c[1], r2, cw, "Model hub", "Hugging Face", L("cloud-download"), outside=True)
d.card(c[2], r1, cw, "Model cache", "one shared disk", L("database"))
d.card(c[2], r2, cw, "Marker file", "pipeline ready", L("flag"))
d.card(c[3], py, cw, "Pods", "offline, read-only", "k8s-pod", logo=True)
d.arrow([(c[0] + cw, r1 + 48), (c[1] - GAP, r1 + 48)])
d.arrow([(c[1] + cw / 2, r2), (c[1] + cw / 2, r1 + CARD_H + GAP)], label="weights", at=(c[1] + cw / 2, 144))
d.arrow([(c[1] + cw, r1 + 30), (c[2] - GAP, r1 + 30)])
d.arrow([(c[1] + cw, r1 + 70), (c[1] + cw + 32, r1 + 70), (c[1] + cw + 32, r2 + 48), (c[2] - GAP, r2 + 48)])
lane = c[3] - 75
d.arrow([(c[2] + cw, r1 + 48), (lane, r1 + 48), (lane, py + 30), (c[3] - GAP, py + 30)])
d.arrow([(c[2] + cw, r2 + 48), (lane, r2 + 48), (lane, py + 66), (c[3] - GAP, py + 66)])
d.pill(lane, (r1 + 48 + py + 30) / 2 - 6, "weights")
d.pill(lane, (py + 66 + r2 + 48) / 2 + 6, "unblocks")
d.save(OUT + "p5-model-path.svg")

print("rest written")
