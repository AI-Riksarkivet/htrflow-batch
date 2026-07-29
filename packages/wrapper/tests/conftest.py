import copy

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


P2_MANIFEST = {
    "@context": "http://iiif.io/api/presentation/2/context.json",
    "@type": "sc:Manifest",
    "label": "P2 vol",
    "sequences": [
        {
            "canvases": [
                {
                    "@id": "http://ex/canvas/1",
                    "label": "f. 1r",
                    "width": 3000,
                    "height": 4000,
                    "images": [
                        {
                            "resource": {
                                "@id": "http://ex/img/full/full/0/default.jpg",
                                "format": "image/jpeg",
                                "service": {
                                    "@id": "http://ex/img",
                                    "profile": "http://iiif.io/api/image/2/level1.json",
                                },
                            }
                        }
                    ],
                }
            ]
        }
    ],
}


@pytest.fixture
def p2_manifest() -> dict:
    """IIIF Presentation 2 manifest (Bodleian-shaped); safe to mutate."""
    return copy.deepcopy(P2_MANIFEST)


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
