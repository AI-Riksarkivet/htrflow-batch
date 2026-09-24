# Setup

Clone the repository, then from its root:

```bash
make install   # uv sync --all-packages
make test      # uv run --all-packages pytest -q — the wrapper, converter and web packages
cd frontend && bun install && bun run test   # the campaign browser
```

The repository is a **uv workspace**: the root `pyproject.toml` declares
`members = ["packages/*"]`, the single root `uv.lock` pins every member, and
the shared virtualenv lives at the repository root. Always sync with
`--all-packages` — a plain `uv sync` prunes the venv back to the virtual root
plus the dev dependency group and drops the members. `uv` manages the
virtualenv and the lockfile; there is no separate `pip install -e .` step.
The frontend is a separate Bun/SvelteKit project under `frontend/`
([Web front & read API](../reference/web.md)).

`make check` runs `ruff format` and `ruff check --fix`; `make typecheck` runs
`ty` on the wrapper, converter and web packages against the workspace venv;
`make ci` runs the type check and then the dagger `checks` and `test`
functions, which is what CI runs — a green `make ci` locally is a strong
signal that a pull request passes ([CI](ci.md)). Constants for a dev cluster
(registry, S3 endpoint, namespace, release) come from `.env`
([Dev cluster](dev-cluster.md#env)).

## The repository

| Path | What it is |
|---|---|
| `packages/wrapper` | The in-pod wrapper: IIIF fetch, streaming, resume, verify, publish, live run log, warm-up entrypoint, ALTO provenance |
| `packages/converter` | `htrflow-campaigns`: validate, render and apply a campaigns repo |
| `packages/web` + `frontend` | The read API and the SvelteKit campaign browser, one image |
| `charts/htrflow-batch` | The Helm chart: Kueue queues, RBAC, NetworkPolicies, Kyverno policies, the web front |
| `charts/htrflow-devstack` | S3, an image registry and the NVIDIA device plugin for a disposable [dev cluster](dev-cluster.md) |
| `examples/campaigns` | The shape of a campaigns repository, with its CI |
| `.docker/` | The image recipes and the [Quickstart](../getting-started/try-it.md) compose stack |
| `docs/` | This site. `docs/features/` holds one story per deliverable, kept out of the site |
| `scripts/` | The line budgets (`scripts/loc-budget.sh`, a CI step), the generated configuration reference, the docs lint |

Each version lives next to what it versions: the charts' `Chart.yaml`
files, the packages' `pyproject.toml` files, and `KUEUE_VERSION` and
`KYVERNO_CHART_VERSION` in the `Makefile` ([Releasing](releasing.md)).

## Tests first

New behaviour gets a failing test before the implementation that makes it
pass, and every bug found on a real cluster becomes a regression test, not
only a fix. [Testing](testing.md) has the four test levels and how to run
each one.
