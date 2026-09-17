"""The hand-drawn diagrams in the "A possible future: DRA and KAI" deck. Run
from the repository root:

    python3 docs/slides/diagrams/build_future.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, CARD_H, GAP

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets") + os.sep


def L(name):
    return "lucide-" + name


# ---------------------------------------------------------------- DRA: claim a device by what it is
d = Diagram(1600, 320)
cw = (1552 - 3 * 80) / 4
c = [24 + i * (cw + 80) for i in range(4)]
r1, r2 = 24, 200
d.card(c[0], r1, cw, "DeviceClass", "gpu.nvidia.com", L("tag"))
d.card(c[1], r1, cw, "GPU driver", "on every node", "k8s-node", logo=True)
d.card(c[2], r1, cw, "ResourceSlice", "each GPU, described", L("list-checks"))
d.card(c[0], r2, cw, "Claim template", "≥ 40 GB GPU memory", L("file-code"))
d.card(c[1], r2, cw, "ResourceClaim", "one per pod", L("file-text"))
d.card(c[2], r2, cw, "Scheduler", "finds a GPU that fits", L("calendar-clock"), strong=True)
d.card(c[3], r2, cw, "Pod", "runs on that GPU", "k8s-pod", logo=True)
d.arrow([(c[1] + cw, r1 + 48), (c[2] - GAP, r1 + 48)])
d.arrow([(c[0] + cw / 2, r1 + CARD_H), (c[0] + cw / 2, r2 - GAP)])
d.arrow([(c[2] + cw / 2, r1 + CARD_H), (c[2] + cw / 2, r2 - GAP)])
for i in range(3):
    d.arrow([(c[i] + cw, r2 + 48), (c[i + 1] - GAP, r2 + 48)])
d.save(OUT + "future-dra.svg")

# ---------------------------------------------------------------- KAI: team queues that lend and take back
d = Diagram(1600, 590)
d.card(520, 24, 560, "Queue: archive", "the cluster's GPUs", L("layers"))
lw = 560
lx = [120, 1600 - 120 - lw]
leaves = [("Queue: transcription", "quota 8 GPUs · weight 2\nnever more than 16"),
          ("Queue: research", "quota 4 GPUs · weight 1\nnever more than 12")]
boxes = [d.tall(lx[i], 216, lw, t, s, L("users")) for i, (t, s) in enumerate(leaves)]
for i in range(2):
    d.arrow([(800 + (i * 2 - 1) * 120, 120), (800 + (i * 2 - 1) * 120, 168), (lx[i] + lw / 2, 168), (lx[i] + lw / 2, 216 - GAP)], dot=(i == 0))
h = boxes[0][3]
d.arrow([(lx[0] + lw, 216 + h / 2 - 22), (lx[1] - GAP, 216 + h / 2 - 22)], dot=False)
d.arrow([(lx[1], 216 + h / 2 + 22), (lx[0] + lw + GAP, 216 + h / 2 + 22)], dot=False)
d.pill(800, 216 + h / 2, "idle GPUs, by weight")
d.card(lx[0], 216 + h + 48, lw, "Campaign pods", "label kai.scheduler/queue", "k8s-pod", logo=True)
d.card(lx[1], 216 + h + 48, lw, "Campaign pods", "label kai.scheduler/queue", "k8s-pod", logo=True)
for i in range(2):
    d.arrow([(lx[i] + lw / 2, 216 + h + 48), (lx[i] + lw / 2, 216 + h + GAP)], dot=False)
d.save(OUT + "future-kai-queues.svg")

# ---------------------------------------------------------------- a window that is a range
d = Diagram(1600, 252)
d.row(24, [
    ("Starts", "with one pod, as soon\nas one GPU is free", L("play")),
    ("Grows", "up to the window\nas GPUs free up", L("layers"), {"strong": True}),
    ("Gives back", "pods above the minimum\nwhen a team reclaims", L("rotate-ccw")),
    ("Resumes", "the volume, from\nthe bucket", L("database")),
], gap=96)
d.save(OUT + "future-window-range.svg")
# ---------------------------------------------------------------- more volumes per card
d = Diagram(1600, 312)
gw = (1552 - 48) / 2
for i, (label, pods) in enumerate((("Today — one volume per card", 1), ("With KAI fractions — four", 4))):
    gx = 24 + i * (gw + 48)
    d.group(gx, 16, gw, 280, label, L("microchip"))
    d.group(gx + 24, 72, gw - 48, 200, "One GPU · 80 GB", inner=True)
    sw = (gw - 96 - 3 * 16) / 4
    for k in range(4):
        sx = gx + 48 + k * (sw + 16)
        if k < pods:
            d.slot(sx, 128, sw, 120, "Volume", "20 GB")
        else:
            d.slot(sx, 128, sw, 120, "unused", "20 GB", used=False)
d.save(OUT + "future-fractions.svg")

# ---------------------------------------------------------------- whole nodes kept free
d = Diagram(1600, 400)
gw = (1552 - 48) / 2
layouts = (("Spread — the default scheduler", [[1, 0, 1, 0], [1, 0, 1, 0]], "no node has 4 GPUs free"),
           ("Packed — KAI", [[1, 1, 1, 1], [0, 0, 0, 0]], "node 2 fits a 4-GPU job"))
for i, (label, nodes, note) in enumerate(layouts):
    gx = 24 + i * (gw + 48)
    d.group(gx, 16, gw, 368, label, L("layers"))
    nw = (gw - 72) / 2
    for n, used in enumerate(nodes):
        nx = gx + 24 + n * (nw + 24)
        d.group(nx, 72, nw, 240, f"Node {n + 1}", "k8s-node", inner=True)
        sw = (nw - 48 - 16) / 2
        for k, u in enumerate(used):
            r, cix = divmod(k, 2)
            d.slot(nx + 24 + cix * (sw + 16), 128 + r * 80, sw, 64, "volume" if u else "free", used=bool(u))
    d.pill(gx + gw / 2, 344, note)
d.save(OUT + "future-packing.svg")

print("future written")
