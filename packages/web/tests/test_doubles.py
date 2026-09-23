"""The doubles cannot drift from the real adapter (audit T2).

`app.py` duck-types its reader, so nothing but this stops a fake from
answering a call the real one could not (or the other way round). Every
double is found rather than listed: a hand-kept list had missed seven of
the fakes the tests actually drive the routes with (2026-09-23 test
audit), and a new one is checked the moment it is written.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from htrflow_web.app import NoCluster
from htrflow_web.kube import Reader, ReaderLike

HERE = Path(__file__).parent

READER_METHODS = {
    name: inspect.signature(fn)
    for name, fn in vars(ReaderLike).items()
    if inspect.isfunction(fn) and not name.startswith("_")
}


def _doubles() -> dict[str, type]:
    """The real adapter, site-only mode's, and every class in the test
    modules that answers any call the routes make."""
    found: dict[str, type] = {"Reader": Reader, "NoCluster": NoCluster}
    for path in sorted(HERE.glob("test_*.py")):
        if path == Path(__file__):
            continue
        name = f"_doubles_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and obj.__module__ == name
                and any(hasattr(obj, method) for method in READER_METHODS)
            ):
                found[f"{path.stem}.{obj.__name__}"] = obj
    return found


DOUBLES = _doubles()


def test_the_search_finds_the_fakes_it_is_for():
    """The search is only as good as what it finds: one of each place a
    fake is written -- a base class, a subclass defined after the tests
    that use it, a hung one, and another module's."""
    assert {
        "Reader",
        "NoCluster",
        "test_app.FakeReader",
        "test_app.SsaReader",
        "test_app._Hung",
        "test_static.EmptyReader",
    } <= set(DOUBLES)


def _shape(fn) -> list[tuple[str, object]]:
    sig = fn if isinstance(fn, inspect.Signature) else inspect.signature(fn)
    return [(p.name, p.kind) for p in sig.parameters.values()]


@pytest.mark.parametrize("double", DOUBLES.values(), ids=DOUBLES.keys())
def test_every_reader_double_answers_the_calls_the_routes_make(double):
    """Each method is bound with the arguments `kube.ReaderLike` declares --
    a fake that dropped `namespace` fails here rather than in production."""
    assert READER_METHODS, "the protocol has methods to check"
    on_class = {
        name
        for base in double.__mro__
        for name in (*vars(base), *getattr(base, "__annotations__", {}))
    }
    for attr in ReaderLike.__annotations__:
        assert attr in on_class, f"{double.__name__} has no {attr}"
    for name, declared in READER_METHODS.items():
        impl = getattr(double, name, None)
        assert impl is not None, f"{double.__name__} has no {name}()"
        args = [f"<{p}>" for p in list(declared.parameters)[1:]]
        inspect.signature(impl).bind(double, *args)
        if impl is NoCluster._no_cluster:
            continue  # the catch-all takes anything, and refuses it
        # By name and kind too: `app.py` passes `force=` and `manager=` by
        # keyword, and a renamed parameter still binds positionally.
        assert _shape(impl) == _shape(declared), f"{double.__name__}.{name}"
