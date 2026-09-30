<script lang="ts">
  import { logout } from "$lib/session.js";

  async function out() {
    await logout().catch(() => {});
    location.assign("/login");
  }
</script>

<button type="button" class="logout" onclick={out}>Log out</button>

<style>
  .logout {
    flex-shrink: 0;
    cursor: pointer;
    background: var(--muted);
    color: var(--foreground);
    border: 1px solid var(--border);
    border-radius: 999px;
    height: 1.8rem;
    padding: 0 0.7rem;
    font: inherit;
    font-size: 0.8rem;
  }

  .logout:hover {
    border-color: var(--primary);
    color: var(--primary);
  }

  .logout:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }
</style>
