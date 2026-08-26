from htrflow_reconciler.attempts import Attempt
from htrflow_reconciler.models import Volume
from htrflow_reconciler.status import JobState, derive, job_name

V = Volume(id="R1", manifest_url="http://m")


def test_job_name_deterministic_and_k8s_safe():
    n = job_name("demo-v1", "R0001203")
    assert n == "htr-demo-v1-r0001203-f7ceccba"
    long = job_name("demo-v1", "x" * 80)
    assert len(long) <= 63
    assert long == job_name("demo-v1", "x" * 80)  # stable
    # The flattened prefix is identical for both pairs; the pair digest is not.
    assert job_name("demo-v1", "R0001203") != job_name("demo", "v1-R0001203")
    assert job_name("a-b", "c") != job_name("a", "b-c")
    # Underscores and dots are sanitized to dashes, deterministically.
    assert job_name("demo_v1", "vol.1").startswith("htr-demo-v1-vol-1-")
    assert job_name("demo_v1", "vol.1") == job_name("demo_v1", "vol.1")


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
    assert derive(V, "p", set(), jobs, {"R1": Attempt(n=1)}, 3) == "retry"


def test_failed_permanent_is_needs_attention():
    n = job_name("p", "R1")
    jobs = {n: _js(failed=True, exit_code=13)}
    assert derive(V, "p", set(), jobs, {}, 3) == "needs-attention"


def test_failed_at_cap_is_needs_attention():
    n = job_name("p", "R1")
    jobs = {n: _js(failed=True, exit_code=1)}
    assert derive(V, "p", set(), jobs, {"R1": Attempt(n=3)}, 3) == "needs-attention"


def test_no_job_no_result_is_pending():
    assert derive(V, "p", set(), {}, {}, 3) == "pending"


def test_terminal_record_is_sticky_without_a_job():
    """R1: the Job is TTL-reaped after 24h; the verdict must outlive it."""
    att = {"R1": Attempt(n=0, terminal="exit-13")}
    assert derive(V, "p", set(), {}, att, 3) == "needs-attention"
    n = job_name("p", "R1")
    assert derive(V, "p", set(), {n: _js(active=True)}, att, 3) == "needs-attention"
    # done still wins: a re-run under the same id that finished is finished
    assert derive(V, "p", {"R1"}, {}, att, 3) == "done"


def test_exit_code_none_falls_back_to_job_failed_reason():
    """R6: the pod may be gone; the Job's Failed condition reason survives.
    ``PodFailurePolicy`` is what the FailJob-on-13 rule produces."""
    n = job_name("p", "R1")
    permanent = {n: JobState(active=False, failed=True, reason="PodFailurePolicy")}
    assert derive(V, "p", set(), permanent, {}, 3) == "needs-attention"
    transient = {n: JobState(active=False, failed=True, reason="BackoffLimitExceeded")}
    assert derive(V, "p", set(), transient, {}, 3) == "retry"
    unknown = {n: JobState(active=False, failed=True)}
    assert derive(V, "p", set(), unknown, {}, 3) == "retry"


def test_deleting_job_is_deleting_whatever_else_it_says():
    """R2: a Job under Foreground deletion is still listed Failed until its
    pod is gone; it must not be charged or resubmitted meanwhile."""
    n = job_name("p", "R1")
    jobs = {
        n: JobState(
            active=False,
            failed=True,
            exit_code=1,
            deletion_timestamp="2026-08-26T10:00:00Z",
        )
    }
    assert derive(V, "p", set(), jobs, {}, 3) == "deleting"


def test_jobstate_carries_the_campaign_label():
    assert (
        JobState(active=True, failed=False, campaign="trolldom").campaign == "trolldom"
    )
    assert JobState(active=True, failed=False).campaign is None
