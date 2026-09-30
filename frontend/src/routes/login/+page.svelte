<script lang="ts">
  import { login, safeNext } from "$lib/session.js";

  let username = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  const messages = {
    wrong: "The result store did not accept that user name or password.",
    throttled: "Too many failed attempts. Wait a minute and try again.",
    store: "The result store could not be reached. Try again shortly.",
  } as const;

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    busy = true;
    error = "";
    let outcome: Awaited<ReturnType<typeof login>>;
    try {
      outcome = await login(username, password);
    } catch {
      outcome = "store";
    }
    busy = false;
    password = "";
    if (outcome === "ok") {
      location.assign(
        safeNext(new URLSearchParams(location.search).get("next")),
      );
    } else {
      error = messages[outcome];
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
