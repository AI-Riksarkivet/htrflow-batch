// Measures how much the campaign list moves while it loads, in a real
// browser. Local only: CI has no browser, so what this measures is pinned in
// CI by the structural tests (src/routes/page.test.ts and
// src/lib/components/CampaignCard.test.ts); this is the instrument those
// tests were written from. See README.md, "Layout stability".
//
// It serves a built dist/ next to a fake read API made from the committed
// contract fixture (src/lib/fixtures/api-contract.json), with every answer
// held back by a varied delay -- the list, then each card's detail in a
// random order, as a slow cluster answers them -- and records every layout
// shift the page makes (the Layout Instability API) with the element that
// moved, plus a filmstrip. A second list answer, one poll later, starts a
// campaign that was queued and adds a new one, which is when a list can
// re-sort under its reader.
//
//   node scripts/measure-shifts.mjs [--out DIR] [--open N] [--width PX]
//        [--scroll PX] [--fail] [--seed N] [--dist DIR] [--budget CLS]
//
// Needs `playwright-core` and a Chromium it can drive, neither of which the
// project installs (see README.md). The build must use a short poll so the
// second answer comes within the run: VITE_RELOAD_MS=4000 bun run build.

import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv
    .slice(2)
    .join(" ")
    .split("--")
    .filter(Boolean)
    .map((a) => a.trim().split(/\s+/)),
);
const DIST = resolve(args.dist ?? join(here, "..", "dist"));
const OUT = resolve(args.out ?? "shifts");
const OPEN = Number(args.open ?? 0);
const SCROLL = Number(args.scroll ?? 0);
const FAIL = "fail" in args;
const WIDTH = Number(args.width ?? 1280);
const BUDGET = Number(args.budget ?? 0.02);
let seed = Number(args.seed ?? 7);

/** A small seeded PRNG, so two runs hold the same answers back as long. */
function random() {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return seed / 0x7fffffff;
}

const fixture = JSON.parse(
  readFileSync(join(here, "../src/lib/fixtures/api-contract.json"), "utf8"),
);

// Fourteen campaigns in every state the list sorts by, each a copy of one
// of the fixture's three rows under a name of its own. A finished one
// sometimes lost pages, which only its detail says ("partially succeeded").
const SHAPES = [
  ["Running", "succeeded", false],
  ["Running", "running", false],
  ["Queued", "pending", false],
  ["Succeeded", "succeeded", false],
  ["Failed", "succeeded", false],
  ["Succeeded", "succeeded", true],
  ["PartiallyFailed", "succeeded", false],
  ["Queued", "succeeded", false],
  ["Succeeded", "succeeded", false],
  ["Unknown", "failed", true],
  ["Running", "succeeded", false],
  ["Succeeded", "succeeded", true],
  ["Paused", "succeeded", false],
  ["Succeeded", "succeeded", false],
];
const NAMES =
  "abbot baker carter dyer ellis fisher glover hunter ivy joiner keeper lister mason nash oakes"
    .split(" ")
    .map((n) => `campaign-${n}`);

function campaign(i, [phase, warmup, gone]) {
  const base = fixture.summaries[i % fixture.summaries.length];
  const day = String(10 + (i % 14)).padStart(2, "0");
  const finished = !["Running", "Queued", "Paused"].includes(phase);
  return {
    ...base,
    name: NAMES[i],
    campaign: NAMES[i],
    phase,
    jobGone: gone,
    warmup:
      warmup === "failed"
        ? {
            phase: "failed",
            reason: base.warmup.reason ?? {
              stage: "warmup",
              error: "bad model id",
              permanent: true,
            },
          }
        : { phase: warmup },
    // A failed volume only where the phase says one failed, so each
    // campaign sorts into the band its phase names.
    counts: {
      total: 1 + (i % 4),
      active: phase === "Running" ? 1 : 0,
      done: phase === "Queued" ? 0 : 1,
      failed: ["Failed", "PartiallyFailed"].includes(phase) ? 1 : 0,
    },
    createdAt: `2026-09-${day}T07:00:00Z`,
    finishedAt: finished ? `2026-09-${day}T0${i % 10}:30:00Z` : null,
  };
}

let list = SHAPES.map((s, i) => campaign(i, s));

