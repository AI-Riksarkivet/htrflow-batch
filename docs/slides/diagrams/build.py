"""The hand-drawn diagrams in the part 1 deck. Run from the repository root:

    python3 docs/slides/diagrams/build.py

then embed the font the way scripts/slides.sh does for Mermaid diagrams:

    for f in docs/slides/assets/part-1-{architecture,inside-what,where-pod-runs,queues}.svg; do
      python3 scripts/slides_embed_font.py "$f" docs/slides/theme/fonts/OpenSans-Regular.woff2
    done
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svgkit import Diagram, MAGENTA, MUTED

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets") + os.sep

# ---------------------------------------------------------------- rough architecture
d = Diagram(1280, 560)
d.group(20, 30, 300, 380, "in git")
d.box(45, 80, 250, 70, "you", "open a pull request", "lucide-user", MAGENTA)
d.box(45, 195, 250, 60, "campaigns repo", None, "lucide-git-branch", MAGENTA)
d.box(45, 300, 250, 70, "converter, in CI", "validate · render", "lucide-file-check", MAGENTA)
d.arrow([(170, 150), (170, 193)])
d.arrow([(170, 255), (170, 298)])
d.box(355, 300, 205, 70, "apply", "Argo CD / platform", "argo")
d.arrow([(295, 335), (353, 335)])
d.group(590, 30, 450, 380, "in the cluster")
d.box(610, 80, 200, 70, "Kyverno", "checks the objects", "kyverno")
d.box(610, 190, 200, 70, "Kueue", "waits for GPUs", "kueue")
d.box(610, 300, 200, 70, "campaign pods", "wrapper + htrflow", "k8s-pod")
d.box(830, 80, 195, 70, "web front", "status · viewer", "lucide-layout-dashboard", MAGENTA)
d.box(830, 300, 195, 70, "warm-up", "model cache", "lucide-hard-drive-download", MAGENTA)
d.arrow([(560, 335), (575, 335), (575, 115), (608, 115)])
d.arrow([(710, 150), (710, 188)])
d.arrow([(710, 260), (710, 298)])
d.arrow([(830, 335), (812, 335)], dashed=True, label="models", lx=821, ly=292)
d.group(1070, 30, 195, 380, "external")
d.box(1085, 80, 165, 70, "browser", None, "lucide-globe", MUTED, external=True)
d.box(1085, 300, 165, 70, "model hub", "Hugging Face", "lucide-cloud-download", MUTED, external=True)
d.arrow([(1085, 115), (1027, 115)])
d.arrow([(1085, 335), (1027, 335)])
d.box(600, 470, 220, 70, "IIIF and images", "external servers", "lucide-images", MUTED, external=True)
d.arrow([(680, 470), (680, 372)], label="pages", lx=668, ly=440, anchor="end")
d.cylinder(840, 468, 200, 74, "S3 bucket", "icon-s3-src")
d.arrow([(760, 370), (760, 425), (930, 425), (930, 466)], label="ALTO · PAGE", lx=845, ly=417)
d.save(OUT + "part-1-architecture.svg")

# ---------------------------------------------------------------- what is inside what
d = Diagram(1280, 540)
d.group(20, 30, 420, 380, "the queue — Kueue")
d.box(50, 80, 360, 70, "ClusterQueue", "the GPU quota", "lucide-layers", MAGENTA)
d.box(50, 190, 360, 70, "LocalQueue", "the line a Job joins", "lucide-door-open", MAGENTA)
d.box(50, 300, 360, 70, "Workload", "one per Job", "kueue")
d.arrow([(230, 150), (230, 188)])
d.arrow([(230, 260), (230, 298)])
d.group(500, 30, 760, 500, "the work")
d.box(700, 80, 360, 70, "campaign file", "one per campaign", "lucide-file-text", MAGENTA)
d.box(700, 190, 360, 70, "Job", "one per campaign", "k8s-job")
d.box(700, 300, 360, 70, "Pod", "one per archival volume", "k8s-pod")
d.box(540, 430, 330, 70, "init container", "waits for the models", "lucide-container", MAGENTA)
d.box(890, 430, 340, 70, "container", "wrapper + htrflow", "lucide-container", MAGENTA)
d.arrow([(880, 150), (880, 188)])
d.arrow([(880, 260), (880, 298)])
d.arrow([(820, 370), (705, 428)])
d.arrow([(940, 370), (1060, 428)])
d.arrow([(700, 225), (412, 335)], dashed=True, label="admitted?", lx=560, ly=262)
d.save(OUT + "part-1-inside-what.svg")

# ---------------------------------------------------------------- where a pod runs
d = Diagram(1280, 520)
d.group(20, 30, 700, 470, "control plane — decides")
d.box(50, 90, 290, 70, "API server", "holds every object", "lucide-server", MAGENTA)
d.box(50, 330, 290, 70, "Kueue", "decides when", "kueue")
d.box(390, 210, 300, 70, "Job controller", "keeps the pods running", "lucide-repeat", MAGENTA)
d.box(390, 390, 300, 70, "scheduler", "decides where", "lucide-calendar-clock", MAGENTA)
d.arrow([(340, 125), (365, 125), (365, 230), (388, 230)])
d.arrow([(340, 365), (365, 365), (365, 260), (388, 260)])
d.arrow([(540, 280), (540, 388)])
d.group(780, 30, 480, 210, "node 1")
d.group(780, 290, 480, 210, "node 2")
for gy in (30, 290):
    d.box(810, gy + 70, 200, 90, "pod", "archival volume", "k8s-pod", isize=40)
    d.box(1030, gy + 70, 200, 90, "pod", "archival volume", "k8s-pod", isize=40)
d.arrow([(690, 425), (740, 425), (740, 150), (778, 150)])
d.arrow([(690, 435), (778, 435)])
d.save(OUT + "part-1-where-pod-runs.svg")

# ---------------------------------------------------------------- local vs cluster queue
d = Diagram(1280, 440)
d.group(20, 20, 720, 190, "namespace: transcription")
d.group(20, 240, 720, 190, "namespace: research")
for gy in (20, 240):
    d.box(60, gy + 70, 280, 80, "campaign Jobs", None, "k8s-job", isize=40)
    d.box(420, gy + 70, 280, 80, "LocalQueue", "the line", "lucide-door-open", MAGENTA)
    d.arrow([(340, gy + 110), (418, gy + 110)])
d.box(860, 170, 380, 100, "ClusterQueue", "the GPUs: quota 8", "lucide-layers", MAGENTA, isize=44)
d.arrow([(700, 130), (780, 130), (780, 205), (858, 205)])
d.arrow([(700, 350), (780, 350), (780, 235), (858, 235)])
d.save(OUT + "part-1-queues.svg")
print("diagrams written")
