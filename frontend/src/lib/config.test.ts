import { describe, expect, test } from "vitest";
import { DEFAULT_STATUS_URL, envInt, resolveStatusUrl } from "./config.js";

describe("config", () => {
  test("window.STATUS_URL wins over the default; empty or missing falls back", () => {
    expect(resolveStatusUrl({ STATUS_URL: "/status.sample.json" })).toBe(
      "/status.sample.json",
    );
    expect(resolveStatusUrl({ STATUS_URL: "" })).toBe(DEFAULT_STATUS_URL);
    expect(resolveStatusUrl({})).toBe(DEFAULT_STATUS_URL);
    expect(resolveStatusUrl(undefined)).toBe(DEFAULT_STATUS_URL);
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
