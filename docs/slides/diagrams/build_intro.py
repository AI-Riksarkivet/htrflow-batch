"""The hand-drawn diagrams in the short "What htrflow-batch adds" deck. Run
from the repository root:

    python3 docs/slides/diagrams/build_intro.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, CARD_H, GAP, MAGENTA, HAIR, MUTED, TINT, PANEL, INK, esc

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets") + os.sep


def L(name):
    return "lucide-" + name


# ---------------------------------------------------------------- the pipeline stays, the campaign is new
d = Diagram(1600, 480)
cw = (1552 - 48 - 2 * 72) / 3
c = [48 + i * (cw + 72) for i in range(3)]
mid = [x + cw / 2 for x in c]
d.group(24, 16, 1552, 200, "htrflow — som förut", L("scroll-text"))
r1 = 72
d.card(c[0], r1, cw, "Pipeline", "hur en sida läses: rader, text", L("scroll-text"))
d.card(c[1], r1, cw, "htrflow", "en mapp med sidor, en GPU", L("microchip"))
d.card(c[2], r1, cw, "Transkription", "en textfil per sida", L("file-text"))
d.arrow([(c[0] + cw, r1 + 48), (c[1] - GAP, r1 + 48)])
d.arrow([(c[1] + cw, r1 + 48), (c[2] - GAP, r1 + 48)])
d.group(24, 264, 1552, 200, "htrflow-batch — nytt", L("layers"))
r2 = 320
d.card(c[0], r2, cw, "Campaign", "vilka volymer, vilken pipeline", L("file-text"), strong=True)
d.card(c[1], r2, cw, "htrflow-batch", "varje volym, på många GPU:er", L("layers"))
d.card(c[2], r2, cw, "Transkriptioner", "en uppsättning per volym", L("database"))
d.arrow([(c[0] + cw, r2 + 48), (c[1] - GAP, r2 + 48)])
d.arrow([(c[1] + cw, r2 + 48), (c[2] - GAP, r2 + 48)])
nx = c[0] + cw - 80
d.arrow([(nx, r1 + CARD_H), (nx, r2 - GAP)], label="pekar ut", at=(nx, 240))
d.save(OUT + "intro-pipeline-campaign.svg")

# ---------------------------------------------------------------- one machine, or many
d = Diagram(1600, 640)
# left: one machine, volumes one after another
d.group(24, 16, 520, 608, "En maskin — htrflow", L("monitor"))
d.card(48, 72, 472, "Din maskin", "en GPU, en mapp med sidor", L("monitor"))
vols = ["R0001203", "R0001204", "R0001205"]
for i, (ref, when) in enumerate(zip(vols, ["först", "sedan", "sedan"])):
    y = 208 + i * (CARD_H + 48)
    d.card(48, y, 472, ref, when, L("images"), strong=(i == 0))
    top = 72 + CARD_H if i == 0 else y - CARD_H - 48 + CARD_H
    d.arrow([(284, top), (284, y - GAP)], dot=(i == 0))
# right: many machines, volumes at the same time
d.group(592, 16, 984, 608, "Många maskiner — htrflow-batch", "k8s-node")
d.card(844, 72, 480, "IIIF / S3", "sidorna, över webben", L("images"), outside=True)
cw = (984 - 48 - 2 * 40) / 3
c = [616 + i * (cw + 40) for i in range(3)]
mid = [x + cw / 2 for x in c]
ry = 296
for i, ref in enumerate(vols):
    d.card(c[i], ry, cw, f"Maskin {i + 1}", ref, "k8s-node", logo=True, strong=True)
d.card(844, 496, 480, "Gemensam lagring (S3)", "alla resultat, från alla maskiner", L("database"))
lane1, lane2 = 232, 440
for i in range(3):
    d.arrow([(1084, 72 + CARD_H), (1084, lane1), (mid[i], lane1), (mid[i], ry - GAP)], dot=(i == 0))
    d.arrow([(mid[i], ry + CARD_H), (mid[i], lane2), (1084, lane2), (1084, 496 - GAP)], dot=False)
d.pill(1084, (72 + CARD_H + lane1) / 2, "sidor")
d.pill(1084, (lane2 + 496) / 2, "resultat")
d.save(OUT + "intro-one-or-many.svg")

# ---------------------------------------------------------------- Kubernetes runs it, Kueue decides when
d = Diagram(1600, 540)
d.group(24, 16, 440, 500, "Campaigns som väntar", L("list-ordered"))
waiting = [("Campaign B", "behöver 4 GPU:er"), ("Campaign C", "behöver 1 GPU"), ("Campaign D", "behöver 2 GPU:er")]
ys = [84 + i * (CARD_H + 48) for i in range(3)]
for (title, sub), y in zip(waiting, ys):
    d.card(48, y, 392, title, sub, L("clock"), strong=(title == "Campaign C"))
kx, ky, kw = 560, 228, 400
d.card(kx, ky, kw, "Kueue", "kön: vem som startar härnäst", "kueue", logo=True, strong=True)
trunk = 508
for i, y in enumerate(ys):
    d.arrow([(440, y + 48), (trunk, y + 48), (trunk, ky + 48), (kx - GAP, ky + 48)], dot=(i == 0))
gx, gw = 1040, 536
d.group(gx, 16, gw, 500, "Kubernetes — klustret", "kubernetes")
sw, sh, sg = 150, 84, 16
sx = [gx + 24 + i * (sw + sg) for i in range(3)]
sy = [84, 84 + sh + sg]
used = [("GPU 1", "Campaign A"), ("GPU 2", "Campaign A"), ("GPU 3", "Campaign A"), ("GPU 4", "ledig: C startar"), ("GPU 5", "ledig"), ("GPU 6", "Campaign A")]
for k, (title, sub) in enumerate(used):
    row, col = divmod(k, 3)
    d.slot(sx[col], sy[row], sw, sh, title, sub, used=not sub.startswith("ledig"))
d.card(gx + 24, 300, gw - 48, "Maskiner, som en dator", "en volym hamnar där en GPU är ledig", "k8s-node", logo=True)
d.card(gx + 24, 300 + CARD_H + 20, gw - 48, "En maskin går sönder?", "volymen flyttar, och fortsätter", L("refresh-cw"))
fy = sy[1] + sh / 2
d.arrow([(kx + kw, ky + 48), (1004, ky + 48), (1004, fy), (sx[0] - GAP, fy)])
d.save(OUT + "intro-kueue.svg")

# ---------------------------------------------------------------- the viewer, schematically
d = Diagram(800, 560)
d.group(24, 16, 752, 528, "Viewern", L("book-open"))
# the page image, with every line outlined
px, py, pw, ph = 48, 72, 392, 448
d.front.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="10" fill="#fbf7ee" stroke="{HAIR}" stroke-width="1.5" filter="url(#sh)"/>')
lines = [0.86, 0.78, 0.92, 0.7, 0.88, 0.6, 0.82]
tx, ty, tw = 480, 72, 272
d.front.append(f'<rect x="{tx}" y="{ty}" width="{tw}" height="{ph}" rx="10" fill="#ffffff" stroke="{HAIR}" stroke-width="1.5" filter="url(#sh)"/>')
for k, frac in enumerate(lines):
    ly = py + 28 + k * 54
    lw = (pw - 56) * frac
    strong = k == 2
    stroke = MAGENTA if strong else "#d9a9bb"
    fill = TINT if strong else "none"
    d.front.append(f'<rect x="{px + 28}" y="{ly}" width="{lw}" height="34" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{3 if strong else 2}"/>')
    # a hand-written line, as a wobbly stroke as long as its box
    n = int((lw - 24) // 24)
    d.front.append(f'<path d="M{px + 40},{ly + 20} q12,-14 24,0' + " t24,0" * (n - 1) + '" '
                   f'fill="none" stroke="#5a4634" stroke-width="2.2" stroke-linecap="round" opacity="0.85"/>')
    # the transcribed text beside it
    bw = (tw - 48) * frac
    d.front.append(f'<rect x="{tx + 24}" y="{ly + 10}" width="{bw}" height="14" rx="7" fill="{MAGENTA if strong else "#cfc5c9"}"/>')
    if strong:
        d.arrow([(px + 28 + lw + GAP, ly + 17), (tx - GAP, ly + 17)], dot=False)
d.front.append(f'<text x="{px + pw / 2}" y="{py + ph - 14}" text-anchor="middle" font-size="18" font-weight="600" fill="{MUTED}">sidan, varje rad markerad</text>')
d.front.append(f'<text x="{tx + tw / 2}" y="{ty + ph - 14}" text-anchor="middle" font-size="18" font-weight="600" fill="{MUTED}">dess text</text>')
d.save(OUT + "intro-viewer.svg")
print("intro diagrams written")

# ---------------------------------------------------------------- grov arkitektur (the first deck's architecture, in Swedish)
d = Diagram(1600, 690)
GH = 176
d.group(24, 16, 1160, GH, "I git", L("git-branch"))
cw = (1160 - 48 - 2 * 56) / 3
xs = [48 + i * (cw + 56) for i in range(3)]
d.card(xs[0], 72, cw, "Du", "öppnar en pull request", L("user"))
d.card(xs[1], 72, cw, "Campaign-repot", "campaigns · pipelines", L("git-pull-request"))
d.card(xs[2], 72, cw, "Converter", "granskar, renderar i CI", L("file-check"))
d.group(1216, 16, 360, GH, "Leverans", L("send"))
d.card(1240, 72, 312, "Apply", "Argo CD eller drift", "argo", logo=True)
for i in range(2):
    d.arrow([(xs[i] + cw, 120), (xs[i + 1] - GAP, 120)])
d.arrow([(xs[2] + cw, 120), (1240 - GAP, 120)])
d.group(24, 256, 1552, GH, "I klustret", "k8s-node")
cw2 = (1552 - 48 - 4 * 40) / 5
c = [48 + i * (cw2 + 40) for i in range(5)]
mid = [x + cw2 / 2 for x in c]
y2 = 312
d.card(c[0], y2, cw2, "Kyverno", "granskar objekt", "kyverno", logo=True)
d.card(c[1], y2, cw2, "Kueue", "väntar på GPU", "kueue", logo=True)
d.card(c[2], y2, cw2, "Pods", "en per volym", "k8s-pod", logo=True, strong=True)
d.card(c[3], y2, cw2, "Warm-up", "fyller cachen", L("hard-drive-download"))
d.card(c[4], y2, cw2, "Webbfront", "status · viewer", L("layout-dashboard"))
d.arrow([(1396, 168), (1396, 224), (mid[0] + 60, 224), (mid[0] + 60, y2 - GAP)], label="objekt", at=(760, 224))
d.arrow([(c[0] + cw2, y2 + 48), (c[1] - GAP, y2 + 48)])
d.arrow([(c[1] + cw2, y2 + 48), (c[2] - GAP, y2 + 48)])
d.arrow([(c[3], y2 + 48), (c[2] + cw2 + GAP, y2 + 48)])
g3, y3 = 496, 552
lane_r, lane_h, lane = 448, 476, 464
cx, cw3 = c[1] - 8, cw2 + 8
d.group(c[0] - 24, g3, cx + cw3 + 16 - (c[0] - 24), GH, "Lagring", L("database"))
d.card(c[0], y3, cw2, "S3-bucket", "ALTO · kvalitet", L("database"))
d.card(cx, y3, cw3, "Bildcache", "valfri, privat", L("database"))
d.group(c[2] - 16, g3, 1576 - c[2] + 16, GH, "Utanför", L("globe"), outside=True)
d.card(c[2], y3, cw2, "IIIF-servrar", "sidbilderna", L("images"), outside=True)
d.card(c[3], y3, cw2, "Modellhub", "Hugging Face", L("cloud-download"), outside=True)
d.card(c[4], y3, cw2, "Webbläsare", "vem som helst", L("globe"), outside=True)
d.arrow([(mid[2] - 80, y2 + CARD_H), (mid[2] - 80, lane_r), (mid[0] + 60, lane_r), (mid[0] + 60, y3 - GAP)],
        label="resultat", at=(mid[0] + 220, lane_r))
d.arrow([(mid[1], y3), (mid[1], lane_h), (mid[2] + 10, lane_h), (mid[2] + 10, y2 + CARD_H + GAP)],
        label="träff", at=(mid[1] + 170, lane_h))
d.arrow([(mid[2] + 90, y3), (mid[2] + 90, y2 + CARD_H + GAP)], label="miss", at=(mid[2] + 90, lane))
d.arrow([(mid[3], y3), (mid[3], y2 + CARD_H + GAP)], label="modeller", at=(mid[3], lane))
d.arrow([(mid[4], y3), (mid[4], y2 + CARD_H + GAP)], label="läser", at=(mid[4], lane))
d.save(OUT + "intro-architecture.svg")
print("architecture written")

# ---------------------------------------------------------------- htrflow: volym + pipeline in, transkription ut
d = Diagram(824, 424)
d.tall(24, 24, 200, "Volym", "sidbilderna", L("images"))
d.tall(24, 224, 200, "Pipeline", "receptet", L("scroll-text"))
d.tall(300, 124, 200, "htrflow", "programvaran", L("microchip"), strong=True)
d.tall(576, 124, 224, "Transkription", "ALTO · PAGE", L("file-text"))
d.arrow([(224, 112), (262, 112), (262, 212), (300 - GAP, 212)])
d.arrow([(224, 312), (262, 312), (262, 212), (300 - GAP, 212)], head=False)
d.arrow([(500, 212), (576 - GAP, 212)])
d.save(OUT + "intro-htrflow.svg")
print("htrflow written")
