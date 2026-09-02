"""htrflow-web: the web front — the read API plus the site it serves.

docs: docs/superpowers/specs/2026-09-01-indexed-jobs-design.md (D8).

No auth here (D8's precondition for T03): the whole front — the campaign
browser, Universal Viewer and /api/v1 alike — is unauthenticated until T03
puts a real API/auth layer in front of it (design doc Non-goals).
"""

from __future__ import annotations
