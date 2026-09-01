import pytest

from htrflow_batch.config import Config, ConfigError

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


def test_max_seconds_default_and_override():
    assert Config.from_env(REQUIRED).max_seconds == 0
    cfg = Config.from_env(dict(REQUIRED, MAX_SECONDS="21600"))
    assert cfg.max_seconds == 21600


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
