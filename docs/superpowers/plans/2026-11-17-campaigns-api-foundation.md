# Campaigns API, part 1: the foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person runs `htrflow-campaigns login` and `htrflow-campaigns whoami` against a service in the dev cluster and gets their own name back, with no Kubernetes login.

**Architecture:** A new workspace package `htrflow-api` (FastAPI) checks a signed access token from the OpenID Connect provider on every `/v1` call and answers `GET /v1/login` (no token) and `GET /v1/whoami`. The converter's CLI gains a small HTTP client, a stored login and the device sign-in. The service ships in the existing `htrflow-campaigns` image as a second program, the chart gains its Deployment behind `api.enabled`, and the devstack chart gains a Keycloak for the dev cluster. In this part the service holds no Kubernetes rights and no store Secret.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, pydantic 2, PyJWT with `cryptography`, httpx, Helm, Keycloak (devstack only).

**Spec:** `docs/superpowers/specs/2026-11-17-campaigns-api-design.md`. This plan covers its order-of-work step 1 and the parts of steps 7 and 8 that step needs. Reads, `campaign logs`, the writes, the rendered `converter.yaml`, the Kubernetes rights and the release follow as later plans, written after this one runs: its last task settles a question their chart work depends on.

## Global Constraints

- **No switch turns the token check off.** No environment variable, chart value or test hook that makes a `/v1` route answer without a verified token, `GET /v1/login` excepted.
- **No state.** The service writes no file and keeps nothing between requests but the provider's signing keys in memory.
- **The token goes no further.** It is never logged, never echoed in an error and never sent anywhere but the service (by the CLI) or checked (by the service).
- **HTTPS only, loopback excepted.** The CLI refuses a server or provider address that is not `https://` unless its host is `localhost`, in `127.0.0.0/8` or `::1`. The service refuses to start on an issuer that breaks the same rule.
- **Accepted signing algorithms are a fixed list of asymmetric ones:** `RS256 RS384 RS512 PS256 PS384 PS512 ES256 ES384 ES512 EdDSA`. Never `none`, never an `HS*`.
- **Errors are two sentences in JSON:** `{"detail": "<what was refused>", "hint": "<what to do>"}` beside the HTTP status. The CLI prints `detail`, then `hint`, on stderr.
- **JSON field names are camelCase**, as in `htrflow-web`'s `/api/v1`.
- **Configuration models follow the repository's one idiom:** pydantic, `frozen=True`, `extra="forbid"`, field aliases are the environment names, a `from_env` classmethod.
- **No new image repository.** The service is a second console script in the `htrflow-campaigns` image.
- **Every image in a chart is pinned by digest**, the devstack's Keycloak included.
- **Repository docs name no person, no host and no site**, and the general docs (everything under `docs/` outside `docs/superpowers`) pass `scripts/docs_lint.py`.
- **Commits are small:** one logical step each, no co-author trailer. Before any push: `make ci` and `bash scripts/loc-budget.sh`.
- **Line budgets** live in `scripts/loc-budget.sh`. A raise is the measured count after the task, with a dated line of reason above it, like the raises already there.
- **Only the local cluster is used.** The owner ruled on 2026-11-17 that Task 8 runs on the local k3s cluster (releases `htr` and `htr-devstack` in namespace `htr-batch`) and that the shared development cluster is never touched. Where this plan says "dev cluster" it means the local one. Other releases and namespaces on the local cluster are not touched either.

## Review Focus

Inputs the spec implies that are most likely to bite a person, each pinned by a test in the task named:

1. **The provider sends group paths (`/htr-operators`) or no groups claim at all.** Expected: a 403 whose hint names the group mapper setting, never a 500 and never a silent match of a nested group. (Task 2)
2. **A token names a signing key the service has not seen while the provider is unreachable.** Expected: a 503 saying the sign-in service could not be reached, and tokens under already-cached keys keep working. (Task 2)
3. **The server address has a path prefix or a trailing slash** (`https://host/control/`). Expected: every request goes to `<prefix>/v1/...` exactly once, with no doubled or dropped segment. (Task 4)
4. **The stored login is unreadable, corrupt, or readable by others.** Expected: the CLI says to sign in again and exits 2 with no traceback; a new file is written with mode `0600` whatever the umask. (Task 4)
5. **The device sign-in is slowed, expires, or is denied in the browser.** Expected: `slow_down` lengthens the wait and carries on; expiry and denial each end with one sentence and exit 1. (Task 5)

## File Structure

| File | Responsibility |
|---|---|
| `packages/api/pyproject.toml` | The `htrflow-api` package and its console script |
| `packages/api/src/htrflow_api/__init__.py` | `MIN_CLIENT` |
| `packages/api/src/htrflow_api/config.py` | `ApiConfig`: the service's whole configuration, from the environment |
| `packages/api/src/htrflow_api/tokens.py` | `Verifier`: token in, `Identity` out, or a refusal |
| `packages/api/src/htrflow_api/app.py` | `create_app`: routes, the guard on `/v1`, the error shape |
| `packages/api/src/htrflow_api/__main__.py` | `main`: uvicorn on port 8083 |
| `packages/converter/src/htrflow_converter/remote.py` | The CLI's side: the address rule, the stored login, `Remote` (requests with a token, refreshed) |
| `packages/converter/src/htrflow_converter/signin.py` | The device sign-in |
| `packages/converter/src/htrflow_converter/cli.py` | `login`, `logout`, `whoami` |
| `.docker/htrflow-campaigns.dockerfile` | Builds and installs both wheels |
| `charts/htrflow-batch/templates/api.yaml` | Deployment, Service, Ingress, NetworkPolicy |
| `charts/htrflow-devstack/templates/keycloak.yaml` | The dev cluster's identity provider and its realm |

---

### Task 1: The `htrflow-api` package and its configuration

**Files:**
- Create: `packages/api/pyproject.toml`, `packages/api/README.md`, `packages/api/src/htrflow_api/__init__.py`, `packages/api/src/htrflow_api/config.py`
- Test: `packages/api/tests/test_config.py`
- Modify: `pyproject.toml` (root `testpaths`), `Makefile` (`typecheck`), `.dagger/checks.go` (`tyCheckCmd`), `scripts/loc-budget.sh`, `scripts/config_reference.py`, `scripts/config_reference.md`, `docs/reference/configuration.md` (generated), every Dockerfile under `.docker/` that bind-mounts or copies the other members' `pyproject.toml`

**Interfaces:**
- Produces:

```python
# htrflow_api/__init__.py
MIN_CLIENT = "0.13.1"   # the oldest htrflow-converter version the service answers

# htrflow_api/config.py
class ApiConfig(BaseModel):          # frozen, extra="forbid"
    issuer: str        = Field(alias="HTRFLOW_API_ISSUER")
    jwks_url: str      = Field("", alias="HTRFLOW_API_JWKS_URL")
    audience: str      = Field(alias="HTRFLOW_API_AUDIENCE")
    client_id: str     = Field(alias="HTRFLOW_API_CLIENT_ID")
    group: str         = Field(alias="HTRFLOW_API_GROUP")
    user_claim: str    = Field("preferred_username", alias="HTRFLOW_API_USER_CLAIM")
    groups_claim: str  = Field("groups", alias="HTRFLOW_API_GROUPS_CLAIM")
    path: str          = Field("", alias="HTRFLOW_API_PATH")
    namespace: str     = Field(alias="HTRFLOW_API_NAMESPACE")
    queue: str         = Field(alias="HTRFLOW_API_QUEUE")
    batch_version: str = Field("dev", alias="HTRFLOW_BATCH_VERSION")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ApiConfig": ...

def is_loopback(host: str) -> bool: ...   # "localhost", 127.0.0.0/8, "::1"
```

