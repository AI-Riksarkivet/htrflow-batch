# Third-party licences

htrflow-batch itself is EUPL-1.2 (`LICENSE`). This page is the inventory of
what the published images ship besides the project's own code, and under
which licences, so that nothing in the chain contradicts EUPL-1.2. It names
licence families, not versions: the exact set of packages and versions in an
image is in its lockfiles (`uv.lock`, `frontend/bun.lock`) and in the SPDX
SBOM attested to every published per-architecture image
([Releasing](releasing.md#signing-sbom-and-provenance)).

## Python packages in the wrapper, converter and web images

The three packages' own runtime dependencies, resolved from `uv.lock`, are
all permissive or weak copyleft:

- MIT (including MIT-0 and MIT-CMU)
- BSD (2- and 3-clause)
- Apache-2.0 (including dual Apache/BSD and Apache AND MIT)
- PSF-2.0
- MPL-2.0 (`certifi`, and dual MPL AND MIT)
- EUPL-1.2 — the project's own three packages (`pip-licenses` reports them
  as UNKNOWN until the venv is re-synced)

No GPL, AGPL, SSPL or unknown third-party licence. Weak copyleft (MPL-2.0)
applies file by file to those libraries only and is compatible with EUPL-1.2
(the EUPL's appendix lists compatible licences; MPL-2.0 is one).

Regenerate the inventory whenever `uv.lock` changes, and check the families
against the table:

```bash
uvx --from pip-licenses pip-licenses --python .venv/bin/python --format=markdown
```

The wrapper image is built **on** the htrflow image, which is EUPL-1.2 like
this repository. htrflow's own dependencies (PyTorch, transformers,
ultralytics and the rest) belong to that repository's inventory, not this
one. One of them is worth naming because it is strong copyleft:
`ultralytics` is AGPL-3.0. AGPL-3.0 is on the EUPL-1.2 compatibility list
(the licence's appendix), and the combination is made in htrflow, which is
where it has to be answered.

## The campaign browser (frontend)

One runtime dependency, `zod` (MIT). SvelteKit and Svelte, whose runtime is
bundled into the built SPA, are MIT. Everything else in
`frontend/package.json` is a development dependency that never ships.

## The viewer

`/uv.html` is the universalviewer4 fork
(<https://github.com/Riksarkivet/universalviewer4>, commit pinned as
`UV4_REF` in `.docker/htrflow-web.dockerfile`), MIT like upstream Universal
Viewer. The image builds it with `.docker/uv4-uv-html.patch` applied, and the
patch's preamble lists what it changes: the configuration fetch (from the
viewer's own origin only), the text overlay's coordinates, and ALTO and
search-hit text rendered as text rather than HTML.

## Images

Every published image carries the OCI label
`org.opencontainers.image.licenses=EUPL-1.2` (set in the dockerfiles), so the
licence travels with the SBOM and the registry record.
