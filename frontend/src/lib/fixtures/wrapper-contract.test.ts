// The wrapper's own output, read the way the run viewer reads it.
//
// manifest.json reaches this page straight from the bucket, with no API in
// between to hold its shape still. The fixture is printed by
// `scripts/wrapper_contract.py` from the wrapper's `publish.run_manifest` and
// committed; a pytest fails when it goes stale, and this fails when the
// schema no longer reads what the wrapper writes.
import { describe, expect, test } from "vitest";
import { runManifestSchema, summarizeRun } from "$lib/run.js";
import contract from "./wrapper-contract.json";
import { dropped } from "./dropped.js";

describe("the wrapper's manifest.json", () => {
  const parsed = runManifestSchema.parse(contract.manifest);

  // The schema passes unknown top-level keys through, so what could vanish
  // is a field inside a page's result -- `error` renamed, say, would leave
  // every failed page without its reason.
  test("parses, and nothing it carries is dropped at any depth", () => {
    expect(dropped(contract.manifest, parsed)).toEqual([]);
  });

  // The optional fields hide a rename from the walk above: an absent
  // `viewer_url` parses as a run with no ALTO links. Each one the page
  // reads has to come out of the parse with a value.
  test("every optional field the page reads comes out with a value", () => {
    expect(parsed.pipeline_yaml).toBeTypeOf("string");
    expect(parsed.wall_seconds).toBeTypeOf("number");
    expect(parsed.viewer_url).toMatch(/^https:\/\/.*\/iiif\.json$/);
    expect(Object.keys(parsed.page_sources ?? {})).toEqual(
      Object.keys(parsed.results),
    );
  });

  test("the summary counts every page outcome the wrapper records", () => {
    const run = summarizeRun(
      parsed.results,
      parsed.page_sources,
      parsed.viewer_url,
    );
    expect([run.pages, run.ok, run.failed, run.skipped]).toEqual([
      parsed.pages,
      2,
      1,
      1,
    ]);
    const failed = run.failedPages[0];
    expect(failed?.error).toMatch(/worker thread died/);
    expect(failed?.source).toMatch(/^https:\/\//);
    expect(failed?.alto).toMatch(/\/alto\/0002\.xml$/);
  });
});
