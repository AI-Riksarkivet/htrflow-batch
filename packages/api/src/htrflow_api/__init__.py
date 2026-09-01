"""htrflow-api: read-only /api/v1/jobs projection of Indexed Jobs.

docs: docs/superpowers/specs/2026-09-01-indexed-jobs-design.md (D8).

No auth here (D8's precondition for T03): the service is meant to sit
behind the viewer nginx on a cluster-internal path, unauthenticated until
T03 adds a real API/auth layer in front of it (design doc Non-goals).
"""

from __future__ import annotations
