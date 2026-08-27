import { describe, expect, test } from "vitest";
import { isTerminalLog, parseRunLog, splitLogLine } from "./runlog.js";

describe("parseRunLog", () => {
  test("classifies and groups a log covering all five kinds", () => {
    const log = [
      "2026-08-25 10:00:01 INFO Starting run for volume R1",
      '2026-08-25 10:00:02 INFO HTTP Request: POST http://model/infer "200 OK"',
      '2026-08-25 10:00:03 INFO HTTP Request: POST http://model/infer "200 OK"',
      "2026-08-25 10:00:04 INFO Initialized YOLO model",
      "2026-08-25 10:00:05 WARNING low confidence on page 3",
      "2026-08-25 10:00:06 ERROR failed to process page 4",
      "Traceback (most recent call last):",
      '  File "run.py", line 10, in <module>',
      '    raise ValueError("boom")',
      "2026-08-25 10:00:07 INFO Done",
    ].join("\n");

    const { groups } = parseRunLog(log);

    expect(groups).toEqual([
      {
        kind: "info",
        lines: ["2026-08-25 10:00:01 INFO Starting run for volume R1"],
      },
      {
        kind: "http",
        lines: [
          '2026-08-25 10:00:02 INFO HTTP Request: POST http://model/infer "200 OK"',
          '2026-08-25 10:00:03 INFO HTTP Request: POST http://model/infer "200 OK"',
        ],
      },
      {
        kind: "model",
        lines: ["2026-08-25 10:00:04 INFO Initialized YOLO model"],
      },
      {
        kind: "warning",
        lines: ["2026-08-25 10:00:05 WARNING low confidence on page 3"],
      },
      {
        kind: "error",
        lines: [
          "2026-08-25 10:00:06 ERROR failed to process page 4",
          "Traceback (most recent call last):",
          '  File "run.py", line 10, in <module>',
          '    raise ValueError("boom")',
        ],
      },
      { kind: "info", lines: ["2026-08-25 10:00:07 INFO Done"] },
    ]);
  });

  test("recognizes TrOCR init and running-inference lines as model", () => {
    const log = [
      "Model 'Riksarkivet/trocr-base' on device cuda:0",
      "Running inference on batch of 8",
    ].join("\n");
    const { groups } = parseRunLog(log);
    expect(groups).toEqual([
      {
        kind: "model",
        lines: [
          "Model 'Riksarkivet/trocr-base' on device cuda:0",
          "Running inference on batch of 8",
        ],
      },
    ]);
  });

  test("coalesces consecutive lines of the same kind into one group", () => {
    const log = ["INFO HTTP Request: GET /a", "INFO HTTP Request: GET /b"].join(
      "\n",
    );
    const { groups } = parseRunLog(log);
    expect(groups).toEqual([
      {
        kind: "http",
        lines: ["INFO HTTP Request: GET /a", "INFO HTTP Request: GET /b"],
      },
    ]);
  });

  test("unescapes bytes-repr logs (the pre-fix upload shape) before parsing", () => {
    const raw = "b'INFO line one\\nWARNING line two\\n'";
    const { groups } = parseRunLog(raw);
    expect(groups).toEqual([
      { kind: "info", lines: ["INFO line one"] },
      { kind: "warning", lines: ["WARNING line two"] },
    ]);
  });

  test("plain text (not bytes-repr) is left alone", () => {
    const { groups } = parseRunLog("INFO plain line");
    expect(groups).toEqual([{ kind: "info", lines: ["INFO plain line"] }]);
  });

  test("empty text yields no groups", () => {
    expect(parseRunLog("")).toEqual({ groups: [] });
  });
});

describe("splitLogLine", () => {
  test("splits a matching INFO line into time, level, and message", () => {
    const line = "2026-08-25 13:28:43,791 INFO Starting run for volume R1";
    expect(splitLogLine(line)).toEqual({
      time: "13:28:43.791",
      level: "INFO",
      msg: "Starting run for volume R1",
    });
  });

  test("splits a matching WARNING line into time, level, and message", () => {
    const line = "2026-08-25 13:28:44,002 WARNING low confidence on page 3";
    expect(splitLogLine(line)).toEqual({
      time: "13:28:44.002",
      level: "WARNING",
      msg: "low confidence on page 3",
    });
  });

  test("a non-matching line (e.g. ultralytics print output) is left whole", () => {
    const line = "Ultralytics YOLOv8.0.196 🚀 Python-3.11.4 torch-2.0.1 CPU";
    expect(splitLogLine(line)).toEqual({ time: null, level: null, msg: line });
  });

  test("an empty string is left whole", () => {
    expect(splitLogLine("")).toEqual({ time: null, level: null, msg: "" });
  });
});

describe("isTerminalLog", () => {
  test("success line ends the run", () => {
    expect(
      isTerminalLog(
        "2026-08-26 07:15:33,903 INFO Wrote AltoXML file to /work/outputs/alto/0022.xml\n" +
          "2026-08-26 08:50:01,000 INFO [R0001696] COMPLETE 480 pages (480 processed) in 5700.0s, viewer: http://x\n",
      ),
    ).toBe(true);
  });

  test("failure lines end the run", () => {
    expect(
      isTerminalLog(
        "... \n2026-08-26 07:15:33,903 ERROR transient failure in stream: boom\n",
      ),
    ).toBe(true);
    expect(
      isTerminalLog(
        "2026-08-26 07:15:33,903 ERROR permanent failure in setup: bad manifest\n",
      ),
    ).toBe(true);
  });

  test("an in-flight log is not terminal", () => {
    expect(
      isTerminalLog(
        "2026-08-26 07:15:33,903 INFO Wrote AltoXML file to /work/outputs/alto/0022.xml\n0022: Done!\n",
      ),
    ).toBe(false);
    expect(isTerminalLog("")).toBe(false);
  });

  test("only the tail counts", () => {
    const early =
      "2026-08-26 07:00:00,000 INFO [v] COMPLETE 2 pages (2 processed) in 1.0s, viewer: x\n";
    const filler = Array.from({ length: 600 }, (_, i) => `line ${i}`).join(
      "\n",
    );
    expect(isTerminalLog(early + filler)).toBe(false);
  });

  test("a failure marker followed by a long traceback is still terminal", () => {
    const marker =
      "2026-08-26 07:00:00,000 ERROR transient failure in stream: CUDA error\n";
    const traceback = Array.from(
      { length: 150 },
      (_, i) => `  File "x.py", line ${i}, in f`,
    ).join("\n");
    expect(isTerminalLog(marker + traceback)).toBe(true);
  });
});
