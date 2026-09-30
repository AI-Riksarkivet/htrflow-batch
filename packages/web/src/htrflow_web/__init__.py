"""htrflow-web: the web front — the read API plus the site it serves.

docs: docs/superpowers/specs/2026-09-01-indexed-jobs-design.md (D8).

Every /api/v1 route needs a session on the results store, checked with the
results proxy (``results.py``, the ``htrflow-results`` entrypoint), which the
web front also passes ``/results`` through to. The static site itself, the
page that asks for the login, is served to anyone who can reach the port.
docs: docs/superpowers/specs/2026-09-30-results-proxy-login-design.md.
"""

from __future__ import annotations
