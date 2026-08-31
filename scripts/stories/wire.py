#!/usr/bin/env python3
"""Regenerate the story tables on docs/features/*/feature.md, the epic table on
docs/features/index.md and the "Features & Stories" nav block in zensical.toml
from the ordered id lists below. Adding a story = new file + its id in a list here.
Ids are stable and never renumbered. Run from anywhere: python3 scripts/stories/wire.py
"""

# ruff: noqa: E501  (prose for the feature pages lives here)

import glob
import os
import re
import textwrap
from pathlib import Path

W = str(Path(__file__).resolve().parents[2]) + "/"
F = W + "docs/features/"


def meta(path):
    t = open(path).read()
    fm = t.split("---")[1]
    return dict(re.findall(r"^(\w+):\s*(.*)$", fm, re.M))


files = {}
for p in glob.glob(F + "*/stories/*.md"):
    files[os.path.basename(p)[:3]] = (os.path.relpath(p, F), meta(p)["title"])


def rows(ids, base):
    out = ["| Id | Story |", "|---|---|"]
    for i in ids:
        rel, title = files[i]
        out.append(f"| [{i}](stories/{os.path.basename(rel)}) | {title} |")
    return "\n".join(out)


B_impl = [
    "B01",
    "B02",
    "B03",
    "B04",
    "B06",
    "B07",
    "B08",
    "B09",
    "B21",
    "B23",
    "B24",
    "B25",
    "B27",
    "B28",
    "B29",
    "B30",
    "B31",
    "B44",
    "B45",
]
C_impl = ["B05", "B22", "B32"]
C_open = [f"C{i:02d}" for i in range(4, 11)]  # C01–C03 retired: not a tool for archivists
B_prod = [
    "B10",
    "B11",
    "B12",
    "B13",
    "B14",
    "B26",
    "B37",
    "B42",
    "B41",
    "B43",
    "B36",
    "B35",
    "B34",
    "B40",
    "B59",
    "B60",
    "B38",
    "B46",
    "B47",
    "B48",
    "B49",
    "B50",
    "B51",
    "B52",
    "B53",
    "B54",
    "B55",
    "B56",
    "B57",
    "B58",
    "B33",
    "B15",
    "B16",
]
B_after = ["B17", "B18", "B19", "B20"]
U_impl = ["U01", "U02", "U03", "U08"]
U_open = ["U04", "U05", "U06", "U07", "U09"]
S_all = [f"S{i:02d}" for i in range(2, 15)]
allids = set(B_impl + B_prod + B_after + U_impl + U_open + S_all + C_impl + C_open)
missing = set(files) - allids
extra = allids - set(files)
assert not missing and not extra, (missing, extra)

# batch feature page
p = F + "batch-kueue-helm/feature.md"
s = open(p).read()
head = s[: s.index("## Stories")]
order = (
    "The productionalisation path is deliberately **govern the inputs → DEV via Argo CD → policy on → promotion pipeline → audit → production**: "
    "B11 makes the campaigns repo a reviewed, protected source of truth (the GitOps side of B04); B12 puts the platform itself under GitOps with Argo CD on the DEV cluster; "
    'B13 turns on the "only our images run" control there and B14 hardens it; B26 puts dependency updates on a routine; B37 and its per-image stories (B41–B45, U08) bring every image — the hand-built GPU wrapper included — under the same CI build, provenance and scan; '
    "B36 makes one local registry the only source of images and B35 puts the models in it as signed artifacts, closing the last internet egress; B34 adds Kargo so a release moves dev → staging → prod only by verified promotion; "
    "B40, B59 and B60 make the system observable in Grafana; B38 and the per-diagram stories (B46–B53, U09) give every part a picture; the per-page stories (B54–B57) add the pages the new machinery needs and B58 keeps every page honest from then on; "
    "B33 then audits the result independently and B15 is the first production promotion. B16 (the measured archive-scale run) needs B10, B11 and B15. B17 can be done any time; B18/B19 as operations and the B16 numbers demand; B20 when feature #2770 lands."
)
s = (
    head
    + f"""## Stories

Story ids are stable identifiers, not a sequence: a number is never reused
or renumbered, and a story added later simply takes the next free number.
Each story is one deliverable; where a story is naturally a list that will
grow (images, diagrams, documentation pages, dashboards) there is one story
per item and a small parent story for the list itself. Reading order is the
tables below.

### Implemented in the repository — awaiting acceptance

{rows(B_impl, "")}

### Not started — productionalisation, in order

{rows(B_prod, "")}

### Not started — after production

{rows(B_after, "")}

{textwrap.fill(order, 76)}
"""
)
open(p, "w").write(s)

# uv4 feature page
p = F + "uv4-linux/feature.md"
s = open(p).read()
head = s[: s.index("## Stories")]
s = (
    head
    + f"""## Stories

### Implemented in the repository — awaiting acceptance

{rows(U_impl, "")}

### Not started

{rows(U_open, "")}

U06 is expected to be superseded by the Search feature's S08 (IIIF Content
Search backed by the index). Story ids are stable
identifiers, never renumbered.
"""
)
open(p, "w").write(s)

# status page feature
p = F + "campaign-status-page/feature.md"
s = open(p).read()
head = s[: s.index("## Stories")]
s = (
    head
    + f"""## Stories

### Implemented in the repository — awaiting acceptance

{rows(C_impl, "")}

### Not started

{rows(C_open, "")}

B05, B22 and B32 were created under the Batch feature and moved here; they
keep their ids (ids are stable, never renumbered). Related stories
elsewhere: S07 (a search page in the same SPA — Search's deliverable), U05
(the address the page is served at), B20 (the quality score shown per page).
"""
)
open(p, "w").write(s)

