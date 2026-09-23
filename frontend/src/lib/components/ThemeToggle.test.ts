import { fireEvent, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// The theme is one module-level store, read from storage when the module
// loads, so each test imports the toggle and the layout afresh -- the second
// test is a page loaded after the first one's choice was saved. Svelte and
// the renderer come fresh with them: one runtime must mount what it built.
let cleanup = () => {};

async function load(): Promise<{ pressed: () => string | null }> {
  vi.resetModules();
  const { createRawSnippet } = await import("svelte");
  const testing = await import("@testing-library/svelte");
  const { render } = testing;
  cleanup = testing.cleanup;
  const Layout = (await import("../../routes/+layout.svelte")).default;
  const ThemeToggle = (await import("./ThemeToggle.svelte")).default;
  render(Layout, {
    children: createRawSnippet(() => ({ render: () => "<main></main>" })),
  });
  render(ThemeToggle);
  return {
    pressed: () =>
      screen
        .getByRole("button", { name: "Dark theme" })
        .getAttribute("aria-pressed"),
  };
}

describe("the theme toggle", () => {
  let stored: Map<string, string>;

  beforeEach(() => {
    stored = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => stored.get(k) ?? null,
      setItem: (k: string, v: string) => void stored.set(k, v),
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete document.documentElement.dataset.theme;
  });

  test("a click applies the other theme to the page and remembers it", async () => {
    const { pressed } = await load();
    // No choice yet: the page follows the OS, which here is light.
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(pressed()).toBe("false");

    await fireEvent.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(pressed()).toBe("true");
    expect(stored.get("htr-theme")).toBe("dark");

    await fireEvent.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(pressed()).toBe("false");
    expect(stored.get("htr-theme")).toBe("light");
  });

  test("a choice saved earlier is the page's theme on the next load", async () => {
    stored.set("htr-theme", "dark");
    const { pressed } = await load();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(pressed()).toBe("true");
  });
});
