import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.model_clients.ollama import OllamaModelClient
from vermay_agent.types import Message


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")


def test_ollama_client_uses_builtin_fallback_config():
    client = OllamaModelClient()

    assert client.model == "deepseek-v4-flash:cloud"
    assert client.base_url == "http://127.0.0.1:11434"
    assert client.timeout_seconds == 120


def test_ollama_client_explicit_args_override_fallback_config():
    client = OllamaModelClient(
        model="override-model",
        base_url="http://override.example/",
        timeout_seconds=9,
    )

    assert client.model == "override-model"
    assert client.base_url == "http://override.example"
    assert client.timeout_seconds == 9


def test_ollama_client_formats_http_error_body():
    error = HTTPError(
        url="http://127.0.0.1:11434/api/chat",
        code=503,
        msg="Service Unavailable",
        hdrs={},
        fp=BytesIO(b'{"error":"model overloaded"}'),
    )

    message = OllamaModelClient()._format_http_error(error)

    assert message == "Ollama request failed: HTTP 503 Service Unavailable: model overloaded"


def test_ollama_client_raises_retryable_provider_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(
            url=request.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=BytesIO(b'{"error":"model overloaded"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ModelProviderError, match="model overloaded") as raised:
        OllamaModelClient().invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.provider == "ollama"
    assert raised.value.status_code == 503
    assert raised.value.retryable is True


def test_ollama_client_raises_protocol_error_for_invalid_response(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"message": {}}),
    )

    with pytest.raises(ModelProtocolError, match="Invalid Ollama response") as raised:
        OllamaModelClient().invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.provider == "ollama"
    assert raised.value.retryable is False


def test_ollama_stream_raises_retryable_connection_error(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("connection refused")),
    )

    with pytest.raises(ModelProviderError, match="connection refused") as raised:
        list(OllamaModelClient().stream_text([Message(role="user", content="hello")], tools=[]))

    assert raised.value.retryable is True
