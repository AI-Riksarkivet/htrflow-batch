// Property-based tests for every guard that decides what may become an href,
// a src or a fetch target, and for the helpers that put an identifier into a
// URL path. Example tests pin the cases someone thought of; these generate
// the ones nobody did — mixed-case schemes, dot segments spelled with
// percent-escapes, userinfo, a look-alike host — and check the guard against
// an oracle written from the URL standard. A failure prints the seed and the
// shrunk counterexample; FC_SEED=<seed> replays it.
import fc from "fast-check";
import { afterEach, describe, expect, test, vi } from "vitest";
import { fetchJob, isHttpUrl, isResultUrl } from "./api.js";
import { HF_BASE, modelUrl } from "./pipeline.js";

const RUNS = { numRuns: 300 };

/** Bases the read API could be configured with (publicResultsBase). */
const base = fc.constantFrom(
  "https://results.example.org/htr-test",
  "https://results.example.org/htr-test/",
  "http://s3.local:9000/bucket/prefix",
  "https://results.example.org",
);

/** Path pieces that have each moved a URL somewhere else at some point. */
const piece = fc.constantFrom(
  "..",
  ".",
  "%2e%2e",
  "%2E%2e",
  ".%2e",
  "%2e",
  "%2f",
  "%2F",
  "%5c",
  "%5C",
  "\\",
  "@",
  ":",
  "%00",
  "%252e%252e",
  "",
  "?",
  "#",
  "evil",
  "htr-test-other",
  "vol-1",
  "run.log",
);
const tail = fc
  .array(fc.tuple(piece, fc.constantFrom("/", "")), { maxLength: 6 })
  .map((parts) => parts.map(([p, sep]) => p + sep).join(""));

/** A string a link could carry: near the base, around it, or anything. */
const candidate = fc.oneof(
  fc
    .tuple(base, fc.constantFrom("", "/"), tail)
    .map(([b, sep, t]) => b + sep + t),
  fc
    .tuple(
      fc.constantFrom(
        "https://user@results.example.org/htr-test/",
        "https://results.example.org@evil.example/htr-test/",
        "https://results.example.org.evil.example/htr-test/",
        "https://evil.example/https://results.example.org/htr-test/",
        "HTTPS://RESULTS.EXAMPLE.ORG/htr-test/",
        "http://results.example.org/htr-test/",
        "https://results.example.org:443/htr-test/",
        "//results.example.org/htr-test/",
        "javascript:alert(1)//https://results.example.org/htr-test/",
        "data:text/html,https://results.example.org/htr-test/",
        " https://results.example.org/htr-test/",
        "https:\\\\results.example.org/htr-test/",
      ),
      tail,
    )
    .map(([p, t]) => p + t),
  fc.webUrl({ withQueryParameters: true, withFragments: true }),
  fc.string(),
);

/**
 * Whether a URL must be refused for `root`: a guard may refuse more, never
 * less. Same scheme, host and port; no userinfo; a path at or below the
 * base's directory once the URL parser has resolved it; and still below it
 * for a server that decodes escaped separators and dots before it resolves
 * dot segments, as some proxies do.
 */
function outside(value: string, root: string): boolean {
  let here: URL;
  let from: URL;
  try {
    here = new URL(value);
    from = new URL(root);
  } catch {
    return true;
  }
  if (here.protocol !== from.protocol || here.host !== from.host) return true;
  if (here.username !== "" || here.password !== "") return true;
  const dir = from.pathname.endsWith("/") ? from.pathname : `${from.pathname}/`;
  if (!here.pathname.startsWith(dir)) return true;
  const below = here.pathname
    .slice(dir.length)
    .replace(/%2f|%5c/gi, "/")
    .replace(/%2e/gi, ".");
  let depth = 0;
  for (const segment of below.split("/")) {
    if (segment === "..") depth -= 1;
    else if (segment !== "." && segment !== "") depth += 1;
    if (depth < 0) return true;
  }
  return false;
}

