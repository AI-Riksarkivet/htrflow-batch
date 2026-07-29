"""S3 adapter. Key helpers are pure; Bucket is a thin boto3 shell that the
tick swaps for a fake in tests (docs: how-it-works/campaigns)."""

from __future__ import annotations

import json
from typing import Any


def manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"{pipeline_id}/{volume_id}/manifest.json"


def synthetic_manifest_key(pipeline_id: str, volume_id: str) -> str:
    return f"sources/{pipeline_id}/{volume_id}/manifest.json"


def failure_log_key(pipeline_id: str, volume_id: str) -> str:
    return f"status/failures/{pipeline_id}/{volume_id}.txt"


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

    def put_text(self, key: str, text: str) -> None:
        self.c.put_object(
            Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="text/plain"
        )

    def done_volumes(self, pipeline_id: str) -> set[str]:
        """Volume ids under ``<pipeline>/`` that carry a written manifest.

        A manifest is the wrapper's completion marker: a volume with ALTO pages
        but no manifest is a partial run, not a done one.
        """
        done: set[str] = set()
        paginator = self.c.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self.bucket, Prefix=f"{pipeline_id}/", Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []):
                vid = cp["Prefix"].rstrip("/").split("/", 1)[1]
                if self.read_json(manifest_key(pipeline_id, vid)) is not None:
                    done.add(vid)
        return done

    def count_pages(self, pipeline_id: str, volume_id: str) -> int:
        n = 0
        paginator = self.c.get_paginator("list_objects_v2")
        prefix = f"{pipeline_id}/{volume_id}/alto/"
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            n += len(page.get("Contents", []))
        return n
