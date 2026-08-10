from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from vermay.cli.prompt import _model_provider_config_from_args, _trace_path
from vermay.cli.subcommands import run_serve_command


def make_args(**overrides):
    values = {
        "model_config": "config/models.json",
        "model": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_model_provider_config_uses_model_config_default(tmp_path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {"model": "test-model"}
    }
  }
}
""",
        encoding="utf-8",
    )

    config = _model_provider_config_from_args(make_args(model_config=str(config_path)))

    assert config is None


def test_model_provider_config_resolves_named_model_selection(tmp_path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    },
    "qwen_vllm": {
      "provider": "openai_compatible",
      "options": {
        "model": "qwen",
        "base_url": "http://localhost:8000/v1"
      }
    }
  }
}
""",
        encoding="utf-8",
    )

    config = _model_provider_config_from_args(make_args(model_config=str(config_path), model="qwen_vllm"))

    assert config.provider == "openai_compatible"
    assert config.options["model"] == "qwen"


def test_trace_path_maps_relative_values_to_traces_dir():
    path = _trace_path("custom.jsonl")

    assert path.name == "custom.jsonl"
    assert path.parent.name == "traces"


def test_trace_path_allows_relative_subpaths_under_traces():
    path = _trace_path("runs/custom.jsonl")

    assert path.name == "custom.jsonl"
    assert path.parent.name == "runs"
    assert path.parent.parent.name == "traces"


def test_trace_path_rejects_relative_escape_from_traces():
    with pytest.raises(ValueError, match="--trace relative path must stay under traces/"):
        _trace_path("../outside.jsonl")


def test_trace_path_preserves_absolute_values(tmp_path):
    path = tmp_path / "custom.jsonl"

    assert _trace_path(str(path)) == Path(path)


def test_serve_command_runs_uvicorn_with_local_defaults(monkeypatch):
    calls = []
    created = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    def fake_create_app(**kwargs):
        created.append(kwargs)
        return "app"

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("vermay.api.app.create_app", fake_create_app)

    run_serve_command([])

    assert created == [{}]
    assert calls == [(("app",), {"host": "127.0.0.1", "port": 8000})]


def test_serve_command_accepts_host_and_port(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    def fake_create_app(**kwargs):
        return "app"

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("vermay.api.app.create_app", fake_create_app)

    run_serve_command(["--host", "0.0.0.0", "--port", "9000"])

    assert calls[0][1]["host"] == "0.0.0.0"
    assert calls[0][1]["port"] == 9000
