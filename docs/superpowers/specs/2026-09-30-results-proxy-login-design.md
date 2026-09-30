# Results behind a login — design

## Goal

Browsers stop reading the results bucket directly. A new pod, the results proxy, serves the result files on
the web front's own origin, and reads them from S3 with **the logged-in user's own S3 keys**. The web front
and the viewer are behind the same login.

Today the campaign browser and the viewer fetch `iiif.json`, ALTO, `progress.json`, `manifest.json` and run
logs straight from the bucket. That needs three things of the store: anonymous read, CORS for the web
front's origin, and a TLS certificate every browser trusts. A store that provides none of them (a private
namespace, an internal certificate) cannot be viewed at all. Anonymous read also makes every result public
to anyone who can reach the store.

Success means:

- The bucket needs no anonymous read and no CORS; its certificate only has to be accepted by the proxy
  (`S3_VERIFY_TLS`, until it can be verified).
- Without logging in, nothing is visible: the campaign browser, `/api/v1/*` and every `/results/…` URL
  answer `401`.
- After logging in with a store account, a finished volume opens in the viewer with text and overlays, the
  run log view follows a running volume, and each person sees exactly what the store lets their account
  read.
- No pod holds a bucket credential of its own for reading results, and no browser holds an S3 key or a
  password in JavaScript.

Out of scope: logins through other identity providers (the earlier plan for this story named GitHub and
Hugging Face org membership through Dex and oauth2-proxy); user management; writing to the bucket from the
web; serving page images (they come from the IIIF source, not the bucket, and stay that way).

## 1. Components

**`htrflow-results`, the proxy.** A second Deployment on the web image (`packages/web`, a second
entrypoint `htrflow-results`), with:

- no bucket credentials of its own, and no Kubernetes token (`automountServiceAccountToken: false`, no
  Role);
- the store's endpoint, bucket and TLS switch from the S3 Secret's non-secret keys (`S3_ENDPOINT`,
  `S3_BUCKET`, optional `S3_VERIFY_TLS`), by `secretKeyRef`. The Secret's `credentials` key is not mounted;
- the session encryption key from its own Secret (`results.sessionSecret`), mounted as a file.

**`htrflow-web`.** Unchanged in what it holds: its Kubernetes token, nothing else. Its reads of the bucket
(progress) go through the proxy with the caller's session (section 4). It passes `/results/…` requests
through to the proxy (section 5), so one origin serves both, with or without an Ingress.

**The frontend.** A login page and a log-out link. The viewer is unchanged: its requests to `/results/…` are
same-origin and carry the session cookie.

## 2. Login and the session

**`POST /results/_login`**, a form with `username` and `password`, over HTTPS.

1. The `Origin` header must be the site's own; otherwise `403`.
2. The proxy derives the store's S3 keys the way HCP defines them: access key = base64 of the username,
   secret key = the hex MD5 of the password. (A store that issues S3 keys directly takes the access key and
   secret key in the same two fields; see section 6.)
3. It checks them with one request, a `HEAD` on a key under the release's namespace:
   - `InvalidAccessKeyId` or `SignatureDoesNotMatch`: wrong username or password → `401`, with a sentence
     saying so.
   - any other answer, a `404` or `403` included: the credentials are valid → the login succeeds. What the
     account may read is the store's decision, on every request after this one.
   - the store unreachable or timing out → `502` / `504`, with a sentence naming the store, not the user.
4. On success it sets the session cookie and answers `204`.

**Failed logins** are limited per client address: after 5 failures within a minute, `429` for a minute.
The limit is in memory, per proxy replica.

**The session cookie** `htr_session`:

- holds the derived access key and secret key, the username and an expiry: never the password;
- is encrypted and authenticated with AES-GCM under a 256-bit key from `results.sessionSecret`, with a
  random nonce per cookie. A cookie that fails to decrypt, or is past its expiry, is no session;
- is `HttpOnly; Secure; SameSite=Strict; Path=/`, and expires after `results.sessionHours` (default 8).
  `Secure` is dropped only when the site is served over plain HTTP (a local dev stack);
- works across proxy replicas without shared state. Rotating the Secret ends every session.

**`POST /results/_logout`** (same `Origin` check) clears the cookie. **`GET /results/_session`** answers
`{"user": "<username>"}` for a valid session, `401` otherwise.

