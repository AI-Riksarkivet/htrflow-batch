// Every failure a person reads on this page, in plain sentences: what
// happened, where, and what to do next. The read API and the wrapper keep
// their machine-readable forms (the `{stage, permanent, error}` object, the
// termination JSON, exit codes) — the humanising happens here, at the edge
// that talks to people, so there is exactly one place to read to know what a
// campaign page can say (B63 Task 20G).
import { ApiUnreachable, type VolumeReason } from "./api.js";
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
 * lists (Python repr — `main._verify` builds it). Deduplicated and in the
 * order they appear; an empty or unparseable message yields none, and the
 * sentence then just says how it went without naming pages.
 */
function pageNames(error: string): string[] {
  const names: string[] = [];
  for (const list of error.matchAll(/=\[([^\]]*)\]/g)) {
    for (const quoted of (list[1] ?? "").matchAll(/'([^']*)'/g)) {
      const name = quoted[1];
      if (name !== undefined && name !== "" && !names.includes(name)) {
        names.push(name);
      }
    }
  }
  return names;
}

function describeVerify(error: string): string {
  const names = pageNames(error);
  const rest = names.length - PAGES_SHOWN;
  const shown =
    names.length === 0
      ? ""
      : ` (${names.slice(0, PAGES_SHOWN).join(", ")}${
          rest > 0 ? ` and ${rest} more` : ""
        })`;
  const count = names.length === 0 ? "Some" : String(names.length);
  const plural = names.length === 1 ? "page" : "pages";
  return (
    `${count} ${plural} could not be processed${shown}; ` +
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
 * One sentence for a read-API call that did not come back usable.
 * `showingLast` says whether the caller still has an older answer on screen,
 * which is the difference between "nothing is here" and "this is stale".
 */
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
    return (
      "This campaign no longer exists (finished campaigns are removed " +
      "after 24 hours)."
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
