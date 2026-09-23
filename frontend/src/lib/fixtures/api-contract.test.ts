// The read API's own output, parsed by the schemas this page reads it with.
//
// The API is packages/web and the page is this one, and nothing tied the two
// together: a field renamed on one side was found by whoever next opened a
// campaign page (2026-09-14 audit). The fixture is printed by
// `scripts/api_contract.py` through the app's own routes and committed; a
// pytest fails when it goes stale, and this fails when the shapes disagree.
import { afterEach, describe, expect, test, vi } from "vitest";
import { z } from "zod";
import {
  ApiUnreachable,
  fetchJob,
  fetchJobs,
  jobDetailSchema,
  jobSummarySchema,
  REAPED_MAX,
  REAPED_PAGE,
  versionSchema,
} from "$lib/api.js";
import { describeApiError } from "$lib/reasons.js";
import contract from "./api-contract.json";
import { dropped } from "./dropped.js";

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

  test("the only fields the page drops, at any depth, are the ones it means to", () => {
    for (const row of contract.summaries)
      expect(dropped(row, jobSummarySchema.parse(row))).toEqual(IGNORED);
    for (const row of contract.details)
      expect(dropped(row, jobDetailSchema.parse(row))).toEqual(IGNORED);
    expect(
      dropped(contract.version, versionSchema.parse(contract.version)),
    ).toEqual([]);
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

  // The page asks for reaped campaigns REAPED_PAGE at a time and never past
  // REAPED_MAX; the numbers are the route's own -- how many it sends unasked,
  // and the last `?reaped=` it answers 200 to. A page asking past the cap
  // gets a 422 and shows the service as unreachable.
  test("the page's reaped window and cap are the API's", () => {
    expect(REAPED_PAGE).toBe(contract.reapedLimits.default);
    expect(REAPED_MAX).toBe(contract.reapedLimits.max);
  });

  describe("what the routes add around the rows", () => {
    afterEach(() => vi.unstubAllGlobals());

    const answer = (body: unknown, status = 200, headers = {}) =>
      vi.fn(
        async () =>
          new Response(JSON.stringify(body), {
            status,
            headers: { "content-type": "application/json", ...headers },
          }),
      );

    // The list's total of reaped campaigns is a header, and a header is a
    // string: read the way fetchJobs reads it, it is a whole count -- the
    // same whether the reaped rows came with it or were not asked for.
    test("the reaped total reads as a whole count through fetchJobs", async () => {
      const total = Number(contract.reapedTotal);
      expect(Number.isInteger(total) && total >= 0).toBe(true);
      const live = contract.summaries.filter((r) => !r.jobGone);
      for (const rows of [contract.summaries, live]) {
        vi.stubGlobal(
          "fetch",
          answer(rows, 200, { "x-reaped-total": contract.reapedTotal }),
        );
        const list = await fetchJobs();
        expect(list.unreadable).toBe(0);
        expect(list.jobs).toHaveLength(rows.length);
        expect(list.reapedTotal).toBe(total);
      }
    });

    // The page reads an error by its status alone -- a 404 is a campaign
    // file that was removed, anything else the service being unreachable --
    // and never shows the body; `detail` is the shape it could rely on.
    test("every error the API answers is a status and a detail sentence", async () => {
      const shape = z.object({
        status: z.number().int().min(400),
        body: z.object({ detail: z.string().min(1) }),
      });
      expect(contract.errors.length).toBeGreaterThan(0);
      for (const error of contract.errors) {
        const { status, body } = shape.parse(error);
        vi.stubGlobal("fetch", answer(body, status));
        const thrown = await fetchJob("ns", "name").catch((e: unknown) => e);
        expect(thrown).toBeInstanceOf(ApiUnreachable);
        expect(describeApiError(thrown, false)).toContain(
          status === 404 ? "This campaign is gone" : `(HTTP ${status})`,
        );
      }
      expect(contract.errors.map((e) => e.status)).toContain(404);
    });
  });
});
