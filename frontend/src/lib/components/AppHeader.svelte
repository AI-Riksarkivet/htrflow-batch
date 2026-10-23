<script lang="ts">
  // The one header every page wears — same logo, same title, same search
  // field in the same place, so navigating between pages never rearranges
  // the chrome. Page-specific items (the run log's live badge, the ALTO
  // page's raw toggle) come in through the `extras` snippet, ahead of the
  // shared right side.
  //
  // The search field: on the campaigns page it only navigates to /search
  // (the default `onsearch`); on /search it IS the search box, bound to
  // the page's query — submit and input are the page's to act on.
  import { goto } from "$app/navigation";
  import type { Snippet } from "svelte";
  import LogoutButton from "#lib/components/LogoutButton.svelte";
  import ThemeToggle from "#lib/components/ThemeToggle.svelte";
  import { fetchVersion } from "#lib/api.js";
  import { REPO_URL } from "#lib/config.js";

  let {
    query = $bindable(""),
    onsearch,
    oninput,
    extras,
  }: {
    query?: string;
    onsearch?: (words: string) => void;
    oninput?: (words: string) => void;
    extras?: Snippet;
  } = $props();

  // What is deployed, read once — nothing can change it while the page is
  // open, and a version nobody could fetch is simply not shown: it is a
  // footnote in the header, never a reason for an alert over the page.
  let version = $state<string | null>(null);
  let webVersion = $state("");

  $effect(() => {
    fetchVersion()
      .then((answer) => {
        version = `htrflow-batch ${answer.version}`;
        webVersion = answer.web;
      })
      .catch(() => {
        // Nothing to name the build with; the header just has no version.
      });
  });

  function submit(e: SubmitEvent): void {
    e.preventDefault();
    const words = query.trim();
    if (onsearch) return onsearch(words);
    void goto(
      words === "" ? "/search" : `/search?q=${encodeURIComponent(words)}`,
    );
  }
</script>

<header class="page">
  <div class="title-row">
    <img class="logo" src="/ra.svg" alt="Riksarkivet" />
    <h1><a class="home" href="/">HTR Campaigns</a></h1>
  </div>
  <div class="header-right">
    {@render extras?.()}
    <!-- Always there, empty until the version is read: it arrives after
         the page, and taking room only then widened this half of the
         header -- on a phone, onto a second line that pushed the content
         down. -->
    <span
      class="version"
      title={version === null
        ? undefined
        : `${version}, read API ${webVersion}`}>{version ?? ""}</span
    >
    <form class="header-search" onsubmit={submit}>
      <input
        type="search"
        bind:value={query}
        oninput={() => oninput?.(query)}
        placeholder="Search pages…"
        aria-label="Search the transcribed pages"
      />
    </form>
    <!-- The GitHub mark, inline: the page loads nothing from a third
         origin (its CSP would not allow it anyway). -->
    <a
      class="repo"
      href={REPO_URL}
      target="_blank"
      rel="noopener"
      aria-label="htrflow-batch on GitHub"
    >
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"
        ><path
          fill="currentColor"
          d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"
        /></svg
      >
    </a>
    <LogoutButton />
    <ThemeToggle />
  </div>
</header>

<style>
  .title-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }

  .logo {
    height: 1.6rem;
    width: auto;
  }

  .home {
    color: inherit;
    text-decoration: none;
  }
  .home:hover {
    text-decoration: underline;
  }

  /* Single-line title on every page, so every header centres vertically. */
  .page {
    align-items: center;
  }

  /* Right-aligned on the line of its own a phone gives it too, so the
     version below grows leftwards there as well. */
  .header-right {
    margin-left: auto;
  }

  /* Both sit in the muted colour and take the link colour on hover: the
     header is chrome, not content, in either theme. The version has room
     held for it before there is one, filled from the right so a longer one
     grows away from the icons beside it; on the narrowest phone it gives
     way, cut short, with the whole name in its title. */
  .version {
    min-width: min(29ch, 100vw - 7rem);
    max-width: calc(100vw - 7rem);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    text-align: right;
    color: var(--muted-foreground);
    font-size: 0.8rem;
    font-variant-numeric: tabular-nums;
  }

  /* Matches the header's pill buttons in height and tone. */
  .header-search input {
    height: 1.8rem;
    padding: 0 0.7rem;
    font: inherit;
    font-size: 0.8rem;
    width: 11rem;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--muted);
    color: var(--foreground);
  }

  .header-search input:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }

  .header-search input::placeholder {
    color: var(--muted-foreground);
  }

  .repo {
    display: inline-flex;
    color: var(--muted-foreground);
  }

  .repo:hover {
    color: var(--primary);
  }

  .repo:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }
</style>
