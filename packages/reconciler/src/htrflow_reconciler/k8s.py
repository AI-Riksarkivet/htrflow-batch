"""k8s adapter — thin shell over kubernetes client. In-cluster config in
the CronJob; kubeconfig fallback for local dev."""

from __future__ import annotations

from typing import Any

from kubernetes import client, config

from .status import JobState


def _exit_code(pod: Any) -> int | None:
    for cs in pod.status.container_statuses or []:
        term = cs.state.terminated
        if term is not None:
            return term.exit_code
    return None


def _newest_first(pods: list[Any]) -> list[Any]:
    """Pods newest-first, so the exit code and the logs describe the SAME pod."""
    return sorted(pods, key=lambda p: p.metadata.creation_timestamp, reverse=True)


class Cluster:
    def __init__(self, namespace: str = "htr-batch") -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.ns = namespace
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def jobs(self) -> dict[str, JobState]:
        """Snapshot of the reconciler's volume Jobs in this namespace, by name.

        The selector carries ``batch.htrflow/managed-by=reconciler`` as well as
        the ``app`` label: hand-run Jobs share the ``app`` label (the operators'
        selectors need it) and have no TTL, so without the extra term they would
        occupy submission-window slots forever.
        """
        return self._job_states(
            "app=htrflow-batch,batch.htrflow/managed-by=reconciler",
            key=lambda j: j.metadata.name,
        )

    def warmups(self) -> dict[str, JobState]:
        """Snapshot of warm-up Jobs, by pipeline id.

        Selected on ``app`` alone: the chart renders the same Job for
        ``values.pipelines`` under Helm's ownership, and either origin is
        proof the cache is warm.
        """
        return self._job_states(
            "app=htrflow-warmup",
            key=lambda j: (j.metadata.labels or {}).get("batch.htrflow/pipeline", ""),
        )

    def _job_states(self, label_selector: str, key) -> dict[str, JobState]:
        """``failed`` is read off the Job's TERMINAL ``Failed`` condition — the
        backoffLimit is exhausted — never off ``status.failed`` pod counts,
        which also tick up while a Job is still retrying (see JobState).
        ``succeeded`` is its mirror, the terminal ``Complete`` condition.
        """
        out: dict[str, JobState] = {}
        jobs = self.batch.list_namespaced_job(self.ns, label_selector=label_selector)
        for j in jobs.items:
            conditions = j.status.conditions or []
            failed = any(c.type == "Failed" and c.status == "True" for c in conditions)
            succeeded = any(
                c.type == "Complete" and c.status == "True" for c in conditions
            )
            exit_code = None
            if failed:
                pods = _newest_first(
                    self.core.list_namespaced_pod(
                        self.ns,
                        label_selector=f"batch.kubernetes.io/job-name={j.metadata.name}",
                    ).items
                )
                codes = [_exit_code(p) for p in pods]
                exit_code = next((c for c in codes if c is not None), None)
            out[key(j)] = JobState(
                active=bool(j.status.active),
                failed=failed,
                succeeded=succeeded,
                exit_code=exit_code,
            )
        return out

    def create_job(self, job: dict) -> None:
        try:
            self.batch.create_namespaced_job(self.ns, job)
        except client.ApiException as e:
            if e.status != 409:  # AlreadyExists is a harmless race (spec §7)
                raise

    def delete_job(self, name: str) -> None:
        try:
            self.batch.delete_namespaced_job(
                self.ns, name, propagation_policy="Foreground"
            )
        except client.ApiException as e:
            if e.status != 404:
                raise

    def get_configmap_steps(self, pipeline_id: str) -> str | None:
        try:
            cm = self.core.read_namespaced_config_map(
                f"htr-pipeline-{pipeline_id}", self.ns
            )
            return (cm.data or {}).get("pipeline.yaml")
        except client.ApiException as e:
            if e.status == 404:
                return None
            raise

    def ensure_configmap(self, pipeline_id: str, steps_yaml: str) -> None:
        body = {
            "metadata": {"name": f"htr-pipeline-{pipeline_id}"},
            "data": {"pipeline.yaml": steps_yaml},
        }
        try:
            self.core.create_namespaced_config_map(self.ns, body)
        except client.ApiException as e:
            if e.status != 409:
                raise

    def job_logs(self, name: str, tail: int = 50) -> str:
        pods = self.core.list_namespaced_pod(
            self.ns, label_selector=f"batch.kubernetes.io/job-name={name}"
        ).items
        if not pods:
            return ""
        pod = _newest_first(pods)[0]
        try:
            return self.core.read_namespaced_pod_log(
                pod.metadata.name, self.ns, tail_lines=tail
            )
        except client.ApiException:
            return ""
