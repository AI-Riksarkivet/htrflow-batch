import re
from pathlib import Path

import pytest

from htrflow_batch.config import Config, ConfigError

#: The env every test below starts from; conftest's fixture builds the same
#: one, but importing it would tie this file to how pytest was invoked.
REQUIRED_ENV = {
    "VOLUME_REF": "SE-RA-1234",
    "IIIF_MANIFEST_URL": "https://x/manifest",
    "PIPELINE_PATH": "/config/pipeline.yaml",
    "PIPELINE_ID": "demo-v1",
    "S3_BUCKET": "htr-results",
    "PUBLIC_RESULTS_BASE": "http://public/htr-results",
}

#: A name that would mean a credential is travelling as an environment
#: variable rather than as the mounted Secret file.
_SECRETISH = r"KEY|TOKEN|PASSWORD|SECRET_ACCESS"

#: Every literal env read in the wrapper's source, not just `Config`'s own
#: fields — `publish.py`, `main.py` and `warmup.py` read names of their own
#: (docs: configuration.md, "Also read from the environment").
_ENV_READ = re.compile(
    r'(?:env|environ)\.get\(\s*["\'](\w+)["\']'
    r'|(?:env|environ)\[\s*["\'](\w+)["\']'
    r'|getenv\(\s*["\'](\w+)["\']'
)


#: The one credential-shaped name allowed to travel as env, and the file
#: that may read it. `HF_TOKEN` is `huggingface_hub`'s own contract; the
#: converter renders it from a `secretKeyRef` into the WARM-UP pod alone,
#: which mounts no S3 Secret, holds no campaign data and exits when its
#: download is done. The campaign pod -- the long-lived one, running third-
#: party model code over fetched images -- still gets nothing: it runs
#: `HF_HUB_OFFLINE=1` against the filled cache and has no route to the Hub.
#: A file route exists for the Hub too (`HF_TOKEN_PATH`), but its default
#: sits inside `HF_HOME`, the cache PVC every campaign pod mounts read-only,
#: so it would have to be mounted elsewhere and pointed at. The exemption is
#: therefore scoped, not general: reading `HF_TOKEN` anywhere but
#: `warmup.py` fails this test.
_ENV_BY_CONTRACT = {"HF_TOKEN": "warmup.py"}


def _literal_env_reads() -> list[tuple[str, str]]:
    """Every literal env read in the wrapper's source, as (file, name)."""
    src = Path(__file__).parents[1] / "src"
    return [
        (path.name, m.group(1) or m.group(2) or m.group(3))
        for path in src.rglob("*.py")
        for m in _ENV_READ.finditer(path.read_text(encoding="utf-8"))
    ]


REQUIRED = {
    "VOLUME_REF": "SE-RA-1234",
    "IIIF_MANIFEST_URL": "https://iiif.example/mock-vol/manifest.json",
    "PIPELINE_PATH": "/config/pipeline.yaml",
    "PIPELINE_ID": "demo-v1",
    "S3_ENDPOINT": "http://rustfs:9000",
    "S3_BUCKET": "htr-results",
    "PUBLIC_RESULTS_BASE": "http://192.0.2.53:30900/htr-results",
}


def test_from_env_defaults():
    cfg = Config.from_env(REQUIRED)
    assert cfg.volume_ref == "SE-RA-1234"
    assert cfg.max_image_width == 2500
    assert cfg.resume is True
    assert cfg.lookahead_pages == 64
    assert cfg.max_pages == 0
    assert cfg.s3_prefix == ""


def test_from_env_overrides():
    env = dict(
        REQUIRED,
        MAX_IMAGE_WIDTH="1200",
        RESUME="false",
        LOOKAHEAD_PAGES="8",
        MAX_PAGES="4",
        S3_PREFIX="batch",
    )
    cfg = Config.from_env(env)
    assert cfg.max_image_width == 1200
    assert cfg.resume is False
    assert cfg.lookahead_pages == 8
    assert cfg.max_pages == 4
    assert cfg.volume_prefix == "batch/demo-v1/SE-RA-1234"


def test_volume_prefix_no_prefix():
    cfg = Config.from_env(REQUIRED)
    assert cfg.volume_prefix == "demo-v1/SE-RA-1234"


def test_missing_required_raises():
    env = dict(REQUIRED)
    del env["VOLUME_REF"]
    with pytest.raises(ConfigError, match="VOLUME_REF"):
        Config.from_env(env)


