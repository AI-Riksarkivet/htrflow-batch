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
    env = dict(REQUIRED, MAX_IMAGE_WIDTH="1200", RESUME="false",
               LOOKAHEAD_PAGES="8", MAX_PAGES="4", S3_PREFIX="batch")
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
    env = dict(REQUIRED); del env["VOLUME_REF"]
    with pytest.raises(ConfigError, match="VOLUME_REF"):
        Config.from_env(env)
