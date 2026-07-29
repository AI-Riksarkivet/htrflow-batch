from htrflow_reconciler.models import Volume
from htrflow_reconciler.status import JobState, derive, job_name

V = Volume(id="R1", manifest_url="http://m")


def test_job_name_deterministic_and_k8s_safe():
    n = job_name("demo-v1", "R0001203")
    assert n == "htr-demo-v1-r0001203"
    long = job_name("demo-v1", "x" * 80)
    assert len(long) <= 63
    assert long == job_name("demo-v1", "x" * 80)  # stable


def test_done_wins_over_everything():
    jobs = {job_name("p", "R1"): JobState(active=True, failed=False, exit_code=None)}
    assert derive(V, "p", {"R1"}, jobs, {}, 3) == "done"


def _js(active=False, failed=False, exit_code=None):
    return JobState(active=active, failed=failed, exit_code=exit_code)


def test_running_and_queued():
    n = job_name("p", "R1")
    assert derive(V, "p", set(), {n: _js(active=True)}, {}, 3) == "running"
    assert derive(V, "p", set(), {n: _js()}, {}, 3) == "queued"


def test_failed_transient_below_cap_is_retry():
    n = job_name("p", "R1")
    jobs = {n: _js(failed=True, exit_code=1)}
    assert derive(V, "p", set(), jobs, {"R1": 1}, 3) == "retry"


def test_failed_permanent_is_needs_attention():
    n = job_name("p", "R1")
    jobs = {n: _js(failed=True, exit_code=13)}
    assert derive(V, "p", set(), jobs, {}, 3) == "needs-attention"


def test_failed_at_cap_is_needs_attention():
    n = job_name("p", "R1")
    jobs = {n: _js(failed=True, exit_code=1)}
    assert derive(V, "p", set(), jobs, {"R1": 3}, 3) == "needs-attention"


def test_no_job_no_result_is_pending():
    assert derive(V, "p", set(), {}, {}, 3) == "pending"
