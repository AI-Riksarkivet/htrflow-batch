"""Config.from_env: the web front's whole env contract (docs: configuration.md)."""

from __future__ import annotations

import pytest

from htrflow_web.kube import Config


def test_missing_results_base_raises():
    with pytest.raises(RuntimeError, match="HTRFLOW_PUBLIC_RESULTS_BASE is required"):
        Config.from_env({})


def test_site_only_does_not_require_a_results_base():
    Config.from_env({"HTRFLOW_WEB_SITE_ONLY": "1"})  # must not raise


def test_site_only_zero_still_counts_as_true():
    """``from_env`` treats any non-empty value as true -- including the
    string "0" -- so a results base is still not required."""
    Config.from_env({"HTRFLOW_WEB_SITE_ONLY": "0"})  # must not raise


def test_results_base_trailing_slash_is_stripped():
    cfg = Config.from_env({"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x/results/"})
    assert cfg.public_results_base == "http://x/results"


def test_namespaces_splits_on_comma_and_strips():
    cfg = Config.from_env(
        {"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x", "HTRFLOW_NAMESPACES": "a, b"}
    )
    assert cfg.namespaces == ("a", "b")


def test_static_dir_passes_through():
    cfg = Config.from_env(
        {"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x", "HTRFLOW_WEB_STATIC": "/site"}
    )
    assert cfg.static_dir == "/site"


def test_internal_results_base_defaults_to_the_public_one():
    """The API pod's own ProgressReader must reach the bucket even when
    nobody set HTRFLOW_INTERNAL_RESULTS_BASE -- true on real AWS, where the
    same URL really does work from inside the cluster."""
    cfg = Config.from_env({"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x/results"})
    assert cfg.internal_results_base == "http://x/results"


def test_internal_results_base_can_differ_from_the_public_one():
    """The PoC: publicResultsBase is a localhost URL reached through an SSH
    forward, which the pod itself cannot resolve to anything but itself."""
    cfg = Config.from_env(
        {
            "HTRFLOW_PUBLIC_RESULTS_BASE": "http://localhost:30900/htr-results",
            "HTRFLOW_INTERNAL_RESULTS_BASE": (
                "http://rustfs.htr-batch.svc.cluster.local:9000/htr-results/"
            ),
        }
    )
    assert (
        cfg.internal_results_base
        == "http://rustfs.htr-batch.svc.cluster.local:9000/htr-results"
    )
