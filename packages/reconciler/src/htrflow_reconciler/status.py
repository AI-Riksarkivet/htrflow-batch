"""Per-volume status derivation — the spec §6 three-way join, as a pure
function so every row of the table is unit-testable."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from pydantic import BaseModel, ConfigDict

from .attempts import Attempt
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
    #: Reason of the terminal ``Failed`` condition (``PodFailurePolicy``,
    #: ``DeadlineExceeded``, ``BackoffLimitExceeded``). The pod may already be
    #: gone when the reconciler looks, so this is the verdict's fallback.
    reason: str | None = None
    #: ``metadata.deletionTimestamp``: a Foreground delete in progress. The
    #: Job stays listed (and Failed) until its pod is gone.
    deletion_timestamp: str | None = None

    @property
    def deleting(self) -> bool:
        return self.deletion_timestamp is not None

    @property
    def in_flight(self) -> bool:
        """Occupies a submission-window slot: pending/running, or Terminating
        (its pod may still hold the GPU). Terminal Jobs do not."""
        return self.deleting or not (self.failed or self.succeeded)


#: Failed-condition reasons that mean the wrapper itself said "permanent":
#: the ``podFailurePolicy`` FailJob rule fires on exit 13 only.
_PERMANENT_REASONS = frozenset({"PodFailurePolicy"})


def is_permanent(job: JobState) -> bool:
    if job.exit_code is not None:
        return job.exit_code == 13
    return job.reason in _PERMANENT_REASONS


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
    jobs: Mapping[str, JobState],
    attempts: Mapping[str, Attempt],
    attempt_cap: int,
) -> str:
    """The spec §6 three-way join: done-set first, then the persisted
    terminal verdict, then the Job snapshot.

    A job whose Complete condition has landed but whose ``manifest.json`` is not
    yet visible in S3 reads as ``queued`` — the done-set is the authority, so
    succeeded jobs show queued for the moment it takes the manifest to appear.

    A terminal record (R1) is sticky whether or not the Job still exists:
    Jobs are TTL-reaped after 24h, and without the record a capped or exit-13
    volume would read ``pending`` and burn a GPU run every day forever.
    """
    if volume.id in done:
        return "done"
    record = attempts.get(volume.id, Attempt())
    if record.terminal:
        return "needs-attention"
    job = jobs.get(job_name(pipeline_id, volume.id))
    if job is None:
        return "pending"
    if job.deleting:
        return "deleting"
    if job.failed:
        if is_permanent(job) or record.n >= attempt_cap:
            return "needs-attention"
        return "retry"
    if job.active:
        return "running"
    return "queued"
