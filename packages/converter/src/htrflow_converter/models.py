"""Domain types for campaign/pipeline YAML, with validation (spec §3).

What is NOT here (B63 Task 22): the image allow-list and the model-revision
requirement. Both are cluster policy, and a rule this package applies is a
rule that only ever sees what this package rendered -- so they are Kyverno
ClusterPolicies the htrflow-batch chart ships, enforced by the API server on
everything the namespace admits and re-run against ``rendered/`` by the
Kyverno CLI in a campaigns repo's CI. The digest-pin *shape* check on
``image`` stays: the renderer builds stable ids out of that digest.

The one config-dependent rule left (``source_template``) reaches the models
through ``ValidationInfo.context`` -- see ``parse.py``, which builds that
context once from ``ConverterConfig``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from datetime import date, datetime
from string import Formatter
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

_MiB = 1024 * 1024
#: `activeDeadlineSeconds` and `ttlSecondsAfterFinished` are int32 in the
#: Kubernetes API, so a larger number is a 422 halfway through an apply.
_INT32_MAX = 2**31 - 1

_VOLUME_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?\Z")
#: A pipeline id: a DNS-1123 label's length with a subdomain's dots, which
#: its ConfigMap and warm-up Job names may carry (``Pipeline._check_id`` for
#: what the warm-up takes off the length). A campaign's name is narrower.
_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?\Z")
_IMAGE_RE = re.compile(r"[a-z0-9./:-]+@sha256:[0-9a-f]{64}\Z")
#: A Kubernetes label VALUE, which is what ``_VOLUME_ID_RE`` already spells
#: out (a volume id becomes one). Under its own name for the settings that
#: are rendered into a label rather than into a name.
_LABEL_VALUE_RE = _VOLUME_ID_RE
#: A Kubernetes object name in its wider form, DNS-1123 *subdomain* -- what
#: a Secret name has to be (``_NAME_RE`` above is the narrower label, which
#: is all a Job name may be). Spelled out rather than simplified to
#: ``[a-z0-9.-]*``: that shorthand accepts ``a..b`` and ``a.-b``, which the
#: API server refuses, so the mistake would be caught at apply time instead
#: of in ``validate``. The 253-character cap is checked beside it.
_SUBDOMAIN_RE = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*\Z"
)
#: A DNS-1123 *label*: lower-case, no dots, at most 63 characters. What a
#: namespace has to be -- the wider subdomain rule below accepts
#: ``htr.batch.example``, which the API server refuses.
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?\Z")
_NOT_A_NAMESPACE = (
    "is not a Kubernetes namespace (got {shown}) — use lower-case letters, "
    'digits and "-", starting and ending with a letter or digit, at most 63 '
    "characters and no dots"
)

#: The settings that name an object the API server has to accept, and the
#: one that names a RuntimeClass. None of them was checked: a name with a
#: capital in it passed ``validate`` and was refused at apply time -- after
#: the render was committed and the campaigns were live.
_NOT_AN_OBJECT_NAME = (
    "is not a Kubernetes object name (got {shown}) — use lower-case letters, "
    'digits, "-" and ".", starting and ending with a letter or digit, at '
    "most 253 characters"
)
#: ``node_selector`` is copied into the pod spec, where both halves of every
#: entry have to be a label: a key is an optional DNS-subdomain prefix and a
#: name, a value is the label alphabet. A pod the API server will not take is
#: a campaign that never starts.
_NOT_A_NODE_LABEL = (
    "has a {what} that is not a Kubernetes node label (got {shown}) — a key "
    'is a name, optionally after a "<dns-prefix>/"; both halves and the '
    'value are letters, digits, ".", "_" and "-", at most 63 characters'
)

#: The converter names the parts of a campaign it splits ``<name>-part1``,
#: ``-part2``, ... (``render.campaign_names``). A campaign file with that
#: ending would share its rendered file, Job and ConfigMap with a part of the
#: campaign it is named after -- and be reported as "append-only" instead,
#: which sends its author looking for a change they never made.
_PART_RE = re.compile(r"-part\d+\Z")
#: ``campaign-<name>`` + this is the status ConfigMap the read API writes
#: beside a campaign's own record (packages/web ``projection``; ``render``
#: and ``cluster`` import it from here). A campaign named ``x-status`` would
#: render a record ConfigMap called ``campaign-x-status`` -- the very name
#: the API writes the status of campaign ``x`` to, and which a prune then
#: keeps or deletes on the wrong campaign's behalf.
STATUS_SUFFIX = "-status"

#: `name`/`id` are taken from the file name (parse.py overrides whatever the
#: YAML says), so the only way to fix either is to rename the file.
_RENAME_THE_FILE = (
    "cannot be a Kubernetes object name — rename the file to lower-case "
    'letters, digits, "." and "-" only, at most 63 characters'
)
#: A DNS-1123 label's length: the cap on a label value, a pod's hostname and
#: so -- through both -- on a Job's name (``render.campaign_names``).
DNS_LABEL = 63
#: What a pipeline's warm-up Job is called. ``cluster`` reads it too: a Job
#: the API server refuses for an immutable field is told a different way
#: out depending on whether it is a warm-up (deleted and created again) or a
#: campaign (left exactly where it is).
WARMUP_PREFIX = "htr-warmup-"
#: A campaign's Job is an Indexed Job, and the API server holds the hostname
#: of its last pod, ``<name>-<completions - 1>``, to a DNS-1123 *label*: no
#: dots. A dotted campaign name passed here and was refused with a 422 at
#: apply time (3087); the length half of the rule is ``render``'s, since it
#: depends on how the campaign splits.
_RENAME_THE_CAMPAIGN = (
    "cannot name a campaign's Job — its pods are called <name>-<index> and "
    "each of those is a hostname, so rename the file to lower-case letters, "
    'digits and "-" only (no dots), at most 63 characters'
)


def shown(value: object) -> str:
    """A value echoed back the way the author wrote it. A number in quotes is
    the mistake behind half of these messages -- YAML then hands us text -- so
    say so, rather than leave them to spot ``5`` against ``"5"``. Only for a
    value that IS a number, though: telling the author of ``suspend: maybe``
    that quotes made it text describes a file they did not write."""
    if isinstance(value, (list, tuple)):
        return "a list"  # never str() -- that is a Python repr, with its quotes
    if isinstance(value, dict):
        return "a block of settings"
    if not isinstance(value, str):
        return str(value).lower()
    digits = value.strip().lstrip("-+").isdigit()
    return f'"{value}"' + (" — quotes make it text" if digits else "")


def _positive_int(v: object) -> bool:
    # `bool` is an `int` in Python: `window: true` is a typo, not a window of 1.
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _http_url(value: str) -> bool:
    try:
        u = urlsplit(value)
    except ValueError:  # a bracketed host it cannot read: `_unopenable` says so
        return value.lower().startswith(("http://", "https://"))
    return u.scheme in ("http", "https") and bool(u.netloc)


#: Source URLs reach a browser: the viewer and the status page build every
#: link with WHATWG ``new URL``, which throws on what ``urlsplit`` lets by --
#: a port past 65535, a ``%`` or ``<`` in the host (audit 0923 F-7). This is
#: a strict subset of what a browser accepts, the one the read API holds its
#: own links to (packages/web ``projection.browser_http_url``), so a URL
#: that passes here opens there.
_BROWSER_BREAKS = re.compile(r"[\x00-\x1f\x7f\\]")
_HOST_LABEL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_NUMERIC_LABEL_RE = re.compile(r"(?:[0-9]+|0[xX][0-9A-Fa-f]*)\Z")
_BAD_HOST = "its host is not a host name or an IP address"


def _letters(label: str) -> bool:
    """ASCII letters, digits and ``-``, and non-ASCII letters written left to
    right: a right-to-left letter brings in IDNA's bidi rule, which a
    browser enforces and this does not try to."""
    return all(
        (ch.isascii() and (ch.isalnum() or ch == "-"))
        or (ch.isalpha() and unicodedata.bidirectional(ch) == "L")
        for ch in label
    )


def _host_label(label: str) -> bool:
    """One label of a host name a browser takes as written. An ``xn--``
    label has to be the one encoding a browser would itself have made of
    what it decodes to -- mapped (case-folded, NFKC) and non-ASCII -- so
    ``xn--bung-fna`` ("Übung") is refused; a raw non-ASCII label is letters
    and digits, which the browser encodes."""
    if not label.isascii():
        return not label.lower().startswith("xn--") and _letters(label)
    if not _HOST_LABEL_RE.match(label):
        return False
    if not label.lower().startswith("xn--"):
        return True
    try:
        decoded = label[4:].encode("ascii").decode("punycode")
        encoded = decoded.encode("punycode").decode("ascii")
    except (UnicodeError, ValueError):
        return False
    return (
        not decoded.isascii()
        and _letters(decoded)
        and decoded == unicodedata.normalize("NFKC", decoded.casefold())
        and encoded == label[4:].lower()
    )


def _browser_host(netloc: str) -> bool:
    """The host in ``netloc`` is a bracketed IPv6 literal with no zone, a
    dotted IPv4 address when its last label is a number, or host-name
    labels; one trailing dot is the root, as a browser reads it."""
    host = netloc.rpartition("@")[2]
    if host.startswith("["):
        literal, _, port = host[1:].partition("]")
        try:
            ipaddress.IPv6Address(literal)
        except ValueError:
            return False
        return "%" not in literal and (port == "" or port.startswith(":"))
    labels = host.partition(":")[0].removesuffix(".").split(".")
    if "" in labels:
        return False
    if _NUMERIC_LABEL_RE.match(labels[-1]):
        try:
            return bool(ipaddress.IPv4Address(".".join(labels)))
        except ValueError:
            return False
    return all(_host_label(label) for label in labels)


def _unopenable(value: str) -> str | None:
    """Why a browser would refuse this http(s) URL, or ``None``."""
    if _BROWSER_BREAKS.search(value):
        return "it has a backslash or a control character in it"
    try:
        u = urlsplit(value)
    except ValueError:
        return _BAD_HOST
    try:
        u.port  # the read is the check: it raises past 65535
    except ValueError:
        return "its port is not a number from 0 to 65535"
    if not _browser_host(u.netloc):
        return _BAD_HOST
    return None


#: A ``volumes.txt`` line separates an ``images:`` volume's URLs with a space
#: (``Volume.source_line``), which only works because no URL may contain one.
#: Comma cannot do that job: a IIIF Image API size segment is a legal comma in
#: the path (``/full/2500,/0/default.jpg``), and splitting on it tore such a
#: URL in half in production (2026-09-14). Whitespace has no such excuse --
#: RFC 3986 has no place for a literal space, tab, newline or CR -- so the
#: rule is enforced here, where the author can still fix it.
_WHITESPACE_RE = re.compile(r"\s")

_PERCENT_ENCODE = "percent-encode a space as %20"

#: Bytes one ``images:`` volume's line of ``volumes.txt`` may take. The
#: campaign Job's shell exports that line's URLs as a single ``IMAGES``
#: environment entry, and Linux caps one entry at 128 KiB
#: (``MAX_ARG_STRLEN``): past that the pod dies with "Argument list too
#: long" before the wrapper starts, and nothing in the campaign says which
#: volume did it. The margin below 128 KiB covers the rest of the entry and
#: leaves the line format somewhere to grow; the API server's own 1 MiB
#: ConfigMap limit is further off again (``render.MAX_BYTES_PER_JOB``).
MAX_IMAGES_BYTES = 100 * 1024
_TOO_MANY_IMAGES = (
    "lists {count} images, whose one line of volumes.txt is {kib} KiB — the "
    "Job exports them as one environment entry, which stops at 128 KiB, so "
    "split the volume in two or give it a IIIF manifest instead"
)

#: Userinfo, stripped out of any URL a problem echoes back: a campaign file
#: should carry no credentials, but a problem line is printed in CI logs and
#: pasted into chat, so one that does must lose them here. The class excludes
#: ``?#`` so a bare ``@`` in a query before the first ``/`` is left alone.
_USERINFO_RE = re.compile(r"(?<=//)[^/@?#]*@")


#: Query parameters whose value signs or authorises the request. A presigned
#: source URL carries its credential there, and a problem line is printed in
#: CI logs and pasted into chat, so the echo drops it the way it already
#: drops userinfo. That is all this can do: the URL itself is stored verbatim
#: in volumes.txt, in the campaign's ConfigMap and in the committed
#: `rendered/` -- a source URL is not a secret (docs: how-it-works/security).
#: Longest name first, so `signature=` is not matched as `sig` and a
#: presigned S3 URL's `X-Amz-Security-Token` is not matched as `token`.
_SIGNED_RE = re.compile(
    r"(?<=[?&])(X-Amz-Security-Token|X-Amz-Signature|X-Amz-Credential"
    r"|signature|token|sig|key)=[^&#]*",
    re.IGNORECASE,
)


def _shown_url(value: str) -> str:
    return _SIGNED_RE.sub(r"\1=***", _USERINFO_RE.sub("***@", value))


#: ``source_template`` is filled in a *before* validator
#: (``Volume._expand``), where a stray placeholder leaves pydantic as a
#: KeyError or an IndexError -- a traceback where a campaign author should
#: get a line about their own file. So the template is checked once, where it
#: is written, and the fill is caught as well.
_BAD_TEMPLATE = (
    "must have {{ref}} in it exactly once and nothing else in braces (got "
    "{shown}) — {{ref}} is where a campaign's bare volume id goes"
)
_UNFILLABLE_TEMPLATE = (
    "cannot be turned into a manifest URL — converter.yaml's source_template "
    "is {shown}, and only {{ref}} can be filled in; give the volume a "
    "manifest: of its own, or fix the template"
)


#: The error type of a bare reference code with no ``source_template`` to
#: expand it. ``parse`` says it once per campaign, not once per volume: a
#: campaign of ten thousand bare codes is one fix, in converter.yaml.
NO_SOURCE_TEMPLATE = "no_source_template"


def _placeholders(template: str) -> list[str]:
    """The names in braces, left to right. A template that is not a format
    string at all (a brace left open) raises, and is refused with the rest."""
    try:
        return [f for _, f, _, _ in Formatter().parse(template) if f is not None]
    except ValueError:
        return []


def _object_name(value: str) -> bool:
    return bool(value) and len(value) <= 253 and bool(_SUBDOMAIN_RE.match(value))


def _label_key(key: str) -> bool:
    prefix, slash, name = key.rpartition("/")
    if slash and not _object_name(prefix):
        return False
    return bool(_LABEL_VALUE_RE.match(name))


def split_image_urls(value: str) -> list[str]:
    """The URLs inside an ``images:`` source, split on whitespace.

    THE definition of that split: the wrapper's ``Config.image_urls`` is the
    same function over ``IMAGES`` (the two packages share no code -- the GPU
    image must not carry the converter's Kubernetes client -- so
    ``test_models.py`` pins them to each other case by case).

    TRANSITION: a value with no whitespace whose every comma-split piece is
    itself an http(s) URL is a line rendered before 2026-09-14, when commas
    joined them. A single URL carrying a comma can never look like that (the
    piece after the comma has no scheme), which is what makes the old format
    safe to keep reading. Known limit: one URL whose query carries another
    http(s) URL after a comma (``?src=https://a,https://b``) is split in two.
    Delete this branch, in both packages, once every campaigns repo has been
    re-rendered.
    """
    urls = value.split()
    if len(urls) == 1 and "," in urls[0]:
        parts = urls[0].split(",")
        if all(p.startswith(("http://", "https://")) for p in parts):
            return parts
    return urls


def parse_source_line(line: str) -> tuple[str, tuple[str, ...]]:
    """A ``volumes.txt`` line read back as ``(id, sources)`` -- what the line
    MEANS, so that two renders of one campaign can be compared across a
    change of separator (``cli``'s append-only check)."""
    vid, _, source = line.partition("\t")  # the FIRST tab, as the Job's shell
    if source.startswith("images:"):
        return vid, tuple(split_image_urls(source.removeprefix("images:")))
    return vid, (source,)


#: A volume id is text, and only a YAML *string* is text as written: YAML
#: 1.1 reads an unquoted ``0012345`` as octal, ``1:20`` as base 60, ``1.10``
#: as a float and ``yes`` as true. ``str()`` of that result is an id the
#: author never wrote (``5349``, ``80``, ``1.1``, ``True``), under which the
#: volume would be fetched and its results published (audit 0923 C-5).
_NOT_TEXT_ID = (
    "has an id that YAML reads as {kind}, not as text — put it in quotes so "
    'it stays as written: - "R0012345", or id: "R0012345"'
)


def _not_text(value: object) -> str | None:
    """What YAML made of an id that is not a string, in the author's words;
    ``None`` for a string. A list or a mapping is left to pydantic, whose
    sentence for that already fits."""
    if value is None:
        return "nothing at all"
    if isinstance(value, bool):
        return f"true or false ({str(value).lower()})"
    if isinstance(value, (int, float)):
        return f"a number ({value})"
    if isinstance(value, (date, datetime)):
        return f"a date ({value.isoformat()})"
    return None


def _as_recorded(info: ValidationInfo) -> bool:
    """Validating a campaign or pipeline as an earlier render recorded it:
    the rules added since then (an id that is text, a URL a browser opens, a
    reader whose text reaches the export) are waived, and ``parse`` keeps the
    result only if it IS that record (``record.unchanged``, a pipeline's
    recorded recipe)."""
    return bool((info.context or {}).get("as_recorded"))


class Volume(BaseModel):
    #: Unknown keys rejected, as on every other model: a volume's stray
    #: ``pages: 1-10`` read as a page range to its author and was dropped
    #: without a word, so every page ran (audit 0923 C-1).
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    manifest: str | None = None
    images: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any, info: ValidationInfo) -> Any:
        kind = _not_text(data.get("id") if isinstance(data, dict) else data)
        if kind is not None and isinstance(data, dict) and _as_recorded(info):
            # What v0.5.0 made of it: the id this volume was rendered under.
            data = {**data, "id": str(data["id"])}
        elif kind is not None and (not isinstance(data, dict) or "id" in data):
            raise ValueError(_NOT_TEXT_ID.format(kind=kind))
        if isinstance(data, str):
            context = info.context or {}
            template = context.get("source_template") or ""
            if not template:
                # What an earlier render made of this bare code, when it
                # came from a template converter.yaml no longer sets: kept,
                # under ``record.unchanged``, like every other rule.
                kept = context.get("recorded_manifests", {}).get(data)
                if kept is not None and _as_recorded(info):
                    return {"id": data, "manifest": kept}
                raise PydanticCustomError(
                    NO_SOURCE_TEMPLATE,
                    "is a bare reference code with no source_template",
                )
            try:
                return {"id": data, "manifest": template.format(ref=data)}
            except (KeyError, IndexError, ValueError) as e:
                raise ValueError(
                    _UNFILLABLE_TEMPLATE.format(shown=shown(template))
                ) from e
        if not isinstance(data, dict) or "id" not in data:
            raise ValueError(
                'has no id — write the entry as "- R1", or as "- id: R1" '
                "with manifest: or images:"
            )
        return data

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _VOLUME_ID_RE.match(v):
            raise ValueError(
                "has an id with characters that are not allowed — use only "
                'letters, digits, ".", "_" and "-", at most 63 of them'
            )
        return v

    @field_validator("manifest")
    @classmethod
    def _check_manifest(cls, v: str | None, info: ValidationInfo) -> str | None:
        if v is not None and _WHITESPACE_RE.search(v):
            raise ValueError(
                f"has a manifest with whitespace in it "
                f'("{_shown_url(v)}") — {_PERCENT_ENCODE}'
            )
        if v is not None and not _http_url(v):
            raise ValueError(
                f'has a manifest that is not an http(s) URL ("{_shown_url(v)}") '
                "— write the whole URL, starting with https://"
            )
        if v is not None and not _as_recorded(info) and (why := _unopenable(v)):
            raise ValueError(
                f'has a manifest a browser cannot open ("{_shown_url(v)}"): {why}'
            )
        return v

    @field_validator("images")
    @classmethod
    def _check_images(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for n, u in enumerate(v, start=1):
            if _WHITESPACE_RE.search(u):
                raise ValueError(
                    f"has image {n} with whitespace in it "
                    f'("{_shown_url(u)}") — {_PERCENT_ENCODE}'
                )
            if not _http_url(u):
                raise ValueError(
                    f"lists an image that is not an http(s) URL "
                    f'("{_shown_url(u)}") — every entry under images: is a whole URL'
                )
            if not _as_recorded(info) and (why := _unopenable(u)):
                raise ValueError(
                    f'has an image a browser cannot open ("{_shown_url(u)}"): {why}'
                )
        return v

    @model_validator(mode="after")
    def _check_source(self) -> "Volume":
        if (self.manifest is not None) == bool(self.images):
            raise ValueError(
                "needs exactly one source — give it manifest: <IIIF manifest "
                "URL>, or images: <list of image URLs>"
            )
        size = len(self.source_line().encode()) if self.images else 0
        if size > MAX_IMAGES_BYTES:
            raise ValueError(
                _TOO_MANY_IMAGES.format(count=len(self.images), kib=size // 1024)
            )
        return self

    def source_line(self) -> str:
        """One line of a campaign's ``volumes.txt`` ConfigMap: the id, a TAB,
        then the source. An ``images:`` volume's URLs are joined with a single
        space -- never a comma, which is legal inside a URL (_WHITESPACE_RE).
        Both readers split on the FIRST tab only: the Job's shell
        (``manifests/campaign-job.yaml``) and ``web.projection._source_url``."""
        if self.manifest is not None:
            return f"{self.id}\t{self.manifest}"
        return f"{self.id}\timages:{' '.join(self.images)}"


#: ``priority`` is rendered as the ``kueue.x-k8s.io/priority-class`` label
#: (``render._campaign_job``) and has to name a WorkloadPriorityClass the
#: chart ships, so it is held to the DNS-label alphabet those names have
#: (the chart's values schema): a case slip is refused here rather than
#: leaving the campaign Queued for ever (see ``ConverterConfig``).
_NOT_A_PRIORITY = (
    "is not a priority class name (got {shown}) — it becomes the "
    "kueue.x-k8s.io/priority-class label, so use lower-case letters, digits "
    'and "-", starting and ending with a letter or digit, at most 63 '
    "characters"
)


class Campaign(BaseModel):
    #: Unknown keys rejected: ``suspended: true`` or ``priorty:`` was dropped
    #: without a word, and the campaign rendered -- and ran -- at the
    #: defaults, unpaused (audit 0923 C-1).
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    pipeline: str
    volumes: list[Volume] = Field(default_factory=list)
    priority: str = ""
    window: int | None = None
    #: Renders ``spec.suspend``; the apply step enforces it against Kueue.
    suspend: bool = False

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _DNS_LABEL_RE.match(v):
            raise ValueError(_RENAME_THE_CAMPAIGN)
        if v.startswith(WARMUP_PREFIX):
            # Same kind, same namespace: it would BE the warm-up Job, and an
            # apply that replaces a warm-up would delete the campaign.
            raise ValueError(
                f'starts with "{WARMUP_PREFIX}", which is what the converter '
                "calls a pipeline's warm-up Job — rename the file"
            )
        if _PART_RE.search(v):
            raise ValueError(
                'ends in "-part<number>", which is what the converter calls '
                "the parts of a campaign it splits — rename the file"
            )
        if v.endswith(STATUS_SUFFIX):
            raise ValueError(
                'ends in "-status", which is what the read API calls the '
                "ConfigMap it writes beside the record of a campaign — "
                "rename the file"
            )
        return v

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, v: str) -> str:
        if v and not _DNS_LABEL_RE.match(v):
            raise ValueError(_NOT_A_PRIORITY.format(shown=shown(v)))
        return v

    @field_validator("pipeline")
    @classmethod
    def _check_pipeline_ref(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "is empty — name one of the files in pipelines/, without the .yaml"
            )
        return v

    @model_validator(mode="after")
    def _check_volumes(self) -> "Campaign":
        if not self.volumes:
            # `completions: 0` is a Job Kubernetes reports as Succeeded the
            # moment it is created: a campaign that is over before it starts,
            # green, with no volume ever fetched and nothing to say why.
            raise ValueError(
                "this campaign lists no volumes — add at least one entry "
                "under volumes:, or remove the file (removing it is how a "
                "finished campaign is retired)"
            )
        return self

    @field_validator("window", mode="before")
    @classmethod
    def _check_window(cls, v: object) -> object:
        if v is not None and not _positive_int(v):
            raise ValueError(f"must be a whole number of 1 or more (got {shown(v)})")
        return v


#: What a step that loads a model (``settings.model`` names the loader) may
#: carry under ``settings``. htrflow's ``Inference.from_config`` pops these
#: three and passes ``model_settings | <the rest>`` to the model, so any other
#: key overrides the same key in ``model_settings`` -- ``revision: null``
#: beside a pinned revision loads the repo's head (audit 2026-09-17, 3058).
#: The chart's model-revision policy refuses the same shape at admission.
_MODEL_STEP_SETTINGS = frozenset({"model", "model_settings", "generation_settings"})

#: What each htrflow model does to the page tree, by the lower-cased class
#: name htrflow resolves ``model:`` by (models/__init__.py). Every model step
#: runs on the tree's LEAVES and attaches its results to them (steps.py,
#: ``Inference.run``), whatever the step is called: a segmentation model adds
#: one level of child regions, a line reader puts a transcription on the node
#: it read. htrflow's ALTO and PAGE templates write the text of a line only
#: inside a region, page -> region -> line (serialization/templates), so a
#: reader with fewer than two segmentation steps before it writes all its
#: text where no export ever shows it -- whatever the models detect.
#: WordLevelTrOCR is not a line reader here: its word regions are one level
#: below the line, where the templates write them.
_SEGMENTATION_MODELS = frozenset({"yolo", "ppdoclayoutv3"})
_LINE_READERS = frozenset({"trocr", "pylaia"})


def _flat_text(steps: list) -> str | None:
    """The sentence refusing a pipeline whose every line reader runs on lines
    no export writes (see above); ``None`` when some text can get there."""
    segmentations, readers = 0, []
    for i, step in enumerate(steps, 1):
        settings = step.get("settings") if isinstance(step, dict) else None
        model = settings.get("model") if isinstance(settings, dict) else None
        name = str(model).lower() if model is not None else ""
        if name in _SEGMENTATION_MODELS:
            segmentations += 1
        elif name in _LINE_READERS:
            readers.append((i, model, segmentations))
        elif name == "wordleveltrocr":
            return None
    if not readers or any(before >= 2 for _, _, before in readers):
        return None
    i, model, before = readers[0]
    count = {0: "no Segmentation step", 1: "1 Segmentation step"}[before]
    return (
        f"put the lines that {model} (step {i}) reads directly on the page, "
        f"with {count} before it — htrflow's ALTO and PAGE export writes only "
        "the text of lines inside a region, so every page would publish "
        "without its text; put a region Segmentation step before the line "
        "step (docs: reference/campaign-yaml.md, Pipeline file)"
    )


#: htrflow's QualityPrediction step (htrflow_qp, to be merged into htrflow):
#: an XGBoost model scores the page from features of the tree htrflow built.
#: Its model is a joblib pickle -- loading it runs code -- so it comes from a
#: Hub repo at a commit, like every other model here, never from a path.
_QP_STEP = "qualityprediction"
_HUB_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
#: The groups quality_prediction.inference reads off htrflow's tree
#: (JSON_FEATURE_GROUPS) -- all this image can compute.
_QP_GROUPS = ("segmentation", "layout", "htr_confidence", "text")
#: The rest of the package's PageFeatureExtractor.FEATURE_GROUPS: they need
#: the page image, a DiT model or a language model this image does not run.
_QP_GROUPS_NOT_RUN = frozenset(
    {
        "image",
        "dit",
        "regionization",
        "ngram",
        "lm",
        "lexicon",
        "interaction",
        "metadata",
    }
)
_TEXT_RECOGNITION = "textrecognition"


def _plain_file(value: object) -> bool:
    return (
        isinstance(value, str)
        and value not in ("", ".", "..")
        and "/" not in value
        and "\\" not in value
    )


def _quality_step_problem(steps: list) -> str | None:
    """The sentence refusing a QualityPrediction step this system cannot run
    as written; ``None`` when there is none or it is fine."""
    names = [
        str(s.get("step", "")).lower() if isinstance(s, dict) else "" for s in steps
    ]
    at = [i for i, n in enumerate(names) if n == _QP_STEP]
    if not at:
        return None
    if len(at) > 1:
        return (
            f"has {len(at)} QualityPrediction steps (steps "
            f"{', '.join(str(i + 1) for i in at)}) — a page gets one predicted "
            "quality; keep one QualityPrediction step"
        )
    i = at[0]
    if _TEXT_RECOGNITION not in names[:i]:
        return (
            f"runs QualityPrediction (step {i + 1}) before any text is read — "
            "it scores the transcription, so put it after the TextRecognition step"
        )
    settings = steps[i].get("settings")
    ms = settings.get("model_settings") if isinstance(settings, dict) else None
    ms = ms if isinstance(ms, dict) else {}
    where = f"QualityPrediction (step {i + 1})"
    if not isinstance(ms.get("model"), str) or not _HUB_REPO_RE.match(ms["model"]):
        return (
            f"{where}: model_settings.model must be a Hugging Face Hub repo id "
            f"(<org>/<repo>), got {shown(ms.get('model'))} — the model is a "
            "pickle, so it is loaded from a pinned Hub commit, never a path"
        )
    if not isinstance(ms.get("revision"), str) or not _COMMIT_RE.match(ms["revision"]):
        return (
            f"{where}: model_settings.revision must be the 40-hex commit of "
            f"{ms['model']}, got {shown(ms.get('revision'))}"
        )
    for key in ("model_file", "bin_config_file"):
        if not _plain_file(ms.get(key)):
            return (
                f"{where}: model_settings.{key} must be a plain file name in "
                f"{ms['model']}, got {shown(ms.get(key))}"
            )
    groups = settings.get("feature_groups") if isinstance(settings, dict) else None
    if groups is not None:
        if (
            not isinstance(groups, list)
            or not groups
            or not all(isinstance(g, str) for g in groups)
        ):
            return f"{where}: feature_groups must be a non-empty list of group names"
        not_run = [g for g in groups if g in _QP_GROUPS_NOT_RUN]
        unknown = [
            g for g in groups if g not in _QP_GROUPS and g not in _QP_GROUPS_NOT_RUN
        ]
        if unknown:
            return (
                f"{where}: unknown feature group {', '.join(map(str, unknown))} — "
                f"use {', '.join(_QP_GROUPS)}"
            )
        if not_run:
            return (
                f"{where}: this image cannot run feature group "
                f"{', '.join(not_run)} — it computes {', '.join(_QP_GROUPS)} only"
            )
    return None


class Pipeline(BaseModel):
    #: Unknown keys rejected: a stale `model_revision:` (removed B63 Task 22
    #: fix round 2 -- nothing read it) gets the same one-line sentence as any
    #: other typo, rather than being silently ignored.
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    image: str
    steps: list[dict]
    #: Per-volume wall-clock budget; overrides converter.yaml's max_seconds.
    max_seconds: int | None = None
    #: How long this pipeline's finished campaign Jobs stay before the Job
    #: controller deletes them; overrides converter.yaml's value.
    ttl_seconds_after_finished: int | None = None
    #: One of converter.yaml's ``sizes`` (B105): what each campaign pod on
    #: this pipeline asks for. Part of the recipe its id names for good.
    size: str | None = Field(default=None, min_length=1)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(_RENAME_THE_FILE)
        # The Job controller copies a Job's name into its pods' `job-name`
        # label, and a label value stops at 63: past that the warm-up is
        # refused, and every campaign on this pipeline waits for its marker.
        if len(WARMUP_PREFIX + v) > DNS_LABEL:
            raise ValueError(
                f"is {len(v)} characters, and its warm-up Job, "
                f"{WARMUP_PREFIX}<id>, has to fit in {DNS_LABEL} — rename the "
                f"file to at most {DNS_LABEL - len(WARMUP_PREFIX)} characters"
            )
        return v

    @field_validator("image")
    @classmethod
    def _check_image(cls, v: str) -> str:
        if not _IMAGE_RE.match(v):
            raise ValueError(
                f'is not pinned to a digest (got "{v}") — write image: '
                "<registry>/<repo>@sha256:<64 hex digits>"
            )
        return v

    @field_validator("steps", mode="before")
    @classmethod
    def _check_steps(cls, v: object) -> object:
        if not isinstance(v, list):
            raise ValueError(
                'must be a list of steps — write steps: and then "- step: '
                '<Name>" entries under it'
            )
        # audit 0923 C-8: neither of these reached anything but the wrapper,
        # which then failed every volume of every campaign on the pipeline.
        if not v:
            raise ValueError(
                "is empty — a pipeline runs at least one step; write steps: "
                'and then "- step: <Name>" entries under it'
            )
        unnamed = [
            str(i)
            for i, step in enumerate(v, 1)
            if isinstance(step, dict) and not step.get("step")
        ]
        if unnamed:
            raise ValueError(
                f'has a step with no "step:" name (step {", ".join(unnamed)}) — '
                'every entry starts "- step: <Name>", the htrflow step it runs'
            )
        unnamable = [
            str(i)
            for i, step in enumerate(v, 1)
            if isinstance(step, dict) and not isinstance(step.get("step"), str)
        ]
        if unnamable:
            raise ValueError(
                f'has a step whose "step:" is not a name (step '
                f'{", ".join(unnamable)}) — every entry starts "- step: <Name>", '
                "the htrflow step it runs"
            )
        # 3098: the wrapper appends its own Export steps and refuses a file
        # that has one; htrflow resolves a step by its lower-cased name.
        exports = [
            i
            for i, step in enumerate(v, 1)
            if isinstance(step, dict) and str(step.get("step", "")).lower() == "export"
        ]
        if exports:
            raise ValueError(
                f"has an Export step (step {', '.join(map(str, exports))}) — the "
                "wrapper appends the Export steps itself; remove it"
            )
        stray = [
            f"step {i} ({step.get('step', '?')}): {', '.join(sorted(map(str, extra)))}"
            for i, step in enumerate(v, 1)
            if isinstance(step, dict)
            and isinstance(settings := step.get("settings"), dict)
            and "model" in settings
            and (extra := set(settings) - _MODEL_STEP_SETTINGS)
        ]
        if stray:
            raise ValueError(
                "has settings beside model_settings in a step that loads a "
                f"model ({'; '.join(stray)}) — htrflow merges those over "
                "model_settings, so one there overrides a pinned revision; "
                "move them under model_settings"
            )
        return v

    @field_validator("steps")
    @classmethod
    def _check_text_reaches_export(cls, v: list[dict], info: ValidationInfo) -> list:
        # audit 0923: a flat pipeline published every page empty and Succeeded.
        if not _as_recorded(info) and (why := _flat_text(v)):
            raise ValueError(why)
        return v

    @field_validator("steps")
    @classmethod
    def _check_quality_step(cls, v: list[dict], info: ValidationInfo) -> list:
        if not _as_recorded(info) and (why := _quality_step_problem(v)):
            raise ValueError(why)
        return v

    @field_validator("max_seconds", "ttl_seconds_after_finished", mode="before")
    @classmethod
    def _check_seconds(cls, v: object) -> object:
        if v is not None and not _positive_int(v):
            raise ValueError(
                f"must be a whole number of seconds, 1 or more (got {shown(v)})"
            )
        if isinstance(v, int) and v > _INT32_MAX:
            raise ValueError(f"must be {_INT32_MAX} or less (got {shown(v)})")
        return v

    def pipeline_yaml(self) -> str:
        return yaml.safe_dump({"steps": self.steps}, sort_keys=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pipeline_yaml().encode()).hexdigest()

    @property
    def recipe_sha256(self) -> str:
        """The steps AND the image, hashed over a canonical form (no PyYAML
        spelling moves it): a new image can load other files for the same
        steps, so it is a new recipe to warm (audit 0923 C-3)."""
        canonical = json.dumps(
            {"image": self.image, "steps": self.steps},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def cache_dir(self) -> str:
        """This recipe's directory on the model-cache PVC, the only part of it
        its warm-up writes and its campaign pods read. By recipe: a changed
        recipe is a new, empty directory whose campaigns wait for its warm-up
        instead of passing the gate on the old marker (audit 0923 C-3). By
        id: a warm-up runs its author's model code (a YOLO ``.pt`` is a
        pickle), so no two pipelines share one (audit 0923 S-2). The 64-hex
        digest after the id keeps two ids from ever meeting."""
        return f"{self.id}-{self.recipe_sha256}"


#: Taints that keep workloads off the control plane. A GPU batch pod has no
#: business on a node that carries one, so a toleration for either is
#: refused rather than copied into every pod.
_CONTROL_PLANE_TAINTS = frozenset(
    {"node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master"}
)


class Toleration(BaseModel):
    """One of ``converter.yaml``'s ``tolerations``: the Kubernetes
    ``Toleration`` shape, spelt the Kubernetes way (``tolerationSeconds``),
    known keys only.

    It was ``list[dict]``, copied into every warm-up and campaign pod as
    written (audit 0923 S-1). ``{operator: Exists}`` with no key tolerates
    every taint there is, so with a node selector a campaign's GPU pods could
    land on the control plane, or on any node tainted to keep them away.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    key: str | None = None
    operator: Literal["Exists", "Equal"] | None = None
    value: str | None = None
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] | None = None
    toleration_seconds: int | None = Field(default=None, alias="tolerationSeconds")

    @model_validator(mode="after")
    def _check_shape(self) -> "Toleration":
        if not self.key:
            raise ValueError(
                "has no key — a toleration without one tolerates every taint "
                "on every node, the control plane's included; name the taint "
                "it is for"
            )
        if not _label_key(self.key):
            raise ValueError(
                "has a key that is not a Kubernetes taint key (got "
                f"{shown(self.key)}) — a key is a name, optionally after a "
                '"<dns-prefix>/"; letters, digits, ".", "_" and "-", at most '
                "63 characters"
            )
        if self.key in _CONTROL_PLANE_TAINTS:
            raise ValueError(
                f"tolerates {self.key} — that taint keeps workloads off the "
                "control plane, and a GPU batch pod has no business there; "
                "remove it"
            )
        if self.operator == "Exists" and self.value:
            raise ValueError(
                "has a value with operator: Exists, which matches the key "
                "alone — drop the value, or use operator: Equal"
            )
        if self.value and not _LABEL_VALUE_RE.match(self.value):
            raise ValueError(
                f"has a value that is not a Kubernetes label value (got "
                f'{shown(self.value)}) — letters, digits, ".", "_" and "-", '
                "at most 63 characters"
            )
        if self.toleration_seconds is not None and self.effect != "NoExecute":
            raise ValueError(
                "has tolerationSeconds without effect: NoExecute, the only "
                "effect it applies to"
            )
        return self

    def manifest(self) -> dict[str, object]:
        """What the pod spec carries: the keys the author wrote, spelt as
        Kubernetes spells them."""
        return self.model_dump(by_alias=True, exclude_none=True)


#: A size's quantities (B105), in the spellings a pod's resources take and
#: this package can compare: whole numbers, with a binary or decimal suffix.
_MEMORY_RE = re.compile(r"([1-9][0-9]*)(Ki|Mi|Gi|Ti|k|M|G|T)?\Z")
_CPU_RE = re.compile(r"[1-9][0-9]*m?\Z")
_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
_UNITS |= {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
#: The least a size's ``/work`` may be: HOME, TMPDIR and the ultralytics
#: settings live there beside the page lookahead (half of it), which has to
#: hold at least a few pages. Below it the lookahead rounds towards 0, which
#: job-shape refuses at apply time (review of PR #35).
_WORKDIR_FLOOR = 512 * 2**20
#: The least a size's memory leaves beside its ``/work``: a floor that
#: catches a slip (2049Mi against 2Gi), not a sizing -- models want GiBs.
_PROCESS_FLOOR = 2**30


def memory_bytes(quantity: str) -> int:
    """A memory quantity ``Size`` has already held to ``_MEMORY_RE``."""
    m = _MEMORY_RE.match(quantity)
    assert m is not None, quantity
    return int(m[1]) * _UNITS[m[2] or ""]


def _label_problem(labels: dict[str, str]) -> str | None:
    """Why ``labels`` could not be a pod's node selector, or ``None``."""
    for key, value in labels.items():
        if not _label_key(key):
            return _NOT_A_NODE_LABEL.format(what="key", shown=shown(key))
        if value and not _LABEL_VALUE_RE.match(value):
            what = f'value for "{key}"'
            return _NOT_A_NODE_LABEL.format(what=what, shown=shown(value))
    return None


def _told_apart(a: dict[str, str], b: dict[str, str]) -> bool:
    """Whether a pod carrying ``a`` as its node selector is refused by a
    flavor labelled ``b``, and the other way round. Kueue matches a pod's
    selector against a flavor only on the keys that flavor names (v0.19
    ``flavorassigner.flavorSelector``), so two flavors are told apart only by
    a key both name, with different values."""
    return any(key in b and b[key] != value for key, value in a.items())


_INDISTINCT = (
    'flavors "{a}" and "{b}" name no label key with different values — Kueue '
    "compares a pod's node selector only on the keys of the flavor it tries, "
    "so a pod meant for one can be admitted on the other and never "
    "scheduled; give every flavor the same label key (nvidia.com/gpu.product, "
    "say) with a value of its own"
)


class Flavor(BaseModel):
    """One of converter.yaml's ``flavors``: a chart ``queue.flavors`` entry's
    name and node labels, repeated (B105). Kueue offers no way for a Job to
    ask for a flavor by name. It tries the ClusterQueue's flavors in order,
    skips one whose node labels the pod's node selector contradicts, and
    writes the chosen flavor's labels into the pod at admission (v0.19
    ``flavorassigner.checkFlavorForPodSets``, ``podset.FromAssignment``). So
    a size's flavor is rendered as these labels on the pod, and they must
    be the chart's."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str
    node_labels: dict[str, str] = Field(alias="nodeLabels")

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _object_name(v):
            raise ValueError(_NOT_AN_OBJECT_NAME.format(shown=shown(v)))
        return v

    @field_validator("node_labels")
    @classmethod
    def _check_labels(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError(
                "has no nodeLabels — a flavor is told apart by its node labels "
                "alone; copy them from the chart's queue.flavors"
            )
        if why := _label_problem(v):
            raise ValueError(why)
        return v


class Size(BaseModel):
    """One of converter.yaml's ``sizes``: what a campaign pod on a pipeline
    that names it asks for (B105). Request and limit are one number: the
    in-memory ``/work`` counts against the memory limit, and a pod using
    more than its request is the first the kubelet evicts under pressure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flavor: str | None = None
    gpu: int = 1
    cpu: str
    memory: str
    #: The memory-backed ``/work`` (``emptyDir.sizeLimit``); the wrapper's
    #: page lookahead is half of it.
    workdir: str = "2Gi"

    @field_validator("gpu", mode="before")
    @classmethod
    def _check_gpu(cls, v: object) -> object:
        if not _positive_int(v):
            raise ValueError(f"must be a whole number of 1 or more (got {shown(v)})")
        return v

    @field_validator("cpu", mode="before")
    @classmethod
    def _check_cpu(cls, v: object) -> object:
        v = str(v) if _positive_int(v) else v
        if not isinstance(v, str) or not _CPU_RE.match(v):
            raise ValueError(
                f"is not a CPU quantity (got {shown(v)}) — a whole number of "
                "cores, or of millicores as 500m"
            )
        return v

    @field_validator("memory", "workdir", mode="before")
    @classmethod
    def _check_memory(cls, v: object, info: ValidationInfo) -> object:
        v = str(v) if _positive_int(v) else v  # bytes, as YAML reads them
        if not isinstance(v, str) or not _MEMORY_RE.match(v):
            raise ValueError(
                f"is not a memory quantity (got {shown(v)}) — a whole number "
                "of bytes, or followed by Ki, Mi, Gi or Ti (or k, M, G, T)"
            )
        if info.field_name == "workdir" and memory_bytes(v) < _WORKDIR_FLOOR:
            raise ValueError(
                f"is under 512Mi (got {shown(v)}) — /work holds HOME, TMPDIR "
                "and the page lookahead, which is half of it"
            )
        return v

    def resources(self) -> dict[str, str]:
        return {"cpu": self.cpu, "memory": self.memory, "nvidia.com/gpu": str(self.gpu)}


#: Settings that were converter policy and are now Kyverno ClusterPolicies
#: the htrflow-batch chart ships (B63 Task 22) -> the chart value that
#: replaces each. ``extra="forbid"`` would reject them as a misspelt
#: setting, which sends their author hunting for a typo instead of at the
#: chart; the sentence below says where the rule went.
_MOVED_TO_THE_CHART = {
    "allowed_image_repos": "security.allowedImageRepos",
    "require_model_revision": "security.requireModelRevision",
}


class ConverterConfig(BaseModel):
    """``converter.yaml`` in the campaigns repo; unknown keys rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_moved_settings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        moved = [key for key in _MOVED_TO_THE_CHART if key in data]
        if moved:
            said = "; ".join(
                f"{key} moved to the htrflow-batch chart "
                f"({_MOVED_TO_THE_CHART[key]}, enforced by Kyverno)"
                for key in moved
            )
            it = "them" if len(moved) > 1 else "it"
            raise ValueError(f"{said} — remove {it} from converter.yaml")
        return data

    namespace: str = "htr-batch"
    queue: str = "htr-batch"
    window: int = Field(default=20, ge=1)
    s3_secret: str = "htr-batch-s3"
    data_pvc: str = "htr-test-data"
    runtime_class: str = "nvidia"
    node_selector: dict[str, str] = Field(default_factory=dict)
    tolerations: list[Toleration] = Field(default_factory=list)
    #: Required: without it every campaign pod exits 13 on every volume,
    #: after its GPU wait (hard-coded audit B10).
    public_results_base: str
    #: How a campaign's bare reference code becomes a manifest URL: ``{ref}``
    #: is the code. No archive's IIIF host is built in, so empty (the
    #: default) allows only ``manifest:`` and ``images:`` volumes.
    source_template: str = ""
    max_seconds: int = Field(default=21600, ge=1, le=_INT32_MAX)
    #: How long a batch pod's `warmup-wait` init container waits for its
    #: pipeline's marker before giving up. It holds the pod's GPU while it
    #: waits, so this is a GPU-hours budget, not a patience setting.
    warmup_wait_seconds: int = Field(default=900, ge=1)
    #: How long a finished campaign Job stays before the Job controller
    #: deletes it. An inspection window, not retention: the campaign's own
    #: ConfigMap is the durable record (B76), and the results are in the
    #: bucket. A day was short enough that a campaign finished on a Friday
    #: was gone before anyone looked at it -- and, until `apply` learnt to
    #: read the record, re-run from the top by the next apply.
    ttl_seconds_after_finished: int = Field(default=7 * 24 * 3600, ge=1, le=_INT32_MAX)
    #: A Secret in ``namespace`` with a ``token`` key: a Hugging Face token
    #: with read scope, for a pipeline whose model is private or gated.
    #: Empty (the default) means the warm-up downloads anonymously. Only the
    #: warm-up Job ever gets it -- campaign Jobs run ``HF_HUB_OFFLINE=1``
    #: against the cache the warm-up filled, so they need no Hub credential
    #: and must not carry one. No chart value pairs with this: like the S3
    #: Secret the object is the operator's, and no chart template names it.
    hf_token_secret: str = ""
    #: The largest manifest, and the largest single image, the wrapper may
    #: fetch. At 0 every volume of every campaign fails the cap.
    manifest_max_bytes: int = Field(default=16 * _MiB, ge=1)
    fetch_max_bytes: int = Field(default=64 * _MiB, ge=1)
    #: The WorkloadPriorityClass names the cluster has -- the chart's
    #: ``queue.priorityClasses``, and the default is the chart's default. A
    #: campaign's ``priority:`` must be one of them: Kueue's webhook never
    #: looks at the label, so a name the cluster has no class for is not
    #: refused at apply time -- the reconciler fails to build the Workload,
    #: raises no event, and the Job stays suspended and reads "Queued" for
    #: ever. This list is what ``validate`` checks instead. Empty means the
    #: cluster offers no priority and every ``priority:`` is refused.
    priority_classes: list[str] = Field(
        default_factory=lambda: ["htr-interactive", "htr-bulk", "htr-idle"]
    )
    #: The chart's ``queue.flavors`` by name and node labels, in its order
    #: (``Flavor``). Empty: the chart's one flavor, which no size names.
    flavors: list[Flavor] = Field(default_factory=list)
    #: Named pod sizes a pipeline's ``size:`` picks (``Size``).
    sizes: dict[str, Size] = Field(default_factory=dict)
    #: The size a pipeline that names none runs at. Unset, it keeps the Job
    #: skeleton's own -- 4 CPU, 8Gi requested and 16Gi at most, 1 GPU, a 2Gi
    #: ``/work`` -- on whichever flavor Kueue tries first with the quota.
    default_size: str | None = None

    def size_of(self, p: Pipeline) -> str | None:
        """The size ``p``'s campaign pods run at: its own, or the default."""
        return p.size or self.default_size

    @field_validator("namespace")
    @classmethod
    def _check_namespace(cls, v: str) -> str:
        if not _DNS_LABEL_RE.match(v):
            raise ValueError(_NOT_A_NAMESPACE.format(shown=shown(v)))
        return v

    @field_validator("priority_classes")
    @classmethod
    def _check_priority_classes(cls, v: list[str]) -> list[str]:
        for name in v:
            if not _DNS_LABEL_RE.match(name):
                raise ValueError(_NOT_A_PRIORITY.format(shown=shown(name)))
        if len(set(v)) != len(v):
            raise ValueError("lists the same class twice")
        return v

    @field_validator("queue", "s3_secret", "data_pvc")
    @classmethod
    def _check_object_name(cls, v: str) -> str:
        if not _object_name(v):
            raise ValueError(_NOT_AN_OBJECT_NAME.format(shown=shown(v)))
        return v

    @field_validator("runtime_class")
    @classmethod
    def _check_runtime_class(cls, v: str) -> str:
        # Empty is a real answer here -- a cluster with no GPU RuntimeClass
        # renders no `runtimeClassName` at all (``render._scheduling``).
        if v and not _object_name(v):
            raise ValueError(_NOT_AN_OBJECT_NAME.format(shown=shown(v)))
        return v

    @field_validator("node_selector")
    @classmethod
    def _check_node_selector(cls, v: dict[str, str]) -> dict[str, str]:
        if why := _label_problem(v):
            raise ValueError(why)
        return v

    @field_validator("sizes")
    @classmethod
    def _check_size_names(cls, v: dict[str, Size]) -> dict[str, Size]:
        for name in v:
            if not _DNS_LABEL_RE.match(name):
                raise ValueError(
                    f"has {shown(name)}, which is not a size name — use "
                    'lower-case letters, digits and "-", at most 63 characters'
                )
        return v

    @model_validator(mode="after")
    def _check_sizes(self) -> "ConverterConfig":
        names = [f.name for f in self.flavors]
        for name in names:
            if names.count(name) > 1:
                raise ValueError(f'"flavors" lists flavor "{name}" twice')
        if self.default_size is not None and self.default_size not in self.sizes:
            raise ValueError(
                f'default_size "{self.default_size}" is not one of the sizes '
                f"({', '.join(self.sizes) or 'none'}) — name one of them, or "
                "leave default_size out"
            )
        labels = {f.name: f.node_labels for f in self.flavors}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if not _told_apart(labels[a], labels[b]):
                    raise ValueError(_INDISTINCT.format(a=a, b=b))
        for name, size in self.sizes.items():
            if memory_bytes(size.memory) - memory_bytes(size.workdir) < _PROCESS_FLOOR:
                raise ValueError(
                    f'size "{name}" has memory {size.memory}, which leaves less '
                    f"than 1Gi beside its workdir ({size.workdir}) — the "
                    "in-memory /work counts against the same limit, and the "
                    "process gets what is left"
                )
            if size.flavor is not None and size.flavor not in labels:
                raise ValueError(
                    f'size "{name}" names flavor "{size.flavor}", which '
                    "converter.yaml's flavors does not list "
                    f"({', '.join(names) or 'none'}) — copy its name and "
                    "nodeLabels from the chart's queue.flavors"
                )
            for key, value in labels.get(size.flavor or "", {}).items():
                if self.node_selector.get(key, value) != value:
                    raise ValueError(
                        f'size "{name}" runs on flavor "{size.flavor}", whose '
                        f"nodeLabels say {key}={value}, but node_selector says "
                        f"{key}={self.node_selector[key]} — no node is both"
                    )
        return self

    @field_validator("public_results_base")
    @classmethod
    def _check_public_results_base(cls, v: str) -> str:
        if why := _unopenable(v) if _http_url(v) else "it is not an http(s) URL":
            raise ValueError(
                "must be the URL browsers read the results bucket at, the "
                f'chart\'s publicResultsBase (got "{_shown_url(v)}": {why}) — '
                "write the whole URL, e.g. https://results.example.org/htr-results"
            )
        return v

    @field_validator("source_template")
    @classmethod
    def _check_source_template(cls, v: str) -> str:
        if v and _placeholders(v) != ["ref"]:
            raise ValueError(_BAD_TEMPLATE.format(shown=shown(v)))
        return v

    @field_validator("hf_token_secret")
    @classmethod
    def _check_hf_token_secret(cls, v: str) -> str:
        if v and (len(v) > 253 or not _SUBDOMAIN_RE.match(v)):
            raise ValueError(
                f"is not a Kubernetes Secret name (got {shown(v)}) — use "
                'lower-case letters, digits, "-" and ".", starting and '
                "ending with a letter or digit, at most 253 characters"
            )
        return v
