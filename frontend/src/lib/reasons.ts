// Every failure a person reads on this page, in plain sentences: what
// happened, where, and what to do next. The read API and the wrapper keep
// their machine-readable forms (the `{stage, permanent, error}` object, the
// termination JSON, exit codes) — the humanising happens here, at the edge
// that talks to people, so there is exactly one place to read to know what a
// campaign page can say (B63 Task 20G).
import {
  ApiUnreachable,
  type CampaignNotice,
  type VolumeProgress,
  type VolumeReason,
} from "./api.js";
import { RELOAD_MS } from "./config.js";

/** A stage name from the wrapper, as the thing it was doing. */
// `config` is deliberately absent: it never reaches this map, because a
// config failure gets its own sentence below rather than a stage word.
const STAGE_WORDS: Record<string, string> = {
  setup: "reading the manifest",
  resume: "checking earlier results",
  load: "loading the model",
  stream: "processing pages",
  verify: "checking results",
  publish: "publishing results",
};

/** How many failed page names a verify sentence spells out before "and N more". */
const PAGES_SHOWN = 3;

/** The wrapper's own messages end in a full stop about as often as not. */
function stop(sentence: string): string {
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

/**
 * The page names inside a verify message's `missing=[...] failed=[...]`
 * lists (Python repr — `main._verify` builds it). With a `key` only that
 * list's names; without one, every name in the message. Deduplicated and in
 * the order they appear; an empty or unparseable message yields none, and
 * the sentence then just says how it went without naming pages.
 */
function pageNames(error: string, key?: "missing" | "failed"): string[] {
  const names: string[] = [];
  const lists =
    key === undefined
      ? /=\[([^\]]*)\]/g
      : new RegExp(`${key}=\\[([^\\]]*)\\]`, "g");
  for (const list of error.matchAll(lists)) {
    for (const quoted of (list[1] ?? "").matchAll(/'([^']*)'/g)) {
      const name = quoted[1];
      if (name !== undefined && name !== "" && !names.includes(name)) {
        names.push(name);
      }
    }
  }
  return names;
}

/** "(p012, p045 and 2 more)", or nothing when the message named no page. */
function naming(names: string[]): string {
  const rest = names.length - PAGES_SHOWN;
  if (names.length === 0) return "";
  return ` (${names.slice(0, PAGES_SHOWN).join(", ")}${
    rest > 0 ? ` and ${rest} more` : ""
  })`;
}

/**
 * A verify failure is now one of two things (the product owner, 2026-09-14):
 * pages MISSING from the results — neither uploaded nor recorded as failed,
 * which a retry converges on — or a run in which every page processed failed,
 * which is a broken model or a dead GPU rather than a bad volume. A page that
 * failed with a reason recorded no longer fails verify at all: the volume
 * completes and names it in manifest.json, so no sentence here covers it.
 */
function describeVerify(error: string): string {
  if (error.startsWith("verify failed: all ")) {
    const n = pageNames(error, "failed").length;
    const count = n === 0 ? "No page" : `None of the ${n} pages`;
    return (
      `${count} processed in this attempt produced a result; the volume is ` +
      "retried automatically — check the model and the GPU."
    );
  }
  // `missing=` first: the message carries a `failed=` list too, and those
  // pages are accounted for — naming them here would tell the reader they
  // are coming back when only the missing ones are.
  const named = pageNames(error, "missing");
  const names = named.length === 0 ? pageNames(error) : named;
  const count = names.length === 0 ? "Some" : String(names.length);
  const plural = names.length === 1 ? "page is" : "pages are";
  return (
    `${count} ${plural} missing from the results${naming(names)}; ` +
    "the volume is retried automatically and only those pages are redone."
  );
}

/**
 * One sentence for a failed volume. The known failures each get their own —
 * they are the ones an operator meets weekly — and anything else is still a
 * sentence: what stage it was in, what went wrong, and whether it comes back.
 */
