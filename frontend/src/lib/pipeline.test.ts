import { describe, expect, test } from "vitest";
import { modelLabel, modelUrl, pipelineModels } from "./pipeline.js";

// The shape the converter renders into the htr-pipeline-<id> ConfigMap.
const yaml = `steps:
- step: Segmentation
  settings:
    model: yolo
    model_settings:
      model: Riksarkivet/yolov9-regions-1
      revision: 6fb01d2e6b4ff1d0e1e30b5b5c1c1a2b3c4d5e6f
    generation_settings:
      batch_size: 4
- step: TextRecognition
  settings:
    model: TrOCR
    model_settings:
      model: Riksarkivet/trocr-base-handwritten-hist-swe-2
      model_kwargs:
        revision: aaaabbbbccccddddeeeeffff0000111122223333
- step: Export
  settings:
    dest: outputs/alto
    format: alto
`;

describe("pipelineModels", () => {
  test("reads both revision placements and skips a step with no model", () => {
    expect(pipelineModels(yaml)).toEqual([
      {
        id: "Riksarkivet/yolov9-regions-1",
        revision: "6fb01d2e6b4ff1d0e1e30b5b5c1c1a2b3c4d5e6f",
      },
      {
        id: "Riksarkivet/trocr-base-handwritten-hist-swe-2",
        revision: "aaaabbbbccccddddeeeeffff0000111122223333",
      },
    ]);
  });

  test("an unpinned model has no revision", () => {
    const unpinned = `steps:
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-lines-within-regions-1
`;
    expect(pipelineModels(unpinned)).toEqual([
      { id: "Riksarkivet/yolov9-lines-within-regions-1", revision: null },
    ]);
  });

  test("a revision under model_kwargs never leaks to the step before it", () => {
    const two = `steps:
- step: Segmentation
  settings:
    model: yolo
    model_settings:
      model: Riksarkivet/yolov9-regions-1
- step: TextRecognition
  settings:
    model: TrOCR
    model_settings:
      model: Riksarkivet/trocr-base-handwritten-hist-swe-2
      model_kwargs:
        revision: aaaabbbbccccddddeeeeffff0000111122223333
`;
    const models = pipelineModels(two);
    expect(models.map((m) => m.revision)).toEqual([
      null,
      "aaaabbbbccccddddeeeeffff0000111122223333",
    ]);
  });

  // TrOCR, WordLevelTrOCR, Donut and DiT pin their processor as well, under
  // processor_kwargs -- a second download, possibly from another repo. The
  // line names the model, so the processor's pin is never taken for it,
  // whichever of the two is written first.
  test("a processor's pin is not read as the model's", () => {
    const both = `steps:
- step: TextRecognition
  settings:
    model: TrOCR
    model_settings:
      model: Riksarkivet/trocr-base-handwritten-hist-swe-2
      processor_kwargs:
        revision: 1111111111111111111111111111111111111111
      model_kwargs:
        revision: aaaabbbbccccddddeeeeffff0000111122223333
`;
    expect(pipelineModels(both).map((m) => m.revision)).toEqual([
      "aaaabbbbccccddddeeeeffff0000111122223333",
    ]);
    const processorOnly = both.replace(/ {6}model_kwargs:\n.*\n/, "");
    expect(pipelineModels(processorOnly).map((m) => m.revision)).toEqual([
      null,
    ]);
  });

  test("no pipeline YAML is no models", () => {
    expect(pipelineModels("")).toEqual([]);
  });
});

describe("modelLabel / modelUrl", () => {
  const pinned = {
    id: "Riksarkivet/yolov9-regions-1",
    revision: "6fb01d2e6b4ff1d0e1e30b5b5c1c1a2b3c4d5e6f",
  };
  const unpinned = { id: "Riksarkivet/yolov9-regions-1", revision: null };

  test("the label is the repo name and a short revision", () => {
    expect(modelLabel(pinned)).toBe("yolov9-regions-1 @6fb01d2");
  });

  test("an unpinned model says so", () => {
    expect(modelLabel(unpinned)).toBe("yolov9-regions-1 unpinned");
  });

  test("the link is the repo tree at that commit", () => {
    expect(modelUrl(pinned)).toBe(
      "https://huggingface.co/Riksarkivet/yolov9-regions-1/tree/" +
        "6fb01d2e6b4ff1d0e1e30b5b5c1c1a2b3c4d5e6f",
    );
  });

  test("an unpinned model links to main", () => {
    expect(modelUrl(unpinned)).toBe(
      "https://huggingface.co/Riksarkivet/yolov9-regions-1/tree/main",
    );
  });
});