- `jwks_url` empty means: read `jwks_uri` from `<issuer>/.well-known/openid-configuration`. It exists because a pod may reach the provider at another address than a person does.
- `path` is the prefix the routes are served under (`""` or `/control`). The app serves the prefix itself, so no Ingress rewrite is needed and a port-forward works the same.

- [ ] **Step 1: Write the failing tests** in `packages/api/tests/test_config.py`

```python
REQUIRED = {
    "HTRFLOW_API_ISSUER": "https://id.example/realms/htrflow",
    "HTRFLOW_API_AUDIENCE": "htrflow-batch",
    "HTRFLOW_API_CLIENT_ID": "htrflow-cli",
    "HTRFLOW_API_GROUP": "htr-operators",
    "HTRFLOW_API_NAMESPACE": "htr-batch",
    "HTRFLOW_API_QUEUE": "htr-batch",
}

def test_from_env_reads_the_required_names_and_the_defaults():
    cfg = ApiConfig.from_env(REQUIRED)
    assert (cfg.user_claim, cfg.groups_claim, cfg.path, cfg.jwks_url) == (
        "preferred_username", "groups", "", "")
    assert cfg.batch_version == "dev"

@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_a_missing_required_name_is_named_in_the_error(missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        ApiConfig.from_env(env)

def test_from_env_ignores_names_that_are_not_its_own():
    assert ApiConfig.from_env({**REQUIRED, "PATH": "/usr/bin"}).issuer

@pytest.mark.parametrize("issuer", [
    "http://id.example/realms/htrflow", "id.example/realms/htrflow", ""])
def test_an_issuer_that_is_not_https_is_refused(issuer):
    with pytest.raises(ValueError, match="HTRFLOW_API_ISSUER"):
        ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_ISSUER": issuer})

@pytest.mark.parametrize("issuer", [
    "http://localhost:18080/realms/htrflow", "http://127.0.0.1:18080/realms/htrflow",
    "http://[::1]:18080/realms/htrflow"])
def test_a_loopback_issuer_may_be_plain_http(issuer):
    assert ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_ISSUER": issuer}).issuer == issuer

@pytest.mark.parametrize("path", ["control", "/control/", "/"])
def test_a_path_is_empty_or_starts_with_a_slash_and_ends_without_one(path):
    with pytest.raises(ValueError, match="HTRFLOW_API_PATH"):
        ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_PATH": path})

def test_the_config_cannot_be_changed():
    with pytest.raises(ValidationError):
        ApiConfig.from_env(REQUIRED).group = "other"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/api/tests/test_config.py`
Expected: an import error, `htrflow_api` does not exist.

- [ ] **Step 3: Create the package**

`packages/api/pyproject.toml` mirrors `packages/web/pyproject.toml`: name `htrflow-api`, the siblings' version and licence, `requires-python = ">=3.12"`, hatchling, `packages = ["src/htrflow_api"]`, script `htrflow-api = "htrflow_api.__main__:main"`, the same pytest options. Dependencies: `fastapi>=0.110`, `uvicorn>=0.29`, `pydantic>=2.13.5`, `httpx>=0.27`, and PyJWT added with `uv add --package htrflow-api "pyjwt[crypto]"` so the floor is the release the lock resolves.

`from_env` picks only the model's own aliases out of the mapping (default `os.environ`), as `htrflow_web.kube.Config.from_env` does, and raises `ValueError` naming the environment variable for each failure.

- [ ] **Step 4: Wire the package into the workspace**

- Root `pyproject.toml`: add `"packages/api/tests"` to `testpaths`.
- `Makefile` `typecheck` and `.dagger/checks.go` `tyCheckCmd`: add `packages/api/src`.
- `scripts/loc-budget.sh`: add `check api "$(count packages/api/src -name '*.py')" 400` beside the others, with a dated reason line.
- Run `grep -rn "packages/converter/pyproject.toml" .docker/` and add `packages/api/pyproject.toml` wherever a Dockerfile mounts or copies the other members' `pyproject.toml`: uv cannot resolve the workspace with a member missing.
- `scripts/config_reference.py`: add a section `("api", "the campaigns API (`htrflow-api`)", _ENV, ApiConfig, None)` beside `web` and `results`, with its prose in `scripts/config_reference.md`; then `make config-reference`.

- [ ] **Step 5: Run the tests and the gates**

Run: `uv run --all-packages pytest -q packages/api/tests/test_config.py && make typecheck && bash scripts/loc-budget.sh && uv run --all-packages pytest -q packages/converter/tests/test_docs_lint.py`
Expected: all pass, and `api` appears in the budget table.

- [ ] **Step 6: Commit**

```bash
git add packages/api pyproject.toml uv.lock Makefile .dagger/checks.go scripts .docker docs/reference/configuration.md
git commit -m "feat(api): the htrflow-api package and its configuration"
```

---

### Task 2: The token check

**Files:**
- Create: `packages/api/src/htrflow_api/tokens.py`
- Test: `packages/api/tests/test_tokens.py`, `packages/api/tests/conftest.py`

**Interfaces:**
- Consumes: `ApiConfig` (Task 1).
- Produces:

```python
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
              "ES256", "ES384", "ES512", "EdDSA")
REFETCH_SECONDS = 60

@dataclass(frozen=True)
class Identity:
    user: str
    groups: tuple[str, ...]

class Refused(Exception):
    def __init__(self, status: int, detail: str, hint: str) -> None: ...

class Verifier:
    def __init__(self, cfg: ApiConfig, fetch_keys: Callable[[], dict] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def verify(self, token: str) -> Identity: ...
```

- `fetch_keys` returns a JWKS document (`{"keys": [...]}`). The default fetches it over httpx with a 5-second timeout, from `cfg.jwks_url` or the discovery document's `jwks_uri`.
- `verify` raises `Refused(401, …)` for a token that is missing a part, unsigned, wrongly signed, expired, or of another issuer or audience; `Refused(403, …)` for a verified token whose groups do not include `cfg.group`; `Refused(503, …)` when a key is needed and the provider cannot be reached.
- `conftest.py` produces fixtures the next task uses too:

```python
@pytest.fixture
def cfg() -> ApiConfig: ...            # test_config.REQUIRED's values
@pytest.fixture
def signer() -> Signer: ...            # an RSA key made in the test
class Signer:
    def __init__(self, kid: str = "k1") -> None: ...   # a fresh RSA key each time
    kid: str
    def jwks(self) -> dict: ...
    def token(self, **overrides) -> str: ...
    # default claims: iss, aud, exp (+300 s), preferred_username "ada",
    # groups ["htr-operators"]; a claim overridden with None is left out;
    # header overrides: alg=..., kid=...
    # alg="none" and alg="HS256" (signed with the public key's PEM as the
    # secret) are put together by hand: PyJWT refuses to make either.
```

- [ ] **Step 1: Write the failing tests** in `packages/api/tests/test_tokens.py`

Each test builds `Verifier(cfg, fetch_keys=signer.jwks)` unless it says otherwise.

