import { describe, expect, test } from "vitest";
import { formatQuality, hasQualityStep, sortByQuality } from "./quality.js";

describe("quality", () => {
  test("two decimals, empty when there is none", () => {
    expect(formatQuality(0.87312)).toBe("0.87");
    expect(formatQuality(1)).toBe("1.00");
    expect(formatQuality(null)).toBe("");
    expect(formatQuality(undefined)).toBe("");
  });

  test("the step is found by htrflow's own case-blind name", () => {
    expect(hasQualityStep(["Segmentation", "QualityPrediction"])).toBe(true);
    expect(hasQualityStep(["qualityprediction"])).toBe(true);
    expect(hasQualityStep(["Segmentation"])).toBe(false);
  });

  test("worst first, unscored last, ties by id", () => {
    const rows = [
      { id: "c", quality: 0.5 },
      { id: "a" },
      { id: "b", quality: 0.2 },
      { id: "d", quality: 0.5 },
    ];
    expect(sortByQuality(rows).map((r) => r.id)).toEqual(["b", "c", "d", "a"]);
  });
});
