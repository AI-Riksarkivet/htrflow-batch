<script lang="ts">
  import { LOGIN_UNREACHABLE, login, safeNext } from "$lib/session.js";

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
  <h1>Log in</h1>
  <p>Use your result-store account.</p>
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
    <button type="submit" disabled={busy}>Log in</button>
  </form>
  {#if error}<p role="alert">{error}</p>{/if}
</main>

<style>
  .login {
    max-width: 22rem;
    margin: 4rem auto;
    padding: 0 1rem;
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
</style>