The secret key derived from a password is as sensitive as the password for this store: anyone holding it
can read what the account can. That is why it never reaches JavaScript and exists only inside the encrypted
cookie and, for the length of a request, in the proxy's memory.

## 3. Serving a file

**`GET` and `HEAD /results/<key>`.** Any other method: `405`.

1. **The session.** No valid session → `401`.
2. **The key**, URL-decoded exactly once, is refused with `404` (before the store is asked) when it has an
   empty segment, a `.` or `..` segment, a backslash or an encoded slash, or when it does not start with
   `<namespace>/` (the release's namespace, from the downward API, as the web front reads it) or
   `status/logs/`.
3. **The object** is read with the session's keys, passing the browser's `If-None-Match` and
   `If-Modified-Since`. The S3 client is configured as the wrapper's already is against HCP: signature v4,
   path-style addressing, checksums only where required, `S3_VERIFY_TLS`, a 5 s connect and 30 s read
   timeout, two retries.
4. **The answer**:

   | Store says | Proxy answers |
   |---|---|
   | the object | `200`, streamed in 1 MiB chunks; `HEAD` the same headers, no body |
   | not modified | `304` with the `ETag` |
   | `NoSuchKey` | `404` |
   | `AccessDenied` | `403`, "your account may not read this" |
   | expired or invalid keys (`InvalidAccessKeyId`, `SignatureDoesNotMatch`) | `401`, and the cookie is cleared |
   | unreachable, 5xx | `502`; a timeout `504` |

**Headers on every file answer:** `Content-Length`, `ETag`, `Last-Modified`, `Cache-Control: private,
no-cache` (the browser revalidates and gets a cheap `304`; no shared cache stores it),
`Content-Security-Policy: default-src 'none'; sandbox` and `X-Content-Type-Options: nosniff`.

**Content type.** Result files are now served from the campaign browser's own origin, and some of what is in
the bucket comes from outside (text htrflow printed into a run log, data copied from an IIIF source). So only
the types htrflow-batch writes pass through as stored: `application/json`, `application/xml`, `text/xml`,
`text/plain` (with their charset). Anything else is sent as `application/octet-stream` with
`Content-Disposition: attachment`, so it is never rendered as a page.

The proxy keeps a bounded cache of S3 clients by access key (least recently used, a few hundred entries), so
a session does not build a client per request.

## 4. The web front behind the login

- **The session check.** The web front cannot decrypt the cookie and must not be able to: it would then hold
  every user's S3 keys. For each `/api/v1/*` request it asks the proxy's `/results/_session`, forwarding the
  request's cookie, and caches the answer per cookie value for 30 seconds (bounded). No valid session →
  `401`.
- **Progress.** The progress reader reads through the proxy (`HTRFLOW_INTERNAL_RESULTS_BASE`, which the chart
  now sets to the proxy's Service, `http://htrflow-results:8082/results`) and forwards the caller's cookie.
  Its cache is keyed by user and URL, never by URL alone: two users may not read the same volumes.
- **`HTRFLOW_S3_VERIFY_TLS`** leaves the web front; the proxy reads `S3_VERIFY_TLS` instead.
- **`/config.js`** is unchanged: `window.RESULTS_BASE` is `resultsUrl`.

## 5. One origin, with or without an Ingress

The web front passes `GET`, `HEAD` and `POST` under `/results/` through to the proxy's Service, streaming the
body both ways and forwarding the cookie, `Origin`, `If-None-Match`, `If-Modified-Since` and the client
address (`X-Forwarded-For`, for the login limit). It reads nothing from the answer. This is the one path in
every mode: a NodePort install, the compose stack and an Ingress install behave the same. (An Ingress may
later route `/results` straight to the proxy; nothing depends on the pass-through.)

**`resultsUrl` points at the proxy.** The wrapper writes absolute URLs built from it into `iiif.json`
(`seeAlso` ALTO links, ids) and `manifest.json` (`viewer_url`), and the viewer follows them. So it becomes
`https://<web front host>/results`. Volumes published under the old URL keep it; run them again to view them
through the proxy.

## 6. Chart and configuration

Chart `htrflow-batch` 0.16.0.

- **New values:** `results.replicas` (1), `results.resources`, `results.sessionSecret` (the name of a Secret
  with key `key`: 32 random bytes, base64; required), `results.sessionHours` (8). The proxy runs the web
  image (`web.image`).
- **New objects:** Deployment and Service `htrflow-results` (port 8082), NetworkPolicy `htr-results`:
  ingress only from `htrflow-web` pods; egress to DNS and the S3 endpoint (`s3Egress`). The web front's
  policy gains egress to `htrflow-results` and loses its S3 egress.
- **Changed:** the web front no longer reads the S3 Secret at all.
- **`resultsUrl`**: documented as `https://<web front host>/results`. The chart refuses a value that is
  not an http(s) URL as today; it cannot know the host, so it does not derive it.
- **Store accounts.** The store's own users are the accounts. For HCP: a user with read permission on the
  namespace; the login derives its S3 keys. For a store that issues S3 keys (RustFS, MinIO, AWS), the login
  form takes the access key as the username and the secret key as the password, with a switch
  `results.keyDerivation: hcp | none` (default `hcp`) saying which.

**The bucket stops needing anonymous read and CORS.** The deploy page loses both requirements. It keeps the
overwrite requirement: the bucket must accept a write over an existing key (on HCP, versioning on, with
pruning of old versions).

**Dev stacks.**

- **devstack chart** (0.5.0): the anonymous-read bucket policy, `rustfs.publicLogs` and `corsOrigins` go.
  The init hook creates a read-only RustFS user (`s3.loginUser`, password from `s3.loginSecret`) for logging
  in, and the devstack sets `results.keyDerivation: none` in its documented install line.
- **compose stack:** the same. `compose_init.py` drops the results bucket's anonymous policy and creates
  the login user. The fixtures bucket that plays the IIIF server keeps its anonymous read.

## 7. Errors a person can act on

- Login: a wrong username or password, too many attempts, the store unreachable — each a sentence on the
  login page.
- `401` anywhere in the campaign browser sends the user to the login page and back afterwards.
- `403` on a volume: "your account may not read this volume", in place of its results.
- The viewer on `401`: a short page with a link to log in.

## 8. Tests

- **Proxy, unit** (`moto` for S3): key rules; each row of the answer table; `304`; the content-type rule and
  the security headers; login with right and wrong keys, the `Origin` check, the failure limit; session
  cookie round trip, tampering, expiry, wrong key; the client cache bound.
- **Web front:** the session gate with a fake proxy (`httpx.MockTransport`); progress cache isolation
  between two users; the `/results` pass-through (methods, headers, streaming, no body buffering).
- **Frontend:** a `401` goes to the login page and returns; the login form's errors; log out.
- **Chart:** the proxy has no ServiceAccount token and no Role; it mounts only the session Secret; the web
  front no longer references the S3 Secret; both NetworkPolicies; `sessionSecret` required.
- **Local end to end:** the proxy against a local S3 server over HTTPS with an untrusted certificate
  (`S3_VERIFY_TLS=false`), one read-only user: login, a file, `304`, a `403` for another prefix, log out.
- **Live:** one volume on a real store, viewed after login; the same URLs `401` before it.

## 9. Documentation

- Deploy: the bucket stays private; store accounts; the session Secret; `resultsUrl` points at `/results`.
- Security: the new boundary (section 2's last paragraph; no pod holds a read credential; the web front
  cannot decrypt sessions), and the content-type rule.
- Viewing: logging in; which volumes open through the proxy.
- The generated configuration reference; chart README upgrade notes and changelog; the decision log: logins
  use the store's own accounts, replacing the earlier GitHub/HF plan.

## 10. Release

v0.9.0, chart 0.16.0, devstack 0.5.0. Upgrade, in one change window: set `results.sessionSecret` (the
render fails without it, and with a leftover `web.internalResultsBase`) and change `resultsUrl` by hand to
`https://<web front host>/results` (not validated: an unchanged value renders and silently breaks the viewer
and `/alto`); every campaigns repo sets `converter.yaml`'s `results_url` to the same URL, since it becomes
each campaign Job's `RESULTS_URL`. Budgets rise for `web`, `frontend` and
`chart`, each with its reason in `scripts/loc-budget.sh`.
