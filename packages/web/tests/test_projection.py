"""Pure projection tests: plain dicts in, no cluster (docs: task-4-brief)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from htrflow_web import projection

CFG = SimpleNamespace(public_results_base="https://results.example.org")
#: `warmup` is required (Task 28 fix round item 4) -- this is what every
#: pre-existing test that does not care about it passes explicitly.
MISSING_WARMUP = {"phase": "missing"}


def _job(
    *,
    name="kyrk",
    namespace="htr-test",
    pipeline="demo-v1",
    completions=7,
    active=1,
    completed="0-2,5",
    failed="3",
    suspend=False,
    conditions=None,
    created="2026-01-01T00:00:00Z",
) -> dict:
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "creationTimestamp": created,
            "labels": {
                "app": "htrflow-batch",
                "htrflow.riksarkivet.se/managed-by": "converter",
                "htrflow.riksarkivet.se/campaign": name,
                "htrflow.riksarkivet.se/pipeline": pipeline,
            },
        },
        "spec": {
            "completions": completions,
            "suspend": suspend,
            "template": {
                "spec": {
                    "volumes": [
                        {"name": "campaign", "configMap": {"name": f"campaign-{name}"}},
                        {
                            "name": "pipeline",
                            "configMap": {"name": f"htr-pipeline-{pipeline}"},
                        },
                    ]
                }
            },
        },
        "status": {
            "active": active,
            "completedIndexes": completed,
            "failedIndexes": failed,
            "conditions": conditions or [],
        },
    }


def _configmap(namespace="htr-test", name="campaign-kyrk", n=7) -> dict:
    ids = [f"vol{i}" for i in range(n)]
    text = "".join(f"{vid}\thttps://iiif.example.org/{vid}/manifest\n" for vid in ids)
    return {
        "metadata": {"name": name, "namespace": namespace},
        "data": {"volumes.txt": text},
    }


PIPELINE_YAML = (
    "steps:\n"
    "- step: Segmentation\n"
    "  settings:\n"
    "    model: yolo\n"
    "- step: TextRecognition\n"
    "  settings:\n"
    "    model: TrOCR\n"
)


def _pipeline_configmap(text=PIPELINE_YAML, name="htr-pipeline-demo-v1") -> dict:
    return {
        "metadata": {"name": name, "namespace": "htr-test"},
        "data": {"pipeline.yaml": text},
    }


def _pod(
    index: int,
    *,
    active=False,
    terminated_message=None,
    created="2026-01-01T00:00:01Z",
    container="wrapper",
) -> dict:
    container_status = {"name": container, "state": {}}
    if active:
        container_status["state"] = {"running": {"startedAt": created}}
    elif terminated_message is not None:
        container_status["state"] = {
            "terminated": {"exitCode": 1, "message": terminated_message}
        }
    return {
        "metadata": {
            "name": f"kyrk-{index}",
            "namespace": "htr-test",
            "creationTimestamp": created,
            "labels": {
                "batch.kubernetes.io/job-name": "kyrk",
                "batch.kubernetes.io/job-completion-index": str(index),
            },
        },
        "status": {"containerStatuses": [container_status]},
    }


class TestParseIndexRanges:
    def test_mixed_ranges_and_singles(self):
        assert projection.parse_index_ranges("0-2,5,7-9") == {0, 1, 2, 5, 7, 8, 9}

    def test_empty_string(self):
        assert projection.parse_index_ranges("") == set()

    def test_none(self):
        assert projection.parse_index_ranges(None) == set()

    def test_single_value(self):
        assert projection.parse_index_ranges("3") == {3}


class TestSummarize:
    def test_counts_and_resultsbase(self):
        job = _job()
        summary = projection.summarize(job, CFG, MISSING_WARMUP)
        assert summary["counts"] == {"total": 7, "active": 1, "done": 4, "failed": 1}
        assert summary["phase"] == "Running"
        assert summary["namespace"] == "htr-test"
        assert summary["name"] == "kyrk"
        assert summary["pipeline"] == "demo-v1"
        assert summary["suspended"] is False
        assert summary["createdAt"] == "2026-01-01T00:00:00Z"
        assert summary["resultsBase"] == "https://results.example.org/htr-test/demo-v1"

    def test_resultsbase_is_always_namespaced(self):
        """The namespaced layout is the only layout (B63 task 15): the
        namespace is in every `resultsBase`, whatever the namespace is."""
        job = _job(namespace="htr-batch")
        summary = projection.summarize(job, CFG, MISSING_WARMUP)
        assert summary["resultsBase"] == "https://results.example.org/htr-batch/demo-v1"

    def test_phase_queued(self):
        job = _job(suspend=True, completed="", failed="")
        assert projection.summarize(job, CFG, MISSING_WARMUP)["phase"] == "Queued"

    def test_phase_paused(self):
        job = _job(suspend=True, completed="0", failed="")
        assert projection.summarize(job, CFG, MISSING_WARMUP)["phase"] == "Paused"

    def test_phase_succeeded(self):
        job = _job(conditions=[{"type": "Complete", "status": "True"}])
        assert projection.summarize(job, CFG, MISSING_WARMUP)["phase"] == "Succeeded"

    def test_phase_failed(self):
        """Nothing completed: the campaign produced nothing."""
        job = _job(completed="", conditions=[{"type": "Failed", "status": "True"}])
        assert projection.summarize(job, CFG, MISSING_WARMUP)["phase"] == "Failed"

    def test_phase_partially_failed(self):
        """The Job gave up, but four indexes had already published."""
        job = _job(conditions=[{"type": "Failed", "status": "True"}])
        assert (
            projection.summarize(job, CFG, MISSING_WARMUP)["phase"] == "PartiallyFailed"
        )

    def test_phase_succeeded_wins_over_failed(self):
        job = _job(
            conditions=[
                {"type": "Complete", "status": "True"},
                {"type": "Failed", "status": "True"},
            ]
        )
        assert projection.summarize(job, CFG, MISSING_WARMUP)["phase"] == "Succeeded"


class TestDetail:
    def test_volume_states_and_reason(self):
        job = _job()
        configmap = _configmap()
        pods = [
            _pod(4, active=True),
            _pod(
                3,
                terminated_message=(
                    '{"permanent": true, "error": "manifest unsupported"}'
                ),
            ),
        ]
        d = projection.detail(
            job, configmap, pods, CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        states = {v["index"]: v["state"] for v in d["volumes"]}
        assert states == {
            0: "done",
            1: "done",
            2: "done",
            3: "failed",
            4: "active",
            5: "done",
            6: "pending",
        }
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        # Structured, not the wrapper's raw JSON text: the card renders a
        # sentence from these fields, and a JSON blob is not a sentence.
        assert row3["reason"] == {
            "stage": None,
            "permanent": True,
            "error": "manifest unsupported",
        }
        row4 = next(v for v in d["volumes"] if v["index"] == 4)
        assert "reason" not in row4
        assert row3["id"] == "vol3"
        assert row3["manifestUrl"] == (
            "https://results.example.org/htr-test/demo-v1/vol3/manifest.json"
        )
        assert row3["iiifUrl"] == (
            "https://results.example.org/htr-test/demo-v1/vol3/iiif.json"
        )
        assert row3["altoPrefix"] == (
            "https://results.example.org/htr-test/demo-v1/vol3/alto/"
        )
        # Absolute URL, no namespace/S3_PREFIX prefix: matches
        # ResultStore.run_log_key() (packages/wrapper/src/htrflow_batch/store.py),
        # which writes the run log outside volume_prefix on purpose.
        assert row3["logUrl"] == (
            "https://results.example.org/status/logs/demo-v1/vol3.txt"
        )

    def test_source_url_is_the_manifest_half_of_the_line(self):
        d = projection.detail(_job(), _configmap(), [], CFG, warmup=MISSING_WARMUP)
        assert d["volumes"][0]["sourceUrl"] == (
            "https://iiif.example.org/vol0/manifest"
        )

    def test_an_images_line_has_no_source_url(self):
        cm = {
            "metadata": {"name": "campaign-kyrk", "namespace": "htr-test"},
            "data": {"volumes.txt": "vol0\timages:https://a/1.jpg https://a/2.jpg\n"},
        }
        d = projection.detail(_job(), cm, [], CFG, warmup=MISSING_WARMUP)
        assert d["volumes"][0]["sourceUrl"] is None

    def test_a_line_without_a_source_has_no_source_url(self):
        cm = {
            "metadata": {"name": "campaign-kyrk", "namespace": "htr-test"},
            "data": {"volumes.txt": "vol0\n"},
        }
        d = projection.detail(_job(), cm, [], CFG, warmup=MISSING_WARMUP)
        assert d["volumes"][0]["sourceUrl"] is None

    def test_failures_capped_and_newest_index_first(self):
        job = _job()
        configmap = _configmap()
        pods = [
            _pod(
                3,
                terminated_message=(
                    '{"permanent": true, "error": "manifest unsupported"}'
                ),
            ),
        ]
        d = projection.detail(
            job, configmap, pods, CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        assert [f["index"] for f in d["failures"]] == [3]

    def test_failures_excludes_failed_index_without_reason(self):
        job = _job()
        configmap = _configmap()
        d = projection.detail(
            job, configmap, [], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        assert d["failures"] == []

    def test_paging(self):
        job = _job()
        configmap = _configmap()
        d = projection.detail(
            job, configmap, [], CFG, offset=5, limit=2, warmup=MISSING_WARMUP
        )
        assert [v["index"] for v in d["volumes"]] == [5, 6]

    def test_newest_pod_wins_reason(self):
        job = _job()
        configmap = _configmap()
        pods = [
            _pod(
                3,
                terminated_message='{"permanent": false, "error": "stale"}',
                created="2026-01-01T00:00:00Z",
            ),
            _pod(
                3,
                terminated_message=(
                    '{"permanent": true, "error": "manifest unsupported"}'
                ),
                created="2026-01-01T00:05:00Z",
            ),
        ]
        d = projection.detail(
            job, configmap, pods, CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"]["error"] == "manifest unsupported"

    def test_a_message_that_is_not_the_wrapper_object_becomes_the_raw_error(self):
        """An older wrapper, a kubelet FallbackToLogsOnError tail or a
        truncated write: the client still gets the three fields, with the
        text it cannot parse in `error` rather than a missing key."""
        pods = [_pod(3, terminated_message="Killed\n")]
        d = projection.detail(_job(), _configmap(), pods, CFG, warmup=MISSING_WARMUP)
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == {
            "stage": None,
            "permanent": None,
            "error": "Killed\n",
        }

    def test_a_non_string_stage_or_permanent_is_dropped_not_passed_through(self):
        pods = [
            _pod(
                3,
                terminated_message=(
                    '{"stage": 7, "permanent": "yes", "error": "boom"}'
                ),
            )
        ]
        d = projection.detail(_job(), _configmap(), pods, CFG, warmup=MISSING_WARMUP)
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == {"stage": None, "permanent": None, "error": "boom"}

    def test_no_configmap_does_not_crash(self):
        d = projection.detail(
            _job(), None, [], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        assert d["volumes"] == []
        assert d["failures"] == []

    def test_configmap_fewer_lines_than_completions(self):
        d = projection.detail(
            _job(), _configmap(n=3), [], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        assert [v["index"] for v in d["volumes"]] == [0, 1, 2]

    def test_pod_deadline_is_named_in_the_reason(self):
        """The per-volume budget is the pod's activeDeadlineSeconds (Task 25):
        the kubelet SIGTERMs the wrapper, which writes the same
        `"error": "SIGTERM"` a node drain produces. Only the pod's
        status.reason separates the two, so the row must carry it."""
        pod = _pod(3, terminated_message='{"stage": "stream", "error": "SIGTERM"}')
        pod["status"]["reason"] = "DeadlineExceeded"
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == {
            "stage": "stream",
            "permanent": None,
            "error": "DeadlineExceeded",
        }

    def test_a_drain_sigterm_keeps_its_reason(self):
        """No status.reason: a drain, not a deadline — leave it alone."""
        pod = _pod(3, terminated_message='{"stage": "stream", "error": "SIGTERM"}')
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"]["error"] == "SIGTERM"

    def test_deadline_leaves_a_non_json_message_alone(self):
        pod = _pod(3, terminated_message="killed")
        pod["status"]["reason"] = "DeadlineExceeded"
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == {"stage": None, "permanent": None, "error": "killed"}

    def test_reason_falls_back_to_the_init_container(self):
        """A `warmup-wait` that gave up is the whole failure: the wrapper
        never started, so it has no `containerStatuses` entry to read and the
        index used to be `failed` with no `reason` at all -- the one case the
        bounded gate was built to make visible. The kubelet puts the init
        container's stderr in its termination message
        (`terminationMessagePolicy: FallbackToLogsOnError`), which is plain
        text, not the wrapper's JSON."""
        sentence = (
            "no warm-up marker at /data/warmup/demo-v1.done after 900s: "
            "the pipeline's warm-up Job has not finished"
        )
        pod = _pod(3)
        pod["status"]["containerStatuses"] = [{"name": "wrapper", "state": {}}]
        pod["status"]["initContainerStatuses"] = [
            {
                "name": "warmup-wait",
                "state": {"terminated": {"exitCode": 13, "message": sentence}},
            }
        ]
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == {
            "stage": None,
            "permanent": None,
            "error": sentence,
        }

    def test_a_succeeded_init_container_is_not_a_reason(self):
        """Every init container terminates -- with exit 0 on the happy path.
        Only a failed one explains the pod."""
        pod = _pod(3, terminated_message='{"error": "verify failed"}')
        pod["status"]["initContainerStatuses"] = [
            {
                "name": "warmup-wait",
                "state": {"terminated": {"exitCode": 0, "message": "fine"}},
            }
        ]
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"]["error"] == "verify failed"

    def test_a_terminated_main_container_outranks_a_failed_init_container(self):
        """Both terminated: the wrapper ran, so its own message is the
        reason; the init container's is only read when the wrapper never
        started."""
        pod = _pod(3, terminated_message='{"error": "verify failed"}')
        pod["status"]["initContainerStatuses"] = [
            {
                "name": "warmup-wait",
                "state": {"terminated": {"exitCode": 13, "message": "gave up"}},
            }
        ]
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"]["error"] == "verify failed"

    def test_laststate_terminated_reason_fallback(self):
        pod = _pod(3)
        pod["status"]["containerStatuses"][0]["state"] = {"running": {}}
        pod["status"]["containerStatuses"][0]["lastState"] = {
            "terminated": {
                "exitCode": 1,
                "message": '{"stage": "load", "permanent": false, "error": "OOM"}',
            }
        }
        d = projection.detail(
            _job(), _configmap(), [pod], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == {
            "stage": "load",
            "permanent": False,
            "error": "OOM",
        }


class TestLatest:
    """The folded card's one-line strip; computed over every volume, so it is
    right for a campaign whose in-flight index is past the first page."""

    def test_active_wins_over_done(self):
        d = projection.detail(
            _job(), _configmap(), [_pod(4, active=True)], CFG, warmup=MISSING_WARMUP
        )
        assert d["latest"]["id"] == "vol4"  # 0-2 and 5 are done

    def test_newest_done_when_nothing_is_active(self):
        d = projection.detail(_job(), _configmap(), [], CFG, warmup=MISSING_WARMUP)
        assert d["latest"]["id"] == "vol5"  # completedIndexes "0-2,5"

    def test_none_when_nothing_has_started(self):
        job = _job(completed="", failed="", active=0)
        d = projection.detail(job, _configmap(), [], CFG, warmup=MISSING_WARMUP)
        assert d["latest"] is None

    def test_ignores_the_offset_limit_window(self):
        """The strip must not be a function of what the card has paged in."""
        job = _job(completions=300, completed="0-250", failed="")
        d = projection.detail(
            job, _configmap(n=300), [], CFG, offset=0, limit=200, warmup=MISSING_WARMUP
        )
        assert [v["index"] for v in d["volumes"]] == list(range(200))
        assert d["latest"]["id"] == "vol250"

    def test_an_active_volume_past_the_first_page_still_wins(self):
        job = _job(completions=300, completed="0-250", failed="")
        pods = [_pod(260, active=True)]
        d = projection.detail(
            job,
            _configmap(n=300),
            pods,
            CFG,
            offset=0,
            limit=200,
            warmup=MISSING_WARMUP,
        )
        assert d["latest"]["id"] == "vol260"
        assert d["latest"]["state"] == "active"


class TestPipeline:
    def test_steps_and_yaml_come_off_the_pipeline_configmap(self):
        d = projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            pipeline_configmap=_pipeline_configmap(),
            warmup=MISSING_WARMUP,
        )
        assert d["pipelineSteps"] == ["Segmentation", "TextRecognition"]
        assert d["pipelineYaml"] == PIPELINE_YAML

    def test_missing_configmap_is_no_steps_not_an_error(self):
        d = projection.detail(_job(), _configmap(), [], CFG, warmup=MISSING_WARMUP)
        assert d["pipelineSteps"] == []
        assert d["pipelineYaml"] == ""

    def test_a_configmap_without_steps_is_no_steps(self):
        d = projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            pipeline_configmap=_pipeline_configmap("model: yolo\n"),
            warmup=MISSING_WARMUP,
        )
        assert d["pipelineSteps"] == []
        assert d["pipelineYaml"] == "model: yolo\n"

    def test_unparsable_yaml_is_no_steps(self):
        d = projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            pipeline_configmap=_pipeline_configmap("steps: [oh: no: yes\n"),
            warmup=MISSING_WARMUP,
        )
        assert d["pipelineSteps"] == []

    def test_a_non_string_step_is_skipped(self):
        """`pipelineSteps` is typed string[] all the way to the chip."""
        d = projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            pipeline_configmap=_pipeline_configmap(
                "steps:\n- step: 3\n- step: Export\n"
            ),
            warmup=MISSING_WARMUP,
        )
        assert d["pipelineSteps"] == ["Export"]

    def test_an_entry_without_a_step_key_is_skipped(self):
        d = projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            pipeline_configmap=_pipeline_configmap(
                "steps:\n- settings: {}\n- step: Export\n"
            ),
            warmup=MISSING_WARMUP,
        )
        assert d["pipelineSteps"] == ["Export"]


