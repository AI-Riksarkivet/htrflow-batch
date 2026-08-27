---
type: Product Backlog Item
id: 2842
parent: 2800
title: Transcribe a volume page-by-page, streaming results to S3
---

# B01 · Transcribe a volume page-by-page, streaming results to S3

**Story.** As an archivist waiting for a volume to be transcribed, I want
each page to appear in S3 as soon as it is done — not when the whole volume
finishes — so that I can start checking results within minutes and nothing
is lost if the job is interrupted.

## Why it matters

`htrflow` on its own transcribes a folder of images and writes a folder of
results. For a 500-page volume that means downloading 500 images first,
waiting an hour, and hoping nothing goes wrong before the end — if the pod is
evicted at page 480, everything is redone. It also means the GPU sits idle
while images download.

## What this delivers

A small "wrapper" program that sits around `htrflow` inside the GPU job and
drives it like a conveyor belt:

- **Reads pages straight from IIIF** (Riksarkivet's image server), a few
  pages ahead of the model, so the GPU never waits for a download.
- **Loads the models once** per volume, not once per page.
- **Uploads each page's result to S3 the moment it is written**, then deletes
  the source image from local disk — a volume of any size fits in a small,
  fixed amount of memory.
- **Resumes.** If a job is killed and restarted, it sees which pages are
  already in S3 and only transcribes the rest.
- **Verifies before declaring victory.** A volume is only marked complete
  when every page in the IIIF manifest has both its ALTO and PAGE file in
  S3. The completion marker (`manifest.json`) is written last, so "the marker
  exists" always means "the volume is whole".
- **Records provenance.** The marker names the exact container image, model
  pipeline and timings that produced the volume.
- **Fails with a reason.** Every way a job can end has a distinct exit code
  and a one-line explanation the operator can read without opening logs.

## Done when

- [ ] A volume given as a IIIF manifest URL is transcribed with no local
      staging of the whole volume; per-page results are visible in S3 during
      the run.
- [ ] Killing the job mid-run and restarting it transcribes only the missing
      pages and produces an identical completion marker.
- [ ] A volume with a missing or broken page is *not* marked complete, and
      the reason is reported.
- [ ] The completion marker records image digest, pipeline and timings.
- [ ] Unit tests cover the download, stream, upload, verify and resume paths.
