from __future__ import annotations

import pytest

from vermay_agent.langgraph_runtime import (
    ModelProviderConfig,
    OllamaModelAdapter,
    OpenAICompatibleModelAdapter,
    build_graph_model_client,
)


def test_model_factory_builds_ollama_adapter_with_options():
    model = build_graph_model_client(
        ModelProviderConfig(
            provider="ollama",
            options={
                "model": "test-model",
                "base_url": "http://ollama.example/",
                "timeout_seconds": "7",
                "tool_calling": "native",
            },
        )
    )

    assert isinstance(model, OllamaModelAdapter)
    assert model.client.model == "test-model"
    assert model.client.base_url == "http://ollama.example"
    assert model.client.timeout_seconds == 7
    assert model.client.tool_calling == "native"


def test_model_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unsupported model provider: missing"):
        build_graph_model_client(ModelProviderConfig(provider="missing"))


def test_model_factory_rejects_unknown_ollama_option():
    with pytest.raises(ValueError, match="unsupported ollama model option\\(s\\): typo"):
        build_graph_model_client(ModelProviderConfig(provider="ollama", options={"typo": "value"}))


def test_model_factory_rejects_invalid_ollama_timeout():
    with pytest.raises(ValueError, match="ollama option 'timeout_seconds' must be a positive integer"):
        build_graph_model_client(ModelProviderConfig(provider="ollama", options={"timeout_seconds": "slow"}))


@pytest.mark.parametrize("value", [True, 1.5, "12.5", "", "0", 0, -1])
def test_model_factory_rejects_non_positive_integer_ollama_timeout(value):
    with pytest.raises(ValueError, match="ollama option 'timeout_seconds' must be .*integer"):
        build_graph_model_client(ModelProviderConfig(provider="ollama", options={"timeout_seconds": value}))


def test_model_factory_rejects_non_string_ollama_model():
    with pytest.raises(ValueError, match="ollama option 'model' must be a string"):
        build_graph_model_client(ModelProviderConfig(provider="ollama", options={"model": 123}))


def test_model_factory_builds_openai_compatible_adapter():
    model = build_graph_model_client(
        ModelProviderConfig(
            provider="openai_compatible",
            options={
                "model": "qwen",
                "base_url": "http://localhost:8000/v1",
                "timeout_seconds": "12",
            },
        )
    )

    assert isinstance(model, OpenAICompatibleModelAdapter)
    assert model.client.model == "qwen"
    assert model.client.base_url == "http://localhost:8000/v1"
    assert model.client.timeout_seconds == 12


def test_model_factory_rejects_unsupported_provider_tool_calling_mode():
    with pytest.raises(ValueError, match="openai_compatible does not support tool_calling='prompt_json'"):
        build_graph_model_client(
            ModelProviderConfig(
                provider="openai_compatible",
                options={
                    "model": "qwen",
                    "base_url": "http://localhost:8000/v1",
                    "tool_calling": "prompt_json",
                },
            )
        )


def test_model_factory_rejects_missing_openai_compatible_options():
    with pytest.raises(ValueError, match="openai_compatible option 'model' is required"):
        build_graph_model_client(
            ModelProviderConfig(
                provider="openai_compatible",
                options={"base_url": "http://localhost:8000/v1"},
            )
        )