```python
def test_a_good_token_gives_the_user_and_the_groups(cfg, signer):
    who = Verifier(cfg, signer.jwks).verify(signer.token(groups=["a", "htr-operators"]))
    assert who == Identity("ada", ("a", "htr-operators"))

@pytest.mark.parametrize("overrides, word", [
    ({"exp": 1}, "expired"),
    ({"iss": "https://elsewhere.example/realms/x"}, "issuer"),
    ({"aud": "htrflow-train"}, "htrflow-batch"),      # names the audience it wants
    ({"aud": None}, "htrflow-batch"),
    ({"exp": None}, "expiry"),
])
def test_a_token_that_is_not_ours_is_a_401(cfg, signer, overrides, word):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(**overrides))
    assert e.value.status == 401 and word in e.value.detail

def test_a_token_signed_by_another_key_is_a_401(cfg, signer):
    other = Signer(kid=signer.kid)
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(other.token())
    assert e.value.status == 401

@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_a_symmetric_or_absent_algorithm_is_a_401(cfg, signer, alg):
    # HS256 signed with the public key as the secret: the classic confusion
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(alg=alg))
    assert e.value.status == 401

@pytest.mark.parametrize("text", ["", "abc", "a.b", "a.b.c.d"])
def test_text_that_is_not_a_token_is_a_401_not_an_error(cfg, signer, text):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(text)
    assert e.value.status == 401

def test_a_token_without_the_group_is_a_403_that_names_it(cfg, signer):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(groups=["other"]))
    assert e.value.status == 403 and "htr-operators" in e.value.detail

# Review Focus 1
@pytest.mark.parametrize("groups", [["/htr-operators"], ["/team/htr-operators"],
                                    None, "htr-operators", [1, 2]])
def test_group_paths_and_malformed_claims_are_a_403_with_the_mapper_in_the_hint(
        cfg, signer, groups):
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(signer.token(groups=groups))
    assert e.value.status == 403 and "full group path" in e.value.hint

def test_the_claim_names_come_from_the_config(cfg, signer):
    cfg2 = cfg.model_copy(update={"user_claim": "email", "groups_claim": "roles"})
    token = signer.token(email="ada@example", roles=["htr-operators"], groups=None)
    assert Verifier(cfg2, signer.jwks).verify(token).user == "ada@example"

def test_keys_are_fetched_once_for_many_tokens(cfg, signer):
    calls = []
    v = Verifier(cfg, lambda: calls.append(1) or signer.jwks())
    for _ in range(3):
        v.verify(signer.token())
    assert len(calls) == 1

def test_an_unknown_key_is_fetched_again_but_only_once_a_minute(cfg, signer):
    now, calls = [0.0], []
    v = Verifier(cfg, lambda: calls.append(1) or signer.jwks(), clock=lambda: now[0])
    v.verify(signer.token())
    with pytest.raises(Refused):
        v.verify(Signer(kid="rotated").token())
    assert len(calls) == 1            # within a minute of the first load: no refetch
    now[0] = REFETCH_SECONDS + 1
    for _ in range(5):
        with pytest.raises(Refused):
            v.verify(Signer(kid="rotated").token())
    assert len(calls) == 2            # one refetch for five unknown keys
    now[0] = 2 * REFETCH_SECONDS + 2
    with pytest.raises(Refused):
        v.verify(Signer(kid="rotated").token())
    assert len(calls) == 3

# Review Focus 2
def test_an_unreachable_provider_is_a_503_and_cached_keys_still_verify(cfg, signer):
    up = [True]
    def fetch():
        if not up[0]:
            raise httpx.ConnectError("down")
        return signer.jwks()
    now = [0.0]
    v = Verifier(cfg, fetch, clock=lambda: now[0])
    v.verify(signer.token())
    up[0] = False
    now[0] = REFETCH_SECONDS + 1
    assert v.verify(signer.token()).user == "ada"
    with pytest.raises(Refused) as e:
        v.verify(Signer(kid="rotated").token())
    assert e.value.status == 503

def test_no_refusal_quotes_the_token(cfg, signer):
    token = signer.token(aud="other")
    with pytest.raises(Refused) as e:
        Verifier(cfg, signer.jwks).verify(token)
    assert token not in e.value.detail + e.value.hint
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/api/tests/test_tokens.py`
Expected: an import error for `htrflow_api.tokens`.

- [ ] **Step 3: Implement `tokens.py`**

Read the token's header without verifying to get `kid`; look the key up in a dict filled from `fetch_keys`; on a miss, refetch if `REFETCH_SECONDS` have passed since the last fetch. Verify with `jwt.decode(token, key, algorithms=ALGORITHMS, audience=cfg.audience, issuer=cfg.issuer, options={"require": ["exp", "iss", "aud"]})`. Groups match by exact name. A lock guards the key cache: uvicorn runs sync routes on a thread pool.

Every 403 about groups carries this hint, verbatim:

```text
Ask whoever runs the sign-in service to add you to the group, and to send group names in the token's groups claim with "full group path" off.
```

Every 401 carries this hint, verbatim: `Sign in again: htrflow-campaigns login`.

- [ ] **Step 4: Run the tests**

Run: `uv run --all-packages pytest -q packages/api/tests/test_tokens.py && make typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api
git commit -m "feat(api): a signed token becomes a user and their groups, or a refusal"
```

---

### Task 3: The app: `/healthz`, `/v1/login`, `/v1/whoami`

**Files:**
- Create: `packages/api/src/htrflow_api/app.py`, `packages/api/src/htrflow_api/__main__.py`
- Test: `packages/api/tests/test_app.py`

**Interfaces:**
- Consumes: `ApiConfig`, `MIN_CLIENT` (Task 1); `Verifier`, `Identity`, `Refused` (Task 2); the `cfg` and `signer` fixtures.
- Produces:

```python
CLIENT_HEADER = "X-Htrflow-Client"     # the CLI's htrflow-converter version

def create_app(cfg: ApiConfig, verifier: Verifier) -> FastAPI: ...
def too_old(client: str, minimum: str = MIN_CLIENT) -> bool: ...
def main() -> None: ...                # __main__.py: uvicorn, 0.0.0.0:8083
```

- Routes, with `p = cfg.path`:
  - `GET /healthz` — `{"ok": true}`, no token, never under the prefix (the probes use it).
  - `GET {p}/v1/login` — no token: `{"issuer", "clientId", "audience", "serverVersion", "minClient"}`.
  - `GET {p}/v1/whoami` — `{"user", "groups", "namespace", "queue", "serverVersion"}`.
- Later plans add routes with `Depends(current_identity)`; the guard is one dependency, not per-route code.

- [ ] **Step 1: Write the failing tests** in `packages/api/tests/test_app.py`

A fixture `client` is `TestClient(create_app(cfg, Verifier(cfg, signer.jwks)))`; `auth(token)` is `{"Authorization": f"Bearer {token}"}`.

