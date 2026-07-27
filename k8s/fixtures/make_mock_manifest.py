"""Generate a minimal IIIF P3 manifest over the htr_demo fixture images.

Canvas width/height are placeholders (the wrapper never reads them; real
dims come from the ALTO at publish time per D19)."""
import json
import sys

BASE = "http://10.16.51.53:30900/htr-fixtures/mock-vol"
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
    manifest["items"].append({
        "id": cid, "type": "Canvas",
        "label": {"none": [f"sida {i}"]},
        "width": 2000, "height": 3000,  # placeholder, unused by wrapper
        "items": [{
            "id": f"{cid}/ap", "type": "AnnotationPage",
            "items": [{
                "id": f"{cid}/anno", "type": "Annotation",
                "motivation": "painting", "target": cid,
                "body": {"id": f"{BASE}/{i:04d}.jpg", "type": "Image",
                          "format": "image/jpeg"},
            }],
        }],
    })
print(json.dumps(manifest, ensure_ascii=False, indent=2))
