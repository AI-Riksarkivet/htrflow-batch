"""Tests for driver.py without requiring htrflow installed (import-guarded).

Uses monkeypatch.setitem(sys.modules, ...) to fake htrflow modules."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest


def test_load_pipeline_path_api(tmp_path, monkeypatch):
    """Test load_pipeline with newer htrflow API: from_config(path_str)."""
    # Setup fake htrflow modules
    mock_export_class = type("Export", (), {})
    mock_export_instances = []

    def mock_export_init(self, dest, fmt):
        self.dest = dest
        self.fmt = fmt
        mock_export_instances.append(self)

    mock_export_class.__init__ = mock_export_init

    called_with = []

    class MockPipeline:
        def __init__(self):
            self.steps = []

        @staticmethod
        def from_config(config):
            called_with.append(config)
            return MockPipeline()

    # Inject fake modules
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)

    # Create a dummy pipeline YAML
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")

    # Import and run (after modules are faked)
    from htrflow_batch.driver import load_pipeline

    pipeline = load_pipeline(str(pipeline_yaml), out_dir)

    # Verify: from_config was called with the path string
    assert called_with == [str(pipeline_yaml)]
    # Verify: two Export steps were appended
    assert len(mock_export_instances) == 2
    assert mock_export_instances[0].fmt == "alto"
    assert mock_export_instances[1].fmt == "page"
    # Verify: Export destinations are under out_dir
    assert str(out_dir / "alto") in mock_export_instances[0].dest
    assert str(out_dir / "page") in mock_export_instances[1].dest


def test_load_pipeline_dict_fallback(tmp_path, monkeypatch):
    """Test load_pipeline with older htrflow API: from_config(config_dict).

    Simulates fallback when path-based API raises TypeError."""
    mock_export_class = type("Export", (), {})
    mock_export_instances = []

    def mock_export_init(self, dest, fmt):
        self.dest = dest
        self.fmt = fmt
        mock_export_instances.append(self)

    mock_export_class.__init__ = mock_export_init

    called_with = []

    class MockPipeline:
        def __init__(self):
            self.steps = []

        @staticmethod
        def from_config(config):
            called_with.append(config)
            # Older API: raise TypeError when given a string
            if isinstance(config, str):
                raise TypeError("string indices must be integers")
            return MockPipeline()

    # Inject fake modules
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)

    # Create a real YAML file with a steps key
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")

    # Import and run
    from htrflow_batch.driver import load_pipeline

    pipeline = load_pipeline(str(pipeline_yaml), out_dir)

    # Verify: from_config was called twice (first with path str, then with dict)
    assert len(called_with) == 2
    assert called_with[0] == str(pipeline_yaml)  # First call (path)
    assert isinstance(called_with[1], dict)  # Second call (dict)
    assert "steps" in called_with[1]  # Dict has "steps" key
    # Verify: two Export steps were appended
    assert len(mock_export_instances) == 2


def test_load_pipeline_rejects_export_steps(tmp_path, monkeypatch):
    """Test load_pipeline rejects pipelines that already contain Export steps."""
    mock_export_class = type("Export", (), {})

    class MockPipeline:
        def __init__(self, has_export=False):
            self.has_export = has_export
            if has_export:
                self.steps = [mock_export_class()]
            else:
                self.steps = []

        @staticmethod
        def from_config(config):
            return MockPipeline(has_export=True)

    # Inject fake modules
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)

    out_dir = tmp_path / "output"
    out_dir.mkdir()
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")

    # Import and run
    from htrflow_batch.driver import load_pipeline

    # Should raise ValueError because the pipeline already has Export steps
    with pytest.raises(ValueError, match="must not contain Export steps"):
        load_pipeline(str(pipeline_yaml), out_dir)
