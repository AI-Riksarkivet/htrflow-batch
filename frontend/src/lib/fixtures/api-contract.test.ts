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
  // never heard of vanishes without a word. Two of them are deliberate: the
  // campaign FILE's name, which only `apply` and a prune care about, and
  // when the Job started, which no part of this page shows. Anything else
  // appearing here is a field somebody added to the API for this page and
  // the page is quietly dropping.
  const IGNORED = ["campaign", "startedAt"];

  function dropped(row: object, parsed: object): string[] {
    const kept = new Set(Object.keys(parsed));
    return Object.keys(row)
      .filter((k) => !kept.has(k))
      .sort();
  }

  test("the only fields the page drops are the ones it means to", () => {
    for (const row of contract.summaries)
      expect(dropped(row, jobSummarySchema.parse(row))).toEqual(IGNORED);
    for (const row of contract.details)
      expect(dropped(row, jobDetailSchema.parse(row))).toEqual(IGNORED);
  });

  test("the fixture really does cover the awkward rows", () => {
    const summaries = contract.summaries.map((r) => jobSummarySchema.parse(r));
    expect(summaries.map((r) => r.phase)).toContain("Unknown");
    expect(summaries.some((r) => r.jobGone)).toBe(true);
    expect(summaries.some((r) => r.finishedAt === null)).toBe(true);
    const reaped = contract.details
      .map((r) => jobDetailSchema.parse(r))
      .find((r) => r.jobGone);
    expect(reaped?.volumes.length).toBeGreaterThan(0);
    expect(reaped?.latest).not.toBeNull();
  });
});
