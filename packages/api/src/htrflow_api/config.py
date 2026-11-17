"""The service's whole env contract: every setting is an ``HTRFLOW_API_*``
variable (plus the image's ``HTRFLOW_BATCH_VERSION``), read once at start-up.
There is no setting that turns the token check off."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


def is_loopback(host: str) -> bool:
    """Whether ``host`` is ``localhost``, in 127.0.0.0/8 or ``::1``."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ApiConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    #: The OpenID provider's issuer URL; must be https, loopback excepted.
    issuer: str = Field(alias="HTRFLOW_API_ISSUER")
    #: Where THIS POD reads the provider's keys: a pod may reach the provider
    #: at another address than a person does. Empty reads ``jwks_uri`` from
    #: ``<issuer>/.well-known/openid-configuration``.
    jwks_url: str = Field("", alias="HTRFLOW_API_JWKS_URL")
    audience: str = Field(alias="HTRFLOW_API_AUDIENCE")
    client_id: str = Field(alias="HTRFLOW_API_CLIENT_ID")
    group: str = Field(alias="HTRFLOW_API_GROUP")
    user_claim: str = Field("preferred_username", alias="HTRFLOW_API_USER_CLAIM")
    groups_claim: str = Field("groups", alias="HTRFLOW_API_GROUPS_CLAIM")
    #: The prefix the routes are served under: "" or e.g. "/control".
    path: str = Field("", alias="HTRFLOW_API_PATH")
    namespace: str = Field(alias="HTRFLOW_API_NAMESPACE")
    queue: str = Field(alias="HTRFLOW_API_QUEUE")
    #: The tag the image was published under; "dev" outside an image.
    batch_version: str = Field("dev", alias="HTRFLOW_BATCH_VERSION")

    @field_validator("issuer")
    @classmethod
    def _https_only(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme == "https" and parts.hostname:
            return v
        if parts.scheme == "http" and is_loopback(parts.hostname or ""):
            return v
        raise ValueError("must be an https:// URL (http only for a loopback host)")

    @field_validator(
        "audience", "client_id", "group", "user_claim", "groups_claim",
        "namespace", "queue",
    )  # fmt: skip
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("path")
    @classmethod
    def _prefix(cls, v: str) -> str:
        if v and (not v.startswith("/") or v.endswith("/")):
            raise ValueError("must be empty, or start with / and not end with /")
        return v

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ApiConfig:
        env = os.environ if env is None else env
        own = {a for f in cls.model_fields.values() if (a := f.alias)}
        try:
            return cls(**{k: v for k, v in env.items() if k in own})
        except ValidationError as e:
            raise ValueError(
                "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
            ) from None
