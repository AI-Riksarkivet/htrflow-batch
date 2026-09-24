// Property-based tests for the sentences a reader meets. reasons.test.ts
// pins every sentence verbatim; these check what must hold for any input
// the read API can hand over — the wrapper's error text is free text, and
// `stage` is whatever string the termination message carried — and that a
// verify sentence counts what the wrapper counted. A failure prints the
// seed; FC_SEED=<seed> replays it.
import fc from "fast-check";
import { describe, expect, test } from "vitest";
import type { VolumeProgress, VolumeReason, VolumeState } from "./api.js";
import {
  describeLastError,
  describeNotice,
  describeProgress,
  describeReason,
} from "./reasons.js";
import { RUNS } from "./fixtures/property.js";

/** Words that only reach a sentence when code interpolates the wrong thing. */
const LEAKS = /undefined|null|NaN|\[object |function |=>/;

/** Stages the wrapper writes, and strings it never does. */
const stage = fc.oneof(
  fc.constantFrom(
    "config",
    "setup",
    "resume",
    "load",
    "stream",
    "verify",
    "publish",
    "warmup",
    "done",
  ),
  fc.constantFrom("constructor", "toString", "__proto__", "hasOwnProperty"),
  fc.string(),
);

const errorText = fc.oneof(
  fc.constantFrom(
    "DeadlineExceeded",
    "MAX_SECONDS",
    "SIGTERM",
    "verify failed",
    "verify failed: all ",
    '{"stage": "load"',
    "  [1, 2",
  ),
  fc.string({ unit: "grapheme" }),
);

const reason: fc.Arbitrary<VolumeReason> = fc.record({
  stage: fc.option(stage, { nil: null }),
  permanent: fc.option(fc.boolean(), { nil: null }),
  error: errorText,
});

describe("describeReason", () => {
  test("any reason is one finished sentence in the page's own words", () => {
    fc.assert(
      fc.property(reason, fc.boolean(), (r, final) => {
        const said = describeReason(r, final);
        expect(said).toMatch(/[.!?]$/);
        // Nothing of its own that is not text: whatever markup or leaked
        // value the sentence has, the wrapper's message brought it.
        for (const bit of said.match(/[<>]/g) ?? [])
          expect(r.error).toContain(bit);
        if (!LEAKS.test(r.error)) expect(said).not.toMatch(LEAKS);
      }),
      RUNS,
    );
  });

  test("an unparsed termination message is never shown raw", () => {
    const blob = fc
      .tuple(
        fc.constantFrom("{", "[", " {", "\n["),
        fc.string({ minLength: 1 }),
      )
      .map(([open, rest]) => open + rest)
      .filter((e) => !e.startsWith("verify failed"));
    fc.assert(
      fc.property(blob, fc.boolean(), (error, final) => {
        const said = describeReason(
          { stage: null, permanent: null, error },
          final,
        );
        expect(said).not.toContain(error.trim());
      }),
      RUNS,
    );
  });
});

describe("a verify sentence counts what the wrapper counted", () => {
  // Page names as the wrapper writes them (zero-padded) or anything else a
  // Python str repr would put in single quotes.
  const page = fc.oneof(
    fc.nat(9999).map((n) => String(n).padStart(4, "0")),
    fc.stringMatching(/^[A-Za-z0-9_.-]{1,12}$/),
  );
  const repr = (names: string[]) =>
    `[${names.map((n) => `'${n}'`).join(", ")}]`;

  /** main._verify's message, clipped at any point after its counts. */
  const verify = fc
    .uniqueArray(page, { minLength: 2, maxLength: 40 })
    .chain((names) =>
      fc.tuple(
        fc.constant(names),
        fc.integer({ min: 1, max: names.length - 1 }),
        fc.nat(),
        fc.boolean(),
      ),
    )
    .map(([names, split, cut, clipped]) => {
      const missing = names.slice(0, split);
      const failed = names.slice(split);
      const head = `verify failed: ${missing.length} missing, ${failed.length} failed`;
      const detail =
        failed.length > 0
          ? ` errors: ${failed[0]}: CUDA error: out of memory`
          : "";
      const full = `${head}${detail} missing=${repr(missing)} failed=${repr(failed)}`;
      const at = head.length + (cut % (full.length - head.length + 1));
      const error = clipped ? `${full.slice(0, at)}...(truncated)` : full;
      return { missing, failed, error };
    });

  test("the number is the wrapper's, and the names it gives add up to it", () => {
    fc.assert(
      fc.property(verify, fc.boolean(), ({ missing, failed, error }, final) => {
        const said = describeReason(
          { stage: "verify", permanent: false, error },
          final,
        );
        const n = missing.length;
        expect(
          said.startsWith(`${n} ${n === 1 ? "page is" : "pages are"} missing`),
        ).toBe(true);
        const listed = /missing from the results \(([^)]*)\)/.exec(said)?.[1];
        if (listed === undefined) return; // clipped before a whole name
        const [names = "", more] = listed.split(" and ");
        const shown = names.split(", ");
        // The names are the missing ones, first to last, never a failed one
        // (those are accounted for and are not coming back).
        expect(shown).toEqual(missing.slice(0, shown.length));
        for (const name of shown) expect(failed).not.toContain(name);
        const rest =
          more === undefined ? 0 : Number(/^(\d+) more$/.exec(more)?.[1]);
        expect(shown.length + rest).toBe(n);
      }),
      RUNS,
    );
  });

  test("every page failing is said with the wrapper's count", () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(page, { minLength: 1, maxLength: 40 }),
        fc.nat(),
        fc.boolean(),
        (failed, cut, final) => {
          const head = `verify failed: all ${failed.length} processed pages failed`;
          const full = `${head} failed=${repr(failed)}`;
          const error = full.slice(
            0,
            head.length + (cut % (full.length - head.length + 1)),
          );
          const said = describeReason(
            { stage: "verify", permanent: false, error },
            final,
          );
          expect(
            said.startsWith(`None of the ${failed.length} pages processed`),
          ).toBe(true);
        },
      ),
      RUNS,
    );
  });
});