export function describeReason(reason: VolumeReason): string {
  const { stage, permanent, error } = reason;
  // DeadlineExceeded is the pod's activeDeadlineSeconds, named by the API
  // (projection._name_the_deadline); MAX_SECONDS is what the wrapper's own
  // watchdog wrote before Task 25 moved that budget to the pod, and a volume
  // whose last pod predates the change still says it.
  if (error === "DeadlineExceeded" || error === "MAX_SECONDS") {
    return (
      "Stopped when this volume's time budget ran out; the next attempt " +
      "resumes from the pages already finished."
    );
  }
  if (error === "SIGTERM") {
    return (
      "The pod was stopped by the cluster (a node drain or a pause); " +
      "the volume will be retried."
    );
  }
  if (error.startsWith("verify failed")) return describeVerify(error);
  if (stage === null && permanent === null && /^\s*[{[]/.test(error)) {
    // The API could not parse the pod's termination message and handed the
    // raw text over. Rendering it would put a JSON blob in front of a
    // person; the run log is one click away in every place this appears.
    return (
      "The pod stopped without a message this page can read; open the run " +
      "log to see what happened."
    );
  }
  if (stage === "config") {
    // Not a manifest problem and not the campaign author's to fix: the env
    // comes from converter.yaml and the chart, so this reader is being sent
    // somewhere else entirely (the wrapper sets this stage around
    // Config.from_env for exactly that reason).
    return (
      `The volume's settings are incomplete or wrong: ${stop(error)} ` +
      "This is a deployment problem, not a manifest problem — check the " +
      "campaign's converter.yaml and the chart values."
    );
  }
  if (stage === "setup" && permanent === true) {
    return (
      `The IIIF manifest could not be read: ${stop(error)} ` +
      "Fix the manifest URL in the campaign file — this volume will not be " +
      "retried."
    );
  }
  if (stage === "warmup" && permanent !== null) {
    // The warm-up Job's own backoffLimit has already exhausted its retries
    // by the time the API reports "failed" (Task 28) — re-applying the
    // pipeline is what starts a new warm-up, not time.
    return permanent
      ? `The warm-up failed: ${stop(error)} Fix the pipeline file, then ` +
          "re-apply it — the warm-up will not retry on its own."
      : `The warm-up failed: ${stop(error)} Re-apply the pipeline to try ` +
          "again.";
  }
  const doing = stage === null ? undefined : STAGE_WORDS[stage];
  const head =
    doing === undefined
      ? `Failed: ${stop(error)}`
      : `Failed while ${doing}: ${stop(error)}`;
  if (permanent === null) return head;
  return permanent
    ? `${head} This volume will not be retried — fix the cause, then put the volume in a new campaign.`
    : `${head} It will be retried automatically.`;
}

/**
 * "12 s ago" / "5 min ago" / "2 h ago" from a count of seconds. The API
 * computes that count itself, from its own clock at fetch time
 * (`VolumeProgress.ageSeconds`) — never here from `updatedAt` compared
 * against the browser's `Date.now()`, since a reader's clock running fast or
 * slow could then show "0 s ago" (or a negative number) for a row that has
 * not just updated at all.
 */
function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

/**
 * What a volume is doing and when it last said so: "processing pages ·
 * updated 12 s ago". The page counts are deliberately NOT here — the status
 * column shows them in the same shape zone 2 shows the campaign's, and
 * saying them again in the same cell made the column two sentences (the
 * product owner, 2026-09-16). Empty when there is nothing to add to the
 * numbers: a finished volume with no timestamp says nothing here.
 */
export function describeProgress(progress: VolumeProgress): string {
  const { stage, ageSeconds } = progress;
  const parts = [];
  // `done` is the one stage left out: the state word beside this line already
  // says it, and a volume now finishes WITH failed pages recorded, so the
  // cell must not read as the word twice over.
  if (stage !== null && stage !== "done")
    parts.push(STAGE_WORDS[stage] ?? stage);
  if (ageSeconds !== null) parts.push(`updated ${formatAge(ageSeconds)}`);
  return parts.join(" · ");
}

function count(n: number, noun: string, verb = ""): string[] {
  return n > 0 ? [`${n} ${noun}${n === 1 ? "" : "s"}${verb}`] : [];
}

/**
 * What went wrong in a campaign, in one line, or null when nothing did:
 * "1 page failed · 2 errors · page 0044: htrflow's Segmentation worker
 * thread died". The counts come first because they are the same shape for
 * every campaign and the eye can scan them; the wrapper's own sentence
 * follows, and the card shows the whole of it in the tooltip. `errors`
 * counts ERROR-and-worse only — the wrapper's own benign WARNINGs (a
 * pipeline rebuild, "manifest covers n/m pages") must not read as something
 * wrong on a healthy run.
 */
export function describeNotice(notice: CampaignNotice): string | null {
  const { pagesFailed, errors, lastError } = notice;
  if (pagesFailed === 0 && errors === 0) return null;
  const parts = [
    ...count(pagesFailed, "page", " failed"),
    ...count(errors, "error"),
  ];
  const last = describeLastError(lastError);
  if (last !== null) parts.push(last);
  return parts.join(" · ");
}

/**
 * The most recent page failure as one sentence: "page 0044: htrflow's
 * Segmentation worker thread died". The wrapper writes the page into its own
 * message as often as not, and the API sends the page beside it — prefixing
 * regardless read "page 0044: page 0044: htrflow's ..." on a live campaign
 * (the product owner, 2026-09-16). Only this page, spelled this way: a
 * message naming a DIFFERENT page is saying something the prefix is not.
 */
export function describeLastError(
  lastError: CampaignNotice["lastError"],
): string | null {
  if (lastError === null) return null;
  const { page, error } = lastError;
  const named = page !== null && error.startsWith(`page ${page}:`);
  return page === null || named ? error : `page ${page}: ${error}`;
}

/**
 * One sentence for a read-API call that did not come back usable.
 * `showingLast` says whether the caller still has an older answer on screen,
 * which is the difference between "nothing is here" and "this is stale".
 */
/** Rows the list could not read (api.fetchJobs): hidden, counted, one next step. */
export function describeUnreadable(n: number): string {
  const what =
    n === 1
      ? "1 campaign could not be read and is not shown"
      : `${n} campaigns could not be read and are not shown`;
  return (
    `${what}. Reload the page; if it keeps happening, the page and the ` +
    "service are running different versions."
  );
}

export function describeApiError(e: unknown, showingLast: boolean): string {
  if (!(e instanceof ApiUnreachable)) {
    // A Zod parse failure: the service answered, but not in the shape this
    // build knows. Nothing the reader can fix except a reload.
    return (
      "The campaign service answered in a form this page doesn't " +
      "understand. Reload the page; if it keeps happening, the page and the " +
      "service are running different versions."
    );
  }
  if (e.message === "HTTP 404") {
    // Not an aged-out Job: a campaign whose Job its TTL reaped is still
    // served from the record its ConfigMaps keep. Only deleting the
    // campaign file from the repo removes those, so a 404 is that.
    return (
      "This campaign is gone: its campaign file has been removed from the " +
      "campaigns repo."
    );
  }
  // An HTTP status is worth showing an operator; a raw fetch error string
  // ("Failed to fetch", "NetworkError when attempting...") is not.
  const status = /^HTTP \d+$/.test(e.message) ? ` (${e.message})` : "";
  const last = showingLast ? "Showing the list we last received. " : "";
  return (
    `Can't reach the campaign service right now${status}. ${last}` +
    `Retrying every ${Math.round(RELOAD_MS / 1000)} seconds.`
  );
}
