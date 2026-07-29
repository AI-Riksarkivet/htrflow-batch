from htrflow_reconciler.models import Volume
from htrflow_reconciler.plan import plan_submissions


def _vols(prefix, n):
    return [Volume(id=f"{prefix}{i}", manifest_url="http://m") for i in range(n)]


def test_round_robin_across_campaigns():
    pending = {"big": _vols("b", 5), "small": _vols("s", 2)}
    out = plan_submissions(pending, in_flight=0, window=4)
    ids = [v.id for _, v in out]
    assert ids == ["b0", "s0", "b1", "s1"]  # small campaign not starved


def test_window_minus_in_flight():
    pending = {"c": _vols("v", 10)}
    assert len(plan_submissions(pending, in_flight=18, window=20)) == 2
    assert plan_submissions(pending, in_flight=20, window=20) == []
    assert plan_submissions(pending, in_flight=25, window=20) == []


def test_empty_campaigns_skipped():
    out = plan_submissions({"a": [], "b": _vols("x", 1)}, 0, 5)
    assert [v.id for _, v in out] == ["x0"]
