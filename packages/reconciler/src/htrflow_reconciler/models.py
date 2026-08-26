"""Domain types for the campaign reconciler (docs: how-it-works/campaigns)."""

from pydantic import BaseModel, ConfigDict, Field


class Volume(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    manifest_url: str | None = None
    images: tuple[str, ...] = ()


class Campaign(BaseModel):
    name: str
    pipeline_id: str
    volumes: list[Volume] = Field(default_factory=list)
    error: str | None = None
    #: Volume ids the file names, filled best-effort even when parsing fails
    #: (audit R14): a malformed sibling entry must not turn this campaign's
    #: published results into "orphans".
    declared_ids: tuple[str, ...] = ()


class PipelineSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    image: str
    steps_yaml: str
    #: sha256 of the parsed steps in canonical JSON (audit R10).
    steps_sha256: str
    #: sha256 of ``steps_yaml`` itself — what the wrapper publishes as
    #: ``pipeline_sha256`` in manifest.json; accepted by the drift guard so
    #: earlier results keep matching.
    legacy_sha256: str = ""