class TestConfigmapRef:
    def test_finds_campaign_volume(self):
        assert projection.configmap_ref(_job()) == "campaign-kyrk"

    def test_finds_the_pipeline_volume(self):
        assert projection.configmap_ref(_job(), "pipeline") == "htr-pipeline-demo-v1"

    def test_missing_campaign_volume(self):
        job = {"spec": {"template": {"spec": {"volumes": []}}}}
        assert projection.configmap_ref(job) is None


def _warmup_job(
    *,
    namespace="htr-test",
    pipeline="demo-v1",
    name="htr-warmup-demo-v1",
    active=0,
    conditions=None,
) -> dict:
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app": "htrflow-warmup",
                "htrflow.riksarkivet.se/managed-by": "converter",
                "htrflow.riksarkivet.se/pipeline": pipeline,
            },
        },
        "status": {"active": active, "conditions": conditions or []},
    }


class TestMatchWarmup:
    def test_matches_by_namespace_and_pipeline_label(self):
        job = _warmup_job()
        other = _warmup_job(namespace="other-ns")
        assert projection.match_warmup(_job(), [other, job]) is job

    def test_no_match_is_none(self):
        job = _warmup_job(pipeline="other-v1")
        assert projection.match_warmup(_job(), [job]) is None

    def test_two_campaigns_share_one_warmup(self):
        """The match is by pipeline, not by campaign name -- two campaign
        rows on the same pipeline resolve to the same warm-up Job."""
        job = _warmup_job()
        assert projection.match_warmup(_job(name="a"), [job]) is job
        assert projection.match_warmup(_job(name="b"), [job]) is job

    def test_no_pipeline_label_never_matches(self):
        """A campaign Job without a pipeline label must not pair up with a
        warm-up Job that also lacks one on None == None."""
        job = _job()
        del job["metadata"]["labels"]["htrflow.riksarkivet.se/pipeline"]
        no_pipeline_warmup = _warmup_job()
        del no_pipeline_warmup["metadata"]["labels"]["htrflow.riksarkivet.se/pipeline"]
        assert projection.match_warmup(job, [no_pipeline_warmup]) is None


