<script lang="ts">
  import { effectiveTheme, toggleTheme } from "$lib/theme.svelte.js";

  const dark = $derived(effectiveTheme() === "dark");
</script>

<button
  type="button"
  class="theme-toggle"
  onclick={toggleTheme}
  aria-pressed={dark}
  aria-label="Dark theme"
  title={dark ? "Switch to light theme" : "Switch to dark theme"}
>
  <span aria-hidden="true">◐</span>
</button>

<style>
  .theme-toggle {
    flex-shrink: 0;
    cursor: pointer;
    background: var(--muted);
    color: var(--foreground);
    border: 1px solid var(--border);
    border-radius: 999px;
    width: 1.8rem;
    height: 1.8rem;
    line-height: 1;
    font-size: 1rem;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .theme-toggle:hover {
    border-color: var(--primary);
    color: var(--primary);
  }

  .theme-toggle:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }
</style>
