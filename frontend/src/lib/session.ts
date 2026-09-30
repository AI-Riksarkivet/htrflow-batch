/** The results proxy's login, behind which the whole site sits. */

export function loginUrl(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`;
}

/** Send the browser to the login page, to come back to where it is now. */
export function goToLogin(): void {
  location.assign(
    loginUrl(location.pathname + location.search + location.hash),
  );
}

export function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export async function login(
  username: string,
  password: string,
): Promise<"ok" | "wrong" | "throttled" | "store"> {
  const res = await fetch("/results/_login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, password }),
  });
  if (res.status === 204) return "ok";
  if (res.status === 401) return "wrong";
  if (res.status === 429) return "throttled";
  return "store";
}

export async function logout(): Promise<void> {
  await fetch("/results/_logout", {
    method: "POST",
    credentials: "same-origin",
  });
}
