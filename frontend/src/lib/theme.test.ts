import { describe, expect, test } from "vitest";
import { nextTheme, readStoredTheme } from "./theme.svelte.js";

describe("readStoredTheme", () => {
  const storage = (value: string | null) => ({ getItem: () => value });

  test("accepts only the two known values", () => {
    expect(readStoredTheme(storage("dark"))).toBe("dark");
    expect(readStoredTheme(storage("light"))).toBe("light");
    expect(readStoredTheme(storage("DARK"))).toBeNull();
    expect(readStoredTheme(storage("<script>"))).toBeNull();
    expect(readStoredTheme(storage(null))).toBeNull();
    expect(readStoredTheme(null)).toBeNull();
  });

  test("a throwing storage reads as no choice", () => {
    expect(
      readStoredTheme({
        getItem: () => {
          throw new Error("blocked");
        },
      }),
    ).toBeNull();
  });
});

describe("nextTheme", () => {
  test("flips the effective theme, OS-derived when no choice", () => {
    expect(nextTheme(null, true)).toBe("light");
    expect(nextTheme(null, false)).toBe("dark");
    expect(nextTheme("dark", false)).toBe("light");
    expect(nextTheme("light", true)).toBe("dark");
  });
});
