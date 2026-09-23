#!/usr/bin/env sh
# JSON schemas for the custom resources charts/htrflow-batch renders -- the
# Kueue queue objects and the Kyverno ClusterPolicies -- so kubeconform
# validates them instead of skipping them (audit 0923, test audit
# TA-infra-15: `-ignore-missing-schemas` skipped 11 of 26 resources, and a
# `resourceGroupz` typo passed). Built from the CRDs of the releases the
# Makefile installs, each file pinned by sha256: a changed file fails here,
# never silently changes what "valid" means.
#
# Every object that lists its properties gets `additionalProperties: false`
# unless the CRD says otherwise (x-kubernetes-preserve-unknown-fields, or an
# additionalProperties of its own): that is what kubeconform's -strict means
# for a built-in kind, and what catches a misspelled field.
#
#   scripts/crd-schemas.sh <out-dir>
#   kubeconform -schema-location default \
#     -schema-location '<out-dir>/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' …
#
# Needs python3 with PyYAML (the workspace venv: PYTHON defaults to
# `uv run --no-sync python`). Honours SSL_CERT_FILE for an intercepting proxy.
set -eu
out=${1:?usage: scripts/crd-schemas.sh <out-dir>}
exec ${PYTHON:-uv run --no-sync python} - "$out" <<'EOF'
import hashlib
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

import yaml

# Kueue as `make install-kueue` installs it (KUEUE_VERSION); Kyverno's
# ClusterPolicy CRD from the release the policy tests' CLI is (v1.19.0).
SOURCES = [
    (
        "https://github.com/kubernetes-sigs/kueue/releases/download/v0.19.5/manifests.yaml",
        "7df370a12494af824f289cf195e89d6c03d461139290ff41a1bd190a5ff8225a",
    ),
    (
        "https://github.com/kyverno/kyverno/releases/download/v1.19.0/kyverno.io_clusterpolicies.yaml",
        "fb041ee35f8e89b5bca566bb6e00cd67fccc30be8859998757333f2a40d79655",
    ),
]
GROUPS = {"kueue.x-k8s.io", "kyverno.io"}


def strict(node):
    if isinstance(node, dict):
        if (
            node.get("type") == "object"
            and "properties" in node
            and "additionalProperties" not in node
            and not node.get("x-kubernetes-preserve-unknown-fields")
        ):
            node["additionalProperties"] = False
        for value in node.values():
            strict(value)
    elif isinstance(node, list):
        for value in node:
            strict(value)
    return node


out = Path(sys.argv[1])
cafile = os.environ.get("SSL_CERT_FILE")
context = ssl.create_default_context(cafile=cafile) if cafile else None
written = 0
for url, sha256 in SOURCES:
    with urllib.request.urlopen(url, timeout=120, context=context) as answer:
        body = answer.read()
    got = hashlib.sha256(body).hexdigest()
    if got != sha256:
        sys.exit(f"{url}: sha256 {got}, pinned {sha256} -- refusing it")
    for doc in yaml.safe_load_all(body):
        if not doc or doc.get("kind") != "CustomResourceDefinition":
            continue
        spec = doc["spec"]
        if spec["group"] not in GROUPS:
            continue
        for version in spec["versions"]:
            schema = (version.get("schema") or {}).get("openAPIV3Schema")
            if not schema:
                continue
            name = f"{spec['names']['kind'].lower()}_{version['name']}.json"
            path = out / spec["group"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(strict(schema)), encoding="utf-8")
            written += 1
print(f"{written} schemas in {out}")
EOF
