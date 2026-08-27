<script lang="ts">
  import "../app.css";
  import { theme } from "$lib/theme.svelte.js";

  let { children } = $props();

  // The explicit choice lives on <html> so the :root tokens (and the body
  // background) follow it; no attribute means "follow the OS".
  $effect(() => {
    if (theme.choice === null) delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme.choice;
  });
</script>

{@render children()}