// A detail that agrees with its list row: the failed volumes first, then
// the active ones, then the done ones, the rest pending -- each shaped like
// the fixture's volume, with progress to match -- so a card's rows say what
// its counts said. A finished campaign sometimes lost pages, which only the
// detail says ("partially succeeded").
function detailOf(job, i) {
  const base = fixture.details[i % fixture.details.length];
  const shape = base.volumes[0];
  const { failed, active, done } = job.counts;
  const lost = job.phase === "Succeeded" && i % 2 === 1 ? 3 : 0;
  const volumes = Array.from({ length: job.counts.total }, (_, k) => {
    const state =
      k < failed
        ? "failed"
        : k < failed + active
          ? "active"
          : k < failed + active + done
            ? "done"
            : "pending";
    const total = 200 + 37 * k;
    const progress =
      state === "pending"
        ? null
        : {
            ...shape.progress,
            done: state === "done" ? total : 120,
            total,
            failed: k === 0 ? lost : 0,
            errors: 0,
            lastError: null,
            stage: state === "done" ? "done" : "stream",
          };
    return {
      ...shape,
      id: `vol${k}`,
      index: k,
      state,
      progress,
      ...(state === "failed"
        ? { reason: { stage: "load", permanent: true, error: "manifest 404" } }
        : {}),
    };
  });
  const read = volumes.filter((v) => v.progress);
  return {
    ...base,
    ...job,
    volumes,
    latest: null,
    // A pipeline that loads models, as every real one does (the fixture's
    // two-step stub names none, and a card draws no models line for it).
    pipelineYaml:
      "steps:\n- step: Segmentation\n  settings:\n    model_settings:\n" +
      "      model: Riksarkivet/yolov9-lines-within-regions-1\n" +
      "      revision: 6fb01d2\n",
    failures: volumes.filter((v) => v.state === "failed"),
    lastError: null,
    errors: 0,
    pagesDone: read.reduce((a, v) => a + v.progress.done, 0),
    pagesTotal: read.reduce((a, v) => a + v.progress.total, 0),
    pagesFailed: lost,
    pagesCoverage: { counted: read.length, of: read.length },
  };
}

let listAnswers = 0;
const log = [];

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

const TYPES = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".json": "application/json",
};

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname;
  const send = (body, headers = {}) => {
    res.writeHead(200, { "content-type": "application/json", ...headers });
    res.end(JSON.stringify(body));
  };
  if (path === "/api/v1/version") {
    await delay(400);
    return send(fixture.version);
  }
  if (path === "/api/v1/jobs") {
    listAnswers += 1;
    // --fail: the poll's answer is an outage, which puts up the banner.
    if (listAnswers === 2 && FAIL) {
      await delay(300);
      return res.writeHead(503).end();
    }
    // The second answer: a queued campaign has started and a new one was
    // declared -- a re-sort and a new card, one poll into the page.
    if (listAnswers === 2) {
      list = list.map((j) =>
        j.name === NAMES[2]
          ? { ...j, phase: "Running", warmup: { phase: "succeeded" } }
          : j,
      );
      list = [campaign(14, ["Queued", "succeeded", false]), ...list];
    }
    await delay(300);
    log.push({ t: Date.now(), what: `list #${listAnswers}` });
    return send(list, {
      "x-reaped-total": String(list.filter((j) => j.jobGone).length),
    });
  }
  const m = path.match(/^\/api\/v1\/jobs\/([^/]+)\/([^/]+)$/);
  if (m) {
    const i = list.findIndex((j) => j.name === decodeURIComponent(m[2]));
    if (i < 0) return res.writeHead(404).end();
    await delay(200 + Math.round(random() * 1300));
    log.push({ t: Date.now(), what: `detail ${m[2]}` });
    return send(detailOf(list[i], NAMES.indexOf(m[2])));
  }
  let file = join(DIST, path === "/" ? "index.html" : path);
  try {
    if (statSync(file).isDirectory()) file = join(file, "index.html");
  } catch {
    file = join(DIST, "index.html");
  }
  res.writeHead(200, {
    "content-type": TYPES[extname(file)] ?? "application/octet-stream",
  });
  res.end(readFileSync(file));
});

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const origin = `http://127.0.0.1:${server.address().port}`;

const { chromium } = await import("playwright-core");
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: WIDTH, height: 900 },
});
const page = await context.newPage();

// Before any of the page's own scripts: the observer, and which cards a
// returning reader left open.
const opened = list
  .slice(0, OPEN)
  .map((j) => `htrflow.card.${j.namespace}/${j.name}`);
