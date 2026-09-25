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
    assert env["PUBLIC_RESULTS_BASE"].startswith(f"{local}/")
    # The page images the mock manifest names are the ones the browser loads.
    assert SERVICES["fixtures-init"]["environment"]["MOCK_BASE"].startswith(f"{local}/")


def test_the_port_knobs_reach_compose_through_make() -> None:
    env_example = (REPO / ".env.example").read_text()
    for key, default in (
        ("HTR_COMPOSE_S3_PORT", "19000"),
        ("HTR_COMPOSE_WEB_PORT", "8080"),
    ):
        assert re.search(rf"^{key}={default}$", env_example, re.M), key
    makefile = (REPO / "Makefile").read_text()
    run = makefile[makefile.index("compose-smoke-run:") :].split("\n\n")[0]
    assert "http://localhost:$(HTR_COMPOSE_WEB_PORT)/uv.html" in run
