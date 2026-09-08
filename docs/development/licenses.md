# Third-party licences

htrflow-batch itself is EUPL-1.2 (`LICENSE`). This page is the inventory
story B62 asks for: what the published images ship besides our own code,
and under which licences, so that nothing in the chain contradicts EUPL-1.2.
Regenerate the Python table with the command under it whenever the
lockfile changes.

## Python packages in the wrapper, converter and web images

The three packages' own runtime dependencies (from `uv.lock`, 64 packages
in the workspace venv on 2026-09-08) are all permissive or weak-copyleft:

| Licence family | Packages |
|---|---|
| MIT (incl. MIT-0, MIT-CMU) | 25 |
| BSD (2- and 3-clause) | 13 |
| Apache-2.0 (incl. dual Apache/BSD, Apache AND MIT) | 17 |
| PSF-2.0 | 2 |
| MPL-2.0 (certifi, tqdm-style dual MPL AND MIT) | 2 |
| Our own three packages (EUPL-1.2, read as UNKNOWN until re-synced) | 3 |

No GPL, AGPL, SSPL or unknown third-party licence. Weak copyleft (MPL-2.0)
applies file-by-file to those libraries only and is compatible with
EUPL-1.2 (Appendix of the EUPL lists compatible licences; MPL-2.0 is one).

```bash
uvx --from pip-licenses pip-licenses --python .venv/bin/python --format=markdown
```

The wrapper image is built **on** the stock htrflow image, which is
EUPL-1.2 like this repository. htrflow's own dependencies (PyTorch,
transformers, ultralytics and the rest) belong to that repository's
inventory, not this one. One of them is worth naming here because it is
strong copyleft: `ultralytics` is AGPL-3.0. AGPL-3.0 is on the EUPL-1.2
compatibility list (the licence's appendix), and the combination is made in
htrflow, which is where it has to be answered.

## The status page (frontend)

One runtime dependency, `zod` (MIT). SvelteKit and Svelte, whose runtime
is bundled into the built SPA, are MIT. Everything else in
`frontend/package.json` is a development dependency that never ships.

## The viewer

`/uv.html` is the Riksarkivet universalviewer4 fork
(https://github.com/Riksarkivet/universalviewer4, ref pinned in the dockerfile), MIT like upstream Universal Viewer.

## Images

Every published image carries the OCI label
`org.opencontainers.image.licenses=EUPL-1.2` (set in the dockerfiles), so
the licence travels with the SBOM and the registry record.
