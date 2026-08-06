import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from vermay.errors import ModelProtocolError, ModelProviderError
from vermay.model_clients.ollama import OllamaModelClient
from vermay.types import Message


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")


def _weather_tool() -> dict:
    return {
        "name": "weather_forecast",
        "description": "Read the weather for one city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "dangerous": False,
    }


def test_ollama_client_uses_builtin_fallback_config():
    client = OllamaModelClient()

    assert client.model == "deepseek-v4-flash:cloud"
    assert client.base_url == "http://127.0.0.1:11434"
    assert client.timeout_seconds == 120
    assert client.tool_calling == "prompt_json"


def test_ollama_client_explicit_args_override_fallback_config():
    client = OllamaModelClient(
        model="override-model",
        base_url="http://override.example/",
        timeout_seconds=9,
    )

    assert client.model == "override-model"
    assert client.base_url == "http://override.example"
    assert client.timeout_seconds == 9


def test_ollama_client_native_mode_uses_standard_tools_and_normalizes_tool_calls(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "weather_forecast",
                                "arguments": {"city": "Beijing"},
                            }
                        }
                    ],
                }
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaModelClient(tool_calling="native")

    response = client.invoke(
        [Message(role="user", content="weather in Beijing")],
        tools=[_weather_tool()],
    )

    assert captured["payload"] == {
        "model": "deepseek-v4-flash:cloud",
        "messages": [{"role": "user", "content": "weather in Beijing"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather_forecast",
                    "description": "Read the weather for one city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "stream": False,
        "options": {"temperature": 0},
    }
    assert "format" not in captured["payload"]
    assert response.content == "Calling tool weather_forecast."
    assert response.tool_call is not None
    assert response.tool_call.name == "weather_forecast"
    assert response.tool_call.arguments == {"city": "Beijing"}


def test_ollama_client_native_mode_preserves_prior_tool_conversation(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"message": {"content": "It is sunny."}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaModelClient(tool_calling="native")

    response = client.invoke(
        [
            Message(role="user", content="weather in Beijing"),
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": "call-1", "name": "weather_forecast", "args": {"city": "Beijing"}}],
            ),
            Message(role="tool", name="weather_forecast", tool_call_id="call-1", content='{"condition":"sunny"}'),
        ],
        tools=[_weather_tool()],
    )

    assert response.content == "It is sunny."
    assert captured["payload"]["messages"] == [
        {"role": "user", "content": "weather in Beijing"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "index": 0,
                        "name": "weather_forecast",
                        "arguments": {"city": "Beijing"},
                    }
                }
            ],
        },
        {"role": "tool", "tool_name": "weather_forecast", "content": '{"condition":"sunny"}'},
    ]


def test_ollama_client_native_mode_uses_plain_text_for_direct_messages(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"message": {"content": "Hello."}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaModelClient(tool_calling="native")

    response = client.invoke([Message(role="user", content="hello")], tools=[])

    assert response.content == "Hello."
    assert "format" not in captured["payload"]
    assert "tools" not in captured["payload"]
    assert captured["payload"]["messages"][0]["content"] == "Answer the user directly in plain text. Do not wrap the answer in JSON."


def test_ollama_client_none_mode_omits_tools_and_rejects_unexpected_tool_calls(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "weather_forecast", "arguments": {"city": "Beijing"}}}],
                }
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaModelClient(tool_calling="none")

    with pytest.raises(ModelProtocolError, match="tools were unavailable") as raised:
        client.invoke([Message(role="user", content="weather in Beijing")], tools=[_weather_tool()])

    assert raised.value.reason == "unexpected_tool_calls"
    assert "format" not in captured["payload"]
    assert "tools" not in captured["payload"]


def test_ollama_client_prompt_json_mode_keeps_legacy_request_shape(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"message": {"content": '{"action":"final","content":"done"}'}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaModelClient(tool_calling="prompt_json")

    response = client.invoke([Message(role="user", content="hello")], tools=[_weather_tool()])

    assert response.content == "done"
    assert captured["payload"]["format"] == "json"
    assert "Return only JSON. Choose one action." in captured["payload"]["messages"][0]["content"]


def test_ollama_client_native_mode_rejects_non_object_tool_arguments(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "weather_forecast", "arguments": ["Beijing"]}}],
                }
            }
        ),
    )
    client = OllamaModelClient(tool_calling="native")

    with pytest.raises(ModelProtocolError, match="arguments must be a JSON object") as raised:
        client.invoke([Message(role="user", content="weather in Beijing")], tools=[_weather_tool()])

    assert raised.value.reason == "invalid_tool_calls"


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
    assert "raw=" not in str(raised.value)


def test_ollama_client_treats_success_error_envelope_as_provider_error(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"error": "cloud model temporarily unavailable"}),
    )

    with pytest.raises(ModelProviderError, match="temporarily unavailable") as raised:
        OllamaModelClient().invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.provider == "ollama"
    assert raised.value.retryable is True


def test_ollama_client_uses_the_smaller_task_deadline_timeout(monkeypatch):
    captured: dict[str, float] = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        return FakeResponse({"message": {"content": '{"action":"final","content":"done"}'}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaModelClient(timeout_seconds=20)

    response = client.invoke(
        [Message(role="user", content="hello")],
        tools=[],
        timeout_seconds=3.5,
    )

    assert response.content == "done"
    assert captured["timeout"] == 3.5


def test_ollama_client_rejects_plain_text_when_task_action_is_required(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {"message": {"content": "Let me check all nodes. Calling tool ssh_kubectl_get."}}
        ),
    )

    with pytest.raises(ModelProtocolError, match="expected a JSON object with an action field"):
        OllamaModelClient().invoke([Message(role="user", content="check nodes")], tools=[])


@pytest.mark.parametrize(
    "content",
    [
        "The weather is clear and 23C.",
        '{"answer":"The weather is clear and 23C."}',
        '{"content":"The weather is clear and 23C."}',
    ],
)
def test_ollama_client_accepts_post_tool_final_answer_compatibility_shape(monkeypatch, content):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"message": {"content": content}}),
    )

    response = OllamaModelClient().invoke(
        [
            Message(role="user", content="check weather"),
            Message(role="tool", name="weather_forecast", content='{"temp_c":"23"}'),
        ],
        tools=[],
    )

    assert response.content == "The weather is clear and 23C."


def test_ollama_stream_raises_retryable_connection_error(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("connection refused")),
    )

    with pytest.raises(ModelProviderError, match="connection refused") as raised:
        list(OllamaModelClient().stream_text([Message(role="user", content="hello")], tools=[]))

    assert raised.value.retryable is True
