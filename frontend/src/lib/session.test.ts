import { describe, expect, test, vi } from "vitest";
import { LOGIN_UNREACHABLE, login, loginUrl, safeNext } from "./session";

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
  test("login words each of the proxy's answers", async () => {
    for (const [status, want] of [
      [204, null],
      [401, /did not accept/],
      [403, /not the site's own address/],
      [429, /Too many login attempts/],
      [413, /too long or malformed/],
      [422, /too long or malformed/],
      [502, LOGIN_UNREACHABLE],
      [503, LOGIN_UNREACHABLE],
      [504, LOGIN_UNREACHABLE],
    ] as const) {
      globalThis.fetch = vi
        .fn()
        .mockResolvedValue(new Response(null, { status }));
      const said = await login("a", "b");
      if (want === null || typeof want === "string") expect(said).toBe(want);
      else expect(said).toMatch(want);
    }
  });

  test("a limit the proxy names is shown in its own words", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      Response.json(
        {
          detail:
            "too many failed logins for this user: wait five minutes and try again",
        },
        { status: 429 },
      ),
    );
    expect(await login("a", "b")).toBe(
      "Too many failed logins for this user: wait five minutes and try again.",
    );
  });

  test("a results service that did not answer is not called the store", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: "the results service did not answer" },
          { status: 502 },
        ),
      );
    expect(await login("a", "b")).toBe(
      "The results service did not answer. Try again shortly.",
    );
  });

  test("a cross-origin refusal says the address is wrong, not the store", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 403 }));
    const sentence = await login("a", "b");
    expect(sentence).toContain("not the site's own address");
    expect(sentence).not.toContain("store");
  });

  test("an answer with no detail falls back to a sentence of its own", async () => {
    for (const status of [429, 502]) {
      globalThis.fetch = vi
        .fn()
        .mockResolvedValue(new Response("<html>", { status }));
      expect(await login("a", "b")).toMatch(/\.$/);
    }
  });
});
