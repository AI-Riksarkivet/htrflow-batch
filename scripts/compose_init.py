"""Compose init: create buckets, upload htr_demo fixture pages, publish the
mock IIIF manifest, and set the anonymous-read policy + CORS on the buckets.

Env: S3_ENDPOINT (default http://rustfs:9000), S3_BUCKET (results bucket,
default htr-results), FIXTURES_BUCKET (default htr-fixtures), PUBLIC_LOGS
("true"/"false", default true — whether status/logs/* is anonymous-readable),
MOCK_BASE (default http://rustfs:9000/htr-fixtures/mock-vol — the
container-internal form used by compose; see .docker/docker-compose.yml
comment for browser-fidelity trade-off), AWS creds via standard vars."""

import json
import os
import subprocess
import sys

import boto3
import httpx

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://rustfs:9000")
RESULTS_BUCKET = os.environ.get("S3_BUCKET", "htr-results")
FIXTURES_BUCKET = os.environ.get("FIXTURES_BUCKET", "htr-fixtures")
PUBLIC_LOGS = os.environ.get("PUBLIC_LOGS", "true").lower() in ("1", "true", "yes")
MOCK_BASE = os.environ.get(
    "MOCK_BASE", f"http://rustfs:9000/{FIXTURES_BUCKET}/mock-vol"
)
HF = "https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/main/.gradio_cache/examples"
# Known-good htr_demo example filenames (from the retired PoC job manifests).
PAGES = [
    "A0062408_00006.jpg",
    "A0070302_00201.jpg",
    "A0073477_00025.jpg",
    "R0003364_00005.jpg",
]

# Reconciler-private state under status/ (audit X14/S6). Everything else —
# <pipeline>/<volume>/*, sources/* — is what the viewer fetches anonymously.
# Mirrors the devstack chart's bucket-policy helper
# (charts/htrflow-devstack/templates/_helpers.tpl): keep the two in step.
PRIVATE_STATUS_KEYS = [
    "status/attempts.json",
    "status/validation.json",
    "status/volumes.json",
    "status/failures/*",
]


def anonymous_read_policy(bucket: str, private_keys: list[str]) -> dict:
    """Allow anonymous GetObject on everything except ``private_keys``.

    An Allow with NotResource, not an Allow + Deny pair: RustFS applies a Deny
    to the credentialed root principal too (verified 2026-08-26), and an
    anonymous-only Condition is ignored.
    """
    statement: dict = {
        "Sid": "AnonymousReadResults",
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": ["s3:GetObject"],
    }
    if private_keys:
        statement["NotResource"] = [f"arn:aws:s3:::{bucket}/{k}" for k in private_keys]
    else:
        statement["Resource"] = [f"arn:aws:s3:::{bucket}/*"]
    return {"Version": "2012-10-17", "Statement": [statement]}


CORS = {
    "CORSRules": [
        {
            "AllowedOrigins": ["*"],
            "AllowedMethods": ["GET", "HEAD"],
            "AllowedHeaders": ["*"],
            "MaxAgeSeconds": 3600,
        }
    ]
}


def main() -> None:
    s3 = boto3.client("s3", endpoint_url=ENDPOINT)
    for bucket in (FIXTURES_BUCKET, RESULTS_BUCKET):
        try:
            s3.create_bucket(Bucket=bucket)
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass

    for i, name in enumerate(PAGES, start=1):
        key = f"mock-vol/{i:04d}.jpg"
        r = httpx.get(f"{HF}/{name}", follow_redirects=True)
        r.raise_for_status()
        s3.put_object(
            Bucket=FIXTURES_BUCKET, Key=key, Body=r.content, ContentType="image/jpeg"
        )
        print("uploaded", key)

    manifest = subprocess.run(
        [sys.executable, "/scripts/make_mock_manifest.py", str(len(PAGES))],
        env={**os.environ, "MOCK_BASE": MOCK_BASE},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    s3.put_object(
        Bucket=FIXTURES_BUCKET,
        Key="mock-vol/manifest.json",
        Body=manifest.encode(),
        ContentType="application/json",
    )

    private = list(PRIVATE_STATUS_KEYS) + ([] if PUBLIC_LOGS else ["status/logs/*"])
    policies = {
        # fixtures: fully public (mock manifest + page images)
        FIXTURES_BUCKET: anonymous_read_policy(FIXTURES_BUCKET, []),
        RESULTS_BUCKET: anonymous_read_policy(RESULTS_BUCKET, private),
    }
    for bucket, policy in policies.items():
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        s3.put_bucket_cors(Bucket=bucket, CORSConfiguration=CORS)
    print("init complete; run logs", "public" if PUBLIC_LOGS else "credentialed")


if __name__ == "__main__":
    main()
