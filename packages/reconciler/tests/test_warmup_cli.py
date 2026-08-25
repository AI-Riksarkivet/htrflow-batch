"""``python -m htrflow_reconciler.warmup``: the warm-up Job for one pipeline
as JSON on stdout, for the manual path (``values.pipelines`` + kubectl)."""

import json

from htrflow_reconciler.warmup import main


def test_cli_renders_the_warmup_job_for_kubectl(capsys):
    rc = main(
        [
            "--pipeline",
            "demo-v1",
            "--image",
            "r/i:tag",
            "--namespace",
            "ns1",
            "--data-pvc",
            "cache",
        ]
    )
    assert rc == 0
    job = json.loads(capsys.readouterr().out)
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == "htr-warmup-demo-v1"
    assert job["metadata"]["namespace"] == "ns1"
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == "r/i:tag"
    assert c["command"] == ["python", "-m", "htrflow_batch.warmup"]
    vols = {v["name"]: v for v in job["spec"]["template"]["spec"]["volumes"]}
    assert vols["data"]["persistentVolumeClaim"]["claimName"] == "cache"
    assert vols["pipeline"]["configMap"]["name"] == "htr-pipeline-demo-v1"
