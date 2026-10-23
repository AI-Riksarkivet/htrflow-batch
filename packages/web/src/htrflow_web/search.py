"""/search on the web front's origin, passed through to the Solr core the
demo campaigns' pages are indexed in (htr-demo-campaigns, solr-search
branch). The SPA's search page asks this origin; Solr answers only its
``select`` handler, with only the parameters a search page needs — the
update handlers, the admin API and every query-time knob below them stay
unreachable behind this route. Read-only and behind the same session check
as /api/v1: the transcripts are what the account may read anyway, and Solr
has no accounts of its own."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response

_LOG = logging.getLogger("htrflow_web.search")

#: What the search page may send. ``wt`` is forced to json, and anything
#: not listed is dropped — an unknown parameter is never the reader's
#: mistake worth a 400 over, it is simply not part of this route.
_ALLOWED = {
    "q",
    "fq",
    "fl",
    "rows",
    "start",
    "sort",
    "hl",
    "hl.fl",
    "hl.snippets",
    "hl.fragsize",
    # Legacy faceting, as far as the search page's pipeline filter goes:
    # field names only, no interval/range/query facets (facet.query would be
    # a second, unvalidated query string).
    "facet",
    "facet.field",
    "facet.mincount",
}

#: One page of hits is a reading aid, never an export: the bucket is the
#: export, and it is what /results is for.
_MAX_ROWS = 50

_TIMEOUT = httpx.Timeout(10.0, read=30.0)


def search_route(
    app: FastAPI,
    solr_url: str,
    session: Callable,
    client: httpx.AsyncClient | None = None,
) -> None:
    """GET /search/select -> {solr_url}/select. `session` is the app's own
    current_session dependency; `client` is injectable so a test never
    touches Solr."""
    base = solr_url.rstrip("/")
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    @app.get("/search/select")
    async def select(request: Request, _=Depends(session)) -> Response:
        params: list[tuple[str, str]] = []
        for key, value in request.query_params.multi_items():
            if key not in _ALLOWED:
                continue
            if key == "rows":
                try:
                    value = str(min(int(value), _MAX_ROWS))
                except ValueError:
                    continue
            params.append((key, value))
        params.append(("wt", "json"))
        try:
            answer = await client.get(f"{base}/select", params=params)
        except httpx.HTTPError as e:
            _LOG.warning("solr select failed: %s", e)
            raise HTTPException(
                status_code=502, detail="the search service did not answer"
            ) from e
        return Response(
            answer.content,
            status_code=answer.status_code,
            media_type=answer.headers.get("content-type", "application/json"),
        )
