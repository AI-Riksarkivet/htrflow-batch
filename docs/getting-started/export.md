# Publish results

`htrflow-campaigns export` copies finished volumes out of the results store
into a folder, one ALTO file per image at the scan's full resolution, for
whatever publishes them. This page is the routine for exporting a whole
pipeline's volumes; every rule the command applies is in the
[CLI reference](../reference/cli.md#export-publishing-finished-volumes).

`<site>` is the web front's address, `<namespace>` the release namespace,
`<pipeline>` the pipeline id the volumes ran on and `<share>` the mounted
folder the publishing side reads.

## 1. Update the command

The export runs on a workstation, from the `htrflow-campaigns` command, not
in the cluster. Options are added to it over time, so install it again
before an export:

```bash
uv tool install --force "git+https://github.com/AI-Riksarkivet/htrflow-batch@<release-tag>#subdirectory=packages/converter"
htrflow-campaigns export --help
```

`@main` in place of the tag gives what is merged but not yet released. The
help lists the options this page uses; one that is missing means the old
command is still the one found.

## 2. Say where the results are and who asks

```bash
export HTRFLOW_RESULTS_URL=https://<site>/results
export HTRFLOW_USER=<user>
read -r -s -p "Password: " HTRFLOW_PASSWORD && export HTRFLOW_PASSWORD
```

The address must end in `/results`. Without the last two lines the command
asks for the login itself, which a run in the background cannot answer.

## 3. List the volumes

One volume id per line in a file; blank lines and `#` comments are skipped.
The export takes the pipeline, not the campaign: volumes of several
campaigns that ran on the same pipeline go in the same list.

Where the workstation cannot reach the image server, each volume's IIIF
manifest is downloaded elsewhere and saved as `<volume id>.json` in one
folder, passed as `--manifests`. Leave the option out where the image
server answers.

## 4. Dry run, then the real one

```bash
htrflow-campaigns export --namespace <namespace> --pipeline <pipeline> \
  --volumes-file volumes.txt --manifests manifests \
  --to <share>/<folder> --report report.csv \
  --fix-positions --join-hyphenation --dry-run
```

A dry run checks every volume and writes only the report. Read it, then run
the same command without `--dry-run`.

| Option | Why it is here |
|---|---|
| `--fix-positions` | htrflow writes word boxes and block outlines from the wrong corner, and a search hit is then drawn off its word. The export puts them where they are on the page. A file that is already right is left alone, so pass it always. |
| `--join-hyphenation` | Marks a word split over a line end as one word, so a search finds the whole word. Only for results with word boxes; without them it changes nothing. |
| `--min-quality 0.75` | The default: a volume whose mean predicted quality is lower is skipped. `--no-quality-filter` exports whatever the score, and is needed for a pipeline without a quality step. |
| `--empty-pages 1` | The default: the first page is published empty. |

Point `--to` at a folder inside the share, never at the bare mount point: an
unmounted share then refuses the run instead of filling a local directory.

## 5. Read the report

One row per volume. The outcomes:

| Outcome | Meaning | What to do |
|---|---|---|
| `exported` (`would export` in a dry run) | The volume's folder is on the share, complete. | Nothing. |
| `below threshold` | Its mean quality is under `--min-quality`. | Leave it out, or export it with a lower threshold. |
| `no scores` | No page has a quality score. | `--no-quality-filter`. |
| `not finished` | It has no `manifest.json` on this pipeline. | Check the pipeline id: a volume rerun on another pipeline is exported from that one. |
| `forbidden` | The login may not read it. | Use an account that may. |
| `failed` | The reason is in the row: a missing manifest file, a page size that cannot be read, results that changed during the export. | Fix the cause and export the volume again. |

The command exits non-zero when any volume is `failed` or `forbidden`.

## A long export

An export of many volumes outlives a terminal session. Start it detached,
with the login already in the environment, and follow its log:

```bash
nohup setsid htrflow-campaigns export … < /dev/null > export.log 2>&1 &
tail -f export.log
```

The log gets a line per volume as it finishes.

## Exporting again

A volume already on the share is replaced as a whole, never mixed with the
earlier files, so an export can be repeated at any time. Volumes published
before an option existed keep what they had until they are exported again:
after adding `--fix-positions` or `--join-hyphenation`, export the volumes
already delivered once more. Nothing is transcribed again for that; the
results in the store are read as they are.

Look at the first volume of a new kind on the publishing side before
exporting the rest.