def test_s3_endpoint_optional():
    env = dict(REQUIRED)
    del env["S3_ENDPOINT"]
    cfg = Config.from_env(env)
    assert cfg.s3_endpoint == ""


def test_byte_caps_default_and_override():
    cfg = Config.from_env(REQUIRED)
    assert cfg.manifest_max_bytes == 16777216
    assert cfg.fetch_max_bytes == 67108864
    cfg = Config.from_env(
        dict(REQUIRED, MANIFEST_MAX_BYTES="1024", FETCH_MAX_BYTES="2048")
    )
    assert cfg.manifest_max_bytes == 1024
    assert cfg.fetch_max_bytes == 2048


def test_the_image_cache_bucket_is_off_unless_set(cfg):
    assert cfg.image_cache_bucket == ""


def test_the_image_cache_bucket_reads_its_env():
    cfg = Config.from_env(dict(REQUIRED, IMAGE_CACHE_BUCKET="images-batch"))
    assert cfg.image_cache_bucket == "images-batch"


def test_download_deadline_default_and_override():
    """3063: one download's wall-clock budget, separate from the per-read
    timeouts and from the pod's own deadline."""
    assert Config.from_env(REQUIRED).download_deadline_seconds == 300.0
    cfg = Config.from_env(dict(REQUIRED, DOWNLOAD_DEADLINE_SECONDS="45"))
    assert cfg.download_deadline_seconds == 45.0


def test_images_is_an_alternative_to_manifest_url():
    env = dict(REQUIRED)
    del env["IIIF_MANIFEST_URL"]
    env["IMAGES"] = "https://x/1.jpg,https://x/2.jpg"
    cfg = Config.from_env(env)
    assert cfg.images == "https://x/1.jpg,https://x/2.jpg"
    assert cfg.manifest_url == ""


def test_images_and_manifest_url_are_mutually_exclusive():
    env = dict(REQUIRED, IMAGES="https://x/1.jpg")
    with pytest.raises(ConfigError, match="exactly one"):
        Config.from_env(env)


def test_neither_images_nor_manifest_url_is_permanent():
    env = dict(REQUIRED)
    del env["IIIF_MANIFEST_URL"]
    with pytest.raises(ConfigError, match="exactly one"):
        Config.from_env(env)


def test_optional_env_is_coerced_by_pydantic():
    """The class-level default is the only default and pydantic does the
    parsing: it accepts the bool words the old hand-rolled _bool did, and a
    value it cannot parse is a ValueError, which _main classifies exit 13."""
    assert Config.from_env(dict(REQUIRED, RESUME="off")).resume is False
    assert Config.from_env(dict(REQUIRED, RESUME="yes")).resume is True
    assert (
        Config.from_env(dict(REQUIRED, LOG_SHIP_SECONDS="2.5")).log_ship_seconds == 2.5
    )
    with pytest.raises(ValueError):
        Config.from_env(dict(REQUIRED, LOOKAHEAD_PAGES="abc"))


def test_no_setting_may_carry_a_secret():
    """The wrapper's S3 credentials are a mounted Secret file
    (``AWS_SHARED_CREDENTIALS_FILE=/secrets/s3/credentials``), never an env
    var: env is readable in ``kubectl describe``, in a crash dump and in
    every child process. A new setting that looks like a credential fails
    here rather than in a review -- and so does a new literal env read
    anywhere in the package, not just a new ``Config`` field."""
    reads = _literal_env_reads()
    # The exemption is for literal reads only. A `Config` field aliased
    # HF_TOKEN is a different thing entirely: `Config` is the CAMPAIGN pod's
    # contract, so such a field would ask the converter to put the token in
    # the one pod that must never hold it.
    exempt = {n for _, n in reads if n in _ENV_BY_CONTRACT}
    looks_like = {
        n
        for n in Config.env_names() + [n for _, n in reads]
        if re.search(_SECRETISH, n)
    }
    carriers = sorted(
        looks_like - exempt | {n for n in Config.env_names() if n in exempt}
    )
    assert carriers == [], (
        f"{carriers}: secrets reach the wrapper as a mounted file, never as env"
    )
    stray = sorted(
        (f, n) for f, n in reads if n in _ENV_BY_CONTRACT and f != _ENV_BY_CONTRACT[n]
    )
    assert stray == [], (
        f"{stray}: the env exemption is per file — only the warm-up may read a token"
    )


