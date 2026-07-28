"""Compose init: create buckets, upload htr_demo fixture pages, publish the
mock IIIF manifest, and open anonymous read + CORS on htr-results.

Env: S3_ENDPOINT (default http://rustfs:9000), MOCK_BASE (default
http://rustfs:9000/htr-fixtures/mock-vol — the container-internal form used
by compose; see .docker/docker-compose.yml comment for browser-fidelity
trade-off), AWS creds via standard vars."""
import json
import os
import subprocess
import sys

import boto3
import httpx

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://rustfs:9000")
MOCK_BASE = os.environ.get("MOCK_BASE", "http://rustfs:9000/htr-fixtures/mock-vol")
HF = "https://huggingface.co/spaces/Riksarkivet/htr_demo/resolve/main/.gradio_cache/examples"
# Known-good htr_demo example filenames (from the retired PoC job manifests).
PAGES = ["A0062408_00006.jpg", "A0070302_00201.jpg", "A0073477_00025.jpg", "R0003364_00005.jpg"]

s3 = boto3.client("s3", endpoint_url=ENDPOINT)
for bucket in ("htr-fixtures", "htr-results"):
    try:
        s3.create_bucket(Bucket=bucket)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass

for i, name in enumerate(PAGES, start=1):
    key = f"mock-vol/{i:04d}.jpg"
    r = httpx.get(f"{HF}/{name}", follow_redirects=True)
    r.raise_for_status()
    s3.put_object(Bucket="htr-fixtures", Key=key, Body=r.content, ContentType="image/jpeg")
    print("uploaded", key)

manifest = subprocess.run(
    [sys.executable, "/scripts/make_mock_manifest.py", str(len(PAGES))],
    env={**os.environ, "MOCK_BASE": MOCK_BASE}, capture_output=True, text=True, check=True,
).stdout
s3.put_object(Bucket="htr-fixtures", Key="mock-vol/manifest.json",
              Body=manifest.encode(), ContentType="application/json")

for bucket in ("htr-fixtures", "htr-results"):
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"AWS": ["*"]},
                       "Action": ["s3:GetObject"], "Resource": [f"arn:aws:s3:::{bucket}/*"]}]}))
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"],
        "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600}]})
print("init complete")