```python
def test_healthz_needs_no_token(client):
    assert client.get("/healthz").json() == {"ok": True}

def test_login_needs_no_token_and_holds_nothing_secret(client, cfg):
    assert client.get("/v1/login").json() == {
        "issuer": cfg.issuer, "clientId": "htrflow-cli", "audience": "htrflow-batch",
        "serverVersion": "dev", "minClient": MIN_CLIENT}

def test_whoami_answers_the_signed_in_person(client, signer):
    body = client.get("/v1/whoami", headers=auth(signer.token())).json()
    assert body == {"user": "ada", "groups": ["htr-operators"], "namespace": "htr-batch",
                    "queue": "htr-batch", "serverVersion": "dev"}

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer"}, {"Authorization": "bearer "}])
def test_no_bearer_token_is_a_401_with_the_challenge_header(client, headers):
    r = client.get("/v1/whoami", headers=headers)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
    assert set(r.json()) == {"detail", "hint"}

def test_a_refusal_keeps_its_status_and_two_sentences(client, signer):
    r = client.get("/v1/whoami", headers=auth(signer.token(groups=["other"])))
    assert r.status_code == 403 and set(r.json()) == {"detail", "hint"}

def test_every_v1_route_but_login_refuses_without_a_token(client):
    # walks app.routes, so a route added later cannot forget the guard
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if "/v1/" in path and not path.endswith("/v1/login"):
            assert client.get(path).status_code == 401, path

def test_the_prefix_is_served_and_the_bare_path_is_not(cfg, signer):
    app = create_app(cfg.model_copy(update={"path": "/control"}), Verifier(cfg, signer.jwks))
    c = TestClient(app)
    assert c.get("/control/v1/login").status_code == 200
    assert c.get("/v1/login").status_code == 404
    assert c.get("/healthz").status_code == 200

@pytest.mark.parametrize("client_version, status", [
    ("0.13.0", 426), ("0.1.0", 426), (MIN_CLIENT, 200), ("9.0.0", 200),
    ("dev", 200), ("0.14.0.dev3", 200), ("", 200)])
def test_a_client_older_than_the_minimum_is_refused_with_both_versions(
        client, signer, client_version, status):
    headers = auth(signer.token()) | ({CLIENT_HEADER: client_version} if client_version else {})
    r = client.get("/v1/whoami", headers=headers)
    assert r.status_code == status
    if status == 426:
        assert client_version in r.json()["detail"] and MIN_CLIENT in r.json()["detail"]

def test_answers_are_not_cached(client):
    assert client.get("/v1/login").headers["Cache-Control"] == "no-store"

def test_an_unhandled_error_is_json_with_two_sentences(cfg):
    class Broken:
        def verify(self, token):
            raise RuntimeError("boom")
    c = TestClient(create_app(cfg, Broken()), raise_server_exceptions=False)
    r = c.get("/v1/whoami", headers=auth("x.y.z"))
    assert r.status_code == 500 and r.json()["detail"] == "internal error"
    assert "boom" not in r.text

def test_the_token_is_not_logged(client, signer, caplog):
    token = signer.token(aud="other")
    with caplog.at_level("DEBUG"):
        client.get("/v1/whoami", headers=auth(token))
    assert token not in caplog.text
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/api/tests/test_app.py`
Expected: an import error for `htrflow_api.app`.

- [ ] **Step 3: Implement `app.py` and `__main__.py`**

One `Depends` reads the `Authorization` header, calls `verifier.verify`, and one exception handler turns `Refused` into its status and the two-sentence body (adding `WWW-Authenticate: Bearer` on a 401). The version check runs before the token check, on every `/v1` route but `login`. `too_old` compares the first three dot-separated numbers; a version it cannot read as three numbers is not too old. An unhandled exception is answered as `{"detail": "internal error", "hint": "Try again; if it stays, tell whoever runs the cluster."}` with status 500 and is logged without the request's headers. `main` builds `ApiConfig.from_env()` and `Verifier(cfg)` and runs uvicorn with access logging that never includes headers.

- [ ] **Step 4: Run the tests**

Run: `uv run --all-packages pytest -q packages/api/tests && make typecheck && bash scripts/loc-budget.sh`
Expected: PASS, `api` within 400.

- [ ] **Step 5: Commit**

```bash
git add packages/api
git commit -m "feat(api): /v1/login and /v1/whoami behind one guard"
```

---

### Task 4: The CLI's side: the address rule, the stored login, `Remote`

**Files:**
- Create: `packages/converter/src/htrflow_converter/remote.py`
- Test: `packages/converter/tests/test_remote.py`
- Modify: `scripts/loc-budget.sh` (converter)

**Interfaces:**
- Produces:

```python
ENV_URL = "HTRFLOW_API_URL"
CLIENT_HEADER = "X-Htrflow-Client"

class RemoteError(Exception):
    def __init__(self, detail: str, hint: str = "", status: int | None = None) -> None: ...

class NotSignedIn(RemoteError): ...

@dataclass(frozen=True)
class Login:
    server: str            # normalised: no trailing slash
    issuer: str
    client_id: str
    token_endpoint: str
    access_token: str
    refresh_token: str     # "" when the provider gave none
    expires_at: float      # seconds since the epoch

def checked_address(url: str, what: str) -> str: ...
    # the normalised URL, or RemoteError naming `what` ("the server", "the sign-in service")
def login_path(env: Mapping[str, str] | None = None) -> Path: ...
    # $XDG_CONFIG_HOME/htrflow/login.json, else ~/.config/htrflow/login.json
def load(env=None) -> Login: ...        # NotSignedIn when absent, unreadable or corrupt
def save(login: Login, env=None) -> None: ...   # file 0600, directory 0700, written whole
def forget(env=None) -> bool: ...       # True when a file was removed

class Remote:
    def __init__(self, login: Login, client: httpx.Client, env=None,
                 now: Callable[[], float] = time.time) -> None: ...
    def get(self, path: str, **params: str) -> dict: ...
    def request(self, method: str, path: str, *, json: object = None,
                params: Mapping[str, str] | None = None) -> httpx.Response: ...

def remote(env=None) -> Remote: ...     # load() + an httpx.Client; HTRFLOW_API_URL, when set,
                                        # must equal the stored server or NotSignedIn
```

- `path` is given from `/v1`, for example `remote.get("/v1/whoami")`; `Remote` puts the server (prefix included) in front.
- A token within 30 seconds of `expires_at` is refreshed first (`grant_type=refresh_token` at `token_endpoint`) and the new `Login` is saved. A refresh the provider refuses, or a 401 from the service, raises `NotSignedIn`.
- A response that is not 2xx raises `RemoteError(detail, hint, status)` from the JSON body; a body that is not that JSON gives `detail = "the server answered <status>"`.
- The CLI's version for `CLIENT_HEADER` is `importlib.metadata.version("htrflow-converter")`.

- [ ] **Step 1: Write the failing tests** in `packages/converter/tests/test_remote.py`

Helpers at the top of the file: `ENV(tmp_path)` is `{"XDG_CONFIG_HOME": str(tmp_path)}`; `client_for(handler)` is `httpx.Client(transport=httpx.MockTransport(handler))`; `LOGIN` is a `Login` for server `https://h.example` whose `expires_at` is far in the future.

