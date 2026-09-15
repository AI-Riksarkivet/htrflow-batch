import { describe, expect, test } from "vitest";
import {
  DEFAULT_API_BASE,
  DEFAULT_RESULTS_BASE,
  envInt,
  resolveApiBase,
  resolveResultsBase,
} from "./config.js";

describe("config", () => {
  test("window.API_BASE wins over the default; empty or missing falls back", () => {
    expect(resolveApiBase({ API_BASE: "http://elsewhere/api/v1" })).toBe(
      "http://elsewhere/api/v1",
    );
    expect(resolveApiBase({ API_BASE: "" })).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase({})).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase(undefined)).toBe(DEFAULT_API_BASE);
  });

  test("the default is same-origin /api/v1", () => {
    expect(DEFAULT_API_BASE).toBe("/api/v1");
  });

  test("window.RESULTS_BASE is where the results are; empty means nobody said", () => {
    expect(resolveResultsBase({ RESULTS_BASE: "https://pub/bucket" })).toBe(
      "https://pub/bucket",
    );
    expect(resolveResultsBase({ RESULTS_BASE: "" })).toBe(DEFAULT_RESULTS_BASE);
    expect(resolveResultsBase({})).toBe(DEFAULT_RESULTS_BASE);
    expect(DEFAULT_RESULTS_BASE).toBe("");
  });

  test("envInt accepts positive integers only", () => {
    expect(envInt("15000", 1)).toBe(15000);
    expect(envInt(undefined, 7)).toBe(7);
    expect(envInt("", 7)).toBe(7);
    expect(envInt("0", 7)).toBe(7);
    expect(envInt("-5", 7)).toBe(7);
    expect(envInt("1.5", 7)).toBe(7);
    expect(envInt("soon", 7)).toBe(7);
  });
});
