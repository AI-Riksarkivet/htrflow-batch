"""Per-volume status derivation — the spec §6 three-way join, as a pure
function so every row of the table is unit-testable."""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict

from .models import Volume


class JobState(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: bool
    failed: bool
    exit_code: int | None = None


def job_name(pipeline_id: str, volume_id: str) -> str:
    raw = f"htr-{pipeline_id}-{volume_id}".lower()
    safe = re.sub(r"[^a-z0-9-]", "-", raw)
    if len(safe) <= 63:
        return safe
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{safe[:54]}-{digest}"


def derive(
    volume: Volume,
    pipeline_id: str,
    done: set[str],
    jobs: dict[str, JobState],
    attempts: dict[str, int],
    attempt_cap: int,
) -> str:
    if volume.id in done:
        return "done"
    job = jobs.get(job_name(pipeline_id, volume.id))
    if job is None:
        return "pending"
    if job.failed:
        if job.exit_code == 13 or attempts.get(volume.id, 0) >= attempt_cap:
            return "needs-attention"
        return "retry"
    if job.active:
        return "running"
    return "queued"