class TestWarmupPhase:
    def test_pending_before_any_pod(self):
        assert projection.warmup_phase(_warmup_job()) == "pending"

    def test_running_while_active(self):
        assert projection.warmup_phase(_warmup_job(active=1)) == "running"

    def test_succeeded_on_complete_condition(self):
        job = _warmup_job(conditions=[{"type": "Complete", "status": "True"}])
        assert projection.warmup_phase(job) == "succeeded"

    def test_failed_on_failed_condition(self):
        job = _warmup_job(conditions=[{"type": "Failed", "status": "True"}])
        assert projection.warmup_phase(job) == "failed"


class TestWrapperReasonOnAWarmupPod:
    def test_reason_from_the_warmup_container(self):
        """No warm-up log exists (Task 28) -- app.py reads the reason off
        the newest pod's `warmup` container the same way it reads a batch
        pod's `wrapper` container, just with a different container name."""
        pods = [
            _pod(
                0,
                terminated_message=(
                    '{"stage": "warmup", "permanent": true,'
                    ' "error": "unknown model class Yolo9"}'
                ),
                container="warmup",
            )
        ]
        assert projection.wrapper_reason(projection.newest(pods), "warmup") == {
            "stage": "warmup",
            "permanent": True,
            "error": "unknown model class Yolo9",
        }


