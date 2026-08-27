// One theme store for every route. null = follow the OS
// (`prefers-color-scheme`); an explicit choice persists to localStorage.
// The pages are prerendered, so storage and matchMedia are only touched in
// the browser, never during the Node prerender pass.
import { browser } from "$app/environment";

export type Theme = "light" | "dark";
export const THEME_KEY = "htr-theme";

/** localStorage is user-writable: anything but the two known values is null. */
export function readStoredTheme(
  storage: Pick<Storage, "getItem"> | null,
): Theme | null {
  try {
    const value = storage?.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** The theme a toggle switches to, given the current choice and the OS. */
export function nextTheme(choice: Theme | null, prefersDark: boolean): Theme {
  const effective = choice ?? (prefersDark ? "dark" : "light");
  return effective === "dark" ? "light" : "dark";
}

// matchMedia is missing in jsdom (tests) and some embedded webviews.
function darkQuery(): MediaQueryList | null {
  return browser && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
}

export const theme = $state<{ choice: Theme | null; prefersDark: boolean }>({
  choice: browser ? readStoredTheme(localStorage) : null,
  prefersDark: darkQuery()?.matches ?? false,
});

darkQuery()?.addEventListener("change", (e) => (theme.prefersDark = e.matches));

export function effectiveTheme(): Theme {
  return theme.choice ?? (theme.prefersDark ? "dark" : "light");
}

export function toggleTheme(): void {
  theme.choice = nextTheme(theme.choice, theme.prefersDark);
  if (!browser) return;
  try {
    localStorage.setItem(THEME_KEY, theme.choice);
  } catch {
    // Private mode / blocked storage: the choice still applies for this page.
  }
}
