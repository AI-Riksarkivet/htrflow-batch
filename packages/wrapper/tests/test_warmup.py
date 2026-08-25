"""The warm-up entrypoint: fill HF_HOME for one pipeline and exit."""

from pathlib import Path

from htrflow_batch.warmup import EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, main


def _env(tmp_path: Path) -> dict:
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("steps: []\n")
    return {
        "PIPELINE_PATH": str(pipeline),
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


def test_warmup_creates_the_writable_dirs_first(tmp_path):
    env = _env(tmp_path)
    seen = {}
    main(env, load=lambda _: seen.update({k: Path(env[k]).is_dir() for k in env}))
    assert seen["HOME"] and seen["TMPDIR"] and seen["YOLO_CONFIG_DIR"]
    assert seen["HF_HOME"]


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