class TestSummarizeWarmup:
    def test_missing_warmup(self):
        assert projection.summarize(_job(), CFG, MISSING_WARMUP)["warmup"] == {
            "phase": "missing"
        }

    def test_carries_the_given_warmup_through(self):
        warmup = {
            "phase": "failed",
            "reason": {"stage": "warmup", "permanent": True, "error": "x"},
        }
        assert projection.summarize(_job(), CFG, warmup)["warmup"] == warmup

    def test_detail_inherits_it_from_the_summary(self):
        d = projection.detail(
            _job(), _configmap(), [], CFG, warmup={"phase": "running"}
        )
        assert d["warmup"] == {"phase": "running"}


def _progress(**kwargs) -> dict:
    """What progress.ProgressReader.fetch answers with, for the tests that
    inject it: every field, since the projection reads all of them."""
    return {
        "done": 0,
        "total": 0,
        "failed": 0,
        "lastPage": None,
        "stage": "stream",
        "updatedAt": None,
        "ageSeconds": None,
        "lastError": None,
        "errors": 0,
        "viewerPublished": False,
        **kwargs,
    }


class TestVolumeProgress:
    """`fetch_progress` is injected, so these stay pure: no bucket, no HTTP."""

    @staticmethod
    def _fetch(known: dict):
        asked = []

        def fetch(results_base: str, volume_id: str, state: str):
            asked.append((volume_id, state))
            return known.get(volume_id)

        return fetch, asked

    def test_progress_is_fetched_from_the_internal_base_not_the_public_one(self):
        """The API pod's own way to the bucket can differ from the one it
        hands the browser (the PoC's localhost publicResultsBase, docs:
        development/local-k3s) -- ProgressReader.fetch must never be asked to
        resolve the public one itself."""
        bases = []

        def fetch(results_base: str, volume_id: str, state: str):
            bases.append(results_base)
            return None

        cfg = SimpleNamespace(
            public_results_base="http://localhost:30900/htr-results",
            internal_results_base=(
                "http://rustfs.htr-batch.svc.cluster.local:9000/htr-results"
            ),
        )
        d = projection.detail(
            _job(), _configmap(), [], cfg, warmup=MISSING_WARMUP, fetch_progress=fetch
        )
        assert bases and all(b.startswith(cfg.internal_results_base) for b in bases)
        # The browser-facing URLs are unaffected -- still the public base.
        assert d["volumes"][0]["manifestUrl"].startswith(cfg.public_results_base)

    def test_each_returned_row_carries_its_progress_or_null(self):
        fetch, _ = self._fetch({"vol0": _progress(done=3, total=4)})
        d = projection.detail(
            _job(), _configmap(), [], CFG, warmup=MISSING_WARMUP, fetch_progress=fetch
        )
        assert d["volumes"][0]["progress"]["done"] == 3
        assert d["volumes"][1]["progress"] is None

    def test_only_the_rows_in_the_answer_are_fetched(self):
        """One GET per row shown, never one per volume in the campaign: the
        cost has to grow with the window, not with the archive (C08). The
        seven-volume campaign answers with two rows plus `latest` (vol5)."""
        fetch, asked = self._fetch({})
        projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            offset=0,
            limit=2,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        assert [v for v, _ in asked] == ["vol0", "vol1", "vol5"]

    def test_at_most_the_cap_is_fetched_even_when_the_page_is_bigger(self):
        """A `limit=1000` page must not turn into a thousand sequential GETs
        through one client (finding 5)."""
        n = 40
        fetch, asked = self._fetch({})
        pods = [_pod(i, active=True) for i in range(n)]
        projection.detail(
            _job(completions=n, active=n, completed="", failed=""),
            _configmap(n=n),
            pods,
            CFG,
            offset=0,
            limit=n,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        assert len(asked) == projection.PROGRESS_FETCH_CAP

    def test_a_bucket_that_does_not_answer_gives_up_inside_the_budget(
        self, monkeypatch
    ):
        """Every fetch here takes a second of the budget. The cap alone let a
        request hold a worker for cap x the HTTP timeout -- 64 s for an
        unreachable bucket -- and forty such requests emptied the threadpool
        that /healthz is answered from (2026-09-14 audit)."""
        ticks = iter(range(100))
        monkeypatch.setattr(projection.time, "monotonic", lambda: float(next(ticks)))
        n = 40
        fetch, asked = self._fetch({})
        pods = [_pod(i, active=True) for i in range(n)]
        body = projection.detail(
            _job(completions=n, active=n, completed="", failed=""),
            _configmap(n=n),
            pods,
            CFG,
            offset=0,
            limit=n,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        assert 0 < len(asked) < projection.PROGRESS_FETCH_CAP
        assert len(asked) <= projection.PROGRESS_FETCH_BUDGET
        assert body["volumes"][0]["progress"] is None, "still a row, just no progress"

    def test_running_rows_are_not_crowded_out_by_a_page_full_of_done_ones(self):
        """Most of a big campaign is done; a few volumes are still running.
        The running ones must not lose the cap to done rows ahead of them."""
        n = 40
        fetch, asked = self._fetch({})
        pods = [_pod(i, active=True) for i in range(n - 3, n)]  # last 3 running
        projection.detail(
            _job(completions=n, active=3, completed="0-35", failed=""),
            _configmap(n=n),
            pods,
            CFG,
            offset=0,
            limit=n,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        asked_ids = {v for v, _ in asked}
        assert {"vol37", "vol38", "vol39"} <= asked_ids
        assert len(asked) == projection.PROGRESS_FETCH_CAP

    def test_the_campaign_sums_the_pages_it_knows_about(self):
        fetch, _ = self._fetch(
            {
                "vol0": _progress(done=3, total=4, stage="done"),
                "vol1": _progress(done=1, total=9),
            }
        )
        d = projection.detail(
            _job(), _configmap(), [], CFG, warmup=MISSING_WARMUP, fetch_progress=fetch
        )
        assert (d["pagesDone"], d["pagesTotal"]) == (4, 13)

    def test_no_progress_anywhere_sums_to_zero(self):
        d = projection.detail(_job(), _configmap(), [], CFG, warmup=MISSING_WARMUP)
        assert (d["pagesDone"], d["pagesTotal"]) == (0, 0)
        assert d["volumes"][0]["progress"] is None

    def test_the_latest_row_and_a_failure_row_carry_progress_too(self):
        """Both are returned outside the page, so both need the field the
        frontend's schema requires."""
        fetch, _ = self._fetch({"vol4": _progress(done=7, total=9)})
        pods = [
            _pod(3, terminated_message='{"error": "boom"}'),
            _pod(4, active=True),
        ]
        d = projection.detail(
            _job(),
            _configmap(),
            pods,
            CFG,
            offset=6,
            limit=1,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        assert d["latest"]["progress"]["done"] == 7
        assert d["failures"][0]["progress"] is None


class TestCampaignNotice:
    """The product owner, 2026-09-08: an exception in the log should show on
    the front page, not only in a log someone downloads."""

    def _detail(self, known: dict, **kwargs):
        return projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            warmup=MISSING_WARMUP,
            fetch_progress=lambda base, vol, state: known.get(vol),
            **kwargs,
        )

    def test_failed_pages_and_errors_are_summed(self):
        d = self._detail(
            {
                "vol0": _progress(failed=2, errors=1),
                "vol1": _progress(failed=1, errors=4),
            }
        )
        assert (d["pagesFailed"], d["errors"]) == (3, 5)

    def test_the_most_recent_failure_carries_its_volume_and_its_log(self):
        older = {"page": "0002", "error": "first"}
        newer = {"page": "0044", "error": "the worker thread died"}
        d = self._detail(
            {
                "vol0": _progress(
                    lastError=older, updatedAt="2026-09-08T09:00:00+00:00"
                ),
                "vol1": _progress(
                    lastError=newer, updatedAt="2026-09-08T09:31:00+00:00"
                ),
            }
        )
        assert d["lastError"] == {
            "page": "0044",
            "error": "the worker thread died",
            "volume": "vol1",
            "logUrl": "https://results.example.org/status/logs/demo-v1/vol1.txt",
        }

    def test_nothing_wrong_is_no_notice(self):
        d = self._detail({"vol0": _progress()})
        assert d["lastError"] is None
        assert (d["pagesFailed"], d["errors"]) == (0, 0)


# --- the status ConfigMap the read API writes (B76) ----------------------

STATUS_FIELDS = {
    "phase",
    "volumesTotal",
    "volumesDone",
    "volumesFailed",
    "startedAt",
    "finishedAt",
    "resultsBase",
}


def _finished_job(**status) -> dict:
    job = {
        "metadata": {
            "name": "kyrk",
            "namespace": "htr-test",
            "labels": {
                "htrflow.riksarkivet.se/pipeline": "demo-v1",
                "htrflow.riksarkivet.se/campaign": "kyrk",
            },
        },
        "spec": {"completions": 3},
        "status": {
            "completedIndexes": "0-1",
            "failedIndexes": "2",
            "startTime": "2026-09-08T08:00:00Z",
            "conditions": [{"type": "Failed", "status": "True"}],
            **status,
        },
    }
    return job


def test_summarize_carries_the_start_and_finish_times():
    """The record's dates: once the Job is reaped these are all the campaign
    page has to say when it ran (B76)."""
    job = _finished_job(completionTime="2026-09-08T10:00:00Z")
    row = projection.summarize(job, CFG, {"phase": "succeeded"})
    assert row["startedAt"] == "2026-09-08T08:00:00Z"
    assert row["finishedAt"] == "2026-09-08T10:00:00Z"


def test_a_failed_job_finishes_at_its_condition_transition():
    """A Failed Job has no completionTime -- the condition is the only clock."""
    job = _finished_job()
    job["status"]["conditions"] = [
        {
            "type": "Failed",
            "status": "True",
            "lastTransitionTime": "2026-09-08T09:30:00Z",
        }
    ]
    row = projection.summarize(job, CFG, {"phase": "succeeded"})
    assert row["finishedAt"] == "2026-09-08T09:30:00Z"


def test_status_record_field_names_are_the_ones_apply_reads():
    """`htrflow-campaigns apply` parses these back to decide whether to leave
    a finished campaign alone, so the names are a contract, not a detail."""
    job = _finished_job(completionTime="2026-09-08T10:00:00Z")
    row = projection.summarize(job, CFG, {"phase": "succeeded"})
    data = projection.status_record(row)
    assert set(data) == STATUS_FIELDS
    assert data["phase"] == "PartiallyFailed"
    assert (data["volumesTotal"], data["volumesDone"]) == ("3", "2")
    assert data["volumesFailed"] == "1"
    assert data["finishedAt"] == "2026-09-08T10:00:00Z"
    assert data["resultsBase"].endswith("/htr-test/demo-v1")
    assert all(isinstance(v, str) for v in data.values())


def test_the_list_endpoints_record_leaves_the_failed_volumes_alone():
    """A list response has no per-volume reasons. Writing an empty
    `failedVolumes` would wipe what the detail endpoint observed, so the
    field is simply absent from what the list writes."""
    row = projection.summarize(_finished_job(), CFG, {"phase": "succeeded"})
    assert "failedVolumes" not in projection.status_record(row)


def test_the_failed_volumes_carry_one_sentence_each_and_are_capped():
    row = projection.summarize(_finished_job(), CFG, {"phase": "succeeded"})
    failures = [{"id": f"vol{i}", "reason": {"error": f"boom {i}"}} for i in range(80)]
    data = projection.status_record(row, failures)
    listed = json.loads(data["failedVolumes"])
    assert len(listed) == 50
    assert listed[0] == {"id": "vol0", "reason": "boom 0"}


def test_the_status_configmap_is_named_and_labelled_for_the_prune():
    row = projection.summarize(_finished_job(), CFG, {"phase": "succeeded"})
    cm = projection.status_configmap(row, {"phase": "Failed"})
    assert cm["metadata"]["name"] == "campaign-kyrk-status"
    assert cm["metadata"]["namespace"] == "htr-test"
    labels = cm["metadata"]["labels"]
    assert labels["htrflow.riksarkivet.se/managed-by"] == "converter"
    assert labels["htrflow.riksarkivet.se/campaign"] == "kyrk"
    assert labels["htrflow.riksarkivet.se/pipeline"] == "demo-v1"
    assert labels["htrflow.riksarkivet.se/kind"] == "status"
    assert cm["data"] == {"phase": "Failed"}


# --- a campaign whose Job the TTL reaped still has a row (B76) -----------

RECORD = {
    "metadata": {
        "name": "campaign-kyrk",
        "namespace": "htr-test",
        "creationTimestamp": "2026-09-08T07:00:00Z",
        "labels": {
            "htrflow.riksarkivet.se/campaign": "kyrk",
            "htrflow.riksarkivet.se/pipeline": "demo-v1",
        },
    },
    "data": {"volumes.txt": "vol0\thttps://iiif.example.org/vol0/manifest\n"},
}


def _stored(**data) -> dict:
    return {
        "metadata": {"name": "campaign-kyrk-status", "namespace": "htr-test"},
        "data": {
            "phase": "Succeeded",
            "volumesTotal": "3",
            "volumesDone": "3",
            "volumesFailed": "0",
            "startedAt": "2026-09-08T08:00:00Z",
            "finishedAt": "2026-09-08T10:00:00Z",
            "resultsBase": "https://results.example.org/htr-test/demo-v1",
            **data,
        },
    }


def test_a_reaped_campaign_is_a_row_like_any_other():
    row = projection.record_summary(RECORD, _stored(), CFG, MISSING_WARMUP)
    assert row["name"] == "kyrk"
    assert row["namespace"] == "htr-test"
    assert row["pipeline"] == "demo-v1"
    assert row["phase"] == "Succeeded"
    assert row["counts"] == {"total": 3, "active": 0, "done": 3, "failed": 0}
    assert row["finishedAt"] == "2026-09-08T10:00:00Z"
    assert row["resultsBase"] == "https://results.example.org/htr-test/demo-v1"
    assert row["jobGone"] is True
    assert set(row) == set(
        projection.summarize(_finished_job(), CFG, MISSING_WARMUP)
    ), "the row must be the same shape a live campaign's is"


def test_a_live_campaign_says_its_job_is_there():
    assert (
        projection.summarize(_finished_job(), CFG, MISSING_WARMUP)["jobGone"] is False
    )


def test_a_record_with_a_phase_this_api_never_writes_is_left_out():
    """Better no row than a guessed one: the page draws what the API says."""
    assert (
        projection.record_summary(RECORD, _stored(phase="Weird"), CFG, MISSING_WARMUP)
        is None
    )


def test_the_reaped_detail_carries_the_failures_the_record_kept():
    status = _stored(
        phase="PartiallyFailed",
        volumesFailed="1",
        failedVolumes='[{"id":"vol2","reason":"manifest 404"}]',
    )
    row = projection.record_summary(RECORD, status, CFG, MISSING_WARMUP)
    body = projection.record_detail(row, status, CFG, None)
    assert body["volumes"] == []  # the per-index states went with the Job
    assert body["latest"] is None
    assert len(body["failures"]) == 1
    failure = body["failures"][0]
    assert failure["id"] == "vol2"
    assert failure["state"] == "failed"
    assert failure["reason"]["error"] == "manifest 404"
    assert failure["iiifUrl"].endswith("/vol2/iiif.json")
    assert failure["logUrl"].endswith("/status/logs/demo-v1/vol2.txt")
    assert (body["pagesDone"], body["pagesTotal"]) == (0, 0)


def test_a_record_whose_failed_volumes_are_not_json_costs_nothing():
    status = _stored(failedVolumes="not json at all")
    row = projection.record_summary(RECORD, status, CFG, MISSING_WARMUP)
    assert projection.record_detail(row, status, CFG, None)["failures"] == []


def test_a_reaped_campaign_with_no_terminal_record_says_unknown():
    """The Job is gone, so nothing is running -- but the last thing anyone
    observed was a campaign still going. Reporting `Running` for ever is the
    one answer that is certainly wrong; the row says the outcome is not
    known and the card's chip says the Job was removed."""
    row = projection.record_summary(
        RECORD, _stored(phase="Running"), CFG, MISSING_WARMUP
    )
    assert row["phase"] == "Unknown"
    assert row["jobGone"] is True


def test_a_reaped_campaign_that_was_paused_is_unknown_too():
    row = projection.record_summary(
        RECORD, _stored(phase="Paused"), CFG, MISSING_WARMUP
    )
    assert row["phase"] == "Unknown"


def test_a_terminal_record_keeps_its_own_phase():
    for phase in projection.FINISHED_PHASES:
        row = projection.record_summary(
            RECORD, _stored(phase=phase), CFG, MISSING_WARMUP
        )
        assert row["phase"] == phase


# --- the record only ever gains (B76 review) ----------------------------


def test_an_empty_failed_volumes_never_replaces_a_stored_one():
    """A campaign's pods are garbage-collected long before its record is, so
    a later detail request legitimately sees no reasons at all -- and the
    record is the only place those sentences survive."""
    stored = {"failedVolumes": '[{"id":"vol2","reason":"manifest 404"}]'}
    merged = projection.merge_record(stored, {"failedVolumes": "[]"})
    assert merged["failedVolumes"] == stored["failedVolumes"]


def test_a_first_empty_failed_volumes_is_still_written():
    assert projection.merge_record({}, {"failedVolumes": "[]"}) == {
        "failedVolumes": "[]"
    }


def test_a_longer_failed_volumes_list_does_replace_the_stored_one():
    stored = {"failedVolumes": '[{"id":"vol2","reason":"manifest 404"}]'}
    fresh = '[{"id":"vol2","reason":"manifest 404"},{"id":"vol3","reason":"x"}]'
    assert (
        projection.merge_record(stored, {"failedVolumes": fresh})["failedVolumes"]
        == fresh
    )


def test_finished_at_never_moves_backwards_and_is_never_blanked():
    stored = {"finishedAt": "2026-09-08T10:00:00Z"}
    assert projection.merge_record(stored, {"finishedAt": ""}) == stored
    earlier = {"finishedAt": "2026-09-08T09:00:00Z"}
    assert projection.merge_record(stored, earlier) == stored
    later = {"finishedAt": "2026-09-08T11:00:00Z"}
    assert projection.merge_record(stored, later) == later


def test_everything_else_is_the_freshest_observation():
    stored = {"phase": "Running", "volumesDone": "1"}
    merged = projection.merge_record(stored, {"phase": "Succeeded", "volumesDone": "3"})
    assert merged == {"phase": "Succeeded", "volumesDone": "3"}


def test_a_pod_with_a_completion_index_that_is_not_a_number_is_skipped():
    """The label is written by the Job controller, but a hand-made pod (or a
    future field) can carry anything. One such pod used to take the whole
    campaign page down with a bare 500 (F1); it is simply not a row's pod."""
    pods = [
        {"metadata": {"labels": {"batch.kubernetes.io/job-completion-index": "x"}}},
        {"metadata": {"labels": {"batch.kubernetes.io/job-completion-index": "0"}}},
    ]
    body = projection.detail(
        _job(completed="", failed=""), _configmap(), pods, CFG, warmup=MISSING_WARMUP
    )
    states = [v["state"] for v in body["volumes"]]
    assert states[0] == "active", "index 0's pod still counts"
    assert set(states[1:]) == {"pending"}, "the unreadable label is nobody's index"


class TestTheRecordStaysUnderTheConfigMapLimit:
    """`failedVolumes` capped the number of entries but not their size, and
    a wrapper writes whatever its termination message said -- a Python
    traceback, a whole pod-template struct. Fifty of those is a ConfigMap
    the API server refuses (2026-09-14 audit)."""

    def _row(self) -> dict:
        return projection.summarize(_job(), CFG, MISSING_WARMUP)

    def _failures(self, n: int, error: str) -> list[dict]:
        return [
            {
                "id": f"vol{i}",
                "reason": {"stage": None, "permanent": None, "error": error},
            }
            for i in range(n)
        ]

    def test_one_reason_is_clipped_to_a_sentence(self):
        data = projection.status_record(self._row(), self._failures(1, "e" * 5000))
        (entry,) = json.loads(data["failedVolumes"])
        assert len(entry["reason"]) == projection.MAX_REASON

    def test_the_whole_field_stays_under_the_byte_cap(self):
        data = projection.status_record(self._row(), self._failures(50, "e" * 5000))
        blob = data["failedVolumes"]
        assert len(blob.encode()) <= projection.MAX_FAILED_VOLUMES
        assert json.loads(blob), "some failures are still recorded"

    def test_an_ordinary_set_of_failures_is_untouched(self):
        data = projection.status_record(self._row(), self._failures(3, "manifest 404"))
        assert json.loads(data["failedVolumes"]) == [
            {"id": f"vol{i}", "reason": "manifest 404"} for i in range(3)
        ]


class TestFinishedAtNeverMovesBackwards:
    """The read API and `htrflow-campaigns apply` both write this field and
    do not see the same clock, so the merge keeps the earlier instant. It
    compared the two as strings, which is not the same question once the two
    writers disagree about the offset (2026-09-14 audit)."""

    def _merged(self, stored: str, fresh: str) -> str:
        return projection.merge_record({"finishedAt": stored}, {"finishedAt": fresh})[
            "finishedAt"
        ]

    def test_a_later_wall_clock_in_another_offset_is_still_later(self):
        # 10:00+02:00 is 08:00Z -- earlier than the 09:00Z on record.
        assert self._merged("2026-01-01T09:00:00Z", "2026-01-01T10:00:00+02:00") == (
            "2026-01-01T09:00:00Z"
        )

    def test_a_genuinely_later_instant_still_wins(self):
        assert self._merged("2026-01-01T09:00:00Z", "2026-01-01T10:00:00Z") == (
            "2026-01-01T10:00:00Z"
        )

    def test_a_timestamp_without_an_offset_is_read_as_utc(self):
        assert self._merged("2026-01-01T09:00:00Z", "2026-01-01T08:00:00") == (
            "2026-01-01T09:00:00Z"
        )

    def test_a_value_that_is_not_a_timestamp_never_replaces_one(self):
        assert self._merged("2026-01-01T09:00:00Z", "soon") == "2026-01-01T09:00:00Z"

    def test_the_first_timestamp_is_taken_whatever_was_there(self):
        assert self._merged("not a date", "2026-01-01T09:00:00Z") == (
            "2026-01-01T09:00:00Z"
        )