# index
p = F + "index.md"
s = open(p).read()
desc = {
    "batch": (
        "Batch using Kueue+Helm chart",
        "batch-kueue-helm",
        "#2800",
        "Transcribe whole archive volumes unattended on the GPU cluster, with results appearing in S3 as they are produced and a web page that shows progress",
        f"{len(B_impl) + len(B_prod) + len(B_after)} ({len(B_impl)} built, {len(B_prod) + len(B_after)} not started)",
    ),
    "status": (
        "Campaigns status page",
        "campaign-status-page",
        "#2923",
        "The read-only status page for the data scientists running campaigns — every campaign and volume, live logs, links into the viewer, at archive scale",
        f"{len(C_impl) + len(C_open)} ({len(C_impl)} built, {len(C_open)} not started)",
    ),
    "uv4": (
        "UV4 linux",
        "uv4-linux",
        "#2801",
        "The Riksarkivet Universal Viewer built and run on Linux so anyone can open a transcribed volume from S3 in the browser, with the text next to the page image",
        f"{len(U_impl) + len(U_open)} ({len(U_impl)} built, {len(U_open)} not started)",
    ),
    "search": (
        "Search — Solr",
        "search-solr",
        "#2811",
        "Make every transcribed line findable across all volumes: a Solr index fed automatically from the results bucket, a search service, and a search page that opens the hit in the viewer with the line highlighted",
        f"{len(S_all)} draft, unreviewed — not in Azure yet",
    ),
}
table = (
    "| Feature | Azure | What it is, in one sentence | Stories |\n|---|---|---|---|\n"
    + "".join(
        f"| [{n}]({d}/feature.md) | {az} | {what} | {cnt} |\n"
        for n, d, az, what, cnt in [
            desc[k] for k in ("batch", "status", "uv4", "search")
        ]
    )
)
s = re.sub(
    r"\| Feature \| Azure \| What it is, in one sentence \| Stories \|\n(\|.*\n)+",
    table,
    s,
)
s = s.replace(
    """The other features under the epic — *Quality prediction step* (#2770) and
*Solr* (#2811) — are tracked in their own repositories; where a story here
depends on them, it says so.""",
    """The remaining feature under the epic — *Quality prediction step* (#2770)
— is tracked in the `htrflow` repository; where a story here depends on it,
it says so.""",
)
s = s.replace(
    """├── uv4-linux/
│   ├── feature.md                 ← Feature #2801 "UV4 linux"
│   └── stories/
│       └── U01-…                  ← one Product Backlog Item each""",
    """├── uv4-linux/
│   ├── feature.md                 ← Feature #2801 "UV4 linux"
│   └── stories/U01-…              ← one Product Backlog Item each
├── campaign-status-page/
│   ├── feature.md                 ← Feature #2923 "HTRflow Campaigns status page"
│   └── stories/C01-…  (+ B05, B22, B32 moved here)
└── search-solr/
    ├── feature.md                 ← Feature #2811 "Solr"
    └── stories/S02-…""",
)
s = s.replace(
    """└── uv4-linux/
    ├── feature.md                 ← Feature #2801 "UV4 linux"
    └── stories/
        └── U01-…                  ← one Product Backlog Item each""",
    """├── uv4-linux/
│   ├── feature.md                 ← Feature #2801 "UV4 linux"
│   └── stories/U01-…              ← one Product Backlog Item each
├── campaign-status-page/
│   ├── feature.md                 ← Feature #2923 "HTRflow Campaigns status page"
│   └── stories/C01-…  (+ B05, B22, B32 moved here)
└── search-solr/
    ├── feature.md                 ← Feature #2811 "Solr"
    └── stories/S02-…""",
)
open(p, "w").write(s)


# nav
def navrows(ids, folder):
    return "".join(
        f'      {{"{i} {files[i][1] if len(files[i][1]) <= 48 else files[i][1][:45] + "…"}" = "features/{folder}/stories/{os.path.basename(files[i][0])}"}},\n'
        for i in ids
    )


nav = (
    """  { "Features & Stories" = [
    {"Overview" = "features/index.md"},
    {"Batch using Kueue+Helm chart" = [
      {"Feature #2800" = "features/batch-kueue-helm/feature.md"},
"""
    + navrows(B_impl + B_prod + B_after, "batch-kueue-helm")
    + """    ]},
    {"UV4 linux" = [
      {"Feature #2801" = "features/uv4-linux/feature.md"},
"""
    + navrows(U_impl + U_open, "uv4-linux")
    + """    ]},
    {"Campaigns status page" = [
      {"Feature #2923" = "features/campaign-status-page/feature.md"},
"""
    + navrows(C_impl + C_open, "campaign-status-page")
    + """    ]},
    {"Search — Solr" = [
      {"Feature #2811" = "features/search-solr/feature.md"},
"""
    + navrows(S_all, "search-solr")
    + """    ]},
  ]},
"""
)
p = W + "zensical.toml"
s = open(p).read()
a = s.index('  { "Features & Stories" = [')
b = s.index('  { "How it Works" = [')
s = s[:a] + nav + s[b:]
open(p, "w").write(s)

# U06 cross-ref to S08
p = F + "uv4-linux/stories/U06-search-inside-a-volume.md"
s = open(p).read()
if "S08" not in s:
    s = s.replace(
        "## Done when",
        "Expected to be superseded by [S08](../../search-solr/stories/S08-iiif-content-search.md) in the Search feature, which backs the same panel with the cross-volume index.\n\n## Done when",
    )
    open(p, "w").write(s)
print("wired", len(files), "stories")
