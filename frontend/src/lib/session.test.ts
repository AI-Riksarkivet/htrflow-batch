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
    expect(safeNext("/alto?src=x#p")).toBe("/alto?src=x#p");
    expect(safeNext("/log?log=a&manifest=b")).toBe("/log?log=a&manifest=b");
    for (const bad of [
      "//evil.example",
      "https://evil.example",
      "/\\evil.example",
      "/\t/evil.example",
      "javascript:alert(1)",
      "/login",
      "/login?next=/x",
      "",
      null,
    ])
      expect(safeNext(bad)).toBe("/");
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
