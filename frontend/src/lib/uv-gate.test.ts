// The viewer's login gate, run as the page runs it (spec §7).
//
// uv.html is not in this repo: the web image clones the UV4 fork at
// UV4_REF and applies .docker/uv4-uv-html.patch to it. The page as the fork
// ships it is committed beside the other fixtures (uv4-uv.html, byte for
// byte, MIT like the rest of UV), the patch's uv.html hunks are applied to
// it here -- a hunk whose context no longer matches fails the test, as it
// would fail `git apply` in the image build -- and the patched page's inline
// script is run against a stand-in UV and fetch.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Vitest runs from frontend/ (vite.config.ts); the patch is the repo's, one
// level up (the dagger check mounts it there too).
const HERE = process.cwd();
const lf = (s: string) => s.replace(/\r\n/g, "\n");

/** `pre` with the patch's hunks for `file` applied, or a thrown error. */
function applyHunks(patch: string, file: string, pre: string): string {
  const start = patch.indexOf(`diff --git a/${file} b/${file}\n`);
  if (start < 0) throw new Error(`the patch has no ${file}`);
  const next = patch.indexOf("\ndiff --git ", start + 1);
  const section = patch.slice(start, next < 0 ? undefined : next + 1);
  const src = pre.split("\n");
  const out: string[] = [];
  let at = 0; // next line of `src` not yet copied
  const hunks = section.split(/^(?=@@ )/m).slice(1);
  for (const hunk of hunks) {
    const [head, ...body] = hunk.split("\n");
    const from = Number(/^@@ -(\d+)/.exec(head ?? "")?.[1]) - 1;
    out.push(...src.slice(at, from));
    at = from;
    for (const line of body) {
      const mark = line[0];
      const text = line.slice(1);
      if (mark === " " || mark === "-") {
        if (src[at] !== text) {
          throw new Error(
            `hunk context at line ${at + 1}: ${JSON.stringify(text)}`,
          );
        }
        at += 1;
        if (mark === " ") out.push(text);
      } else if (mark === "+") {
        out.push(text);
      }
    }
  }
  out.push(...src.slice(at));
  return out.join("\n");
}

function patchedScript(): string {
  const patch = lf(
    readFileSync(resolve(HERE, "../.docker/uv4-uv-html.patch"), "utf8"),
  );
  const pre = lf(
    readFileSync(resolve(HERE, "src/lib/fixtures/uv4-uv.html"), "utf8"),
  );
  const page = applyHunks(patch, "src/uv.html", pre);
  // Parsed as a browser parses it, not matched with a pattern: an HTML
  // tag's case, attributes and spacing are the parser's business.
  const doc = new DOMParser().parseFromString(page, "text/html");
  const scripts = [...doc.querySelectorAll("script:not([src])")];
  expect(scripts).toHaveLength(1); // the one inline block uv_csp hashes
  return scripts[0]?.textContent ?? "";
}

const MANIFEST_PATH = "/results/htr-test/demo-v1/vol0/iiif.json";

function runViewer(manifest: string, answer: () => Promise<Response>) {
  const init = vi.fn(() => ({ on: vi.fn() }));
  vi.stubGlobal("UV", {
    IIIFURLAdapter: class {
      getInitialData() {
        return { iiifManifestId: manifest };
      }
      get() {
        return undefined;
      }
    },
    init,
  });
  const fetchMock = vi.fn(answer);
  vi.stubGlobal("fetch", fetchMock);
  const listen = vi
    .spyOn(document, "addEventListener")
    .mockImplementation(() => {});
  new Function(patchedScript())();
  const ready = listen.mock.calls.find((c) => c[0] === "DOMContentLoaded");
  listen.mockRestore();
  (ready?.[1] as () => void)();
  return { init, fetchMock };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe("uv.html's login gate", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="uv" class="uv"></div>';
    window.history.replaceState(
      null,
      "",
      `/uv.html#?manifest=${encodeURIComponent(location.origin + MANIFEST_PATH)}`,
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  test("not logged in: a link to the login page that comes back here", async () => {
    const { init, fetchMock } = runViewer(
      location.origin + MANIFEST_PATH,
      async () => new Response(null, { status: 401 }),
    );
    await settle();
    expect(fetchMock).toHaveBeenCalledWith(
      location.origin + MANIFEST_PATH,
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(init).not.toHaveBeenCalled();
    const link = document.querySelector("#uv a");
    expect(link).toHaveTextContent("Log in");
    expect(link).toHaveAttribute(
      "href",
      "/login?next=" +
        encodeURIComponent(location.pathname + location.search + location.hash),
    );
  });

  test("an account that may not read the volume is told so", async () => {
    const { init } = runViewer(
      location.origin + MANIFEST_PATH,
      async () => new Response(null, { status: 403 }),
    );
    await settle();
    expect(init).not.toHaveBeenCalled();
    expect(document.getElementById("uv")).toHaveTextContent(
      /^Your account may not read this volume\.$/,
    );
    expect(document.querySelector("#uv a")).toBeNull();
  });

  test.each([200, 404, 502])(
    "a %i loads the viewer as before",
    async (status) => {
      const { init } = runViewer(
        location.origin + MANIFEST_PATH,
        async () => new Response("{}", { status }),
      );
      await settle();
      expect(init).toHaveBeenCalledTimes(1);
    },
  );

  test("a network failure loads the viewer as before", async () => {
    const { init } = runViewer(location.origin + MANIFEST_PATH, async () => {
      throw new TypeError("Failed to fetch");
    });
    await settle();
    expect(init).toHaveBeenCalledTimes(1);
  });

  test.each([
    ["another origin", "https://iiif.example.org/results/vol0/manifest"],
    ["this origin, not /results/", "/uv-iiif-config.json"],
  ])("%s: never asked, loaded at once", async (_what, manifest) => {
    const { init, fetchMock } = runViewer(manifest, async () => {
      throw new Error("asked");
    });
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(init).toHaveBeenCalledTimes(1);
  });
});
