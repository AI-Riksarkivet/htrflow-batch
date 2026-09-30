"""Compose init: create buckets, upload htr_demo fixture pages and publish
the mock IIIF manifest. The fixtures bucket plays an external IIIF server,
so it gets an anonymous-read policy and CORS; the results bucket gets
neither -- results are read through the results proxy, logged in -- and
loses any an earlier stack left on the same volume. The login user and the
proxy's session key are the login-init service's
(scripts/compose_login_init.sh).

Env: S3_ENDPOINT (default http://rustfs:9000), S3_BUCKET (results bucket,
default htr-results), FIXTURES_BUCKET (default htr-fixtures),
IMAGE_CACHE_BUCKET (private image cache bucket, default empty — none
created), MOCK_BASE (the address the mock manifest names its pages at;
compose sets the localhost form that the wrapper and the browser both
resolve, see .docker/docker-compose.yml), AWS creds via standard vars."""

import json
import os
import subprocess
import sys

import boto3
import httpx

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://rustfs:9000")
RESULTS_BUCKET = os.environ.get("S3_BUCKET", "htr-results")
FIXTURES_BUCKET = os.environ.get("FIXTURES_BUCKET", "htr-fixtures")
IMAGE_CACHE_BUCKET = os.environ.get("IMAGE_CACHE_BUCKET", "")
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


def anonymous_read_policy(bucket: str) -> dict:
    """Anonymous GetObject on every key: the stand-in IIIF server."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AnonymousReadFixtures",
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }


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
    buckets = (FIXTURES_BUCKET, RESULTS_BUCKET)
    if IMAGE_CACHE_BUCKET:
        buckets += (IMAGE_CACHE_BUCKET,)  # private: no policy, no CORS
    for bucket in buckets:
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

    # fixtures: fully public (mock manifest + page images), read cross-origin
    s3.put_bucket_policy(
        Bucket=FIXTURES_BUCKET, Policy=json.dumps(anonymous_read_policy(FIXTURES_BUCKET))
    )
    s3.put_bucket_cors(Bucket=FIXTURES_BUCKET, CORSConfiguration=CORS)
    # results: private. Both deletes succeed on a bucket that has neither.
    s3.delete_bucket_policy(Bucket=RESULTS_BUCKET)
    s3.delete_bucket_cors(Bucket=RESULTS_BUCKET)
    print("init complete; results are private")

if __name__ == "__main__":
    main()
