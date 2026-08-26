"""S3 adapter. Key helpers are pure; Bucket is a thin boto3 shell that the
tick swaps for a fake in tests (docs: how-it-works/campaigns).

The key layout assumes the wrapper runs with an EMPTY ``S3_PREFIX``: results
land at ``<pipeline>/<volume>/…``, which is why ``jobspec.build_job`` pins
``S3_PREFIX=""`` explicitly rather than letting the S3 secret supply one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from typing import Any

from botocore.exceptions import ClientError

_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


def manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"{pipeline_id}/{volume_id}/manifest.json"


def synthetic_manifest_key(
    pipeline_id: str, volume_id: str, images: Sequence[str]
) -> str:
    """The key carries a short hash of the image list (audit R9): the
    manifest is write-once per key, so an ``images:`` edit in git yields a
    new manifest instead of being ignored forever."""
    digest = hashlib.sha256("\n".join(images).encode()).hexdigest()[:8]
    return f"sources/{pipeline_id}/{volume_id}/{digest}/manifest.json"


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


def volumes_key() -> str:
    """Per-volume probe cache (page count, run-log presence, synthetic
    manifest key) keyed by the manifest.json mtime — what makes the steady
    state O(1) S3 calls per done volume (audit X1)."""
    return "status/volumes.json"


#: HEADs per done_volumes probe run concurrently; boto3 clients are
#: thread-safe, and the RustFS/S3 endpoint handles this fan-out easily.
_HEAD_WORKERS = 16


class Bucket:
    """Thin wrapper over a boto3 S3 client bound to one bucket. ``calls``
    counts round trips for the tick summary (audit O5)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.c = client
        self.bucket = bucket
        self.calls = 0

    def read_json(self, key: str) -> dict | None:
        self.calls += 1
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return json.loads(body)
        except self.c.exceptions.NoSuchKey:
            return None

    def write_json(self, key: str, obj: dict) -> None:
        self.calls += 1
        self.c.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(obj).encode(),
            ContentType="application/json",
        )

    def read_text(self, key: str) -> str | None:
        self.calls += 1
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.c.exceptions.NoSuchKey:
            return None
        return body.decode("utf-8", errors="replace")

    def delete(self, key: str) -> None:
        self.calls += 1
        self.c.delete_object(Bucket=self.bucket, Key=key)

    def put_text(self, key: str, text: str) -> None:
        self.calls += 1
        self.c.put_object(
            Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="text/plain"
        )

    def exists(self, key: str) -> bool:
        """HEAD probe — existence without downloading the body."""
        self.calls += 1
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
        vids: list[str] = []
        paginator = self.c.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self.bucket, Prefix=f"{pipeline_id}/", Delimiter="/"
        ):
            self.calls += 1
            for cp in page.get("CommonPrefixes", []):
                vids.append(cp["Prefix"].rstrip("/").split("/", 1)[1])
        self.calls += len(vids)
        with ThreadPoolExecutor(max_workers=_HEAD_WORKERS) as pool:
            mtimes = pool.map(lambda v: self._manifest_mtime(pipeline_id, v), vids)
        return {vid: mtime for vid, mtime in zip(vids, mtimes) if mtime is not None}

    def _manifest_mtime(self, pipeline_id: str, vid: str) -> str | None:
        try:
            head = self.c.head_object(
                Bucket=self.bucket, Key=manifest_key(pipeline_id, vid)
            )
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in _MISSING_CODES:
                return None
            raise
        return (
            head["LastModified"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    def count_pages(self, pipeline_id: str, volume_id: str) -> int:
        n = 0
        paginator = self.c.get_paginator("list_objects_v2")
        prefix = f"{pipeline_id}/{volume_id}/alto/"
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            self.calls += 1
            n += len(page.get("Contents", []))
        return n
