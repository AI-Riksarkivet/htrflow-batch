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


class PipelineSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    image: str
    steps_yaml: str
    steps_sha256: str
