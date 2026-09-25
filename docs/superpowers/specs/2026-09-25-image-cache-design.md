# S3 image cache — design

## Goal

An optional cache of source page images in S3. When it is on, a campaign pod looks for each page's image in
the cache before downloading it from IIIF. On a miss it downloads the image as today and stores it in the
cache, so the next run of the same volume, under any pipeline or campaign, needs nothing from the IIIF server.

Success means this: a volume run twice with the cache on fetches nothing from IIIF the second time and
produces the same ALTO. With the cache off, nothing changes.

Scope:

- The cache works for every volume.
- The live test uses volume `R0001203` only.

## 1. Switching it on, and the key

**Setting.** An optional block in `converter.yaml`:

```yaml
image_cache:
  bucket: images-batch
```

- The converter renders it into every campaign pod as the env var `IMAGE_CACHE_BUCKET`.
- **Absent means off.** Rendered manifests are then byte-identical to today: no new env var, and the golden
  files are unchanged.
- The bucket name must be a valid S3 bucket name (3–63 characters of lowercase letters, digits, `.` and
  `-`, starting and ending with a letter or digit). Anything else is refused with the converter's usual
  one-line reason.
- The cache uses the same S3 store as the results, with the same endpoint and credentials, from the S3
  Secret the pod already mounts. Only the bucket differs.

**Key.** `{ref}/{ref}_{page:05d}.jpg`, for example `R0001203/R0001203_00001.jpg`.

- `ref` is the volume's id as the campaign file names it: a bare reference code such as `R0001203`, or the
  `id:` given to a manifest or images volume. It is the wrapper's `VOLUME_REF`.
- `page` is the page's 1-based position in the manifest, zero-padded to exactly 5 digits.
- The key contains no `S3_PREFIX` and no pipeline id. The cache is shared across pipelines and campaigns,
  because a volume's source images are the same whichever pipeline reads them.
- A volume with more than 99 999 pages cannot be keyed. The wrapper skips the cache for that whole volume
  and says so once in the run log.

**What is stored.** The image exactly as the run fetched it: the IIIF request capped at `MAX_IMAGE_WIDTH`.

- A later run reuses it as it is, even when its pipeline asks for another width.
- The ALTO coordinates always describe the image that was actually processed, so the viewer's alignment is
  unaffected.

## 2. The fetch

This lives in the wrapper's existing per-page fetch, inside the bounded-lookahead download pool. Nothing
else in the stream changes.

When `IMAGE_CACHE_BUCKET` is set, for each page:

1. **Look.** S3 GET `{ref}/{ref}_{page:05d}.jpg`, streamed to the page's local file under the same
   `FETCH_MAX_BYTES` cap as a download.
2. **Hit.** The file gets the same checks a downloaded image gets: a known raster signature, the byte cap
   and the `MAX_IMAGE_PIXELS` cap.
   - If it passes, the page uses it and IIIF is not contacted.
   - If it fails (a corrupt or non-image object), it counts as a **miss**. The page is downloaded, and the
     bad object is overwritten. A warning names the key.
3. **Miss.** The page is downloaded exactly as today: the same retries, failure classes and `Retry-After`
   handling. After a successful download the image is PUT to the cache key.

**The cache never makes a run worse.**

- "Not found" is a miss.
- Any other error on the GET (a timeout, a 5xx, access denied, a missing bucket) also falls back to the
  download. It is logged once per run, not once per page. A missing or unreadable bucket gets one sentence
  that names the bucket.
- A failed PUT is logged, and the page carries on.
- A page is never failed or deferred because of the cache.
- Cache calls use the wrapper's existing S3 client and its bounded connect and read timeouts and retries.

**Unchanged:**

- resume, the verify gate and provenance;
- `page_sources` and `page_source_digests`, which still name the IIIF image a page comes from, so resume's
  "has this page's source changed?" check keeps working;
- `bytes_fetched`, which keeps meaning bytes downloaded from the source (IIIF).

Two runs of the same volume at once both write the same keys; the last write wins, which is harmless.

**Visible.** With the cache on:

- `manifest.json` carries `"image_cache": {"bucket": str, "hits": int, "misses": int, "stored": int}`;
- the run log has one line with the same counts.

With the cache off, neither is written.

## 3. Access, docs and tests

**Access.**

- The cache bucket is private. It is not in the results bucket policy, and neither the web front nor any
  published manifest links to it. Source images can carry access rules that the ALTO does not.
- The wrapper does not create the bucket.
- The dev stack chart (`charts/htrflow-devstack`) gains an optional `s3.imageCacheBucket`. When it is set,
  the bucket-setup hook creates that bucket, with no public policy.
- The compose stack's `compose_init.py` does the same when an image-cache bucket is configured.

**Docs.**

- Reference:
  - `image_cache` in the `converter.yaml` reference (`reference/campaign-yaml.md`);
  - the key layout in `reference/s3-layout.md`;
  - the `image_cache` counts in the `manifest.json` section.
- How-to: a short "Cache source images" section on the Deploy page covering:
  - creating the bucket;
  - setting `image_cache.bucket`;
  - what hits and misses look like in the run log.
- The site docs rules apply: no names, dates, versions, hosts or hardware.

**Tests.**

- **Wrapper** (moto S3, the existing fixtures):
  - a hit skips IIIF;
  - a miss downloads and stores at `R0001203/R0001203_00001.jpg`;
  - a corrupt cached object is replaced;
  - a GET error falls back to the download;
  - a failed PUT leaves the page ok;
  - page 100 000 and above skips the cache for the volume;
  - the counts are right;
  - end to end, a volume run twice: the second run has `bytes_fetched: 0`, and `hits` equals its page count;
  - with the cache off, `manifest.json` is byte-identical to today.
- **Converter:**
  - without `image_cache`, the golden renders are unchanged;
  - with it, the campaign pod carries `IMAGE_CACHE_BUCKET`;
  - a bad bucket name is refused.
- **Charts:** a devstack render test, with and without `s3.imageCacheBucket`.
- **Live, on the local k3s cluster, volume `R0001203` only:**
  1. Run one fills the cache, and `images-batch/R0001203/R0001203_00001.jpg` exists.
  2. Run two reports all hits, `bytes_fetched: 0` and no IIIF requests, and its ALTO matches run one's.

## Out of scope

- A separate command or Job that pre-fills the cache (approach B). It can reuse this code later.
- Eviction or expiry. The bucket's own lifecycle rules can handle that if wanted.
- A separate S3 endpoint or credentials for the cache.
- Caching manifests or ALTO.
