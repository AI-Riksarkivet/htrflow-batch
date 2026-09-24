"""Predicted page quality (docs: reference/s3-layout).

htrflow's QualityPrediction step scores each page's transcription and its
ALTO template writes the score as ``Page/@PC`` (ALTO's page confidence,
0-1). It is read here, off the file the wrapper parses on upload anyway, so
the number every reader sees is the one in the ALTO a person opens -- and a
resumed page's score is read back the way its size is. This module is the
one place that knows the score's shape.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence

import yaml

#: What the step predicts: bag-of-words F1 against a ground truth.
TARGET = "bow_f1"
#: How many of a volume's worst pages its summary names.
LOWEST = 5
#: Decimals kept in the published files.
DIGITS = 4


def parse_alto_quality(root: ET.Element) -> float | None:
    """The page's ``PC``, or ``None`` when it has none that is a number in
    [0, 1] -- an absent, empty or hand-edited value is no score, never a
    page failure."""
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "Page":
            continue
        try:
            value = float(elem.get("PC", ""))
        except ValueError:
            return None
        return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None
    return None


def qp_model(pipeline_text: str) -> tuple[str | None, str | None]:
    """The QualityPrediction step's Hub repo and revision, as the pipeline
    names them; ``(None, None)`` when it has no such step."""
    try:
        config = yaml.safe_load(pipeline_text)
    except yaml.YAMLError:
        return None, None
    steps = config.get("steps") if isinstance(config, dict) else None
    for step in steps if isinstance(steps, list) else []:
        if (
            isinstance(step, dict)
            and str(step.get("step", "")).lower() == "qualityprediction"
        ):
            settings = step.get("settings")
            ms = settings.get("model_settings") if isinstance(settings, dict) else None
            ms = ms if isinstance(ms, dict) else {}
            model, revision = ms.get("model"), ms.get("revision")
            return (
                model if isinstance(model, str) else None,
                revision if isinstance(revision, str) else None,
            )
    return None, None


def summary(
    scores: Mapping[str, float], pipeline_text: str, canvases: Sequence[str]
) -> dict | None:
    """The volume's quality block. ``canvases`` is the page names in
    ``iiif.json``'s item order, so a lowest page links straight to its
    canvas; ``None`` with no score at all -- nothing is added then."""
    if not scores:
        return None
    model, revision = qp_model(pipeline_text)
    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    index = {name: i for i, name in enumerate(canvases)}
    return {
        "target": TARGET,
        "model": model,
        "revision": revision,
        "mean": round(sum(scores.values()) / len(scores), DIGITS),
        "min": round(ordered[0][1], DIGITS),
        "scored": len(scores),
        "lowest": [
            {"page": name, "quality": round(value, DIGITS), "canvas": index.get(name)}
            for name, value in ordered[:LOWEST]
        ],
    }
