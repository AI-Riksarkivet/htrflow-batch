import re
from pathlib import Path

import pytest

from htrflow_batch.config import Config, ConfigError

#: A name that would mean a credential is travelling as an environment
#: variable rather than as the mounted Secret file.
_SECRETISH = r"KEY|TOKEN|PASSWORD|SECRET_ACCESS"

#: Every literal env read in the wrapper's source, not just `Config`'s own
#: fields — `publish.py`, `main.py` and `warmup.py` read six names of their
#: own (docs: configuration.md, "Also read from the environment").
_ENV_READ = re.compile(
    r'(?:env|environ)\.get\(\s*["\'](\w+)["\']'
    r'|(?:env|environ)\[\s*["\'](\w+)["\']'
    r'|getenv\(\s*["\'](\w+)["\']'
)


def _literal_env_reads() -> list[str]:
    src = Path(__file__).parents[1] / "src"
    names = [
        m.group(1) or m.group(2) or m.group(3)
        for path in src.rglob("*.py")
        for m in _ENV_READ.finditer(path.read_text(encoding="utf-8"))
    ]
    return names


REQUIRED = {
    "VOLUME_REF": "SE-RA-1234",
    "IIIF_MANIFEST_URL": "https://iiif.example/mock-vol/manifest.json",
    "PIPELINE_PATH": "/config/pipeline.yaml",
    "PIPELINE_ID": "demo-v1",
    "S3_ENDPOINT": "http://rustfs:9000",
    "S3_BUCKET": "htr-results",
    "PUBLIC_RESULTS_BASE": "http://10.16.51.53:30900/htr-results",
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


def test_max_seconds_is_not_a_wrapper_setting():
    """The per-volume budget is the pod's activeDeadlineSeconds now; a stray
    MAX_SECONDS in the env must be ignored, not resurrect a wrapper field."""
    cfg = Config.from_env(dict(REQUIRED, MAX_SECONDS="21600"))
    assert not hasattr(cfg, "max_seconds")


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
    names = Config.env_names() + _literal_env_reads()
    carriers = sorted({n for n in names if re.search(_SECRETISH, n)})
    assert carriers == [], (
        f"{carriers}: secrets reach the wrapper as a mounted file, never as env"
    )
