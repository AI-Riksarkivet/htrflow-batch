"""The warm-up entrypoint: fill HF_HOME for one pipeline and exit."""

import json
from pathlib import Path

from htrflow_batch.warmup import EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, main


def _env(tmp_path: Path) -> dict:
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("steps: []\n")
    return {
        "PIPELINE_PATH": str(pipeline),
        "PIPELINE_ID": "demo-v1",
        "HF_HOME": str(tmp_path / "hf"),
        "HOME": str(tmp_path / "work" / "home"),
        "TMPDIR": str(tmp_path / "work" / "tmp"),
        "YOLO_CONFIG_DIR": str(tmp_path / "work" / "ultralytics"),
    }


def test_warmup_instantiates_the_pipeline_once(tmp_path):
    """Instantiating the pipeline IS the download: htrflow builds every step's
    model at construction, so the exact files a batch Job will load land in
    HF_HOME — no second parser of the pipeline YAML."""
    loaded = []
    rc = main(_env(tmp_path), load=lambda path: loaded.append(path))
    assert rc == EXIT_OK
    assert loaded == [str(tmp_path / "pipeline.yaml")]


def test_warmup_writes_the_done_marker_on_success(tmp_path):
    """Batch pods' init container gates on <data>/warmup/<pipeline_id>.done
    (<data> is HF_HOME's parent) before running (docs: wrapper)."""
    rc = main(_env(tmp_path), load=lambda _: None)
    assert rc == EXIT_OK
    assert (tmp_path / "warmup" / "demo-v1.done").is_file()


def test_warmup_writes_no_marker_on_failure(tmp_path):
    def boom(_):
        raise OSError("connection reset")

    rc = main(_env(tmp_path), load=boom)
    assert rc == EXIT_TRANSIENT
    assert not (tmp_path / "warmup" / "demo-v1.done").exists()


def test_warmup_refuses_to_run_offline(tmp_path):
    """Offline warm-up cannot download anything: a mis-wired Job must fail
    loudly (permanent) rather than "succeed" and gate the pipeline open."""
    rc = main({**_env(tmp_path), "HF_HUB_OFFLINE": "1"}, load=lambda _: None)
    assert rc == EXIT_PERMANENT


def test_warmup_missing_pipeline_is_permanent(tmp_path):
    env = {**_env(tmp_path), "PIPELINE_PATH": str(tmp_path / "nope.yaml")}
    assert main(env, load=lambda _: None) == EXIT_PERMANENT


def test_warmup_download_failure_is_transient(tmp_path):
    def boom(_):
        raise OSError("connection reset")

    assert main(_env(tmp_path), load=boom) == EXIT_TRANSIENT


def test_warmup_permanent_failure_writes_termination_message(tmp_path):
    """No warm-up log exists (the Job mounts no S3 secret) — the termination
    message is the only place the bad model id reaches the campaign card."""
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}

    def boom(_):
        raise NotImplementedError("Model Yolo9 is not supported")

    rc = main(env, load=boom)
    assert rc == EXIT_PERMANENT
    assert json.loads(term_path.read_text()) == {
        "stage": "warmup",
        "permanent": True,
        "error": "Model Yolo9 is not supported",
    }


def test_warmup_transient_failure_writes_termination_message(tmp_path):
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}

    def boom(_):
        raise OSError("connection reset")

    rc = main(env, load=boom)
    assert rc == EXIT_TRANSIENT
    assert json.loads(term_path.read_text()) == {
        "stage": "warmup",
        "permanent": False,
        "error": "connection reset",
    }


def test_warmup_bad_config_is_permanent(tmp_path):
    """W12: a typo'd step/model or malformed YAML looped forever as a
    transient warm-up; nothing about it changes on retry."""
    import yaml

    for exc in (
        ValueError("1 validation error for PipelineConfig"),
        yaml.YAMLError("while parsing"),
        KeyError("segmentatoin"),  # unknown step: htrflow STEPS[...]
        NotImplementedError("Model Yolo9 is not supported"),
    ):

        def boom(_, exc=exc):
            raise exc

        assert main(_env(tmp_path), load=boom) == EXIT_PERMANENT, exc
