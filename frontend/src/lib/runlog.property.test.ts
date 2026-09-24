// Property-based tests for the run-log parser. A run log is whatever the pod
// wrote — tracebacks, progress bars, bytes reprs, partial lines cut by a
// live upload — so the page must render any text at all, lose none of it,
// and decide "this run is over" the same way however much follows the
// wrapper's last line. A failure prints the seed; FC_SEED=<seed> replays it.
import fc from "fast-check";
import { describe, expect, test } from "vitest";
import { isTerminalLog, parseRunLog, splitLogLine, tailOf } from "./runlog.js";
import { RUNS } from "./fixtures/property.js";

/** Lines shaped like a wrapper log, with the odd one that is not. */
const line = fc.oneof(
  fc.constantFrom(
    "2026-01-02 03:04:05,678 INFO HTTP Request: GET https://x/y 200",
    "2026-01-02 03:04:05,678 INFO Initialized YOLO model",
    "2026-01-02 03:04:05,678 WARNING manifest covers 3/4 pages",
    "2026-01-02 03:04:05,678 ERROR page 0003 failed",
    "Traceback (most recent call last):",
    '  File "/app/main.py", line 1, in <module>',
    "    raise RuntimeError('x')",
    "RuntimeError: x",
    "",
  ),
  fc.string({ unit: "grapheme" }).map((s) => s.replace(/\n/g, " ")),
);
const lines = fc.array(line, { maxLength: 40 });

/** The wrapper's last line on each exit path (main.py). */
const terminal = fc.oneof(
  fc
    .tuple(fc.stringMatching(/^[A-Za-z0-9._-]{1,20}$/), fc.nat(5000))
    .map(
      ([vol, n]) =>
        `2026-01-02 03:04:05,678 INFO [${vol}] COMPLETE ${n} pages in 12s`,
    ),
  fc
    .tuple(
      fc.constantFrom("permanent", "transient"),
      fc.constantFrom("setup", "load", "stream", "verify", "publish"),
    )
    .map(
      ([kind, stage]) =>
        `2026-01-02 03:04:05,678 ERROR ${kind} failure in ${stage}: boom`,
    ),
);