describe("isHttpUrl", () => {
  test("accepts only what parses as an absolute http(s) URL with a host", () => {
    fc.assert(
      fc.property(candidate, (value) => {
        if (!isHttpUrl(value)) return;
        // What the DOM gets is `value` itself, so it must already say
        // http(s):// — not merely parse to it after whitespace is stripped.
        expect(value).toMatch(/^https?:\/\//i);
        const url = new URL(value);
        expect(["http:", "https:"]).toContain(url.protocol);
        expect(url.hostname).not.toBe("");
      }),
      RUNS,
    );
  });

  test("never accepts a script, data or protocol-relative URL, in any case", () => {
    const risky = fc
      .tuple(
        fc.constantFrom("javascript:", "data:", "vbscript:", "//", "file:"),
        fc.array(fc.boolean(), { maxLength: 12 }),
        fc.string(),
      )
      .map(([scheme, upper, rest]) =>
        [...scheme]
          .map((c, i) => (upper[i] === true ? c.toUpperCase() : c))
          .join("")
          .concat(rest),
      );
    fc.assert(
      fc.property(risky, (value) => {
        expect(isHttpUrl(value)).toBe(false);
      }),
      RUNS,
    );
  });
});

describe("isResultUrl", () => {
  test("never accepts a URL outside the results base", () => {
    fc.assert(
      fc.property(candidate, base, (value, root) => {
        if (isResultUrl(value, root)) expect(outside(value, root)).toBe(false);
      }),
      // The escaped-separator case is a narrow slice of the space: more
      // runs here find it every time rather than now and then.
      { numRuns: 2000 },
    );
  });

  test("accepts every result URL the read API builds", () => {
    // Volume ids as the converter admits them (models.py _VOLUME_ID_RE),
    // escaped the way the read API escapes them (quote(…, safe="")).
    const volume = fc.stringMatching(
      /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$/,
    );
    const file = fc.constantFrom(
      "run.log",
      "manifest.json",
      "iiif.json",
      "alto/0001.xml",
    );
    fc.assert(
      fc.property(base, volume, file, (root, vol, name) => {
        const url = `${root.replace(/\/$/, "")}/${encodeURIComponent(vol)}/${name}`;
        expect(isResultUrl(url, root)).toBe(true);
      }),
      RUNS,
    );
  });

  test("with no base configured, is exactly isHttpUrl", () => {
    fc.assert(
      fc.property(candidate, (value) => {
        expect(isResultUrl(value, "")).toBe(isHttpUrl(value));
      }),
      RUNS,
    );
  });
});

describe("identifiers put into a URL path come back out unchanged", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // Any string but a dot segment: the URL standard resolves `.` and `..`
  // (and their percent-escapes) away, so no escaping can carry one, and a
  // Kubernetes name or a Hugging Face repo segment is never one.
  const segment = fc
    .string({ unit: "grapheme", minLength: 1 })
    .filter((s) => s !== "." && s !== "..");

  function decoded(url: URL): string[] {
    return url.pathname.split("/").slice(1).map(decodeURIComponent);
  }

  test("fetchJob's namespace and name", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        seen.push(url);
        throw new Error("not a real request");
      }),
    );
    await fc.assert(
      fc.asyncProperty(
        segment,
        segment,
        fc.nat(),
        fc.nat(),
        async (ns, name, offset, limit) => {
          seen.length = 0;
          await fetchJob(ns, name, offset, limit).catch(() => undefined);
          const url = new URL(seen[0] as string, "https://page.example");
          expect(decoded(url)).toEqual(["api", "v1", "jobs", ns, name]);
          expect(url.searchParams.get("offset")).toBe(String(offset));
          expect(url.searchParams.get("limit")).toBe(String(limit));
          expect(url.hash).toBe("");
        },
      ),
      RUNS,
    );
  });

  test("modelUrl's repo id and revision, always on the Hugging Face host", () => {
    fc.assert(
      fc.property(
        // A repo id's segments are split on "/", so none of them holds one.
        fc.array(
          segment.filter((s) => !s.includes("/")),
          { minLength: 1, maxLength: 3 },
        ),
        fc.option(segment, { nil: null }),
        (parts, revision) => {
          const href = modelUrl({ id: parts.join("/"), revision });
          expect(href).not.toBeNull();
          const url = new URL(href as string);
          expect(url.origin).toBe(HF_BASE);
          expect(decoded(url)).toEqual([...parts, "tree", revision ?? "main"]);
          expect(url.search + url.hash).toBe("");
        },
      ),
      RUNS,
    );
  });

  test("modelUrl never leaves the Hugging Face host, whatever the YAML says", () => {
    fc.assert(
      fc.property(
        fc.string(),
        fc.option(fc.string(), { nil: null }),
        (id, revision) => {
          const href = modelUrl({ id, revision });
          if (href !== null) expect(new URL(href).origin).toBe(HF_BASE);
        },
      ),
      RUNS,
    );
  });
});
