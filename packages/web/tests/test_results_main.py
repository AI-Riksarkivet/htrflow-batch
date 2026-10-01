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