describe("parseRunLog", () => {
  test("never throws, and every line comes back, in order, exactly once", () => {
    fc.assert(
      fc.property(lines, fc.boolean(), (ls, trailingNewline) => {
        const text = ls.join("\n") + (trailingNewline ? "\n" : "");
        // A bytes repr is its own case, below.
        fc.pre(!text.startsWith("b'"));
        const { groups } = parseRunLog(text);
        const expected = text.split("\n");
        if (expected.at(-1) === "") expected.pop();
        expect(groups.flatMap((g) => g.lines)).toEqual(expected);
      }),
      RUNS,
    );
  });

  test("groups are maximal: never empty, never two of a kind side by side", () => {
    fc.assert(
      fc.property(fc.string({ unit: "grapheme" }), (text) => {
        const { groups } = parseRunLog(text);
        for (const [i, group] of groups.entries()) {
          expect(group.lines.length).toBeGreaterThan(0);
          if (i > 0) expect(group.kind).not.toBe(groups[i - 1]?.kind);
        }
      }),
      RUNS,
    );
  });

  test("a log uploaded as a Python bytes repr reads like the log itself", () => {
    // Lines without a backslash or quote, which the repr would escape.
    const plain = fc.array(
      line.filter((l) => !/[\\'\t]/.test(l)),
      { minLength: 1, maxLength: 20 },
    );
    fc.assert(
      fc.property(plain, (ls) => {
        fc.pre(!ls.join("\n").startsWith("b'"));
        expect(parseRunLog(`b'${ls.join("\\n")}'`)).toEqual(
          parseRunLog(ls.join("\n")),
        );
      }),
      RUNS,
    );
  });
});

describe("splitLogLine", () => {
  test("never throws; a logging-format line splits into what built it", () => {
    fc.assert(
      fc.property(
        fc.date({
          min: new Date("2000-01-01T00:00:00Z"),
          max: new Date("2099-12-31T23:59:59Z"),
          noInvalidDate: true,
        }),
        fc.constantFrom("INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"),
        fc.string({ unit: "grapheme" }).map((s) => s.replace(/[\n\r]/g, " ")),
        (when, level, msg) => {
          const [day, clock] = when.toISOString().slice(0, 23).split("T");
          const [hms, ms] = (clock as string).split(".");
          const split = splitLogLine(`${day} ${hms},${ms} ${level} ${msg}`);
          expect(split).toEqual({ time: `${hms}.${ms}`, level, msg });
        },
      ),
      RUNS,
    );
    fc.assert(
      fc.property(fc.string({ unit: "grapheme" }), (text) => {
        const split = splitLogLine(text);
        if (split.level === null)
          expect(split).toEqual({ time: null, level: null, msg: text });
        else expect(text.endsWith(` ${split.level} ${split.msg}`)).toBe(true);
      }),
      RUNS,
    );
  });
});

describe("isTerminalLog", () => {
  test("never throws, whatever the text", () => {
    fc.assert(
      fc.property(fc.string({ unit: "binary" }), (text) => {
        expect(typeof isTerminalLog(text)).toBe("boolean");
      }),
      RUNS,
    );
  });

  test("a terminal line stays terminal under the tracebacks that follow it", () => {
    // The failure line is followed by the traceback(s): up to the 500-line
    // window, whatever they say, the run is over.
    fc.assert(
      fc.property(
        lines,
        terminal,
        // size "max": lengths across the whole window, not only short ones;
        // and, every so often, exactly the window's last line.
        fc.array(line, { maxLength: 499, size: "max" }),
        fc.boolean(),
        (before, last, some, full) => {
          const after = full
            ? [...some, ...Array<string>(499 - some.length).fill("")]
            : some;
          const text = [...before, last, ...after].join("\n");
          expect(isTerminalLog(text)).toBe(true);
        },
      ),
      { numRuns: 100 },
    );
  });

  test("the vocabulary of a running log is never mistaken for its end", () => {
    // Every line a live wrapper writes before its last one: "COMPLETE" and
    // "failure in" only ever appear in the terminal line's own shape.
    const running = fc.array(
      fc.oneof(
        line.filter((l) => !/COMPLETE|failure in/.test(l)),
        fc.constantFrom(
          "2026-01-02 03:04:05,678 INFO page 0001 COMPLETED",
          "2026-01-02 03:04:05,678 WARNING transient failure, retrying",
          "2026-01-02 03:04:05,678 INFO [vol] COMPLETE pages",
        ),
      ),
      { maxLength: 60 },
    );
    fc.assert(
      fc.property(running, (ls) => {
        expect(isTerminalLog(ls.join("\n"))).toBe(false);
      }),
      RUNS,
    );
  });
});

describe("tailOf", () => {
  test("a whole-line suffix within the limit, and cutting twice cuts nothing more", () => {
    fc.assert(
      fc.property(
        // Many lines, so the limit falls inside the text and between lines.
        fc
          .array(fc.string({ unit: "grapheme", maxLength: 20 }), {
            maxLength: 40,
            size: "max",
          })
          .map((ls) => ls.join("\n")),
        fc.nat(300),
        (text, limit) => {
          const tail = tailOf(text, limit);
          expect(text.endsWith(tail)).toBe(true);
          if (text.length <= limit) {
            expect(tail).toBe(text);
            return;
          }
          expect(tail.length).toBeLessThanOrEqual(limit);
          const start = text.length - tail.length;
          // The first line shown is a whole one: the tail starts right after a
          // newline, or — with no newline within reach — is the last `limit`
          // characters exactly.
          expect(
            text[start - 1] === "\n" ||
              (tail.length === limit && !tail.includes("\n")),
          ).toBe(true);
          expect(tailOf(tail, limit)).toBe(tail);
        },
      ),
      RUNS,
    );
  });
});