def test_provenance_fields_default_to_unknown_and_read_their_env():
    cfg = Config.from_env(REQUIRED)
    assert cfg.image_digest == "unknown"
    assert cfg.htrflow_base_revision == "unknown"
    cfg = Config.from_env(
        dict(
            REQUIRED,
            IMAGE_DIGEST="docker.io/x@sha256:abc",
            HTRFLOW_BASE_REVISION="v0.2.6-35f48a7",
        )
    )
    assert cfg.image_digest == "docker.io/x@sha256:abc"
    assert cfg.htrflow_base_revision == "v0.2.6-35f48a7"


# -- the IMAGES separator (2026-09-14) -------------------------------------


def _images(value: str) -> list[str]:
    env = dict(REQUIRED)
    del env["IIIF_MANIFEST_URL"]
    return Config.from_env(dict(env, IMAGES=value)).image_urls


def test_images_splits_on_whitespace_and_keeps_a_iiif_size_comma_whole():
    """`/full/2500,/0/default.jpg` is a legal IIIF Image API size request; a
    comma split tore it in half and failed setup on "/0/default.jpg"."""
    a = "https://images.example.org/archives!R0001203_00044/full/2500,/0/default.jpg"
    b = "https://images.example.org/archives!R0001203_00045/full/2500,/0/default.jpg"
    assert _images(f"{a} {b}") == [a, b]
    assert _images(a) == [a]


def test_images_ignores_runs_of_whitespace_and_a_trailing_newline():
    assert _images("  https://x/1.jpg \t https://x/2.jpg\n") == [
        "https://x/1.jpg",
        "https://x/2.jpg",
    ]


def test_images_still_accepts_a_comma_joined_list_from_an_older_render():
    assert _images("https://x/1.jpg,https://x/2.jpg") == [
        "https://x/1.jpg",
        "https://x/2.jpg",
    ]


def test_the_comma_fallback_never_takes_a_url_that_merely_contains_a_comma():
    """Every comma-split piece has to be an http(s) URL of its own, or the
    value is one URL that happens to carry a comma."""
    url = "https://x/full/2500,/0/default.jpg"
    assert _images(url) == [url]
    assert _images(f"{url} https://x/2.jpg") == [url, "https://x/2.jpg"]


@pytest.mark.parametrize(
    "value",
    ["../other", "a/b", "..", ".", "vol ume", "vol\nume", "vol#1", "a..b"],
)
@pytest.mark.parametrize("name", ["VOLUME_REF", "PIPELINE_ID"])
def test_key_shaped_fields_are_refused(name, value):
    """W12: both go verbatim into every S3 key the run writes
    (`<pipeline>/<volume>/...`) and into the public `viewer_url`. A campaign
    file is the source, so the shape is checked once, here, and the run fails
    permanently rather than writing outside its own prefix."""
    env = dict(REQUIRED_ENV, **{name: value})
    with pytest.raises(ValueError, match="letters, digits"):
        Config.from_env(env)


@pytest.mark.parametrize("value", ["SE-RA-1234", "demo-v1", "vol.2", "a_b", "0"])
def test_key_shaped_fields_accept_the_names_campaigns_use(value):
    Config.from_env(dict(REQUIRED_ENV, VOLUME_REF=value, PIPELINE_ID=value))


def test_the_default_lookahead_is_half_the_jobs_workdir():
    """The lookahead lives in the Job's memory-backed /work, next to the
    outputs, HOME and TMPDIR (audit 0923 E-14). A pipeline at a named size
    gets LOOKAHEAD_BYTES rendered from its own workdir; one without runs the
    Job skeleton's /work and this default, so the two are held together."""
    import yaml

    from htrflow_batch.stream import LOOKAHEAD_BYTES

    skeleton = (
        Path(__file__).resolve().parents[2]
        / "converter/src/htrflow_converter/manifests/campaign-job.yaml"
    )
    volumes = yaml.safe_load(skeleton.read_text())["spec"]["template"]["spec"]
    work = next(v for v in volumes["volumes"] if v["name"] == "work")["emptyDir"]
    assert work["medium"] == "Memory" and work["sizeLimit"].endswith("Gi")
    half = int(work["sizeLimit"].removesuffix("Gi")) * 1024**3 // 2
    default = Config.from_env(REQUIRED_ENV).lookahead_bytes
    assert default == LOOKAHEAD_BYTES == half
