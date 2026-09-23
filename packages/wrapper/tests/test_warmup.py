"""The warm-up entrypoint: fill HF_HOME for one pipeline and exit."""

import json
import logging
import os
import signal
from pathlib import Path

import pytest

from htrflow_batch.warmup import (
    EXIT_OK,
    EXIT_PERMANENT,
    EXIT_SIGTERM,
    EXIT_TRANSIENT,
    main,
)


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


def _repository_not_found():
    import httpx
    from huggingface_hub.errors import RepositoryNotFoundError

    # huggingface_hub's HfHubHTTPError wants a real response object across
    # its supported versions; the content is irrelevant here.
    response = httpx.Response(404, request=httpx.Request("GET", "https://hf.co"))
    return RepositoryNotFoundError(
        "Repository Not Found for url: ...", response=response
    )


def _yaml_error():
    import yaml

    return yaml.YAMLError("while parsing")


@pytest.mark.parametrize(
    ("make_error", "permanent"),
    [
        (lambda: OSError("connection reset"), False),
        # W12: a typo'd step/model or malformed YAML looped forever as a
        # transient warm-up; nothing about it changes on retry
        (lambda: ValueError("1 validation error for PipelineConfig"), True),
        (_yaml_error, True),
        (lambda: KeyError("segmentatoin"), True),  # unknown step: STEPS[...]
        (lambda: NotImplementedError("Model Yolo9 is not supported"), True),
        # W2: htrflow hands a step's settings to its constructor as kwargs
        (lambda: TypeError("__init__() got an unexpected keyword argument 'x'"), True),
        # a bogus HF model id (401/404 from the Hub) is a config mistake
        (_repository_not_found, True),
    ],
    ids=[
        "download-failure",
        "invalid-config",
        "malformed-yaml",
        "unknown-step",
        "unknown-model",
        "mistyped-setting",
        "bad-repo-id",
    ],
)
def test_a_failed_warmup_is_classified_reported_and_leaves_no_marker(
    tmp_path, make_error, permanent
):
    """How a load failure ends: exit 13 for a config mistake the Job's
    backoffLimit must stop retrying, 1 for what a retry can fix. No warm-up
    log exists (the Job mounts no S3 secret), so the termination message is
    the only place the cause reaches the campaign card -- and no marker opens
    the pipeline's gate."""
    error = make_error()
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}

    def boom(_):
        raise error

    assert main(env, load=boom) == (EXIT_PERMANENT if permanent else EXIT_TRANSIENT)
    assert json.loads(term_path.read_text()) == {
        "stage": "warmup",
        "permanent": permanent,
        "error": str(error),
    }
    assert not (tmp_path / "warmup" / "demo-v1.done").exists()


def test_warmup_refuses_to_run_offline(tmp_path):
    """Offline warm-up cannot download anything: a mis-wired Job must fail
    loudly (permanent) rather than "succeed" and gate the pipeline open."""
    term_path = tmp_path / "termination-log"
    env = {
        **_env(tmp_path),
        "HF_HUB_OFFLINE": "1",
        "TERMINATION_LOG_PATH": str(term_path),
    }
    rc = main(env, load=lambda _: None)
    assert rc == EXIT_PERMANENT
    term = json.loads(term_path.read_text())
    assert term["stage"] == "warmup" and term["permanent"] is True
    assert "HF_HUB_OFFLINE" in term["error"]


def test_warmup_missing_pipeline_is_permanent(tmp_path):
    term_path = tmp_path / "termination-log"
    env = {
        **_env(tmp_path),
        "PIPELINE_PATH": str(tmp_path / "nope.yaml"),
        "TERMINATION_LOG_PATH": str(term_path),
    }
    assert main(env, load=lambda _: None) == EXIT_PERMANENT
    term = json.loads(term_path.read_text())
    assert term["stage"] == "warmup" and term["permanent"] is True
    assert "nope.yaml" in term["error"]


def test_warmup_local_entry_not_found_is_transient(tmp_path):
    """The cache is simply not warm yet -- a re-warm and a retry fix it. The
    two hub lines disagree about the MRO (on 0.x the error is also a
    ValueError, on 1.x it is not), so the assertion is the classification."""
    from huggingface_hub.errors import LocalEntryNotFoundError

    assert issubclass(LocalEntryNotFoundError, OSError)  # true on both lines

    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}

    def boom(_):
        raise LocalEntryNotFoundError("model 'x' not found in the local cache")

    rc = main(env, load=boom)
    assert rc == EXIT_TRANSIENT
    term = json.loads(term_path.read_text())
    assert term["permanent"] is False


def test_warmup_unwritable_marker_dir_is_permanent(tmp_path, caplog):
    """B75/X4: a warm-up that cannot write its marker used to log a warning and
    exit 0 — a green Job whose campaigns then wait out their init container."""
    # basicConfig in main() is a no-op under pytest's root handler, so without
    # this the success line is never captured and the ordering assert below
    # passes on an empty caplog.
    caplog.set_level(logging.INFO, logger="htrflow_batch.warmup")
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    term_path = tmp_path / "termination-log"
    env = {
        **_env(tmp_path),
        "HF_HOME": str(blocked / "hf"),
        "TERMINATION_LOG_PATH": str(term_path),
    }
    rc = main(env, load=lambda _: None)
    assert rc == EXIT_PERMANENT
    term = json.loads(term_path.read_text())
    assert term["stage"] == "warmup" and term["permanent"] is True
    assert str(blocked / "warmup" / "demo-v1.done") in term["error"]
    assert "warm-up complete" not in caplog.text  # marker comes first


