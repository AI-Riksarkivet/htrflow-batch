"""attempts.json v2: ``{"<pid>/<vid>": {"n": int, "terminal": str|null}}``.
v1 stored bare ints; they are migrated on read so a live bucket keeps its
budgets across the upgrade."""

from htrflow_reconciler.attempts import Attempt, dump_attempts, load_attempts


def test_v1_ints_migrate_to_records():
    got = load_attempts({"demo-v1/R1": 2})
    assert got == {"demo-v1/R1": Attempt(n=2, terminal=None)}


def test_v2_records_round_trip():
    raw = {"demo-v1/R1": {"n": 1, "terminal": "exit-13"}}
    got = load_attempts(raw)
    assert got["demo-v1/R1"].terminal == "exit-13"
    assert dump_attempts(got) == raw


def test_junk_records_are_dropped_not_fatal():
    got = load_attempts({"a": "junk", "b": None, "c": {"n": "x"}, "d": {"n": 1}})
    assert got == {"d": Attempt(n=1)}


def test_non_mapping_is_empty():
    assert load_attempts(None) == {}
    assert load_attempts(["x"]) == {}
