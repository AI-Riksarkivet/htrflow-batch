"""Pure projection tests: plain dicts in, no cluster (docs: task-4-brief)."""

from __future__ import annotations

from types import SimpleNamespace

from htrflow_api import projection

CFG = SimpleNamespace(public_results_base="https://results.example.org")


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


def _pod(
    index: int, *, active=False, terminated_message=None, created="2026-01-01T00:00:01Z"
) -> dict:
    container_status = {"name": "wrapper", "state": {}}
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
        summary = projection.summarize(job, CFG)
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
        summary = projection.summarize(job, CFG)
        assert summary["resultsBase"] == "https://results.example.org/htr-batch/demo-v1"

    def test_phase_queued(self):
        job = _job(suspend=True, completed="", failed="")
        assert projection.summarize(job, CFG)["phase"] == "Queued"

    def test_phase_paused(self):
        job = _job(suspend=True, completed="0", failed="")
        assert projection.summarize(job, CFG)["phase"] == "Paused"

    def test_phase_succeeded(self):
        job = _job(conditions=[{"type": "Complete", "status": "True"}])
        assert projection.summarize(job, CFG)["phase"] == "Succeeded"

    def test_phase_failed(self):
        job = _job(conditions=[{"type": "Failed", "status": "True"}])
        assert projection.summarize(job, CFG)["phase"] == "Failed"


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
        d = projection.detail(job, configmap, pods, CFG, offset=0, limit=200)
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
        assert row3["reason"] == '{"permanent": true, "error": "manifest unsupported"}'
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
        d = projection.detail(job, configmap, pods, CFG, offset=0, limit=200)
        assert [f["index"] for f in d["failures"]] == [3]

    def test_failures_excludes_failed_index_without_reason(self):
        job = _job()
        configmap = _configmap()
        d = projection.detail(job, configmap, [], CFG, offset=0, limit=200)
        assert d["failures"] == []

    def test_paging(self):
        job = _job()
        configmap = _configmap()
        d = projection.detail(job, configmap, [], CFG, offset=5, limit=2)
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
        d = projection.detail(job, configmap, pods, CFG, offset=0, limit=200)
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == '{"permanent": true, "error": "manifest unsupported"}'

    def test_no_configmap_does_not_crash(self):
        d = projection.detail(_job(), None, [], CFG, offset=0, limit=200)
        assert d["volumes"] == []
        assert d["failures"] == []

    def test_configmap_fewer_lines_than_completions(self):
        d = projection.detail(_job(), _configmap(n=3), [], CFG, offset=0, limit=200)
        assert [v["index"] for v in d["volumes"]] == [0, 1, 2]

    def test_laststate_terminated_reason_fallback(self):
        pod = _pod(3)
        pod["status"]["containerStatuses"][0]["state"] = {"running": {}}
        pod["status"]["containerStatuses"][0]["lastState"] = {
            "terminated": {"exitCode": 1, "message": '{"permanent": false}'}
        }
        d = projection.detail(_job(), _configmap(), [pod], CFG, offset=0, limit=200)
        row3 = next(v for v in d["volumes"] if v["index"] == 3)
        assert row3["reason"] == '{"permanent": false}'


class TestConfigmapRef:
    def test_finds_campaign_volume(self):
        assert projection.configmap_ref(_job()) == "campaign-kyrk"

    def test_missing_campaign_volume(self):
        job = {"spec": {"template": {"spec": {"volumes": []}}}}
        assert projection.configmap_ref(job) is None
