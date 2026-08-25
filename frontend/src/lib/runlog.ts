// Pure parser for htrflow wrapper run logs. Groups consecutive lines by a
// coarse classification so the viewer can collapse noisy HTTP chatter and
// highlight model/warning/error lines without a logging framework on either
// end — the wrapper just writes plain text to the pod's stdout.

export type LogGroupKind = "http" | "model" | "warning" | "error" | "info";

export interface LogGroup {
  kind: LogGroupKind;
  lines: string[];
}

export interface ParsedLog {
  groups: LogGroup[];
}

const MODEL_RE = /Initialized (YOLO|TrOCR)|Model '.*' on device|Running inference/;

/**
 * Old broken logs (fixed by the k8s.py `job_logs` decode bug) were uploaded
 * as the Python `str(bytes)` repr: `b'line one\nline two'` — a single line
 * with literal backslash-n escapes rather than real newlines. Unwrap that
 * shape back into real text before classifying, so historical logs still
 * render sensibly.
 */
function unescapeBytesRepr(text: string): string {
  if (!text.startsWith("b'")) return text;
  let inner = text.slice(2);
  if (inner.endsWith("'")) inner = inner.slice(0, -1);
  return inner.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
}

function isTracebackContinuation(line: string): boolean {
  return /^\s/.test(line) || line.trimStart().startsWith('File "');
}

export function parseRunLog(text: string): ParsedLog {
  const unescaped = unescapeBytesRepr(text);
  const rawLines = unescaped.split("\n");
  // A trailing newline splits into a trailing "" element — drop it so it
  // doesn't become a spurious empty final group.
  const lastLine = rawLines[rawLines.length - 1];
  const lines = rawLines.length > 0 && lastLine === "" ? rawLines.slice(0, -1) : rawLines;

  const groups: LogGroup[] = [];
  let inTraceback = false;
  for (const line of lines) {
    let kind: LogGroupKind;
    if (inTraceback && isTracebackContinuation(line)) {
      kind = "error";
    } else {
      inTraceback = false;
      if (line.includes("INFO HTTP Request:")) {
        kind = "http";
      } else if (MODEL_RE.test(line)) {
        kind = "model";
      } else if (line.includes("WARNING")) {
        kind = "warning";
      } else if (line.includes("ERROR") || line.includes("Traceback")) {
        kind = "error";
        inTraceback = true;
      } else {
        kind = "info";
      }
    }

    const last = groups.at(-1);
    if (last !== undefined && last.kind === kind) {
      last.lines.push(line);
    } else {
      groups.push({ kind, lines: [line] });
    }
  }
  return { groups };
}
