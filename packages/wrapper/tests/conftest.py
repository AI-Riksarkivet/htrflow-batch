import boto3
import pytest
from moto import mock_aws

from htrflow_batch.config import Config


def _canvas(i: int, service_id: str) -> dict:
    return {
        "id": f"{service_id}/canvas",
        "type": "Canvas",
        "label": {"none": [f"page {i}"]},
        "width": 3507,
        "height": 4962,
        "items": [
            {
                "type": "AnnotationPage",
                "items": [
                    {
                        "type": "Annotation",
                        "motivation": "painting",
                        "body": {
                            "id": f"{service_id}/full/max/0/default.jpg",
                            "type": "Image",
                            "service": [{"id": service_id, "type": "ImageService3"}],
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_manifest() -> dict:
    base = "https://iiif.example/mock-vol"
    return {
        "id": f"{base}/manifest.json",
        "type": "Manifest",
        "label": {"sv": ["Testvolym"]},
        "items": [_canvas(i, f"{base}/page-{i:05d}") for i in range(1, 4)],
    }


REQUIRED_ENV = {
    "VOLUME_REF": "SE-RA-1234",
    "IIIF_MANIFEST_URL": "https://x/manifest",
    "PIPELINE_PATH": "/config/pipeline.yaml",
    "PIPELINE_ID": "demo-v1",
    "S3_ENDPOINT": "",  # empty -> boto3 default endpoint (moto intercepts)
    "S3_BUCKET": "htr-results",
    "PUBLIC_RESULTS_BASE": "http://public/htr-results",
}


@pytest.fixture
def cfg(tmp_path) -> Config:
    env = dict(REQUIRED_ENV, WORKDIR_PATH=str(tmp_path / "work"))
    return Config.from_env(env)


@pytest.fixture
def s3(cfg):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=cfg.s3_bucket)
        yield client
