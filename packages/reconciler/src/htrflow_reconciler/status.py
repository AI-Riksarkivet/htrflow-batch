"""Per-volume status derivation — the spec §6 three-way join, as a pure
function so every row of the table is unit-testable."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from pydantic import BaseModel, ConfigDict

from .models import Volume


class JobState(BaseModel):
    """A snapshot of one k8s Job as the reconciler sees it.

    ``failed`` means the Job reached a terminal ``Failed`` condition (its
    ``backoffLimit`` is exhausted) — NOT merely that some pod failed while
    another attempt is still retrying. A Job with a failed pod but remaining
    retries is ``active``, not ``failed``.

    ``succeeded`` is the terminal ``Complete`` condition, the mirror of
    ``failed``: neither state occupies a slot in the submission window.
    """

    model_config = ConfigDict(frozen=True)

    active: bool
    failed: bool
    succeeded: bool = False
    exit_code: int | None = None


def job_name(pipeline_id: str, volume_id: str) -> str:
    """Deterministic, DNS-1123-safe Job name for a (pipeline, volume) pair.

    The sanitized prefix alone is ambiguous — ``("a-b", "c")`` and
    ``("a", "b-c")`` flatten to the same string — so the name ALWAYS carries an
    8-char digest over the pair, hashed with a separator that cannot occur in
    either field. The prefix is trimmed (and any trailing hyphen dropped) so the
    result stays ≤63 chars and both starts and ends alphanumeric.
    """
    digest = hashlib.sha256(f"{pipeline_id}\x00{volume_id}".encode()).hexdigest()[:8]
    raw = f"htr-{pipeline_id}-{volume_id}".lower()
    prefix = re.sub(r"[^a-z0-9-]", "-", raw)[:54].rstrip("-")
    return f"{prefix}-{digest}"


def derive(
    volume: Volume,
    pipeline_id: str,
    done: Mapping[str, str] | AbstractSet[str],
    jobs: dict[str, JobState],
    attempts: dict[str, int],
    attempt_cap: int,
) -> str:
    """The spec §6 three-way join: done-set first, then the Job snapshot.

    A job whose Complete condition has landed but whose ``manifest.json`` is not
    yet visible in S3 reads as ``queued`` — the done-set is the authority, so
    succeeded jobs show queued for the moment it takes the manifest to appear.
    """
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
