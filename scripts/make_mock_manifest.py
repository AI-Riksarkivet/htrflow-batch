"""Generate a minimal IIIF P3 manifest over the htr_demo fixture images.

Canvas width/height are placeholders (the wrapper never reads them; real
dims come from the ALTO at publish time per D19).
Set MOCK_BASE to point at a different S3 endpoint; compose sets MOCK_BASE=http://rustfs:9000/htr-fixtures/mock-vol
so the wrapper container can fetch the images (host port 19000). Outside compose
the default is derived from HTR_S3_ENDPOINT (repo-root .env, PoC NodePort)."""

import json
import os
import sys

_S3 = os.environ.get("HTR_S3_ENDPOINT", "http://localhost:30900").rstrip("/")
BASE = os.environ.get("MOCK_BASE", f"{_S3}/htr-fixtures/mock-vol")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 4

manifest = {
    "@context": "http://iiif.io/api/presentation/3/context.json",
    "id": f"{BASE}/manifest.json",
    "type": "Manifest",
    "label": {"sv": ["Mock-volym (htr_demo exempelbilder)"]},
    "items": [],
}
for i in range(1, N + 1):
    cid = f"{BASE}/canvas/{i:04d}"
    manifest["items"].append(
        {
            "id": cid,
            "type": "Canvas",
            "label": {"none": [f"sida {i}"]},
            "width": 2000,
            "height": 3000,  # placeholder, unused by wrapper
            "items": [
                {
                    "id": f"{cid}/ap",
                    "type": "AnnotationPage",
                    "items": [
                        {
                            "id": f"{cid}/anno",
                            "type": "Annotation",
                            "motivation": "painting",
                            "target": cid,
                            "body": {
                                "id": f"{BASE}/{i:04d}.jpg",
                                "type": "Image",
                                "format": "image/jpeg",
                            },
                        }
                    ],
                }
            ],
        }
    )
print(json.dumps(manifest, ensure_ascii=False, indent=2))
