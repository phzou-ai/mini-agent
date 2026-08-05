from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.model_clients.openai_compatible import OpenAICompatibleModelClient
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


def test_openai_compatible_client_omits_tools_when_no_tools(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")
    response = client.invoke([Message(role="user", content="hello")], tools=[])

    assert response.content == "done"
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]


def test_openai_compatible_client_none_mode_omits_supplied_tools(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        tool_calling="none",
    )
    response = client.invoke([Message(role="user", content="hello")], tools=[{"name": "echo"}])

    assert response.content == "done"
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]


def test_openai_compatible_client_none_mode_rejects_unexpected_tool_calls(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {"name": "echo", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
    )
    client = OpenAICompatibleModelClient(
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        tool_calling="none",
    )

    with pytest.raises(ModelProtocolError, match="tools were unavailable") as raised:
        client.invoke([Message(role="user", content="hello")], tools=[{"name": "echo"}])

    assert raised.value.reason == "unexpected_tool_calls"


def test_openai_compatible_client_rejects_prompt_json_tool_mode():
    with pytest.raises(ValueError, match="does not support tool_calling='prompt_json'"):
        OpenAICompatibleModelClient(
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            tool_calling="prompt_json",
        )


def test_openai_compatible_client_sends_standard_tool_messages(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")
    client.invoke(
        [
            Message(role="assistant", content="", tool_calls=[{"name": "echo", "args": {"value": "hi"}, "id": "call-1"}]),
            Message(role="tool", content="hi", name="echo", tool_call_id="call-1"),
        ],
        tools=[
            {
                "name": "echo",
                "description": "Echo a value.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            }
        ],
    )

    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo a value.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }
    ]
    assert captured["payload"]["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": "{\"value\": \"hi\"}"},
                }
            ],
        },
        {"role": "tool", "content": "hi", "tool_call_id": "call-1"},
    ]


def test_openai_compatible_client_preserves_returned_tool_call_id(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{\"value\":\"hi\"}"},
                                }
                            ]
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")
    response = client.invoke([Message(role="user", content="hello")], tools=[{"name": "echo"}])

    assert response.tool_call is not None
    assert response.tool_call.id == "call-1"
    assert response.tool_call.name == "echo"
    assert response.tool_call.arguments == {"value": "hi"}


def test_openai_compatible_client_preserves_all_returned_tool_calls(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{\"value\":\"first\"}"},
                                },
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{\"value\":\"second\"}"},
                                },
                            ]
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")
    response = client.invoke([Message(role="user", content="hello")], tools=[{"name": "echo"}])

    assert response.content == "Calling tools: echo, echo."
    assert [tool_call.id for tool_call in response.tool_calls] == ["call-1", "call-2"]
    assert [tool_call.arguments for tool_call in response.tool_calls] == [
        {"value": "first"},
        {"value": "second"},
    ]


@pytest.mark.parametrize(
    "status_code, retryable",
    [
        (401, False),
        (429, True),
        (503, True),
    ],
)
def test_openai_compatible_client_raises_typed_http_errors(monkeypatch, status_code, retryable):
    def fake_urlopen(request, timeout):
        raise HTTPError(
            url=request.full_url,
            code=status_code,
            msg="request failed",
            hdrs={},
            fp=BytesIO(b'{"error":{"message":"provider unavailable"}}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")

    with pytest.raises(ModelProviderError, match=f"HTTP {status_code}") as raised:
        client.invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.provider == "openai_compatible"
    assert raised.value.status_code == status_code
    assert raised.value.retryable is retryable


def test_openai_compatible_client_marks_connection_errors_retryable(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("connection refused")),
    )
    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")

    with pytest.raises(ModelProviderError, match="connection refused") as raised:
        client.invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.retryable is True


def test_openai_compatible_client_marks_timeout_errors_retryable(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")

    with pytest.raises(ModelProviderError, match="timed out") as raised:
        client.invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.retryable is True


def test_openai_compatible_client_raises_protocol_error_for_invalid_response(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"choices": []}),
    )
    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")

    with pytest.raises(ModelProtocolError, match="Invalid OpenAI-compatible response") as raised:
        client.invoke([Message(role="user", content="hello")], tools=[])

    assert raised.value.provider == "openai_compatible"
    assert raised.value.retryable is False


def test_openai_compatible_client_uses_the_smaller_task_deadline_timeout(monkeypatch):
    captured: dict[str, float] = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        return FakeResponse({"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        timeout_seconds=20,
    )

    response = client.invoke(
        [Message(role="user", content="hello")],
        tools=[],
        timeout_seconds=3.5,
    )

    assert response.content == "done"
    assert captured["timeout"] == 3.5


def test_openai_compatible_client_rejects_invalid_tool_arguments(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "not-json"},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
    )
    client = OpenAICompatibleModelClient(model="gpt-4o", base_url="https://api.openai.com/v1")

    with pytest.raises(ModelProtocolError, match="arguments must be a JSON object"):
        client.invoke([Message(role="user", content="hello")], tools=[{"name": "echo"}])
