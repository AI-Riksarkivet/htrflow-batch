"""htrflow-converter: campaign/pipeline YAML -> manifests (B63).

Spec: docs/superpowers/specs/2026-09-01-indexed-jobs-design.md.
"""

from .models import Campaign, ConverterConfig, Pipeline, Volume
from .parse import ValidationError, load

__all__ = [
    "Campaign",
    "ConverterConfig",
    "Pipeline",
    "Volume",
    "ValidationError",
    "load",
]