def test_warmup_without_pipeline_id_is_permanent(tmp_path):
    """No PIPELINE_ID means no marker can ever be named — a mis-wired Job, not
    a warm-up to call successful."""
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}
    del env["PIPELINE_ID"]
    assert main(env, load=lambda _: None) == EXIT_PERMANENT
    term = json.loads(term_path.read_text())
    assert term["permanent"] is True and "PIPELINE_ID" in term["error"]


def test_warmup_sigterm_writes_a_termination_message_and_exits_143(
    tmp_path, hard_exits
):
    """The Job's activeDeadlineSeconds (1 h) kills a slow first download; the
    campaign card must show why, not an empty message."""
    before = signal.getsignal(signal.SIGTERM)
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}

    def killed(_):
        os.kill(os.getpid(), signal.SIGTERM)

    rc = main(env, load=killed)
    assert rc == EXIT_SIGTERM == 143
    assert hard_exits == [EXIT_SIGTERM]
    assert json.loads(term_path.read_text()) == {
        "stage": "warmup",
        "permanent": False,
        "error": "SIGTERM",
    }
    assert not (tmp_path / "warmup" / "demo-v1.done").exists()
    assert signal.getsignal(signal.SIGTERM) is before  # handler restored


def test_warmup_says_a_token_is_present_without_saying_what_it_is(tmp_path, caplog):
    """A private model that resolved, or a 404 on one that did not, is read
    back from the warm-up pod's log; one line saying the token was there is
    the difference between the two. The value never goes in the log, and
    neither does its length -- that is a hint at which token it is."""
    secret = "hf_ThisIsNotARealToken"
    env = {**_env(tmp_path), "HF_TOKEN": secret}
    with caplog.at_level(logging.INFO, logger="htrflow_batch.warmup"):
        assert main(env, load=lambda _: None) == EXIT_OK
    said = [m for m in caplog.messages if "HF_TOKEN" in m]
    assert len(said) == 1, caplog.messages
    assert secret not in caplog.text
    assert str(len(secret)) not in said[0]


def test_warmup_says_nothing_about_a_token_when_there_is_none(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="htrflow_batch.warmup"):
        assert main(_env(tmp_path), load=lambda _: None) == EXIT_OK
    assert [m for m in caplog.messages if "HF_TOKEN" in m] == []


def test_warmup_redacts_urls_in_its_output(tmp_path, capsys):
    """W8: the warm-up pod mounts no S3 Secret, so it ships no run log and
    `kubectl logs` is where its failure is read -- and huggingface_hub names
    the URL it called, query and all. The batch wrapper's RedactingFormatter
    belongs here too; basicConfig installs a plain one."""
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(tmp_path / "term")}

    def boom(_):
        raise OSError("Connection error for url: https://hf.co/api/models?token=S3CRET")

    assert main(env, load=boom) == EXIT_TRANSIENT
    err = capsys.readouterr().err
    assert "S3CRET" not in err
    assert "https://hf.co/api/models" in err


def _recording_htrflow(fake_htrflow) -> list:
    """The real ``_load`` path (driver.build_pipeline) over a fake htrflow
    whose ``from_config`` records the build: empty means no model loaded."""
    built: list = []
    pipeline = type("Pipeline", (), {"from_config": staticmethod(built.append)})
    fake_htrflow(pipeline={"Pipeline": pipeline})
    return built


def test_warmup_refuses_an_export_step_without_loading_a_model(tmp_path, fake_htrflow):
    """3098: the warm-up is where a pipeline the batch Job would refuse must
    fail, once, instead of going green and letting every index load the
    weights first."""
    built = _recording_htrflow(fake_htrflow)
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}
    Path(env["PIPELINE_PATH"]).write_text(
        "steps:\n  - step: Export\n    settings: {dest: out, format: alto}\n"
    )

    assert main(env) == EXIT_PERMANENT
    assert built == []
    reason = json.loads(term_path.read_text())
    assert reason["permanent"] is True
    assert "must not contain Export steps" in reason["error"]
    assert not (tmp_path / "warmup" / "demo-v1.done").exists()


def test_warmup_refuses_a_pin_a_key_beside_model_settings_overrides(
    tmp_path, fake_htrflow
):
    """3058: htrflow gives the model ``model_settings | <the other keys>``, so
    ``revision: null`` beside a pinned revision loads the repo's head. The
    warm-up is the one pod that reaches the Hub, so it refuses before a single
    file is fetched, permanently, and says which model and why."""
    built = _recording_htrflow(fake_htrflow)
    term_path = tmp_path / "termination-log"
    env = {**_env(tmp_path), "TERMINATION_LOG_PATH": str(term_path)}
    pin = "7c44178d85926b4a096c55c89bf224855a201fbf"
    Path(env["PIPELINE_PATH"]).write_text(
        "steps:\n"
        "  - step: Segmentation\n"
        "    settings:\n"
        "      model: yolo\n"
        "      model_settings:\n"
        "        model: Riksarkivet/yolov9-regions-1\n"
        f"        revision: {pin}\n"
        "      revision: null\n"
    )

    assert main(env) == EXIT_PERMANENT
    assert built == []
    error = json.loads(term_path.read_text())["error"]
    assert error == (
        "step 1 (Segmentation): model Riksarkivet/yolov9-regions-1 does not load "
        f"its pinned revision — model_settings.revision is {pin}, but the revision key "
        "beside model_settings overrides it and htrflow would load revision "
        "None; move every model setting under model_settings"
    )
