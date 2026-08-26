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

const LOG_LINE_RE =
  /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3}) (INFO|WARNING|ERROR|DEBUG|CRITICAL) (.*)$/;

export interface SplitLogLine {
  time: string | null;
  level: string | null;
  msg: string;
}

/**
 * Split one htrflow/wrapper log line (python `logging` format — date, time
 * with comma-milliseconds, LEVEL, message) into columns for the viewer. Lines
 * that don't match the shape (ultralytics prints, traceback continuations,
 * bytes-repr artifacts, ...) render as-is with no time/level.
 */
export function splitLogLine(line: string): SplitLogLine {
  const m = LOG_LINE_RE.exec(line);
  if (m === null) return { time: null, level: null, msg: line };
  // Groups are non-optional in LOG_LINE_RE, so a match guarantees all five
  // captures are present — TS just can't see that through RegExpExecArray.
  const time = m[2] as string;
  const ms = m[3] as string;
  const level = m[4] as string;
  const msg = m[5] as string;
  return { time: `${time}.${ms}`, level, msg };
}

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

// The wrapper's last lines on each exit path (main.py): success logs
// "[<volume>] COMPLETE <n> pages ...", failures log "<kind> failure in <stage>:".
const TERMINAL_RE = /\] COMPLETE \d+ pages|(permanent|transient) failure in \w+:/;

/**
 * True once a shipped run log carries the wrapper's terminal line, i.e. the
 * volume's process has exited and the object will not change again. Only the
 * tail is inspected: the marker is always among the last lines, and live logs
 * can be large.
 */
export function isTerminalLog(text: string): boolean {
  const tail = text.split("\n").slice(-50).join("\n");
  return TERMINAL_RE.test(tail);
}
