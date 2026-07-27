import pytest


def _canvas(i: int, service_id: str) -> dict:
    return {
        "id": f"{service_id}/canvas",
        "type": "Canvas",
        "label": {"none": [f"page {i}"]},
        "width": 3507, "height": 4962,
        "items": [{
            "type": "AnnotationPage",
            "items": [{
                "type": "Annotation",
                "motivation": "painting",
                "body": {
                    "id": f"{service_id}/full/max/0/default.jpg",
                    "type": "Image",
                    "service": [{"id": service_id, "type": "ImageService3"}],
                },
            }],
        }],
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
