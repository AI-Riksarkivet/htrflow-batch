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
    "issuer", ["http://id.example/realms/htrflow", "id.example/realms/htrflow", ""]
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


@pytest.mark.parametrize("path", ["control", "/control/", "/"])
def test_a_path_is_empty_or_starts_with_a_slash_and_ends_without_one(path):
    with pytest.raises(ValueError, match="HTRFLOW_API_PATH"):
        ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_PATH": path})


def test_a_path_with_a_prefix_is_kept():
    assert ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_PATH": "/control"}).path == (
        "/control"
    )


def test_the_config_cannot_be_changed():
    with pytest.raises(ValidationError):
        ApiConfig.from_env(REQUIRED).group = "other"
