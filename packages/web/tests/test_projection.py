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

    def test_a_failed_index_whose_pod_is_gone_is_still_a_failure(self):
        """The index is in failedIndexes; nothing is left to say why. It is
        a failure all the same, and leaving it out of `failures` left it out
        of the record too -- where a reaped campaign then called it done
        (3074)."""
        d = projection.detail(_job(), _configmap(), [], CFG, warmup=MISSING_WARMUP)
        assert [f["index"] for f in d["failures"]] == [3]
        assert "reason" not in d["failures"][0]

    def test_a_pod_killed_without_a_message_says_how_it_was_killed(self):
        """An OOM kill leaves no termination message: the wrapper never got
        to write one. The kubelet's own reason and exit code are what there
        is, and a failure with them is one a reader can act on (3074)."""
        pod = _pod(3)
        pod["status"]["containerStatuses"][0]["state"] = {
            "terminated": {"exitCode": 137, "reason": "OOMKilled"}
        }
        d = projection.detail(_job(), _configmap(), [pod], CFG, warmup=MISSING_WARMUP)
        assert d["failures"][0]["reason"] == {
            "stage": None,
            "permanent": None,
            "error": "OOMKilled (exit code 137)",
        }

    def test_an_evicted_pod_is_named_by_the_pods_own_reason(self):
        pod = _pod(3)
        pod["status"]["reason"] = "Evicted"
        pod["status"]["containerStatuses"][0]["state"] = {
            "terminated": {"exitCode": 137, "reason": "ContainerStatusUnknown"}
        }
        d = projection.detail(_job(), _configmap(), [pod], CFG, warmup=MISSING_WARMUP)
        assert d["failures"][0]["reason"]["error"] == "Evicted"

    def test_a_pod_that_exited_cleanly_has_no_reason(self):
        pod = _pod(0)
        pod["status"]["containerStatuses"][0]["state"] = {
            "terminated": {"exitCode": 0, "reason": "Completed"}
        }
        d = projection.detail(_job(), _configmap(), [pod], CFG, warmup=MISSING_WARMUP)
        assert "reason" not in d["volumes"][0]

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

    def test_the_rows_in_the_answer_are_fetched_before_the_rest(self):
        """The rows a reader is looking at first -- its one failure (vol3)
        and `latest` (vol5) ahead of the page (vol0, vol1) -- then the rest
        of the campaign's run volumes, for the campaign's totals. Pending
        ones have nothing in the bucket and are never asked (3076)."""
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
        assert [v for v, _ in asked] == ["vol3", "vol5", "vol0", "vol1", "vol2"]

    def test_at_most_the_cap_is_fetched_even_when_the_page_is_bigger(self):
        """A `limit=1000` page must not turn into a thousand sequential GETs
        through one client (finding 5)."""
        n = projection.PROGRESS_FETCH_CAP + 40
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
        n = projection.PROGRESS_FETCH_CAP + 40
        fetch, asked = self._fetch({})
        pods = [_pod(i, active=True) for i in range(n - 3, n)]  # last 3 running
        projection.detail(
            _job(completions=n, active=3, completed=f"0-{n - 4}", failed=""),
            _configmap(n=n),
            pods,
            CFG,
            offset=0,
            limit=n,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        asked_ids = {v for v, _ in asked}
        assert {f"vol{i}" for i in range(n - 3, n)} <= asked_ids
        assert len(asked) == projection.PROGRESS_FETCH_CAP

    def test_the_totals_are_the_whole_campaigns_however_big_it_is(self):
        """500 volumes, one of which -- far past the cap and the page --
        lost pages. Summed over the <=32 rows fetched, the campaign's totals
        jumped between polls and it read as clean (3076). Cached answers
        cost nothing, so once every run volume has been read the totals
        are every volume's, and the coverage says so."""
        n = 500
        known = {
            f"vol{i}": _progress(done=10, total=10, failed=int(i == 300))
            for i in range(n)
        }
        network: list[str] = []

        def fetch(base, vol_id, state):
            network.append(vol_id)
            return known[vol_id]

        def cached(base, vol_id, state):
            return True, known[vol_id]

        d = projection.detail(
            _job(completions=n, active=0, completed=f"0-{n - 1}", failed=""),
            _configmap(n=n),
            [],
            CFG,
            limit=200,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
            cached_progress=cached,
        )
        assert network == [], "every answer was in the cache"
        assert (d["pagesDone"], d["pagesTotal"], d["pagesFailed"]) == (
            n * 10,
            n * 10,
            1,
        )
        assert d["pagesCoverage"] == {"counted": n, "of": n}

    def test_only_network_misses_count_against_the_cap(self):
        n = 4 * projection.PROGRESS_FETCH_CAP
        cache = {f"vol{i}": _progress(done=1, total=1) for i in range(0, n, 2)}
        network: list[str] = []

        def fetch(base, vol_id, state):
            network.append(vol_id)
            return _progress(done=1, total=1)

        def cached(base, vol_id, state):
            return (True, cache[vol_id]) if vol_id in cache else (False, None)

        d = projection.detail(
            _job(completions=n, active=0, completed=f"0-{n - 1}", failed=""),
            _configmap(n=n),
            [],
            CFG,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
            cached_progress=cached,
        )
        assert len(network) == projection.PROGRESS_FETCH_CAP
        assert d["pagesCoverage"] == {
            "counted": n // 2 + projection.PROGRESS_FETCH_CAP,
            "of": n,
        }
        assert d["pagesTotal"] == d["pagesCoverage"]["counted"]

    def test_a_bucket_that_answered_nothing_does_not_count_as_read(self):
        """A GET that failed is not an answer: coverage must not reach the
        whole campaign on a bucket that never replied, or the page would
        call a campaign clean that nobody could read."""
        d = projection.detail(
            _job(),
            _configmap(),
            [],
            CFG,
            warmup=MISSING_WARMUP,
            fetch_progress=lambda *_a: None,
        )
        assert d["pagesCoverage"] == {"counted": 0, "of": 5}

    def test_the_deadline_is_spent_on_the_running_rows_first(self, monkeypatch):
        """The deadline loop walked rows in page order, so a slow bucket
        spent the budget on done rows while the running ones -- the numbers
        actually moving -- got none (3076)."""
        ticks = iter(range(100))
        monkeypatch.setattr(projection.time, "monotonic", lambda: float(next(ticks)))
        n = 40
        fetch, asked = self._fetch({})
        pods = [_pod(i, active=True) for i in range(n - 3, n)]
        projection.detail(
            _job(completions=n, active=3, completed=f"0-{n - 4}", failed=""),
            _configmap(n=n),
            pods,
            CFG,
            limit=n,
            warmup=MISSING_WARMUP,
            fetch_progress=fetch,
        )
        assert {v for v, _ in asked[:3]} == {"vol37", "vol38", "vol39"}

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
    "jobUid",
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
    "data": {
        "volumes.txt": (
            "vol0\thttps://iiif.example.org/vol0/manifest\n"
            "vol1\timages:https://img.example.org/a.jpg https://img.example.org/b.jpg\n"
            "vol2\thttps://iiif.example.org/vol2/manifest\n"
        )
    },
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


def test_a_reaped_rows_links_come_from_the_config_this_process_has():
    """The record keeps the base the campaign ran under, and it is worth
    keeping -- but it is informational. The row's links and the progress
    this process fetches for them have to be the same place, and the
    progress base was always derived while the links were not (2026-09-14
    review): a bucket that moved gave a page of links to the old one."""
    status = _stored(resultsBase="https://moved.example.org/old/demo-v1")
    row = projection.record_summary(RECORD, status, CFG, MISSING_WARMUP)
    assert row["resultsBase"] == "https://results.example.org/htr-test/demo-v1"
    body = projection.record_detail(row, RECORD, status, CFG, None)
    assert body["volumes"][0]["iiifUrl"].startswith(row["resultsBase"])


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


class TestTheReapedDetailStillHasItsVolumes:
    """The Job's completedIndexes went with the Job, but manifest.json,
    iiif.json, alto/ and the run log are all still in the bucket -- and the
    campaign page showed no rows at all, so nobody could open any of them
    (R1, the product owner, 2026-09-14). The rows are rebuilt from what
    survives: volumes.txt on the campaign record, the failures the status
    record kept, and the phase for everything else."""

    def _body(self, status: dict, **kwargs) -> dict:
        row = projection.record_summary(RECORD, status, CFG, MISSING_WARMUP)
        return projection.record_detail(row, RECORD, status, CFG, None, **kwargs)

    def _failed(self) -> dict:
        return _stored(
            phase="PartiallyFailed",
            volumesDone="2",
            volumesFailed="1",
            failedVolumes='[{"id":"vol2","reason":"manifest 404"}]',
        )

    def test_every_volume_the_campaign_ran_is_a_row_again(self):
        body = self._body(self._failed())
        assert [v["id"] for v in body["volumes"]] == ["vol0", "vol1", "vol2"]
        assert [v["index"] for v in body["volumes"]] == [0, 1, 2]

    def test_the_links_are_the_ones_a_live_row_would_carry(self):
        vol0 = self._body(self._failed())["volumes"][0]
        base = "https://results.example.org/htr-test/demo-v1"
        assert vol0["manifestUrl"] == f"{base}/vol0/manifest.json"
        assert vol0["iiifUrl"] == f"{base}/vol0/iiif.json"
        assert vol0["altoPrefix"] == f"{base}/vol0/alto/"
        assert vol0["logUrl"].endswith("/status/logs/demo-v1/vol0.txt")
        assert vol0["sourceUrl"] == "https://iiif.example.org/vol0/manifest"

    def test_an_images_volume_has_no_source_manifest_to_open(self):
        assert self._body(self._failed())["volumes"][1]["sourceUrl"] is None

    def test_a_volume_the_record_named_is_failed_with_its_reason(self):
        vol2 = self._body(self._failed())["volumes"][2]
        assert vol2["state"] == "failed"
        assert vol2["reason"]["error"] == "manifest 404"

    def test_every_other_volume_took_the_campaigns_own_ending(self):
        assert [v["state"] for v in self._body(self._failed())["volumes"]] == [
            "done",
            "done",
            "failed",
        ]

    def test_an_outcome_nobody_recorded_leaves_its_rows_unknown(self):
        """`Unknown` is the record's own word for "nobody wrote down how
        this ended". Calling every row `done` said the opposite, and
        contradicted the record's own volumesFailed whenever the detail
        endpoint never got to name the failures (2026-09-14 review)."""
        status = _stored(phase="Running", finishedAt="", volumesFailed="1")
        body = self._body(status)
        assert body["phase"] == "Unknown"
        assert [v["state"] for v in body["volumes"]] == ["unknown"] * 3
        assert body["latest"] is None, "nothing is known to have finished"

    def test_a_volume_the_record_named_is_failed_even_then(self):
        status = _stored(
            phase="Running",
            finishedAt="",
            volumesFailed="1",
            failedVolumes='[{"id":"vol2","reason":"manifest 404"}]',
        )
        states = [v["state"] for v in self._body(status)["volumes"]]
        assert states == ["unknown", "unknown", "failed"]

    def test_a_campaign_that_failed_outright_has_no_done_rows(self):
        status = _stored(phase="Failed", volumesDone="0", volumesFailed="3")
        assert {v["state"] for v in self._body(status)["volumes"]} == {"failed"}

    def test_the_failures_list_is_the_failed_rows_themselves(self):
        body = self._body(self._failed())
        assert [v["id"] for v in body["failures"]] == ["vol2"]
        assert body["failures"][0]["index"] == 2, "its real index, not its rank"

    def test_the_folded_card_shows_the_last_volume_that_finished(self):
        assert self._body(self._failed())["latest"]["id"] == "vol1"

    def test_the_rows_are_paged_like_a_live_campaigns(self):
        body = self._body(self._failed(), offset=1, limit=1)
        assert [v["id"] for v in body["volumes"]] == ["vol1"]
        assert [v["id"] for v in body["failures"]] == ["vol2"], "never paged"

    def test_the_pages_come_from_the_bucket_like_a_live_campaigns(self):
        """A done volume's manifest.json is still there, and it is what says
        the viewer manifest was published."""
        asked: list[tuple[str, str]] = []

        def fetch(base: str, vol_id: str, state: str) -> dict | None:
            asked.append((vol_id, state))
            return _progress(done=4, total=4, stage="done") if state == "done" else None

        body = self._body(self._failed(), fetch_progress=fetch)
        assert ("vol0", "done") in asked
        assert body["pagesTotal"] == 8, "the two done volumes"
        assert body["volumes"][0]["progress"]["done"] == 4

    def test_a_failure_nobody_could_name_leaves_the_other_rows_unknown(self):
        """The record counts two failed volumes and names one. Which of the
        other two failed is not written down anywhere, so neither is `done`
        -- that claimed an OOM-killed volume had finished (3074)."""
        status = _stored(
            phase="PartiallyFailed",
            volumesDone="1",
            volumesFailed="2",
            failedVolumes='[{"id":"vol2","reason":"manifest 404"}]',
        )
        states = [v["state"] for v in self._body(status)["volumes"]]
        assert states == ["unknown", "unknown", "failed"]

    def test_a_failure_recorded_without_a_reason_is_failed_with_none(self):
        """The live page named it failed with no message; the record keeps
        exactly that -- a failed row, not one with an empty sentence."""
        status = _stored(
            phase="PartiallyFailed",
            volumesDone="2",
            volumesFailed="1",
            failedVolumes='[{"id":"vol0","reason":""}]',
        )
        body = self._body(status)
        assert [v["state"] for v in body["volumes"]] == ["failed", "done", "done"]
        assert "reason" not in body["volumes"][0]
        assert [f["id"] for f in body["failures"]] == ["vol0"]

    def test_a_record_whose_failed_volumes_are_not_json_costs_nothing(self):
        body = self._body(_stored(failedVolumes="not json at all"))
        assert body["failures"] == []
        assert [v["state"] for v in body["volumes"]] == ["done"] * 3

    def test_a_campaign_with_no_record_of_its_volumes_still_answers(self):
        """The campaign ConfigMap is gone (a prune took it) but the status
        one is not: no rows, and the page says what it knows."""
        row = projection.record_summary(RECORD, self._failed(), CFG, MISSING_WARMUP)
        body = projection.record_detail(row, None, self._failed(), CFG, None)
        assert body["volumes"] == []
        assert body["latest"] is None


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


def test_two_disjoint_failed_volumes_lists_are_both_kept():
    """The pods of the first failure are collected before the second one
    happens, so each detail request names a different volume. Compared as
    strings the later list replaced the earlier one, and vol5's reason was
    gone for good (3075)."""
    stored = {"failedVolumes": '[{"id":"vol5","reason":"manifest 404"}]'}
    fresh = {"failedVolumes": '[{"id":"vol900","reason":"OOMKilled (exit code 137)"}]'}
    merged = json.loads(projection.merge_record(stored, fresh)["failedVolumes"])
    assert merged == [
        {"id": "vol900", "reason": "OOMKilled (exit code 137)"},
        {"id": "vol5", "reason": "manifest 404"},
    ]


def test_the_newest_reason_for_a_volume_wins_but_a_blank_never_erases_one():
    stored = {
        "failedVolumes": (
            '[{"id":"vol1","reason":"SIGTERM"},{"id":"vol2","reason":"manifest 404"}]'
        )
    }
    fresh = {
        "failedVolumes": (
            '[{"id":"vol1","reason":"DeadlineExceeded"},{"id":"vol2","reason":""}]'
        )
    }
    merged = json.loads(projection.merge_record(stored, fresh)["failedVolumes"])
    assert merged == [
        {"id": "vol1", "reason": "DeadlineExceeded"},
        {"id": "vol2", "reason": "manifest 404"},
    ]


def test_the_merged_failed_volumes_are_capped_like_a_fresh_list():
    def listed(ids) -> str:
        return json.dumps([{"id": f"vol{i}", "reason": "x"} for i in ids])

    stored = {"failedVolumes": listed(range(40))}
    fresh = {"failedVolumes": listed(range(100, 140))}
    merged = json.loads(projection.merge_record(stored, fresh)["failedVolumes"])
    assert len(merged) == 50
    assert merged[0]["id"] == "vol100", "the fresh observation first"


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


def test_a_volume_id_is_encoded_into_every_url_a_row_carries():
    """Volume ids come off a campaign's volumes.txt, a file people edit in a
    git repo. The progress URL encoded them and the row's own links did not,
    so `../` in an id walked out of the campaign's prefix in the href a
    reader clicks (2026-09-14 review)."""
    cm = {
        "metadata": {"name": "campaign-kyrk", "namespace": "htr-test"},
        "data": {"volumes.txt": "../../status\thttps://iiif.example.org/x\n"},
    }
    row = projection.detail(
        _job(completed="", failed=""), cm, [], CFG, warmup=MISSING_WARMUP
    )["volumes"][0]
    assert row["id"] == "../../status", "the id itself is the id"
    base = "https://results.example.org/htr-test/demo-v1"
    assert row["manifestUrl"] == f"{base}/..%2F..%2Fstatus/manifest.json"
    assert row["iiifUrl"] == f"{base}/..%2F..%2Fstatus/iiif.json"
    assert row["altoPrefix"] == f"{base}/..%2F..%2Fstatus/alto/"
    assert row["logUrl"].endswith("/status/logs/demo-v1/..%2F..%2Fstatus.txt")


def test_an_ordinary_volume_id_is_left_alone_in_its_urls():
    row = projection.detail(
        _job(completed="", failed=""), _configmap(n=1), [], CFG, warmup=MISSING_WARMUP
    )["volumes"][0]
    base = "https://results.example.org/htr-test/demo-v1"
    assert row["manifestUrl"] == f"{base}/vol0/manifest.json"
    assert row["logUrl"] == "https://results.example.org/status/logs/demo-v1/vol0.txt"


def test_an_oom_killed_volume_is_failed_live_and_after_the_reap():
    """The whole path of 3074: an index killed before its wrapper could say
    why is a failure on the live page, is written into the record, and is
    still a failure once the Job -- and every pod -- is gone."""
    job = _job(
        completions=3,
        active=0,
        completed="1-2",
        failed="0",
        conditions=[{"type": "Failed", "status": "True"}],
    )
    pod = _pod(0)
    pod["status"]["containerStatuses"][0]["state"] = {
        "terminated": {"exitCode": 137, "reason": "OOMKilled"}
    }
    live = projection.detail(job, _configmap(n=3), [pod], CFG, warmup=MISSING_WARMUP)
    assert live["phase"] == "PartiallyFailed"
    assert [f["id"] for f in live["failures"]] == ["vol0"]

    status = {"data": projection.status_record(live, live["failures"])}
    record = {**RECORD, "data": {"volumes.txt": _configmap(n=3)["data"]["volumes.txt"]}}
    row = projection.record_summary(record, status, CFG, MISSING_WARMUP)
    reaped = projection.record_detail(row, record, status, CFG, None)
    assert [v["state"] for v in reaped["volumes"]] == ["failed", "done", "done"]
    assert reaped["failures"][0]["reason"]["error"] == "OOMKilled (exit code 137)"


# --- who owns which field of the record (3075, 3081) ---------------------


def _managed(*keys: str, manager: str = "htrflow-campaigns") -> dict:
    """One managedFields entry, as the API server records a server-side
    apply: the data keys and labels that manager set."""
    return {
        "manager": manager,
        "operation": "Apply",
        "fieldsType": "FieldsV1",
        "fieldsV1": {
            "f:data": {f"f:{k}": {} for k in keys},
            "f:metadata": {"f:labels": {"f:htrflow.riksarkivet.se/kind": {}}},
        },
    }


APPLY_KEYS = (*sorted(STATUS_FIELDS),)


def _running_row(**status) -> dict:
    job = _job(completed="0-1", failed="", **status)
    return projection.summarize(job, CFG, MISSING_WARMUP)


def _stored_cm(data: dict, *managed: dict, rv: str = "41") -> dict:
    return {
        "metadata": {
            "name": "campaign-kyrk-status",
            "namespace": "htr-test",
            "resourceVersion": rv,
            "managedFields": list(managed),
        },
        "data": data,
    }


class TestRecordWrite:
    def test_a_first_record_is_the_whole_observation_unforced(self):
        row = _running_row()
        fresh = projection.status_record(row, job_uid="uid-1")
        body, force = projection.record_write(None, row, fresh)
        assert body["data"] == projection.merge_record({}, fresh)
        assert body["metadata"]["labels"]["htrflow.riksarkivet.se/kind"] == "status"
        assert force is False

    def test_an_unchanged_record_is_not_written(self):
        row = _running_row()
        fresh = projection.status_record(row, job_uid="uid-1")
        assert projection.record_write(_stored_cm(dict(fresh)), row, fresh) is None

    def test_once_apply_recorded_the_ending_only_failed_volumes_are_sent(self):
        """`htrflow-campaigns apply` force-owns the ending it read off the
        Job. Sending those fields again, unforced, with any value that
        differs -- a trailing slash on resultsBase, a campaign label -- was a
        409 on every poll, and failedVolumes, the one field only this API
        can write, never landed (3081). Its keys and labels are left out of
        the body; server-side apply keeps them, since apply still owns them."""
        row = projection.summarize(_finished_job(), CFG, MISSING_WARMUP)
        theirs = {
            **projection.status_record(row, job_uid="uid-1"),
            "resultsBase": "https://results.example.org//htr-test/demo-v1",
        }
        stored = _stored_cm(theirs, _managed(*APPLY_KEYS))
        failures = [{"id": "vol2", "reason": {"error": "manifest 404"}}]
        fresh = projection.status_record(row, failures, job_uid="uid-1")
        body, force = projection.record_write(stored, row, fresh)
        assert body["data"] == {
            "failedVolumes": '[{"id":"vol2","reason":"manifest 404"}]'
        }
        assert "labels" not in body["metadata"]
        assert force is False

    def test_a_record_of_another_job_is_replaced_not_merged(self):
        """The record carried no Job uid, so a Job recreated under the same
        name inherited the old one's failures and finishedAt -- and never
        shrinking, kept them (3075). A different uid is a different run:
        nothing stored is about this one."""
        old = {
            **projection.status_record(
                projection.summarize(_finished_job(), CFG, MISSING_WARMUP),
                [{"id": "vol2", "reason": {"error": "manifest 404"}}],
                job_uid="uid-old",
            ),
            "finishedAt": "2026-09-08T10:00:00Z",
        }
        row = _running_row()
        fresh = projection.status_record(row, job_uid="uid-new")
        body, _ = projection.record_write(_stored_cm(old), row, fresh)
        assert body["data"] == fresh
        assert "failedVolumes" not in body["data"]
        assert body["data"]["finishedAt"] == ""

    def test_taking_another_jobs_record_over_from_apply_is_forced_on_a_version(
        self,
    ):
        """apply owns the old run's fields, and an unforced write of this
        run's values would be refused for ever. Forced -- but only on the
        version this request read, so an ending apply writes in between is
        a 409 rather than overwritten."""
        old = {"phase": "Succeeded", "jobUid": "uid-old"}
        stored = _stored_cm(old, _managed("phase", "jobUid"), rv="99")
        row = _running_row()
        fresh = projection.status_record(row, job_uid="uid-new")
        body, force = projection.record_write(stored, row, fresh)
        assert force is True
        assert body["metadata"]["resourceVersion"] == "99"
        assert body["data"]["phase"] == "Running"

    def test_a_record_written_before_the_uid_existed_is_this_jobs(self):
        """Every record on a cluster upgraded to this has no jobUid. Read as
        another Job's, each would be wiped along with the only copy of its
        failure reasons; read as this one's, it is merged as before."""
        kept = '[{"id":"vol1","reason":"manifest 404"}]'
        row = _running_row()
        fresh = projection.status_record(row, [], job_uid="uid-1")
        stored = _stored_cm({"phase": "Running", "failedVolumes": kept})
        body, force = projection.record_write(stored, row, fresh)
        assert body["data"]["failedVolumes"] == kept
        assert body["data"]["jobUid"] == "uid-1"
        assert force is False
