"""The service's whole env contract: every setting is an ``HTRFLOW_API_*``
variable (plus the image's ``HTRFLOW_BATCH_VERSION``), read once at start-up.
There is no setting that turns the token check off."""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

#: The prefix shape charts/htrflow-batch takes for ``api.path`` (_helpers.tpl):
#: no braces (the router would read a parameter the guard reads as text), no
#: dots, no empty segment.
PATH_SHAPE = re.compile(r"(/[A-Za-z0-9_-]+)+")


def checked_prefix(path: str) -> str:
    if path and not PATH_SHAPE.fullmatch(path):
        raise ValueError(
            "path must be empty, or /segments of letters, digits, _ and - "
            "with no / at the end"
        )
    return path


def _scheme_and_host(url: str) -> tuple[str, str]:
    """The scheme and host of an absolute URL with no user name, query,
    fragment, whitespace or bad port; ValueError otherwise. The same shape
    the chart and the CLI take."""
    parts = urlsplit(url)
    _ = parts.port  # raises on a port that is not a number
    if (
        not parts.hostname
        or "@" in parts.netloc
        or "?" in url
        or "#" in url
        or any(c.isspace() for c in url)
    ):
        raise ValueError("must have a host and no user name, query or fragment")
    return parts.scheme, parts.hostname


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
    #: at another address than a person does, so plain http to any host is
    #: taken (the chart allows it only inside the namespace). Empty reads
    #: ``jwks_uri`` from ``<issuer>/.well-known/openid-configuration``.
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
        """Kept exactly as given: it must equal the tokens' ``iss``."""
        rule = (
            "must be an https:// URL (http only for a loopback host) with no "
            "user name, query or fragment"
        )
        try:
            scheme, host = _scheme_and_host(v)
        except ValueError:
            raise ValueError(rule) from None
        if scheme == "https" or (scheme == "http" and is_loopback(host)):
            return v
        raise ValueError(rule)

    @field_validator("jwks_url")
    @classmethod
    def _jwks(cls, v: str) -> str:
        rule = (
            "must be an http:// or https:// URL with a host and no user name, "
            "query or fragment"
        )
        if not v:
            return v
        try:
            scheme, _ = _scheme_and_host(v)
        except ValueError:
            raise ValueError(rule) from None
        if scheme not in ("http", "https"):
            raise ValueError(rule)
        return v

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
        return checked_prefix(v)

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
