import pytest
from api_fakes import REQUIRED
from pydantic import ValidationError

from htrflow_api.config import ApiConfig


def test_from_env_reads_the_required_names_and_the_defaults():
    cfg = ApiConfig.from_env(REQUIRED)
    assert (cfg.user_claim, cfg.groups_claim, cfg.path, cfg.jwks_url) == (
        "preferred_username",
        "groups",
        "",
        "",
    )
    assert cfg.batch_version == "dev"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_a_missing_required_name_is_named_in_the_error(missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        ApiConfig.from_env(env)


def test_from_env_ignores_names_that_are_not_its_own():
    assert ApiConfig.from_env({**REQUIRED, "PATH": "/usr/bin"}).issuer


@pytest.mark.parametrize(
    "issuer",
    [
        "http://id.example/realms/htrflow",
        "id.example/realms/htrflow",
        "",
        "https://user@id.example/realms/htrflow",
        "https://id.example@evil.example/realms/htrflow",
        "https://id.example/realms/htrflow?x=1",
        "https://id.example/realms/htrflow?",
        "https://id.example/realms/htrflow#f",
        "http://localhost/realms/htrflow?x=1",
        "https://id.example/realms/htr flow",
    ],
)
def test_an_issuer_that_is_not_https_is_refused(issuer):
    with pytest.raises(ValueError, match="HTRFLOW_API_ISSUER"):
        ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_ISSUER": issuer})


@pytest.mark.parametrize(
    "issuer",
    [
        "http://localhost:18080/realms/htrflow",
        "http://127.0.0.1:18080/realms/htrflow",
        "http://127.8.9.1/realms/htrflow",
        "http://[::1]:18080/realms/htrflow",
    ],
)
def test_a_loopback_issuer_may_be_plain_http(issuer):
    cfg = ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_ISSUER": issuer})
    assert cfg.issuer == issuer


def test_an_issuer_is_kept_exactly_as_given():
    issuer = "https://id.example/realms/htrflow/"
    assert ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_ISSUER": issuer}).issuer == (
        issuer
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://keys.example/jwks",
        "http://keycloak:8080/realms/htrflow/protocol/openid-connect/certs",
        "http://keys.elsewhere.example/jwks",
    ],
)
def test_a_jwks_url_may_be_http_or_https_to_a_host(url):
    assert ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_JWKS_URL": url}).jwks_url == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://x",
        "file:///etc/passwd",
        "not a url",
        "http:///jwks",
        "https://user@keys.example/jwks",
        "https://keys.example/jwks?x=1",
        "https://keys.example/jwks#f",
        "https://keys.example/j wks",
        "https://keys.example:port/jwks",
    ],
)
def test_any_other_jwks_url_refuses_the_start_and_is_named(url):
    with pytest.raises(ValueError, match="HTRFLOW_API_JWKS_URL"):
        ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_JWKS_URL": url})


@pytest.mark.parametrize(
    "path",
    ["control", "/control/", "/", "/{p}", "/a b", "/a/../b", "/a//b", "/a.b", "/a#b"],
)
def test_a_path_is_empty_or_starts_with_a_slash_and_ends_without_one(path):
    with pytest.raises(ValueError, match="HTRFLOW_API_PATH"):
        ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_PATH": path})


@pytest.mark.parametrize("path", ["/control", "/a/b", "/a-b_c", ""])
def test_a_path_with_a_prefix_is_kept(path):
    assert ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_PATH": path}).path == path


def test_the_config_cannot_be_changed():
    with pytest.raises(ValidationError):
        ApiConfig.from_env(REQUIRED).group = "other"


@pytest.mark.parametrize(
    "name",
    [
        "HTRFLOW_API_AUDIENCE",
        "HTRFLOW_API_CLIENT_ID",
        "HTRFLOW_API_GROUP",
        "HTRFLOW_API_NAMESPACE",
        "HTRFLOW_API_QUEUE",
        "HTRFLOW_API_USER_CLAIM",
        "HTRFLOW_API_GROUPS_CLAIM",
    ],
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_value_is_refused_and_named(name, blank):
    with pytest.raises(ValueError, match=name):
        ApiConfig.from_env({**REQUIRED, name: blank})
