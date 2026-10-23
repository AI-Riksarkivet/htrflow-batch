// The /search page's model, kept fetch-free so every branch is unit-tested
// (search.test.ts): how the reader's words become a Solr query, how the
// answer becomes hits and facets, and how a hit becomes the ALTO viewer's
// URL. The page holds the state; this file holds the decisions.

import { z } from "zod";

// One page of hits is a reading aid; the bucket is the export. Matches the
// proxy's cap (packages/web/src/htrflow_web/search.py).
export const PAGE_ROWS = 20;

// Live search waits this long after the last keystroke before asking.
export const DEBOUNCE_MS = 250;

// Fuzzy-matching a short token finds mostly noise; an HTR misreading of a
// long word is one edit away, of a short one rarely the one you meant.
const FUZZY_MIN_LENGTH = 4;

// Solr query-parser special characters: escaped, a reader's `(` or `AND`
// is text, never syntax — the page has no advanced mode to lose it in.
const SPECIALS = /([+\-&|!(){}[\]^"~*?:\\/])/g;

export function escapeToken(token: string): string {
  return token.replace(SPECIALS, "\\$1");
}

// "god helsa" -> content_txt:(god helsa~1): every token must appear (the
// default operator is AND in this core's schema), long ones fuzzily.
export function buildQ(words: string): string {
  const tokens = words
    .trim()
    .split(/\s+/)
    .filter((t) => t !== "")
    .map((t) =>
      t.length >= FUZZY_MIN_LENGTH ? `${escapeToken(t)}~1` : escapeToken(t),
    );
  return tokens.length === 1
    ? `content_txt:${tokens[0]}`
    : `content_txt:(${tokens.join(" ")})`;
}

export interface SearchParams {
  words: string;
  pipelines: string[]; // facet selections; empty = all
  start: number; // paging offset
}

export function buildParams({
  words,
  pipelines,
  start,
}: SearchParams): URLSearchParams {
  const params = new URLSearchParams({
    q: buildQ(words),
    fl: "id,pipeline_s,volume_s,page_s,image_url_s,viewer_url_s",
    rows: String(PAGE_ROWS),
    start: String(start),
    hl: "on",
    "hl.fl": "content_txt",
    "hl.snippets": "3",
    "hl.fragsize": "200",
    facet: "on",
    "facet.field": "pipeline_s",
    "facet.mincount": "1",
  });
  for (const p of pipelines) params.append("fq", `pipeline_s:"${p}"`);
  return params;
}

// ---- The answer -----------------------------------------------------------

const hitSchema = z.object({
  id: z.string(),
  pipeline_s: z.string().optional(),
  volume_s: z.string().optional(),
  page_s: z.string().optional(),
  image_url_s: z.string().optional(),
  viewer_url_s: z.string().optional(),
});
export type Hit = z.infer<typeof hitSchema>;

const answerSchema = z.object({
  response: z.object({
    numFound: z.number(),
    docs: z.array(hitSchema),
  }),
  highlighting: z
    .record(
      z.string(),
      z.object({ content_txt: z.array(z.string()).optional() }),
    )
    .optional(),
  facet_counts: z
    .object({
      facet_fields: z.record(
        z.string(),
        z.array(z.union([z.string(), z.number()])),
      ),
    })
    .optional(),
});

export interface FacetCount {
  value: string;
  count: number;
}

export interface SearchAnswer {
  numFound: number;
  hits: Hit[];
  highlighting: Record<string, { content_txt?: string[] | undefined }>;
  pipelines: FacetCount[];
}

export class QuerySyntaxError extends Error {}

// Maps the HTTP answer to a SearchAnswer, or throws QuerySyntaxError when
// Solr's parser refused the query (400) — the page's "did you mean"-style
// hint, where every other failure is its plain error banner.
export function parseAnswer(status: number, body: unknown): SearchAnswer {
  if (status === 400)
    throw new QuerySyntaxError("Solr could not parse that query");
  const parsed = answerSchema.safeParse(body);
  if (!parsed.success)
    throw new Error(
      `unexpected search answer: ${parsed.error.issues[0]?.message}`,
    );
  const flat = parsed.data.facet_counts?.facet_fields?.pipeline_s ?? [];
  const pipelines: FacetCount[] = [];
  for (let i = 0; i + 1 < flat.length; i += 2) {
    pipelines.push({ value: String(flat[i]), count: Number(flat[i + 1]) });
  }
  return {
    numFound: parsed.data.response.numFound,
    hits: parsed.data.response.docs,
    highlighting: parsed.data.highlighting ?? {},
    pipelines,
  };
}

// One line of context around the match, else the transcript's opening when
// a hit has none (a filter-only match, a token the highlighter skipped).
export function snippetFor(
  hit: Hit,
  highlighting: SearchAnswer["highlighting"],
): string | null {
  return highlighting[hit.id]?.content_txt?.[0] ?? null;
}

// A hit's page in this deployment's own ALTO viewer (/alto?src=): the
// results base the deployment serves, plus the doc's id parts. The /alto
// route itself re-checks the URL against the results bucket (isResultUrl).
export function altoUrl(resultsBase: string, hit: Hit): string | null {
  if (!resultsBase || !hit.pipeline_s || !hit.volume_s || !hit.page_s)
    return null;
  const base = resultsBase.endsWith("/") ? resultsBase : `${resultsBase}/`;
  return `${base}htr-batch/${hit.pipeline_s}/${hit.volume_s}/alto/${hit.page_s}.xml`;
}

export function pageCount(numFound: number): number {
  return Math.max(1, Math.ceil(numFound / PAGE_ROWS));
}

export function pageOf(start: number): number {
  return Math.floor(start / PAGE_ROWS) + 1;
}