```python
@pytest.mark.parametrize("url, out", [
    ("https://h.example", "https://h.example"),
    ("https://h.example/control/", "https://h.example/control"),
    ("http://localhost:8083/control", "http://localhost:8083/control"),
    ("http://127.0.0.1:8083", "http://127.0.0.1:8083"),
    ("http://[::1]:8083/", "http://[::1]:8083"),
])
def test_an_https_or_loopback_address_is_accepted_and_normalised(url, out):
    assert checked_address(url, "the server") == out

@pytest.mark.parametrize("url", ["http://h.example", "h.example", "ftp://h.example",
                                 "http://localhost.example", "https://", ""])
def test_any_other_address_is_refused_naming_what_it_was(url):
    with pytest.raises(RemoteError, match="the server"):
        checked_address(url, "the server")

def test_a_saved_login_is_read_back_and_only_the_user_can_read_it(tmp_path):
    old = os.umask(0o022)
    try:
        save(LOGIN, ENV(tmp_path))
    finally:
        os.umask(old)
    path = login_path(ENV(tmp_path))
    assert load(ENV(tmp_path)) == LOGIN
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

# Review Focus 4
@pytest.mark.parametrize("content", [None, "", "{", "[]", '{"server": "https://h"}'])
def test_no_file_or_a_broken_one_is_not_signed_in(tmp_path, content):
    if content is not None:
        p = login_path(ENV(tmp_path)); p.parent.mkdir(parents=True); p.write_text(content)
    with pytest.raises(NotSignedIn) as e:
        load(ENV(tmp_path))
    assert "htrflow-campaigns login" in e.value.hint

def test_save_replaces_a_file_others_could_read(tmp_path):
    p = login_path(ENV(tmp_path)); p.parent.mkdir(parents=True)
    p.write_text("{}"); p.chmod(0o644)
    save(LOGIN, ENV(tmp_path))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600

# Review Focus 3
@pytest.mark.parametrize("server", ["https://h.example", "https://h.example/control"])
def test_requests_go_to_the_prefix_once(tmp_path, server):
    seen = []
    def handler(request):
        seen.append(str(request.url)); return httpx.Response(200, json={"user": "ada"})
    r = Remote(replace(LOGIN, server=server), client_for(handler), ENV(tmp_path))
    r.get("/v1/whoami")
    assert seen == [f"{server}/v1/whoami"]

def test_a_request_carries_the_token_and_the_client_version(tmp_path):
    ...  # Authorization == "Bearer " + LOGIN.access_token; CLIENT_HEADER is the package version

def test_an_expiring_token_is_refreshed_first_and_the_new_one_saved(tmp_path):
    ...  # expires_at = now + 10: one POST to token_endpoint with grant_type=refresh_token
         # and client_id, then the GET with the new token; load() gives the new Login

def test_a_refused_refresh_or_a_401_is_not_signed_in(tmp_path):
    ...  # token endpoint 400 {"error": "invalid_grant"} -> NotSignedIn; service 401 -> NotSignedIn

def test_a_refusal_carries_the_servers_two_sentences(tmp_path):
    ...  # 403 {"detail": "d", "hint": "h"} -> RemoteError with .detail "d", .hint "h", .status 403

def test_a_body_that_is_not_ours_still_gives_a_sentence(tmp_path):
    ...  # 502 text/html -> RemoteError detail "the server answered 502"

def test_an_unreachable_server_is_a_sentence_not_a_traceback(tmp_path):
    ...  # handler raises httpx.ConnectError -> RemoteError naming the server's address

def test_the_environment_address_must_be_the_one_signed_in_to(tmp_path):
    ...  # save(LOGIN); remote({ENV_URL: "https://other.example", ...}) -> NotSignedIn naming both
```

Write each `...` out as a full test with the assertions its comment names.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_remote.py`
Expected: an import error for `htrflow_converter.remote`.

- [ ] **Step 3: Implement `remote.py`**

`save` writes a temporary file in the same directory, created with `os.open(..., 0o600)`, and renames it over the target. `load` treats every failure to read or parse, and any missing field, as `NotSignedIn`. Neither the token nor the refresh token appears in any exception text.

- [ ] **Step 4: Run the tests, raise the converter's budget to the measured count**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_remote.py && make typecheck && bash scripts/loc-budget.sh`
Expected: PASS after the budget line is raised with its reason.

- [ ] **Step 5: Commit**

```bash
git add packages/converter scripts/loc-budget.sh
git commit -m "feat(cli): a stored login and requests that carry it"
```

---

### Task 5: `login`, `logout` and `whoami`

**Files:**
- Create: `packages/converter/src/htrflow_converter/signin.py`
- Modify: `packages/converter/src/htrflow_converter/cli.py` (the parser near line 1891, the dispatch near line 2100), `docs/reference/cli.md`, `scripts/loc-budget.sh`
- Test: `packages/converter/tests/test_signin.py`, `packages/converter/tests/test_remote_cli.py`

**Interfaces:**
- Consumes: `Login`, `RemoteError`, `NotSignedIn`, `checked_address`, `save`, `forget`, `remote` (Task 4); `GET /v1/login` and `GET /v1/whoami` (Task 3).
- Produces:

```python
# signin.py
def device_login(server: str, client: httpx.Client, say: Callable[[str], None],
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.time) -> Login: ...
```

- Commands:
  - `htrflow-campaigns login [--server URL]` — the address is `--server`, else `HTRFLOW_API_URL`, else refused with exit 2. Exit 0 prints `signed in to <server> as <user>` (it calls `whoami` to say so).
  - `htrflow-campaigns logout` — removes the stored login; exit 0 either way, saying which.
  - `htrflow-campaigns whoami [--json]` — prints user, groups, namespace, queue and both versions; `--json` prints the body as sent.
- Exit codes for all three: `0` done, `1` refused by the server or the sign-in service or unreachable, `2` not signed in or no address.
- `logout` is this plan's addition; the spec lists `login` and `whoami` only.

- [ ] **Step 1: Write the failing tests** in `packages/converter/tests/test_signin.py`

The fake provider is an `httpx.MockTransport` serving `GET <server>/v1/login`, `GET <issuer>/.well-known/openid-configuration`, the device endpoint and the token endpoint. `sleep` records what it was asked and returns at once.

```python
def test_a_sign_in_prints_the_link_and_the_code_and_returns_the_login():
    ...  # say() got verification_uri_complete (or uri + user_code when it is absent);
         # the device request carried client_id and scope "openid";
         # the Login holds server, issuer, client_id, token_endpoint, both tokens,
         # expires_at == now + expires_in

def test_it_waits_while_the_person_has_not_answered():
    ...  # two "authorization_pending" then tokens: sleep == [5, 5, 5] with interval 5

# Review Focus 5
def test_slow_down_lengthens_the_wait_by_five_seconds_and_carries_on():
    ...  # pending, slow_down, tokens with interval 5: sleep == [5, 5, 10]

@pytest.mark.parametrize("error, word", [("expired_token", "expired"),
                                         ("access_denied", "denied")])
def test_expiry_and_denial_end_with_one_sentence(error, word):
    ...  # RemoteError whose detail contains `word`; nothing was saved

def test_the_code_running_out_ends_the_wait():
    ...  # device answer expires_in 15, interval 5, always pending:
         # RemoteError "expired" after three sleeps, not an endless loop

def test_a_provider_that_names_another_issuer_is_refused():
    ...  # discovery "issuer" != /v1/login "issuer" -> RemoteError naming both

@pytest.mark.parametrize("field", ["device_authorization_endpoint", "token_endpoint"])
def test_a_provider_without_the_device_sign_in_is_refused(field):
    ...  # discovery lacks `field` -> RemoteError saying the device sign-in is not on

def test_an_endpoint_that_is_not_https_or_loopback_is_refused():
    ...  # token_endpoint "http://id.example/token" -> RemoteError "the sign-in service"

def test_a_client_older_than_the_servers_minimum_is_told_before_signing_in():
    ...  # /v1/login minClient "99.0.0" -> RemoteError naming both versions;
         # no request reached the provider
```

And in `packages/converter/tests/test_remote_cli.py`, with `signin.device_login` and `remote.remote` monkeypatched:

```python
def test_login_without_an_address_exits_2_and_says_both_ways(capsys):
    assert cli.main(["login"]) == 2
    assert "--server" in capsys.readouterr().err and "HTRFLOW_API_URL" in ...

def test_login_saves_and_says_who(tmp_path, capsys): ...          # exit 0, "signed in to … as ada"
def test_login_refused_exits_1_and_saves_nothing(tmp_path): ...
def test_login_interrupted_exits_130_and_saves_nothing(tmp_path): ...   # KeyboardInterrupt
def test_whoami_prints_the_rows_and_json_prints_the_body(capsys): ...
def test_whoami_when_not_signed_in_exits_2_with_the_hint(capsys): ...
def test_whoami_refused_exits_1_with_both_sentences(capsys): ...  # detail then hint on stderr
def test_logout_removes_the_login_and_is_fine_without_one(tmp_path, capsys): ...
```

