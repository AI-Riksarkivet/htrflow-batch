"""S3 adapter. Key helpers are pure; Bucket is a thin boto3 shell that the
tick swaps for a fake in tests (docs: how-it-works/campaigns).

The key layout assumes the wrapper runs with an EMPTY ``S3_PREFIX``: results
land at ``<pipeline>/<volume>/…``, which is why ``jobspec.build_job`` pins
``S3_PREFIX=""`` explicitly rather than letting the S3 secret supply one.
"""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from botocore.exceptions import ClientError

_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


def manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"{pipeline_id}/{volume_id}/manifest.json"


def synthetic_manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"sources/{pipeline_id}/{volume_id}/manifest.json"


def failure_log_key(pipeline_id: str, volume_id: str) -> str:
    return f"status/failures/{pipeline_id}/{volume_id}.txt"


def run_log_key(pipeline_id: str, volume_id: str) -> str:
    return f"status/logs/{pipeline_id}/{volume_id}.txt"


def warmup_log_key(pipeline_id: str) -> str:
    return f"status/warmup/{pipeline_id}.log"


def status_key() -> str:
    return "status/status.json"


def attempts_key() -> str:
    return "status/attempts.json"


def validation_key() -> str:
    return "status/validation.json"


class Bucket:
    """Thin wrapper over a boto3 S3 client bound to one bucket."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.c = client
        self.bucket = bucket

    def read_json(self, key: str) -> dict | None:
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return json.loads(body)
        except self.c.exceptions.NoSuchKey:
            return None

    def write_json(self, key: str, obj: dict) -> None:
        self.c.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(obj).encode(),
            ContentType="application/json",
        )

    def read_text(self, key: str) -> str | None:
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.c.exceptions.NoSuchKey:
            return None
        return body.decode("utf-8", errors="replace")

    def delete(self, key: str) -> None:
        self.c.delete_object(Bucket=self.bucket, Key=key)

    def put_text(self, key: str, text: str) -> None:
        self.c.put_object(
            Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="text/plain"
        )

    def exists(self, key: str) -> bool:
        """HEAD probe — existence without downloading the body."""
        try:
            self.c.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in _MISSING_CODES:
                return False
            raise

    def done_volumes(self, pipeline_id: str) -> dict[str, str]:
        """Volume id -> manifest.json LastModified (ISO-8601 UTC) under
        ``<pipeline>/``. A manifest is the wrapper's completion marker; its
        mtime is when the volume finished publishing. Still HEAD-only.
        """
        done: dict[str, str] = {}
        paginator = self.c.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self.bucket, Prefix=f"{pipeline_id}/", Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []):
                vid = cp["Prefix"].rstrip("/").split("/", 1)[1]
                try:
                    head = self.c.head_object(
                        Bucket=self.bucket, Key=manifest_key(pipeline_id, vid)
                    )
                except ClientError as e:
                    code = str(e.response.get("Error", {}).get("Code", ""))
                    if code in _MISSING_CODES:
                        continue
                    raise
                done[vid] = (
                    head["LastModified"]
                    .astimezone(timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                )
        return done

    def count_pages(self, pipeline_id: str, volume_id: str) -> int:
        n = 0
        paginator = self.c.get_paginator("list_objects_v2")
        prefix = f"{pipeline_id}/{volume_id}/alto/"
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            n += len(page.get("Contents", []))
        return n
