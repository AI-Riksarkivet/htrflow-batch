"""S3 result store: deterministic keys, explicit content types (docs: wrapper)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from .config import Config

#: Per-page outputs, in upload order. ALTO is what the viewer manifest and
#: every reader key on, so it lands LAST: a crash between the two PUTs can
#: leave a PAGE without its ALTO (harmless, reprocessed), never the reverse.
PAGE_FORMATS = ("page", "alto")


class ResultStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bucket = cfg.s3_bucket
        self.prefix = cfg.volume_prefix
        # W6: default boto timeouts (60 s connect/read, legacy retries) let an
        # S3 outage pin every PUT for minutes and the run for hours. Bounded
        # here; stream.consume aborts after N consecutive upload failures.
        self.client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint or None,
            config=BotoConfig(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        # Run-log uploads are best-effort and periodic: a dead S3 must not
        # pin a shipping thread (or the final upload at exit) for the default
        # minutes of connect/read timeouts times legacy retries.
        self._log_client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint or None,
            config=BotoConfig(
                connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}
            ),
        )

    def _key(self, rel: str) -> str:
        return f"{self.prefix}/{rel}"

    def _list_stems(self, fmt: str) -> set[str]:
        names: set[str] = set()
        paginator = self.client.get_paginator("list_objects_v2")
        prefix = self._key(f"{fmt}/")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                stem = obj["Key"].rsplit("/", 1)[-1]
                if stem.endswith(".xml"):
                    names.add(stem[:-4])
        return names

    def done_pages(self) -> set[str]:
        """Pages with EVERY format present (W2): a page whose PAGE XML never
        landed is not done, whatever its ALTO says."""
        done: set[str] | None = None
        for fmt in PAGE_FORMATS:
            stems = self._list_stems(fmt)
            done = stems if done is None else done & stems
        return done or set()

    # fresh listing after the run — the D8 verify gate reads this
    uploaded_pages = done_pages

    def upload_page(self, name: str, files: dict[str, Path]) -> None:
        missing = [fmt for fmt in PAGE_FORMATS if fmt not in files]
        if missing:
            raise ValueError(f"page {name}: missing {', '.join(missing)} output")
        # W3: parse before the first PUT. An unparseable file that reached S3
        # would fail publish once and then count as done on the retry.
        bodies: dict[str, bytes] = {}
        for fmt in PAGE_FORMATS:
            data = files[fmt].read_bytes()
            try:
                ET.fromstring(data)
            except ET.ParseError as e:
                raise ValueError(
                    f"page {name}: {fmt} XML is not well-formed: {e}"
                ) from e
            bodies[fmt] = data
        for fmt in PAGE_FORMATS:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(f"{fmt}/{name}.xml"),
                Body=bodies[fmt],
                ContentType="application/xml",
            )

    def put_json(self, rel_key: str, obj: dict) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(rel_key),
            Body=json.dumps(obj, ensure_ascii=False).encode(),
            ContentType="application/json",
        )

    def put_json_at(self, key: str, obj: dict) -> None:
        """Write JSON at a bucket key outside the per-volume prefix, honoring
        S3_PREFIX like everything else — used for the IMAGES synthetic
        manifest under ``sources/`` (docs: wrapper, IMAGES)."""
        full_key = f"{self.cfg.s3_prefix}/{key}" if self.cfg.s3_prefix else key
        self.client.put_object(
            Bucket=self.bucket,
            Key=full_key,
            Body=json.dumps(obj, ensure_ascii=False).encode(),
            ContentType="application/json",
        )

    def put_bytes(self, rel_key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(rel_key),
            Body=data,
            ContentType=content_type,
        )

    def put_text(self, rel_key: str, text: str, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(rel_key),
            Body=text.encode(),
            ContentType=content_type,
        )

    def run_log_key(self) -> str:
        """Bucket-root key the read API/frontend read the run log from
        (``status/logs/<pipeline>/<volume>.txt``) — deliberately not under
        ``volume_prefix``: it is a shared namespace, not per-volume."""
        return f"status/logs/{self.cfg.pipeline_id}/{self.cfg.volume_ref}.txt"

    def put_run_log(self, text: str) -> None:
        self._log_client.put_object(
            Bucket=self.bucket,
            Key=self.run_log_key(),
            Body=text.encode("utf-8", errors="replace"),
            ContentType="text/plain; charset=utf-8",
        )

    def get_bytes(self, rel_key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=self._key(rel_key))
        return obj["Body"].read()

    def get_json_or_none(self, rel_key: str) -> dict | None:
        """A JSON object under the volume prefix, or None when the key is
        missing or not a JSON object (a store/network error still raises)."""
        try:
            data = self.get_bytes(rel_key)
        except self.client.exceptions.NoSuchKey:
            return None
        try:
            obj = json.loads(data)
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None