Write each `...` out as a full test with the assertions its comment names.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_signin.py packages/converter/tests/test_remote_cli.py`
Expected: import errors and `argparse` refusing `login`.

- [ ] **Step 3: Implement `signin.py` and the three commands**

`device_login` follows RFC 8628: `interval` defaults to 5 when the provider sends none; the wait ends when the device code's `expires_in` has passed. The three commands import `remote` and `signin` inside their functions, as the other commands import `cluster`, so `validate` and `render` still import neither `httpx` session code nor anything else new.

- [ ] **Step 4: Document the commands** in `docs/reference/cli.md`: a section "Signing in" after "Commands", with the three commands, the address rule, where the login is stored and the exit codes.

- [ ] **Step 5: Run the tests and the gates**

Run: `uv run --all-packages pytest -q packages/converter/tests && make typecheck && bash scripts/loc-budget.sh`
Expected: PASS after the converter's budget line is raised with its reason.

- [ ] **Step 6: Commit**

```bash
git add packages/converter docs/reference/cli.md scripts/loc-budget.sh
git commit -m "feat(cli): login, logout and whoami"
```

---

### Task 6: The image holds both programs

**Files:**
- Modify: `.docker/htrflow-campaigns.dockerfile`, `scripts/image-inputs-changed.sh`, `.dagger/published.go` (only if it lists the programs an image must hold)
- Test: `packages/converter/tests/test_entrypoints.py` (existing; it must still pass)

**Interfaces:**
- Produces: the `htrflow-campaigns` image with `/app/.venv/bin/htrflow-api` beside `/app/.venv/bin/htrflow-campaigns`. The entry point is unchanged.

- [ ] **Step 1: Build both packages in the image**

In both `uv sync` lines add `--package htrflow-api` beside `--package htrflow-converter`; add `packages/api/pyproject.toml` to the bind mounts and `COPY packages/api packages/api`; build and install both wheels (`uv build --wheel --package htrflow-api` with the same flags as the converter's).

- [ ] **Step 2: Make a change under `packages/api` count as a change to this image**

Run: `grep -n "converter" scripts/image-inputs-changed.sh .dagger/*.go .github/workflows/*.yml`
Add `packages/api` wherever `packages/converter` stands as an input of the campaigns image.

- [ ] **Step 3: Build and check**

Run: `make build-campaigns`, then with the tag it prints:
`docker run --rm --entrypoint /app/.venv/bin/htrflow-api <image>` 
Expected: it exits non-zero with a `ValueError` naming `HTRFLOW_API_ISSUER`, which shows the program is there and reads its configuration.
Run: `docker run --rm <image> whoami`
Expected: exit 2 and the not-signed-in sentence.

- [ ] **Step 4: Run the entry point test**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_entrypoints.py packages/converter/tests/test_packaging.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .docker scripts .dagger .github
git commit -m "build(campaigns): the image holds htrflow-api beside htrflow-campaigns"
```

---

### Task 7: The chart

**Files:**
- Create: `charts/htrflow-batch/templates/api.yaml`
- Modify: `charts/htrflow-batch/values.yaml`, `charts/htrflow-batch/values.schema.json`, `charts/htrflow-batch/templates/_helpers.tpl` (`htrflow-batch.validate`), `charts/htrflow-batch/ci/full-values.yaml`, `charts/htrflow-batch/Chart.yaml` (version, by `docs/development/releasing.md`), `docs/reference/chart.md`, `docs/how-it-works/security.md`, `scripts/loc-budget.sh` (chart)
- Test: `packages/converter/tests/test_chart_render.py`

**Interfaces:**
- Consumes: the image and program of Task 6, the environment names of Task 1, port 8083 and `/healthz` of Task 3.
- Produces these values (all under `api`):

```yaml
api:
  enabled: false
  image: ""                 # the htrflow-campaigns image, by digest
  path: /control            # served under this prefix on the web front's host
  issuer: ""
  jwksUrl: ""               # "" = found from the issuer
  audience: ""
  clientId: ""
  group: ""
  userClaim: preferred_username
  groupsClaim: groups
  allowNoIngress: false     # true = no Ingress; reached by port-forward only
  resources:
    requests: { cpu: 50m, memory: 128Mi }
    limits: { cpu: 500m, memory: 256Mi }
  network:
    identityCidrs: []       # where the sign-in service's key endpoint is
    identityPorts: [443]
    identityInNamespace: false   # true = a pod labelled app=keycloak in this namespace (the devstack's)
```

- Objects when `api.enabled`, all named `htrflow-api` and labelled `app: htrflow-api`:
  - **Deployment**: one replica, `command: ["/app/.venv/bin/htrflow-api"]`, the restricted pod and container contexts the web Deployment uses, `automountServiceAccountToken: false`, probes on `/healthz` port 8083, `HTRFLOW_API_NAMESPACE` from the downward API, `HTRFLOW_API_QUEUE` from `queue.name`, the other `HTRFLOW_API_*` from the values (the two with empty defaults only when set), a `/tmp` emptyDir.
  - **Service**: ClusterIP, port 8083. Never a NodePort.
  - **Ingress** (unless `allowNoIngress`): its own object, on `web.ingress.host`, `className`, `tlsSecretName` and `annotations`, path `api.path` with `pathType: Prefix`.
  - **NetworkPolicy** `htr-api` (when `network.enabled`): in on 8083 from what the web front's policy admits (`network.web.ingressFrom`); out to `identityCidrs` on `identityPorts`, or to pods labelled `app: keycloak` on 8080 when `identityInNamespace`. DNS is the namespace-wide policy's.
- The chart's refusals, verbatim:

```text
api.enabled needs web.ingress.enabled and web.ingress.tlsSecretName: the campaigns API is reached through the Ingress with TLS and nothing else, because a token must not travel unencrypted. For a cluster with no Ingress, set api.allowNoIngress=true and reach it by port-forward
```
```text
api.enabled needs api.<key>: <one clause per key>
```
for `image`, `issuer`, `audience`, `clientId` and `group`, one refusal each;
```text
api.path must start with "/" and not end with one, and must not be /api, /results or a path under them: the web front serves those. It may be empty only with api.allowNoIngress=true
```
```text
api.enabled with network.enabled needs a route to the sign-in service's keys: list its ranges in api.network.identityCidrs, or set api.network.identityInNamespace=true for the devstack's Keycloak
```

- [ ] **Step 1: Write the failing tests** in `packages/converter/tests/test_chart_render.py`

`API_SETS` is the minimal set that turns the service on with an Ingress: `api.enabled=true`, an `api.image` with a digest, the four required names, `web.service.type=ClusterIP`, `web.ingress.enabled=true`, `web.ingress.host=htr.example`, `web.ingress.tlsSecretName=htr-tls`, and `json:api.network.identityCidrs=["192.0.2.10/32"]`.

```python
def test_the_default_render_has_no_api_objects(): ...
def test_the_api_deployment_runs_the_program_with_its_environment(): ...
    # command, every HTRFLOW_API_* name, namespace from fieldRef, queue == queue.name,
    # automountServiceAccountToken False, no serviceAccountName, both probes on /healthz
def test_empty_optional_values_set_no_environment_name(): ...   # JWKS_URL absent when jwksUrl == ""
def test_the_api_service_is_cluster_ip_whatever_the_web_service_is(): ...
def test_the_api_ingress_has_the_webs_host_class_and_tls_and_its_own_path(): ...
def test_allow_no_ingress_renders_no_ingress_and_needs_no_tls(): ...
def test_the_api_policy_admits_the_ingress_and_reaches_only_the_sign_in_service(): ...
def test_the_api_policy_reaches_an_in_namespace_keycloak_by_label(): ...
def test_the_api_image_must_carry_a_digest(): ...
@pytest.mark.parametrize("key", ["image", "issuer", "audience", "clientId", "group"])
def test_each_required_api_value_is_refused_by_name(key): ...
def test_api_without_ingress_tls_is_refused_with_the_sentence(): ...   # the first refusal, verbatim
@pytest.mark.parametrize("path", ["", "control", "/control/", "/api", "/api/v2", "/results"])
def test_a_colliding_or_malformed_api_path_is_refused(path): ...
def test_an_empty_api_path_is_fine_without_an_ingress(): ...
def test_api_with_no_route_to_the_sign_in_service_is_refused(): ...
def test_the_full_values_render_turns_the_api_on(): ...
def test_the_schema_refuses_an_unknown_api_key(): ...                  # api.typo=1 fails the render
```

Write each as a full test asserting on the parsed objects or on `stderr`, in the style of the tests already in this file.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_chart_render.py -k api`
Expected: FAIL, the schema refuses `api`.

- [ ] **Step 3: Write the template, the values, the schema and the refusals**

Follow `templates/web.yaml` and `templates/results.yaml` for shape and helpers (`requireDigest`, `pullPolicy`, `restrictedPod`, `restrictedContainer`, `labels`). The refusals go in `htrflow-batch.validate`, so they fire whatever else is off. Turn the service on in `ci/full-values.yaml`. Before keeping `/control` as the default, check it is not a route of the page (`frontend/src/routes`); if it is, stop and ask the owner for another name.

- [ ] **Step 4: Check the policies still admit the chart's own pods**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_policy_admission.py packages/converter/tests/test_chart_agreement.py && make helm-template`
Expected: PASS. If the image rules refuse the new Deployment on the full render, add the campaigns image's repository to that values file's allow-list, as the web image's is.

- [ ] **Step 5: Document** the values in `docs/reference/chart.md`, and in `docs/how-it-works/security.md` one paragraph on the new pod: what reaches it, what it reaches, that it holds no Kubernetes rights and no Secret in this release. Run `make config-reference` if the chart's keys are part of the generated page.

- [ ] **Step 6: Run the gates**

Run: `uv run --all-packages pytest -q packages/converter/tests && bash scripts/loc-budget.sh && ZENSICAL="$PWD/.venv/bin/zensical" scripts/docs-site.sh build --clean --strict`
Expected: PASS after the chart's budget line is raised with its reason.

- [ ] **Step 7: Commit**

```bash
git add charts/htrflow-batch packages/converter/tests/test_chart_render.py docs scripts/loc-budget.sh
git commit -m "feat(chart): the campaigns API behind api.enabled"
```

---

### Task 8: Keycloak on the dev cluster, and the whole path run there

**Files:**
- Create: `charts/htrflow-devstack/templates/keycloak.yaml`
- Modify: `charts/htrflow-devstack/values.yaml`, `charts/htrflow-devstack/values.schema.json`, `charts/htrflow-devstack/templates/validate.yaml` (or the helper it includes), `charts/htrflow-devstack/ci/full-values.yaml`, `charts/htrflow-devstack/Chart.yaml`, `docs/development/dev-cluster.md`
- Test: `packages/converter/tests/test_chart_render.py`

**Interfaces:**
- Consumes: `api.network.identityInNamespace` (Task 7); the whole of Tasks 1 to 7.
- Produces these devstack values:

```yaml
keycloak:
  enabled: false
  image: ""                          # quay.io/keycloak/keycloak, by digest
  hostname: http://localhost:18080   # what a token's issuer is built from
  realm: htrflow
  clientId: htrflow-cli
  audience: htrflow-batch
  group: htr-operators
  adminPassword: ""
  userPassword: ""                   # for both test users
```

- Objects when `keycloak.enabled`: a ConfigMap with the realm, a Deployment and a Service `keycloak` on 8080, the pod labelled `app: keycloak`.
- The realm holds: one public client (`clientId`) with only the device grant on; an audience mapper that adds `audience` to the access token; a group mapper that writes group names to the `groups` claim with full path off; the group; a user `htr-dev` in the group and a user `htr-outsider` outside it.
- Keycloak runs `start-dev --import-realm` with `KC_HOSTNAME=<hostname>`, `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` and `KC_HTTP_ENABLED=true`. The intent: a token's issuer is always `<hostname>/realms/<realm>`, which the developer reaches by port-forward, while the service's pod fetches the keys from `http://keycloak:8080/realms/<realm>/protocol/openid-connect/certs` (`api.jwksUrl`).
- The devstack's refusals, verbatim:

```text
keycloak.enabled needs devStack.insecureDefaults=true: this Keycloak runs in development mode with passwords from the values and is for a development cluster only
```
```text
keycloak.enabled needs keycloak.image by digest, keycloak.adminPassword and keycloak.userPassword
```

- [ ] **Step 1: Confirm the target is the local cluster.** `kubectl config current-context` and `helm list -n htr-batch` must show the local k3s cluster with releases `htr` and `htr-devstack`. Anything else: stop and report. Before each `helm upgrade` in Step 6, render the upgrade (`helm upgrade --dry-run=server`) and compare it with `helm get manifest`: only the new Keycloak and campaigns API objects, and the allow-list entry their images need, may differ. If anything else would change, stop and report.

- [ ] **Step 2: Write the failing chart tests**

```python
def test_the_devstack_renders_no_keycloak_by_default(): ...
def test_keycloak_without_insecure_defaults_is_refused_with_the_sentence(): ...
def test_keycloak_without_a_digest_or_passwords_is_refused(): ...
def test_the_keycloak_pod_carries_the_label_the_api_policy_admits(): ...   # app: keycloak, port 8080
def test_the_realm_has_one_public_device_client_and_both_mappers(): ...
    # parse the ConfigMap's JSON: publicClient true, standardFlowEnabled false,
    # the device grant attribute "true", an audience mapper with the audience,
    # a group mapper with claim.name "groups" and full.path "false"
def test_the_realm_has_one_user_in_the_group_and_one_outside_it(): ...
```

- [ ] **Step 3: Run them and see them fail**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_chart_render.py -k keycloak`
Expected: FAIL, the schema refuses `keycloak`.

- [ ] **Step 4: Write the template, values, schema and refusals**

Resolve the image's digest with `docker buildx imagetools inspect quay.io/keycloak/keycloak:<newest stable>` and pin it; the image must have a build for the dev node's architecture. Check the three `KC_*` names and the realm's field names against that version's own documentation before relying on them: the names above are from memory of the current hostname options and are the thing most likely to be out of date. An exported realm from the running instance is the authority for the JSON's shape.

- [ ] **Step 5: Run the chart tests and commit**

Run: `uv run --all-packages pytest -q packages/converter/tests/test_chart_render.py && make helm-template`

```bash
git add charts/htrflow-devstack packages/converter/tests/test_chart_render.py
git commit -m "feat(devstack): a Keycloak with the realm the campaigns API needs"
```

- [ ] **Step 6: Run the whole path on the dev cluster**

Build the campaigns image for the node's architecture and push it where the dev cluster pulls from, as `docs/development/dev-cluster.md` describes for the other images. Upgrade the devstack release with `keycloak.enabled=true` and the main release with:

```text
api.enabled=true  api.allowNoIngress=true  api.image=<the pushed digest>
api.issuer=http://localhost:18080/realms/htrflow
api.jwksUrl=http://keycloak:8080/realms/htrflow/protocol/openid-connect/certs
api.audience=htrflow-batch  api.clientId=htrflow-cli  api.group=htr-operators
api.network.identityInNamespace=true
```

Then, with `kubectl port-forward svc/keycloak 18080:8080` and `kubectl port-forward svc/htrflow-api 8083:8083` running:

| Run | Expected |
|---|---|
| `htrflow-campaigns login --server http://localhost:8083/control` and sign in as `htr-dev` at the printed link | `signed in to http://localhost:8083/control as htr-dev` |
| `htrflow-campaigns whoami` | user `htr-dev`, group `htr-operators`, the release's namespace and queue |
| `htrflow-campaigns logout`, `login` again as `htr-outsider` | exit 1; the refusal names `htr-operators` |
| `curl -s -o /dev/null -w '%{http_code}' localhost:8083/control/v1/whoami` | `401` |
| `kubectl logs deploy/htrflow-api \| grep -c eyJ` | `0`: no token in the log |
| `kubectl get deploy/htrflow-api -o jsonpath='{.spec.template.spec.automountServiceAccountToken}'` | `false` |
| stop the Keycloak port-forward, `htrflow-campaigns whoami` | still answers: the service checks the token with keys it has |

- [ ] **Step 7: If the issuer does not line up, stop and report**

If `login` succeeds but `whoami` is refused over the issuer, or the pod cannot fetch the keys, the hostname settings did not do what Step 4 intends. Record what the token's `iss` was and what the pod saw, and report to the owner with the two ways on: a host name both sides resolve, or an Ingress with TLS in the devstack. Do not add an Ingress controller to the devstack on your own.

- [ ] **Step 8: Write it down** in `docs/development/dev-cluster.md`: a section "The campaigns API on the dev cluster" with the values, the two port-forwards, the sign-in and what Step 6 showed, in the page's present tense and without the cluster's name.

- [ ] **Step 9: Run every gate and commit**

Run: `make ci && bash scripts/loc-budget.sh && ZENSICAL="$PWD/.venv/bin/zensical" scripts/docs-site.sh build --clean --strict`
Expected: PASS.

```bash
git add docs/development/dev-cluster.md
git commit -m "docs(dev-cluster): signing in to the campaigns API on a development cluster"
```

---

### Task 9: The READMEs and the diagrams

Added on 2026-11-17 at the owner's request: the branch had changed no diagram and neither the root README nor the converter's.

**Files:**
- Modify: `docs/slides/diagrams/build_docs.py` (the source of every SVG under `docs/assets/diagrams`)
- Create: `docs/assets/diagrams/seq-sign-in.svg` (generated)
- Modify: `docs/assets/diagrams/architecture.svg` (generated), `docs/how-it-works/architecture.md`, `docs/reference/cli.md`, `README.md`, `packages/converter/README.md`

**Interfaces:**
- Consumes: what Tasks 1 to 7 built, read from the code: `packages/api/src/htrflow_api/app.py` and `tokens.py`, `packages/converter/src/htrflow_converter/signin.py` and `remote.py`, `charts/htrflow-batch/templates/api.yaml`.
- Produces: no code. Two diagrams and four pages that say what exists in this release and nothing more.

What the pictures must say, and must not:

- The campaigns API is **optional** (`api.enabled`, off by default). In this release it answers the sign-in details and `whoami`, holds **no Kubernetes rights** and mounts no Secret. It does not start, pause or remove anything yet, and `apply` reaches the cluster exactly as before. No arrow from the campaigns API to Jobs, Kueue or the bucket.
- It is reached through the web front's Ingress with TLS, or by a port-forward on a development cluster.
- It checks each token against the sign-in service's published signing keys; the token is never forwarded.

- [ ] **Step 1: The map (`architecture.svg`).** In `build_docs.py`, the "Kubernetes cluster" group's second row has a free first column: put a card there for the campaigns API (title and two sub-lines within the kit's widths), marked optional the way the diagram marks the optional image cache. Show the sign-in service as an outside party and one arrow from the campaigns API to it for the signing keys, and the CLI reaching the campaigns API from outside. Arrows never cross a card or a group label; if the present layout cannot carry both arrows cleanly, widen the "Outside" group to all three columns or add one lane, and say in the report what you chose. Change nothing else on the map.

- [ ] **Step 2: A new sequence diagram (`seq-sign-in.svg`)**, drawn with `seqkit.Steps` like `seq-campaign.svg`, titled "Signing in, in order". Actors: the person, the CLI, the campaigns API, the sign-in service. Steps, each checked against the code before it is drawn:
  1. the CLI asks the campaigns API for the sign-in details, with no token;
  2. the CLI reads the sign-in service's discovery document and asks for a device code;
  3. the CLI prints a link and a code; the person signs in in a browser;
  4. a loop: the CLI asks the sign-in service for the token, waiting between 5 and 60 seconds each time;
  5. the CLI stores the sign-in in a file only the person can read;
  6. `whoami`: the CLI calls the campaigns API with the token;
  7. the campaigns API fetches the signing keys the first time, or when a token names a key it has not seen;
  8. the campaigns API checks the signature, the issuer, the audience, the expiry and the group, and answers the user, groups, namespace and queue.
  A note under it: the token goes to the campaigns API and nowhere else.

- [ ] **Step 3: Generate and look.** Run `python3 docs/slides/diagrams/build_docs.py`. `git status` must show only `architecture.svg` changed and `seq-sign-in.svg` new among the SVGs; if the run rewrites any other SVG, do not commit those and report it. Rasterise both (`uv run --no-project --with cairosvg python -c ...` into the session scratchpad, never into the repository) and read the PNGs: no clipped label, no arrow through a card, nothing overlapping. The kit's own fit check is the authority for label widths.

- [ ] **Step 4: The pages.**
  - `docs/how-it-works/architecture.md`: the map's alt text names the campaigns API; one short paragraph (or one row, if the page lists its parts in a table) saying what it is, that it is optional, and what it does and does not do in this release.
  - `docs/reference/cli.md`, section "Signing in": embed `seq-sign-in.svg` with alt text that says what the picture shows.
  - `README.md`: two or three plain sentences where the README describes how a person works with the system: signing in exists, it is optional, with a link to the CLI reference's "Signing in". Do not touch the README's overview diagram: one campaign's path from git to the viewer does not pass through the campaigns API in this release.
  - `packages/converter/README.md`: name `login`, `logout` and `whoami` beside the commands it lists, one line each.

- [ ] **Step 5: The gates.** The strict site build exits 0 (`ZENSICAL=<a zensical outside the worktree> scripts/docs-site.sh build --clean --strict`); `uv run --all-packages pytest -q packages/converter/tests/test_docs_lint.py packages/converter/tests/test_documented_installs.py` passes; the general docs name no person, host or site and say "a loopback address", never a literal one.

- [ ] **Step 6: Commit**, the diagram source with its two SVGs in one commit and the pages in another.

```bash
git add docs/slides/diagrams/build_docs.py docs/assets/diagrams/architecture.svg docs/assets/diagrams/seq-sign-in.svg
git commit -m "docs(diagrams): the campaigns API on the map, and signing in, in order"
git add docs/how-it-works/architecture.md docs/reference/cli.md README.md packages/converter/README.md
git commit -m "docs: the campaigns API and signing in, in the READMEs and the architecture page"
```

---

## What the next plans take from this one

- The guard is the middleware in `app.py` (`_Guard`) in front of every `/v1` path: it checks the client version and the token before routing, whatever the method and however a route is registered, with `GET /v1/login` the one exception. `Depends(current_identity)` is not a guard: it only hands a route its verified caller, and raises (a 500) if the guard did not run. Every later route that needs the caller declares it.
- `Remote.get` and `Remote.request` are the CLI's only way to the service.
- The Deployment gains a ServiceAccount, the `apply.rbac` Role, the store Secret and the rendered `converter.yaml` in the plan that adds the first call needing them, with `automountServiceAccountToken` turned on there and not before.
- Task 8's result decides whether later dev-cluster runs keep the port-forward or need an Ingress.
