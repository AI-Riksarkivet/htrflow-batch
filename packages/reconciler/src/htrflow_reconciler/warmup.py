"""Render the warm-up Job for one pipeline as JSON — the manual path.

The reconciler warms every pipeline it sees in the campaigns repo on its own;
this exists for pipelines that only live in the chart's ``values.pipelines``
(the PoC example Job), so that path shares the one Job spec instead of a
hand-written YAML that drifts::

    python -m htrflow_reconciler.warmup --pipeline demo-v1 --image <ref> \\
        | kubectl apply -f -
"""

from __future__ import annotations

import argparse
import json
import sys

from .jobspec import ReconcilerConfig, build_warmup_job
from .models import PipelineSpec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="htrflow_reconciler.warmup", description=__doc__)
    ap.add_argument("--pipeline", required=True, help="pipeline id (ConfigMap suffix)")
    ap.add_argument(
        "--image", required=True, help="htrflow-batch image ref to warm with"
    )
    ap.add_argument("--namespace", default="htr-batch")
    ap.add_argument("--data-pvc", default="htr-test-data")
    args = ap.parse_args(argv)
    spec = PipelineSpec(
        id=args.pipeline, image=args.image, steps_yaml="", steps_sha256=""
    )
    cfg = ReconcilerConfig(
        public_results_base="", namespace=args.namespace, data_pvc=args.data_pvc
    )
    json.dump(build_warmup_job(spec, cfg), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
