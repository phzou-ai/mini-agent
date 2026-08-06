from pathlib import Path

import pytest

from vermay.model_selection import (
    resolve_model_selection,
    resolve_named_model_selection,
    resolve_named_router_model_selection,
    resolve_router_model_selection,
)


def write_models_config(path: Path) -> None:
    path.write_text(
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


def test_model_selection_resolves_default_fixed_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    write_models_config(config_path)

    config = resolve_model_selection(config_path=config_path)

    assert config.provider == "ollama"
    assert config.options == {}


def test_model_selection_resolves_named_fixed_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    write_models_config(config_path)

    config = resolve_model_selection(
        config_path=config_path,
        model_name="qwen_vllm",
    )

    assert config.provider == "openai_compatible"
    assert config.options["model"] == "qwen"


def test_named_model_selection_returns_configured_model_name(tmp_path: Path):
    config_path = tmp_path / "models.json"
    write_models_config(config_path)

    selection = resolve_named_model_selection(config_path=config_path)

    assert selection.name == "local_ollama"
    assert selection.config.provider == "ollama"


def test_router_model_selection_resolves_configured_router_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "router_model": "router-small",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    },
    "router-small": {
      "provider": "ollama",
      "options": {
        "model": "router:small"
      }
    }
  }
}
""",
        encoding="utf-8",
    )

    selection = resolve_named_router_model_selection(config_path=config_path)

    assert selection.name == "router-small"
    assert selection.config.provider == "ollama"
    assert selection.config.options["model"] == "router:small"


def test_router_model_selection_falls_back_to_primary_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    write_models_config(config_path)

    selection = resolve_named_router_model_selection(config_path=config_path)

    assert selection.name == "local_ollama"
    assert selection.config.provider == "ollama"
    assert selection.config.options == {}


def test_router_model_selection_explicit_override_takes_precedence(tmp_path: Path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "router_model": "router-small",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    },
    "router-small": {
      "provider": "ollama",
      "options": {}
    },
    "router-override": {
      "provider": "openai_compatible",
      "options": {
        "model": "router",
        "base_url": "http://localhost:8000/v1"
      }
    }
  }
}
""",
        encoding="utf-8",
    )

    selection = resolve_named_router_model_selection(config_path=config_path, model_name="router-override")

    assert selection.name == "router-override"
    assert selection.config.provider == "openai_compatible"
    assert selection.config.options["model"] == "router"


def test_model_selection_rejects_unknown_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    write_models_config(config_path)

    with pytest.raises(ValueError, match="model is not defined: missing"):
        resolve_model_selection(config_path=config_path, model_name="missing")


def test_router_model_selection_rejects_unknown_configured_router_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "router_model": "missing",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    }
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model is not defined: missing"):
        resolve_router_model_selection(config_path=config_path)


def test_router_model_selection_rejects_empty_router_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "router_model": "",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    }
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="router_model must be a non-empty string"):
        resolve_router_model_selection(config_path=config_path)


def test_model_selection_rejects_missing_primary_model(tmp_path: Path):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    }
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must define primary_model"):
        resolve_model_selection(config_path=config_path)