describe("the campaign and progress lines", () => {
  const count = fc.nat(10_000);
  const lastError = fc.option(
    fc.record({
      page: fc.option(fc.string(), { nil: null }),
      error: fc.string({ unit: "grapheme" }),
      volume: fc.constant("vol-1"),
      logUrl: fc.constant("https://results.example.org/htr-test/vol-1/run.log"),
    }),
    { nil: null },
  );

  test("describeNotice says each count once, as a number, or nothing at all", () => {
    fc.assert(
      fc.property(count, count, lastError, (pagesFailed, errors, last) => {
        const said = describeNotice({ pagesFailed, errors, lastError: last });
        if (pagesFailed === 0 && errors === 0) {
          expect(said).toBeNull();
          return;
        }
        const parts = (said as string).split(" · ");
        if (pagesFailed > 0)
          expect(parts[0]).toBe(
            `${pagesFailed} page${pagesFailed === 1 ? "" : "s"} failed`,
          );
        if (errors > 0)
          expect(parts[pagesFailed > 0 ? 1 : 0]).toBe(
            `${errors} error${errors === 1 ? "" : "s"}`,
          );
        const tail = describeLastError(last);
        if (tail !== null) expect(said?.endsWith(tail)).toBe(true);
      }),
      RUNS,
    );
  });

  test("describeLastError names the page once, whatever the message says", () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^\d{4}$/),
        fc.string({ unit: "grapheme" }),
        fc.boolean(),
        (page, message, prefixed) => {
          const error = prefixed ? `page ${page}: ${message}` : message;
          const said = describeLastError({
            page,
            error,
            volume: "vol-1",
            logUrl: "https://results.example.org/htr-test/vol-1/run.log",
          }) as string;
          expect(said.startsWith(`page ${page}: `)).toBe(true);
          expect(said.startsWith(`page ${page}: page ${page}:`)).toBe(
            error.startsWith(`page ${page}: page ${page}:`),
          );
        },
      ),
      RUNS,
    );
  });

  test("describeProgress never says an age for a volume that has stopped", () => {
    fc.assert(
      fc.property(
        fc.option(stage, { nil: null }),
        fc.option(fc.nat(10 * 24 * 3600), { nil: null }),
        fc.constantFrom<VolumeState>(
          "active",
          "unknown",
          "done",
          "failed",
          "pending",
        ),
        (s, ageSeconds, state) => {
          const progress: VolumeProgress = {
            done: 0,
            total: 0,
            failed: 0,
            lastPage: null,
            stage: s,
            updatedAt: null,
            ageSeconds,
            lastError: null,
            errors: 0,
            viewerPublished: false,
            quality: null,
          };
          const said = describeProgress(progress, state);
          const moving = state === "active" || state === "unknown";
          expect(/(^| · )updated \d+ (s|min|h) ago$/.test(said)).toBe(
            moving && ageSeconds !== null,
          );
          if (s === null || !LEAKS.test(s)) expect(said).not.toMatch(LEAKS);
        },
      ),
      RUNS,
    );
  });
});
