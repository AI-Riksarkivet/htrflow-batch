import base64

import pytest

from htrflow_web.__main__ import results_app_from_env


def test_the_app_is_built_from_the_environment(tmp_path):
    key = tmp_path / "key"
    key.write_text(base64.b64encode(bytes(32)).decode())
    app = results_app_from_env(
        {
            "HTRFLOW_RESULTS_NAMESPACE": "ns",
            "S3_BUCKET": "b",
            "HTRFLOW_SESSION_KEY_FILE": str(key),
        }
    )
    assert app.state.cfg.namespace == "ns"


def test_a_missing_key_file_stops_startup(tmp_path):
    with pytest.raises(FileNotFoundError):
        results_app_from_env(
            {
                "HTRFLOW_RESULTS_NAMESPACE": "ns",
                "S3_BUCKET": "b",
                "HTRFLOW_SESSION_KEY_FILE": str(tmp_path / "missing"),
            }
        )


def test_the_proxy_follows_a_rotated_key_without_a_restart(tmp_path):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from htrflow_web.session import SessionCodec  # noqa: PLC0415

    key = tmp_path / "key"
    key.write_text(base64.b64encode(bytes(32)).decode())
    app = results_app_from_env(
        {
            "HTRFLOW_RESULTS_NAMESPACE": "ns",
            "S3_BUCKET": "b",
            "HTRFLOW_SESSION_KEY_FILE": str(key),
        }
    )
    c = TestClient(app)
    c.cookies.set("htr_session", SessionCodec(bytes(32), 8).seal("anna", "a", "s"))
    assert c.get("/results/_session").json() == {"user": "anna"}
    rotated = tmp_path / "key.new"
    rotated.write_text(base64.b64encode(bytes([1] * 32)).decode())
    rotated.replace(key)
    assert c.get("/results/_session").status_code == 401
