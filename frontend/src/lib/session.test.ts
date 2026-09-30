import { describe, expect, test, vi } from "vitest";
import { login, loginUrl, safeNext } from "./session";

describe("session helpers", () => {
  test("loginUrl keeps where the user was", () => {
    expect(loginUrl("/log?log=a&manifest=b")).toBe(
      "/login?next=%2Flog%3Flog%3Da%26manifest%3Db",
    );
  });
  test("safeNext refuses other sites", () => {
    expect(safeNext("/alto?src=x")).toBe("/alto?src=x");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });
  test("login maps the proxy's answers", async () => {
    for (const [status, want] of [
      [204, "ok"],
      [401, "wrong"],
      [429, "throttled"],
      [502, "store"],
    ] as const) {
      globalThis.fetch = vi
        .fn()
        .mockResolvedValue(new Response(null, { status }));
      expect(await login("a", "b")).toBe(want);
    }
  });
});