await page.addInitScript((keys) => {
  for (const k of keys) localStorage.setItem(k, "open");
  window.__shifts = [];
  window.__seen = [];
  const t0 = performance.now();
  const describe = (n) => {
    if (!n || n.nodeType !== 1)
      return n?.nodeType === 3
        ? `#text "${n.textContent.trim().slice(0, 30)}"`
        : String(n);
    const card = n
      .closest?.(".campaign")
      ?.querySelector(".camp-name")?.textContent;
    const cls =
      typeof n.className === "string" && n.className
        ? "." + n.className.trim().split(/\s+/).join(".")
        : "";
    return `${n.tagName.toLowerCase()}${cls}${card ? ` in ${card}` : ""} "${(n.textContent ?? "").trim().slice(0, 30)}"`;
  };
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      if (e.hadRecentInput) continue;
      window.__shifts.push({
        t: Math.round(e.startTime),
        value: e.value,
        sources: e.sources.map((s) => ({
          node: describe(s.node),
          from: [
            s.previousRect.x,
            s.previousRect.y,
            s.previousRect.width,
            s.previousRect.height,
          ].map(Math.round),
          to: [
            s.currentRect.x,
            s.currentRect.y,
            s.currentRect.width,
            s.currentRect.height,
          ].map(Math.round),
        })),
      });
    }
  }).observe({ type: "layout-shift", buffered: true });
  // What a reader saw of the list's own states, the first time each showed.
  const watch = () => {
    const text = document.querySelector("main")?.innerText ?? "";
    for (const word of ["Loading", "No campaigns", "API", "could not"]) {
      if (text.includes(word) && !window.__seen.some((s) => s.word === word))
        window.__seen.push({ word, t: Math.round(performance.now() - t0) });
    }
    requestAnimationFrame(watch);
  };
  requestAnimationFrame(watch);
}, opened);

mkdirSync(OUT, { recursive: true });
const started = Date.now();
await page.goto(origin + "/", { waitUntil: "commit" });
const frames = [];
for (let i = 0; i < 20; i++) {
  const name = `frame-${String(i).padStart(2, "0")}.png`;
  // The very first frames can come before there is a page to capture.
  const ok = await page.screenshot({ path: join(OUT, name) }).then(
    () => true,
    () => false,
  );
  if (ok) frames.push({ t: Date.now() - started, name });
  await delay(150);
}
// A reader who has scrolled down the list by the time the poll lands.
if (SCROLL > 0) await page.mouse.wheel(0, SCROLL);
// Past the first poll (VITE_RELOAD_MS=4000) and every detail it sets off.
await delay(Math.max(0, 8000 - (Date.now() - started)));
await page.screenshot({ path: join(OUT, "after-poll.png"), fullPage: true });
const shifts = await page.evaluate(() => window.__shifts);
const seen = await page.evaluate(() => window.__seen);
await browser.close();
server.close();

// CLS as browsers report it: the worst session window (gaps < 1 s, at most
// 5 s long), beside the plain sum of every shift.
let cls = 0;
let windowSum = 0;
let windowStart = -Infinity;
let last = -Infinity;
for (const s of shifts) {
  if (s.t - last > 1000 || s.t - windowStart > 5000) {
    windowSum = 0;
    windowStart = s.t;
  }
  windowSum += s.value;
  last = s.t;
  cls = Math.max(cls, windowSum);
}
const total = shifts.reduce((a, s) => a + s.value, 0);
const report = {
  width: WIDTH,
  open: OPEN,
  cls,
  total,
  seen,
  shifts,
  frames,
  server: log.map((l) => ({ ...l, t: l.t - started })),
};
writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 2));
console.log(
  `width ${WIDTH}px, ${OPEN} card(s) open: CLS ${cls.toFixed(4)} (sum ${total.toFixed(4)}), ${shifts.length} shift(s)`,
);
for (const s of seen) console.log(`  showed "${s.word}" at ${s.t} ms`);
for (const s of shifts) {
  console.log(`  ${s.t} ms  ${s.value.toFixed(4)}`);
  for (const src of s.sources)
    console.log(
      `      ${src.node}  ${src.from.join(",")} -> ${src.to.join(",")}`,
    );
}
console.log(`filmstrip and report.json in ${OUT}`);
process.exitCode = cls <= BUDGET ? 0 : 1;
