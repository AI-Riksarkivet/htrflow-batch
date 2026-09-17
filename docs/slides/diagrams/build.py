"""The hand-drawn diagrams in the opening of the part 1 deck. Run from the
repository root:

    python3 docs/slides/diagrams/build.py

The rest of the decks' diagrams are in build_rest.py.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, CARD_H, GAP

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets") + os.sep


def L(name):
    return "lucide-" + name


# ---------------------------------------------------------------- rough architecture
d = Diagram(1600, 690)
GH = 176
# row 1: git, then delivery
d.group(24, 16, 1160, GH, "In git", L("git-branch"))
cw = (1160 - 48 - 2 * 56) / 3
xs = [48 + i * (cw + 56) for i in range(3)]
d.card(xs[0], 72, cw, "You", "open a pull request", L("user"))
d.card(xs[1], 72, cw, "Campaigns repo", "campaigns · pipelines", L("git-pull-request"))
d.card(xs[2], 72, cw, "Converter", "checks, renders in CI", L("file-check"))
d.group(1216, 16, 360, GH, "Delivery", L("send"))
d.card(1240, 72, 312, "Apply", "Argo CD or platform", "argo", logo=True)
for i in range(2):
    d.arrow([(xs[i] + cw, 120), (xs[i + 1] - GAP, 120)])
d.arrow([(xs[2] + cw, 120), (1240 - GAP, 120)])

# row 2: the cluster
d.group(24, 256, 1552, GH, "In the cluster", "k8s-node")
cw2 = (1552 - 48 - 4 * 40) / 5
c = [48 + i * (cw2 + 40) for i in range(5)]
mid = [x + cw2 / 2 for x in c]
y2 = 312
d.card(c[0], y2, cw2, "Kyverno", "checks objects", "kyverno", logo=True)
d.card(c[1], y2, cw2, "Kueue", "waits for GPUs", "kueue", logo=True)
d.card(c[2], y2, cw2, "Pods", "one per volume", "k8s-pod", logo=True, strong=True)
d.card(c[3], y2, cw2, "Warm-up", "fills the cache", L("hard-drive-download"))
d.card(c[4], y2, cw2, "Web front", "status · viewer", L("layout-dashboard"))
d.arrow([(1396, 168), (1396, 224), (mid[0] + 60, 224), (mid[0] + 60, y2 - GAP)], label="objects", at=(760, 224))
d.arrow([(c[0] + cw2, y2 + 48), (c[1] - GAP, y2 + 48)])
d.arrow([(c[1] + cw2, y2 + 48), (c[2] - GAP, y2 + 48)])
d.arrow([(c[3], y2 + 48), (c[2] + cw2 + GAP, y2 + 48)])

# row 3: storage, and the outside world
g3, y3, lane = 496, 552, 464
d.group(c[0] - 24, g3, cw2 + 48, GH, "Storage", L("database"))
d.card(c[0], y3, cw2, "S3 bucket", "ALTO · PAGE", L("database"))
d.group(c[2] - 24, g3, 1576 - c[2] + 24, GH, "Outside", L("globe"), outside=True)
d.card(c[2], y3, cw2, "IIIF servers", "the page images", L("images"), outside=True)
d.card(c[3], y3, cw2, "Model hub", "Hugging Face", L("cloud-download"), outside=True)
d.card(c[4], y3, cw2, "Browser", "anyone reading", L("globe"), outside=True)
d.arrow([(mid[2] - 50, y2 + CARD_H), (mid[2] - 50, lane), (mid[0] + 60, lane), (mid[0] + 60, y3 - GAP)],
        label="results", at=(mid[1], lane))
d.arrow([(mid[2] + 50, y3), (mid[2] + 50, y2 + CARD_H + GAP)], label="pages", at=(mid[2] + 50, lane))
d.arrow([(mid[3], y3), (mid[3], y2 + CARD_H + GAP)], label="models", at=(mid[3], lane))
d.arrow([(mid[4], y3), (mid[4], y2 + CARD_H + GAP)], label="reads", at=(mid[4], lane))
d.save(OUT + "part-1-architecture.svg")

# ---------------------------------------------------------------- what is inside what
d = Diagram(1600, 576)
cw = (1552 - 48 - 3 * 56) / 4
c = [48 + i * (cw + 56) for i in range(4)]
mid = [x + cw / 2 for x in c]
# the work: campaign file, Job, Pod, and the pod's two containers
d.group(24, 16, 1552, 304, "The work", L("file-text"))
ry = 72 + (208 - CARD_H) / 2
d.card(c[0], ry, cw, "Campaign file", "one per campaign", L("file-text"))
d.card(c[1], ry, cw, "Job", "one per campaign", "k8s-job", logo=True)
d.card(c[2], ry, cw, "Pod", "one per volume", "k8s-pod", logo=True, strong=True)
d.card(c[3], 72, cw, "Init container", "waits for models", L("container"))
d.card(c[3], 72 + CARD_H + 16, cw, "Container", "wrapper + htrflow", L("container"))
d.arrow([(c[0] + cw, ry + 48), (c[1] - GAP, ry + 48)])
d.arrow([(c[1] + cw, ry + 48), (c[2] - GAP, ry + 48)])
fork = c[2] + cw + 28
d.arrow([(c[2] + cw, ry + 36), (fork, ry + 36), (fork, 120), (c[3] - GAP, 120)])
d.arrow([(c[2] + cw, ry + 60), (fork, ry + 60), (fork, 72 + CARD_H + 16 + 48), (c[3] - GAP, 72 + CARD_H + 16 + 48)])
# the queue: ClusterQueue holds LocalQueues, which hold Workloads
d.group(24, 384, 1552, 176, "The queue — Kueue", "kueue")
d.card(c[0], 440, cw, "ClusterQueue", "the GPU quota", L("layers"))
d.card(c[1], 440, cw, "LocalQueue", "the line a Job joins", L("door-open"))
d.card(c[2], 440, cw, "Workload", "one per Job", "kueue", logo=True)
d.arrow([(c[0] + cw, 488), (c[1] - GAP, 488)])
d.arrow([(c[1] + cw, 488), (c[2] - GAP, 488)])
d.arrow([(mid[2] - 60, 440), (mid[2] - 60, 352), (mid[1], 352), (mid[1], ry + CARD_H + GAP)],
        label="admits", at=((mid[1] + mid[2] - 60) / 2, 352))
d.save(OUT + "part-1-inside-what.svg")

# ---------------------------------------------------------------- where a pod runs
d = Diagram(1600, 432)
d.group(24, 16, 760, 400, "Control plane — decides", L("server"))
a, b, cw = 48, 48 + 330 + 52, 330
r1, r2 = 96, 280
d.card(a, r1, cw, "API server", "holds every object", L("server"))
d.card(a, r2, cw, "Kueue", "decides when", "kueue", logo=True)
d.card(b, r1, cw, "Job controller", "keeps pods running", L("repeat"))
d.card(b, r2, cw, "Scheduler", "decides where", L("calendar-clock"))
lane = a + cw + 26
d.arrow([(a + cw, r1 + 30), (b - GAP, r1 + 30)])
d.arrow([(a + cw, r2 + 48), (lane, r2 + 48), (lane, r1 + 70), (b - GAP, r1 + 70)])
d.arrow([(b + cw / 2, r1 + CARD_H), (b + cw / 2, r2 - GAP)])
for i, gy in enumerate((16, 232)):
    d.group(848, gy, 728, 184, f"Node {i + 1}", "k8s-node")
    for k in range(2):
        d.card(872 + k * 352, gy + 72, 328, "Pod", "archival volume", "k8s-pod", logo=True)
d.arrow([(b + cw, r2 + 48), (872 - GAP, r2 + 48)])
d.arrow([(b + cw, r2 + 20), (816, r2 + 20), (816, 136), (872 - GAP, 136)])
d.save(OUT + "part-1-where-pod-runs.svg")

# ---------------------------------------------------------------- local vs cluster queue
d = Diagram(1440, 424)
for gy, ns in ((16, "transcription"), (232, "research")):
    d.group(24, gy, 760, 176, f"Namespace: {ns}", L("folder"))
    d.card(48, gy + 56, 320, "Campaign Jobs", None, "k8s-job", logo=True)
    d.card(440, gy + 56, 320, "LocalQueue", "the line", L("door-open"))
    d.arrow([(368, gy + 104), (440 - GAP, gy + 104)])
d.card(960, 164, 456, "ClusterQueue", "the GPUs: quota 8", L("layers"), strong=True)
d.arrow([(760, 120), (872, 120), (872, 192), (960 - GAP, 192)])
d.arrow([(760, 336), (872, 336), (872, 232), (960 - GAP, 232)])
d.save(OUT + "part-1-queues.svg")
print("diagrams written")
