<script lang="ts">
  import { LOGIN_UNREACHABLE, login, safeNext } from "#lib/session.js";

  let username = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    busy = true;
    error = "";
    let refused: string | null;
    try {
      refused = await login(username, password);
    } catch {
      refused = LOGIN_UNREACHABLE;
    }
    busy = false;
    password = "";
    if (refused === null) {
      location.assign(
        safeNext(new URLSearchParams(location.search).get("next")),
      );
    } else {
      error = refused;
    }
  }
</script>

<main class="login">
  <!-- One panel in the campaign card's shape: --card fill, 3px left edge
       carrying the accent. The one page with no header, so it wears the
       logo and wordmark itself. -->
  <div class="panel">
    <div class="brand">
      <img class="logo" src="/ra.svg" alt="Riksarkivet" />
      <span class="wordmark">HTR Campaigns</span>
    </div>
    <h1>Log in</h1>
    <form onsubmit={submit}>
      <label
        >User name
        <input bind:value={username} autocomplete="username" required /></label
      >
      <label
        >Password
        <input
          type="password"
          bind:value={password}
          autocomplete="current-password"
          required
        />
      </label>
      <button type="submit" disabled={busy} aria-busy={busy}>Log in</button>
    </form>
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  </div>
</main>

<style>
  .login {
    display: grid;
    place-items: center;
  }

  .panel {
    width: 100%;
    max-width: 22rem;
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--primary);
    border-radius: var(--radius);
    padding: 1.75rem 1.75rem 1.5rem;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 1.25rem;
  }

  .logo {
    height: 1.6rem;
    width: auto;
  }

  .wordmark {
    font-weight: 600;
    letter-spacing: 0.01em;
  }

  h1 {
    margin-bottom: 1.25rem;
  }

  form,
  label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  form {
    gap: 1rem;
  }

  label {
    font-size: 0.8rem;
    color: var(--muted-foreground);
  }

  input {
    height: 2rem;
    padding: 0 0.7rem;
    font: inherit;
    font-size: 0.9rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--muted);
    color: var(--foreground);
  }

  input:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }

  button {
    height: 2.1rem;
    margin-top: 0.25rem;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--primary);
    border-radius: var(--radius);
    background: var(--primary);
    color: var(--on-strong);
  }

  button:hover:not(:disabled) {
    background: color-mix(in oklab, var(--primary), var(--foreground) 12%);
  }

  button:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }

  button:disabled {
    cursor: wait;
    opacity: 0.6;
  }

  .error-banner {
    margin: 1rem 0 0;
    padding: 0.5rem 0.75rem;
    font-size: 0.85rem;
    background: var(--destructive-soft);
    color: var(--destructive);
    border-left: 3px solid var(--destructive);
    border-radius: var(--radius);
  }
</style>
