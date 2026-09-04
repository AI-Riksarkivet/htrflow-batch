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
