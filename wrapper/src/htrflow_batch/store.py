"""S3 result store: deterministic keys, explicit content types (DESIGN.md §5.4)."""
from __future__ import annotations

import json
from pathlib import Path

import boto3

from .config import Config


class ResultStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bucket = cfg.s3_bucket
        self.prefix = cfg.volume_prefix
        self.client = boto3.client("s3", endpoint_url=cfg.s3_endpoint or None)

    def _key(self, rel: str) -> str:
        return f"{self.prefix}/{rel}"

    def done_pages(self) -> set[str]:
        names: set[str] = set()
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket,
                                       Prefix=self._key("alto/")):
            for obj in page.get("Contents", []):
                stem = obj["Key"].rsplit("/", 1)[-1]
                if stem.endswith(".xml"):
                    names.add(stem[:-4])
        return names

    # fresh listing after the run — the D8 verify gate reads this
    uploaded_pages = done_pages

    def upload_page(self, name: str, files: dict[str, Path]) -> None:
        for fmt, path in files.items():
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(f"{fmt}/{name}.xml"),
                Body=path.read_bytes(),
                ContentType="application/xml",
            )

    def put_json(self, rel_key: str, obj: dict) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(rel_key),
            Body=json.dumps(obj, ensure_ascii=False).encode(),
            ContentType="application/json",
        )

    def put_text(self, rel_key: str, text: str, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(rel_key),
            Body=text.encode(), ContentType=content_type,
        )

    def get_bytes(self, rel_key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=self._key(rel_key))
        return obj["Body"].read()
