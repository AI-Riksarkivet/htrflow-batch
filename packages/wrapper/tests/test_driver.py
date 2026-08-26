"""Tests for driver.py without requiring htrflow installed (import-guarded).

Uses monkeypatch.setitem(sys.modules, ...) to fake htrflow modules."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest


def test_load_pipeline_path_api(tmp_path, monkeypatch):
    """Test load_pipeline with newer htrflow API: from_config(path_str).

    Verifies pipeline is reconstructed with combined steps (originals + exports)."""
    # Setup fake htrflow modules
    mock_export_class = type("Export", (), {})

    def mock_export_init(self, dest, fmt):
        self.dest = dest
        self.fmt = fmt

    mock_export_class.__init__ = mock_export_init

    called_with = []

    class MockPipeline:
        def __init__(self, steps=None):
            self.steps = steps if steps is not None else []

        @staticmethod
        def from_config(config):
            called_with.append(config)
            return MockPipeline(steps=[])

    # Inject fake modules
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(
        sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline
    )
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
    # Verify: returned pipeline has 2 steps (the original empty list + 2 Exports)
    assert len(pipeline.steps) == 2
    # Verify: Export steps are in the pipeline with correct format names
    assert pipeline.steps[0].fmt == "alto"
    assert pipeline.steps[1].fmt == "page"
    # Verify: Export destinations are under out_dir
    assert str(out_dir / "alto") in pipeline.steps[0].dest
    assert str(out_dir / "page") in pipeline.steps[1].dest


def test_load_pipeline_dict_fallback(tmp_path, monkeypatch):
    """Test load_pipeline with older htrflow API: from_config(config_dict).

    Simulates fallback when path-based API raises TypeError."""
    mock_export_class = type("Export", (), {})

    def mock_export_init(self, dest, fmt):
        self.dest = dest
        self.fmt = fmt

    mock_export_class.__init__ = mock_export_init

    called_with = []

    class MockPipeline:
        def __init__(self, steps=None):
            self.steps = steps if steps is not None else []

        @staticmethod
        def from_config(config):
            called_with.append(config)
            # Older API: raise TypeError when given a string
            if isinstance(config, str):
                raise TypeError("string indices must be integers")
            return MockPipeline(steps=[])

    # Inject fake modules
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(
        sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline
    )
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
    # Verify: returned pipeline has 2 steps (original empty list + 2 Exports)
    assert len(pipeline.steps) == 2
    assert pipeline.steps[0].fmt == "alto"
    assert pipeline.steps[1].fmt == "page"
    # Verify: Export destinations are correct
    assert str(out_dir / "alto") in pipeline.steps[0].dest
    assert str(out_dir / "page") in pipeline.steps[1].dest


def test_load_pipeline_rejects_export_steps(tmp_path, monkeypatch):
    """Test load_pipeline rejects pipelines that already contain Export steps."""
    mock_export_class = type("Export", (), {})

    class MockPipeline:
        def __init__(self, steps=None):
            self.steps = steps if steps is not None else []

        @staticmethod
        def from_config(config):
            # Pipeline with an existing Export step
            return MockPipeline(steps=[mock_export_class()])

    # Inject fake modules
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(
        sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline
    )
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


def _inject_old_api_fake_htrflow(monkeypatch):
    """Old-API fake: from_config(path_str) raises TypeError, forcing the
    dict-fallback branch (which is what actually opens/parses the YAML)."""
    mock_export_class = type("Export", (), {})

    class MockPipeline:
        def __init__(self, steps=None):
            self.steps = steps if steps is not None else []

        @staticmethod
        def from_config(config):
            if isinstance(config, str):
                raise TypeError("string indices must be integers")
            return MockPipeline(steps=[])

    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")

    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class

    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(
        sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline
    )
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)


def test_load_pipeline_malformed_yaml_is_permanent(tmp_path, monkeypatch):
    """Malformed pipeline YAML must surface as ValueError (main.py's
    permanent/exit-13 bucket), not yaml.YAMLError (which main.py's bare
    `except Exception` would misclassify as transient/exit-1)."""
    _inject_old_api_fake_htrflow(monkeypatch)

    out_dir = tmp_path / "output"
    out_dir.mkdir()
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: [unclosed")

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(pipeline_yaml), out_dir)


def test_load_pipeline_missing_file_is_permanent(tmp_path, monkeypatch):
    """A nonexistent pipeline path must also surface as ValueError, not
    FileNotFoundError."""
    _inject_old_api_fake_htrflow(monkeypatch)

    out_dir = tmp_path / "output"
    out_dir.mkdir()

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(tmp_path / "does-not-exist.yaml"), out_dir)


def test_load_pipeline_model_download_oserror_stays_transient(tmp_path, monkeypatch):
    """from_config instantiates models (HF downloads); a network OSError
    there is retryable and must NOT be wrapped into ValueError (which
    main.py classifies permanent/exit-13). Final-review parked finding."""
    mock_export_class = type("Export", (), {})

    class MockPipeline:
        def __init__(self, steps=None):
            self.steps = steps if steps is not None else []

        @staticmethod
        def from_config(config):
            raise OSError("We couldn't connect to 'https://huggingface.co'")

    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")
    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class
    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(
        sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline
    )
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)

    out_dir = tmp_path / "output"
    out_dir.mkdir()
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")  # valid YAML: config is fine

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(OSError, match="huggingface"):
        load_pipeline(str(pipeline_yaml), out_dir)


def _inject_process_fakes(monkeypatch):
    fake_htrflow = ModuleType("htrflow")
    fake_pipeline_mod = ModuleType("htrflow.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")
    fake_steps.auto_import = lambda paths: [object()]
    monkeypatch.setitem(sys.modules, "htrflow", fake_htrflow)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)


class _NoopPipeline:
    def run(self, document):
        pass


def test_process_page_returns_both_formats(tmp_path, monkeypatch):
    _inject_process_fakes(monkeypatch)
    out_dir = tmp_path / "outputs"
    for fmt in ("alto", "page"):
        (out_dir / fmt).mkdir(parents=True)
        (out_dir / fmt / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    files = process_page(_NoopPipeline(), image, out_dir)
    assert set(files) == {"alto", "page"}


def test_process_page_raises_when_a_format_is_missing(tmp_path, monkeypatch):
    """W2: a page with ALTO but no PAGE XML must fail here, not be uploaded
    half-complete and later verified as done."""
    _inject_process_fakes(monkeypatch)
    out_dir = tmp_path / "outputs"
    (out_dir / "alto").mkdir(parents=True)
    (out_dir / "alto" / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    with pytest.raises(RuntimeError, match="page"):
        process_page(_NoopPipeline(), image, out_dir)


@pytest.mark.parametrize(
    "exc", [KeyError("segmentatoin"), NotImplementedError("Model X is not supported")]
)
def test_load_pipeline_unknown_step_or_model_is_permanent(tmp_path, monkeypatch, exc):
    """htrflow raises KeyError for an unknown step name (STEPS[...]) and
    NotImplementedError for an unknown model class; both are config
    mistakes and must become ValueError (exit 13), not a transient retry."""
    mock_export_class = type("Export", (), {})

    class MockPipeline:
        def __init__(self, steps=None):
            self.steps = steps or []

        @staticmethod
        def from_config(config):
            raise exc

    fake_pipeline_pipeline = ModuleType("htrflow.pipeline.pipeline")
    fake_steps = ModuleType("htrflow.pipeline.steps")
    fake_pipeline_pipeline.Pipeline = MockPipeline
    fake_steps.Export = mock_export_class
    monkeypatch.setitem(sys.modules, "htrflow", ModuleType("htrflow"))
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", ModuleType("htrflow.pipeline"))
    monkeypatch.setitem(
        sys.modules, "htrflow.pipeline.pipeline", fake_pipeline_pipeline
    )
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(pipeline_yaml), tmp_path / "out")
