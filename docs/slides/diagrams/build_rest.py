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

# ---------------------------------------------------------------- part 1: htrflow in a pod
d = Diagram(1600, 744)
cw = (1552 - 48 - 2 * 96) / 3
c = [48 + i * (cw + 96) for i in range(3)]


def stage(gy, gh, label, icon, cards):
    d.group(24, gy, 1552, gh, label, icon)
    for i, (t, s, ic, o) in enumerate(cards):
        d.card(c[i], gy + 56, cw, t, s, ic, **o)
        if i:
            d.arrow([(c[i - 1] + cw, gy + 104), (c[i] - GAP, gy + 104)])


stage(16, 176, "Get ready", L("hard-drive-download"), [
    ("Wait for models", "in the cache", L("hard-drive-download"), {}),
    ("Read the manifest", "or the image list", L("file-text"), {}),
    ("Resume", "skip pages already done", L("list-checks"), {})])
stage(240, 248, "Page by page", L("repeat"), [
    ("Fetch the page", "one at a time", L("download"), {}),
    ("htrflow", "runs your pipeline", L("scroll-text"), {"strong": True}),
    ("Upload", "PAGE and ALTO", L("upload"), {})])
stage(536, 176, "Finish", L("flag"), [
    ("Verify", "every page accounted for", L("search-check"), {}),
    ("Publish", "manifest.json last", L("cloud-upload"), {}),
    ("Exit", "the GPU is free", L("power"), {})])
loop = 240 + 56 + CARD_H + 40
d.arrow([(c[2] + cw / 2, 240 + 56 + CARD_H), (c[2] + cw / 2, loop), (c[0] + cw / 2, loop), (c[0] + cw / 2, 240 + 56 + CARD_H + GAP)],
        label="next page", at=(c[1] + cw / 2, loop))
d.arrow([(800, 192), (800, 240 - GAP)], dot=False)
d.arrow([(800, 488), (800, 536 - GAP)], dot=False)
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

# ---------------------------------------------------------------- part 1: cohort, queues, flavors, nodes
d = Diagram(1600, 584)
cw = (1552 - 2 * 56) / 3
col = [24 + i * (cw + 56) for i in range(3)]
mid = [x + cw / 2 for x in col]
d.group(24, 16, 1552, 176, "Cohort — the archive", L("users"))
d.card(120, 72, 560, "ClusterQueue: transcription", "quota 8 large · 4 small", L("layers"))
d.card(920, 72, 560, "ClusterQueue: research", "quota 4 small · 4 interruptible", L("layers"))
d.arrow([(680, 96), (920 - GAP, 96)], dot=False)
d.arrow([(920, 144), (680 + GAP, 144)], dot=False)
d.pill(800, 120, "lend idle quota")
fy, lane = 296, 244
for i, (name, label) in enumerate((("large", "gpu=large"), ("small", "gpu=small"), ("interruptible", "spot=true"))):
    d.card(col[i], fy, cw, f"Flavor: {name}", f"nodes labelled {label}", L("tag"))
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
