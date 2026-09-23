// The read API's own output, parsed by the schemas this page reads it with.
//
// The API is packages/web and the page is this one, and nothing tied the two
// together: a field renamed on one side was found by whoever next opened a
// campaign page (2026-09-14 audit). The fixture is printed by
// `scripts/api_contract.py` from real projection output and committed; a
// pytest fails when it goes stale, and this fails when the shapes disagree.
import { describe, expect, test } from "vitest";
import { jobDetailSchema, jobSummarySchema } from "$lib/api.js";
import contract from "./api-contract.json";

describe("the read API's contract", () => {
  test("every campaign row parses", () => {
    for (const row of contract.summaries)
      expect(() => jobSummarySchema.parse(row)).not.toThrow();
  });

  test("every campaign detail parses", () => {
    for (const row of contract.details)
      expect(() => jobDetailSchema.parse(row)).not.toThrow();
  });

  // Zod strips unknown keys, so a field the API sends and the schema has
  // never heard of vanishes without a word -- at any depth: a volume's
  // `sourceUrl` renamed `source` still parses, as a `.catch(null)` no-link,
  // and a `reason` renamed `why` parses as a volume with no reason. Two
  // drops are deliberate: the campaign FILE's name, which only `apply` and
  // a prune care about, and when the Job started, which no part of this
  // page shows. Anything else appearing here is a field somebody added to
  // the API for this page and the page is quietly dropping.
  const IGNORED = ["campaign", "startedAt"];

  /** Every key of `raw` missing from `parsed`, as a path with `[]` for arrays. */
  function dropped(raw: unknown, parsed: unknown, path = ""): string[] {
    if (Array.isArray(raw) && Array.isArray(parsed))
      return [
        ...new Set(raw.flatMap((r, i) => dropped(r, parsed[i], `${path}[]`))),
      ];
    if (!isObject(raw) || !isObject(parsed)) return [];
    return Object.keys(raw)
      .flatMap((k) => {
        const at = path === "" ? k : `${path}.${k}`;
        return k in parsed ? dropped(raw[k], parsed[k], at) : [at];
      })
      .sort();
  }

  function isObject(v: unknown): v is Record<string, unknown> {
    return typeof v === "object" && v !== null && !Array.isArray(v);
  }

  test("the only fields the page drops, at any depth, are the ones it means to", () => {
    for (const row of contract.summaries)
      expect(dropped(row, jobSummarySchema.parse(row))).toEqual(IGNORED);
    for (const row of contract.details)
      expect(dropped(row, jobDetailSchema.parse(row))).toEqual(IGNORED);
  });

  // A lenient field hides a rename from the walk above only if the fixture
  // never sends it: `sourceUrl` falls back to null and `reason` is optional,
  // so both have to come out of the parse with a value somewhere.
  test("the lenient fields come out of the parse with a value", () => {
    const volumes = contract.details.flatMap((r) => {
      const d = jobDetailSchema.parse(r);
      return [...d.volumes, ...d.failures, ...(d.latest ? [d.latest] : [])];
    });
    expect(volumes.some((v) => v.sourceUrl !== null)).toBe(true);
    expect(volumes.some((v) => v.reason !== undefined)).toBe(true);
  });
});
