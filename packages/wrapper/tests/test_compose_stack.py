"""The compose stack is the first thing a newcomer runs (Try it), so it must
come up from a fresh clone with nothing built and nothing but its ports free.

A newcomer walkthrough found three ways it did not: the wrapper's default
image was a tag nobody publishes, so `docker compose up` fell back to
building the whole image from source; the host ports were literals, so a
taken port was a dead end; and the mock manifest named the page images at an
address only the wrapper container could resolve, so the viewer showed the
transcription over a black page.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((REPO / ".docker" / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
S3_PORT = "${HTR_COMPOSE_S3_PORT:-19000}"
WEB_PORT = "${HTR_COMPOSE_WEB_PORT:-8080}"
_DIGEST = re.compile(r"@sha256:([0-9a-f]{64})")


def _default(image: str) -> str:
    """The image a service runs when its override variable is unset."""
    match = re.fullmatch(r"\$\{HTR_[A-Z_]+_IMAGE:-(.+)\}", image)
    assert match, image
    return match.group(1)


def _digests(path: str, repo: str) -> set[str]:
    text = (REPO / path).read_text()
    return set(re.findall(re.escape(repo) + r"@sha256:([0-9a-f]{64})", text))


def test_the_stack_runs_the_release_this_commit_pins() -> None:
    """Both images default to the published digests the release commit pins
    elsewhere (`publishedPins` in .dagger/published.go), so the two never
    drift and `up` pulls instead of building."""
    wrapper = _default(SERVICES["wrapper"]["image"])
    web = _default(SERVICES["web"]["image"])
    pinned_wrapper = _digests(
        "packages/converter/src/htrflow_converter/template/pipelines/demo-v1.yaml",
        "docker.io/riksarkivet/htrflow-batch",
    )
    pinned_web = _digests(
        "charts/htrflow-batch/values.yaml", "docker.io/riksarkivet/htrflow-web"
    )
    assert wrapper.startswith("riksarkivet/htrflow-batch@")
    assert web.startswith("riksarkivet/htrflow-web@")
    assert {_DIGEST.search(wrapper).group(1)} == pinned_wrapper
    assert {_DIGEST.search(web).group(1)} == pinned_web


def test_nothing_in_the_stack_is_built_behind_the_readers_back() -> None:
    """`make compose-smoke` builds the checkout's images itself and runs them
    with `--no-build`; a `build:` left on a service only means that a missing
    image turns `up` into a full image build."""
    assert [name for name, svc in SERVICES.items() if "build" in svc] == []


def test_host_ports_are_settable_and_every_url_follows_them() -> None:
    rustfs = SERVICES["rustfs"]
    # RustFS listens on the same port inside as on the host, and the wrapper
    # shares its network namespace, so `localhost:<port>` means the same
    # server to the wrapper and to the browser.
    assert rustfs["ports"] == [f"{S3_PORT}:{S3_PORT}"]
    assert rustfs["environment"]["RUSTFS_ADDRESS"] == f"0.0.0.0:{S3_PORT}"
    assert S3_PORT in " ".join(rustfs["healthcheck"]["test"])
    assert SERVICES["web"]["ports"] == [f"{WEB_PORT}:8081"]

    wrapper = SERVICES["wrapper"]
    assert wrapper["network_mode"] == "service:rustfs"
    local = f"http://localhost:{S3_PORT}"
    env = wrapper["environment"]
    assert env["S3_ENDPOINT"] == local
    assert env["IIIF_MANIFEST_URL"].startswith(f"{local}/")
    # Results are read on the web front's origin, through its /results.
    assert env["RESULTS_URL"] == f"http://localhost:{WEB_PORT}/results"
    # The page images the mock manifest names are the ones the browser loads.
    assert SERVICES["fixtures-init"]["environment"]["MOCK_BASE"].startswith(f"{local}/")


def _smoke_recipe() -> str:
    """`compose-smoke-run` with the two Makefile variables above it spelled
    out, as make expands them."""
    makefile = (REPO / "Makefile").read_text()
    block = makefile[makefile.index("COMPOSE_WEB :=") :].split("\n\n")[0]
    names = dict(re.findall(r"^(COMPOSE_\w+) := (.*)$", block, re.M))
    recipe = block[block.index("compose-smoke-run:") :]
    for _ in names:  # a variable may name another
        for name, value in names.items():
            recipe = recipe.replace(f"$({name})", value)
    return recipe


def test_the_port_knobs_reach_compose_through_make() -> None:
    env_example = (REPO / ".env.example").read_text()
    for key, default in (
        ("HTR_COMPOSE_S3_PORT", "19000"),
        ("HTR_COMPOSE_WEB_PORT", "8080"),
    ):
        assert re.search(rf"^{key}={default}$", env_example, re.M), key
    assert "http://localhost:$(HTR_COMPOSE_WEB_PORT)/uv.html" in _smoke_recipe()


# --- The results bucket is private: a login user and the results proxy ------

def _login_init() -> str:
    return (REPO / "scripts" / "compose_login_init.sh").read_text()


def test_the_results_proxy_runs_beside_the_web_front_on_the_same_image() -> None:
    results = SERVICES["results"]
    assert results["image"] == SERVICES["web"]["image"]
    # the image's ENTRYPOINT is htrflow-web; a compose `command` would only
    # hand it arguments
    assert results["entrypoint"] == ["/app/.venv/bin/htrflow-results"]
    assert "command" not in results
    env = results["environment"]
    assert env["HTRFLOW_KEY_DERIVATION"] == "none"  # RustFS issues the keys
    assert env["HTRFLOW_TRUSTED_HOPS"] == "1"  # the web front, no Ingress
    assert env["S3_ENDPOINT"] == f"http://rustfs:{S3_PORT}"
    assert env["S3_BUCKET"] == SERVICES["wrapper"]["environment"]["S3_BUCKET"]
    # it reads the keys the wrapper writes: <namespace>/<pipeline>/<volume>/
    assert env["HTRFLOW_RESULTS_NAMESPACE"] == (
        SERVICES["wrapper"]["environment"]["S3_PREFIX"]
    )
    # no port on the host: only the web front reaches it
    assert "ports" not in results


def test_the_proxy_and_the_web_front_hold_no_bucket_credential() -> None:
    for name in ("results", "web"):
        env = SERVICES[name]["environment"]
        assert not [k for k in env if k.startswith("AWS_")], name
    assert SERVICES["web"]["environment"]["HTRFLOW_RESULTS_PROXY"] == (
        "http://results:8082/results"
    )


def test_the_session_key_is_generated_at_init_and_read_by_the_proxy() -> None:
    results = SERVICES["results"]
    key_file = results["environment"]["HTRFLOW_SESSION_KEY_FILE"]
    assert key_file == "/session/key"
    assert "session:/session:ro" in results["volumes"]
    assert results["depends_on"]["login-init"] == {
        "condition": "service_completed_successfully"
    }
    assert "session:/session" in SERVICES["login-init"]["volumes"]
    assert "session" in COMPOSE["volumes"]
    script = _login_init()
    # 32 random bytes, base64, readable by the web image's user alone
    assert "head -c 32 /dev/urandom | base64" in script
    assert "chmod 0400" in script and 'chown "$SESSION_KEY_OWNER"' in script
    assert SERVICES["login-init"]["environment"]["SESSION_KEY_OWNER"] == "1000:1000"


def test_a_read_only_login_user_is_created_with_rustfs_own_client() -> None:
    init = SERVICES["login-init"]
    assert re.fullmatch(r"rustfs/rc@sha256:[0-9a-f]{64}", init["image"])
    # the same client, pinned the same, as the devstack chart's init hook
    devstack = yaml.safe_load(
        (REPO / "charts" / "htrflow-devstack" / "values.yaml").read_text()
    )
    assert init["image"] == devstack["rustfs"]["init"]["image"].split("  #")[0]
    # the image's entrypoint is `rc` itself: the script replaces it
    assert init["entrypoint"] == ["/bin/sh", "/scripts/compose_login_init.sh"]
    assert "command" not in init
    env = init["environment"]
    assert env["LOGIN_USER"] == "${HTR_DEV_LOGIN_USER:-htr-reader}"
    assert env["LOGIN_PASSWORD"] == "${HTR_DEV_LOGIN_PASSWORD:-htr-reader-pass}"
    assert env["S3_BUCKET"] == SERVICES["wrapper"]["environment"]["S3_BUCKET"]
    script = _login_init()
    for line in (
        "rc admin policy create local htr-read /tmp/read-policy.json",
        'rc admin user add local "$LOGIN_USER" "$LOGIN_PASSWORD"',
        'rc admin policy attach local htr-read --user "$LOGIN_USER"',
    ):
        assert line in script, line
    # GetObject on the results bucket, nothing else
    assert '"Action":["s3:GetObject"]' in script
    assert '"Resource":["arn:aws:s3:::%s/*"]' in script
    assert "ListBucket" not in script and "PutObject" not in script


def test_the_login_knobs_reach_compose_through_make() -> None:
    env_example = (REPO / ".env.example").read_text()
    makefile = (REPO / "Makefile").read_text()
    for key, default in (
        ("HTR_DEV_LOGIN_USER", "htr-reader"),
        ("HTR_DEV_LOGIN_PASSWORD", "htr-reader-pass"),
    ):
        assert re.search(rf"^{key}={default}$", env_example, re.M), key
        assert re.search(rf"^export [^\n]*(?:\\\n[^\n]*)*\b{key}\b", makefile, re.M), key


def test_the_smoke_logs_in_and_reads_a_result_through_the_web_front() -> None:
    """`make compose-smoke` starts the proxy with the web front, and proves
    the path a person takes: no cookie is a 401, a login is a cookie, and
    the cookie reads the volume's iiif.json."""
    run = _smoke_recipe()
    # --wait: the proxy answers before the first request, not a 502
    assert "docker compose up --no-build -d --wait web results" in run
    check = " ".join(SERVICES["results"]["healthcheck"]["test"])
    assert "http://127.0.0.1:8082/healthz" in check
    assert "/results/_login" in run
    assert "Origin: http://localhost:$(HTR_COMPOSE_WEB_PORT)" in run
    assert "/results/$(HTR_NAMESPACE)/demo-v1/mock-vol/iiif.json" in run
    # and one person's login does not carry over to a request without it
    last = run.rstrip().splitlines()[-1]
    assert last.startswith("\ttest ") and last.endswith("= 401"), last
    assert "-b" not in last
