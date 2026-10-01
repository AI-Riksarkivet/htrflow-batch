/** The results proxy's login, behind which the whole site sits. */

export function loginUrl(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`;
}

/**
 * A result file the results proxy refused (a 403): the user is logged in,
 * and their store account may not read it (spec §7).
 */
export const FORBIDDEN_FILE = "Your account may not read this file.";

/** Send the browser to the login page, to come back to where it is now. */
export function goToLogin(): void {
  if (location.pathname === "/login") return;
  location.assign(
    loginUrl(location.pathname + location.search + location.hash),
  );
}

/**
 * Where to go after logging in: a path on this site, else "/". Parsed the
 * way a browser will, since it reads "/\\evil.example" and "/\t/evil.example"
 * as "//evil.example". Never the login page itself.
 */
export function safeNext(raw: string | null): string {
  if (!raw) return "/";
  try {
    const u = new URL(raw, location.origin);
    if (u.origin !== location.origin) return "/";
    if (u.pathname === "/login" || u.pathname.startsWith("/login/")) return "/";
    return u.pathname + u.search + u.hash;
  } catch {
    return "/";
  }
}

/**
 * The `detail` sentence the web front and the results proxy put in an error
 * body, when there is one.
 */
export async function errorDetail(res: Response): Promise<string | undefined> {
  try {
    const body: unknown = await res.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    return typeof detail === "string" && detail !== "" ? detail : undefined;
  } catch {
    return undefined;
  }
}

/** What the login page says when the results service cannot be reached. */
export const LOGIN_UNREACHABLE =
  "The result store could not be reached. Try again shortly.";

function sentence(detail: string): string {
  return detail.charAt(0).toUpperCase() + detail.slice(1) + ".";
}

/**
 * Log in. `null` when the proxy set the session cookie, else the sentence
 * the login page shows.
 */
export async function login(
  username: string,
  password: string,
): Promise<string | null> {
  const res = await fetch("/results/_login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, password }),
  });
  if (res.status === 204) return null;
  if (res.status === 401)
    return "The result store did not accept that user name or password.";
  // The proxy refuses a login whose Origin is not the address the web front
  // forwards as the browser's: a front proxy that drops X-Forwarded-Host.
  if (res.status === 403)
    return (
      "The login was refused: this page is not the site's own address. Open " +
      "the site at its usual address; if that is where you are, the proxy in " +
      "front of it must pass the browser's host and scheme on."
    );
  // The body the proxy refused before asking the store: too large for the
  // web front's pass-through (413), or fields past the proxy's limits (422).
  if (res.status === 413 || res.status === 422)
    return "The user name or password is too long or malformed.";
  // A 429 names its limit (per address, per user, or logins at once); a 502,
  // 503 or 504 says what did not answer.
  const detail = await errorDetail(res);
  if (res.status === 429)
    return detail
      ? sentence(detail)
      : "Too many login attempts. Wait a few minutes and try again.";
  return detail ? `${sentence(detail)} Try again shortly.` : LOGIN_UNREACHABLE;
}

export async function logout(): Promise<void> {
  await fetch("/results/_logout", {
    method: "POST",
    credentials: "same-origin",
  });
}
