import pytest

from vermay.errors import ModelProtocolError
from vermay.model_clients.ollama import OllamaModelClient
from vermay.types import Message


def parse(content: str):
    return OllamaModelClient()._parse_content(content)


def test_parse_final_action():
    response = parse('{"action":"final","content":"done"}')

    assert response.content == "done"
    assert response.tool_call is None


def test_parse_tool_call_action():
    response = parse('{"action":"tool_call","name":"ssh_kubectl_get","arguments":{"resource":"pods"}}')

    assert response.content == "Calling tool ssh_kubectl_get."
    assert response.tool_call is not None
    assert response.tool_call.name == "ssh_kubectl_get"
    assert response.tool_call.arguments == {"resource": "pods"}


def test_parse_request_user_input_shorthand_as_tool_call():
    response = parse(
        '{"action":"request_user_input","prompt":"Proceed?",'
        '"choices":["yes","no"]}'
    )

    assert response.content == "Calling tool request_user_input."
    assert response.tool_call is not None
    assert response.tool_call.name == "request_user_input"
    assert response.tool_call.arguments == {
        "prompt": "Proceed?",
        "choices": ["yes", "no"],
    }


def test_parse_embedded_tool_call_action():
    response = parse(
        'I will read the cluster state.\n\n'
        '{"action":"tool_call","name":"ssh_kubectl_get","arguments":{"resource":"pods","namespace":"all"}}'
    )

    assert response.content == "Calling tool ssh_kubectl_get."
    assert response.tool_call is not None
    assert response.tool_call.name == "ssh_kubectl_get"
    assert response.tool_call.arguments == {"resource": "pods", "namespace": "all"}


def test_parse_embedded_tool_call_after_thinking_markup():
    response = parse(
        "<think>First I need cluster state.</think>\n"
        '{"action":"tool_call","name":"ssh_kubectl_get","arguments":{"resource":"nodes"}}'
    )

    assert response.content == "Calling tool ssh_kubectl_get."
    assert response.tool_call is not None
    assert response.tool_call.name == "ssh_kubectl_get"


def test_parse_ignores_action_inside_thinking_markup():
    response = parse(
        "<think>{\"action\":\"tool_call\",\"name\":\"wrong_tool\",\"arguments\":{}}</think>\n"
        '{"action":"final","content":"done"}'
    )

    assert response.content == "done"
    assert response.tool_call is None


def test_parse_plain_text_as_invalid_agent_action():
    with pytest.raises(ModelProtocolError, match="expected a JSON object with an action field"):
        parse("## Status\nAll pods are running.")



def test_parse_tool_announcement_without_json_as_invalid_agent_action():
    with pytest.raises(ModelProtocolError, match="expected a JSON object with an action field"):
        parse("Let me check all nodes.\n\nCalling tool ssh_kubectl_get for nodes.</think>")



def test_parse_content_only_json_as_invalid_agent_action():
    with pytest.raises(ModelProtocolError, match="unsupported action None"):
        parse('{"content":"plain answer"}')


def test_parse_malformed_json_as_invalid_agent_action():
    with pytest.raises(ModelProtocolError, match="expected a JSON object with an action field"):
        parse('{"action":"final","content":')


def test_parse_json_fenced_in_markdown_code_block():
    response = parse('```json\n{"action":"final","content":"from fence"}\n```')

    assert response.content == "from fence"
    assert response.tool_call is None


def test_parse_unknown_action_is_invalid_agent_action():
    with pytest.raises(ModelProtocolError, match="unsupported action 'wait'"):
        parse('{"action":"wait","content":"later"}')



def test_parse_tool_call_missing_name_is_invalid():
    with pytest.raises(ModelProtocolError, match="name and object arguments are required"):
        parse('{"action":"tool_call","arguments":{"pattern":"error"}}')


def test_parse_tool_call_missing_arguments_defaults_to_empty_dict():
    response = parse('{"action":"tool_call","name":"ssh_kubectl_get"}')

    assert response.tool_call is not None
    assert response.tool_call.name == "ssh_kubectl_get"
    assert response.tool_call.arguments == {}


def test_parse_first_tool_call_when_model_returns_multiple_json_actions():
    response = parse(
        "\n".join(
            [
                '{"action":"tool_call","name":"ssh_kubectl_get","arguments":{"resource":"nodes","namespace":"all"}}',
                '{"action":"tool_call","name":"ssh_kubectl_get","arguments":{"resource":"pods","namespace":"all"}}',
            ]
        )
    )

    assert response.content == "Calling tool ssh_kubectl_get."
    assert response.tool_call is not None
    assert response.tool_call.name == "ssh_kubectl_get"
    assert response.tool_call.arguments == {"resource": "nodes", "namespace": "all"}


def test_ollama_protocol_prompt_uses_standard_tool_message_error_language():
    messages = [Message(role="user", content="hello")]
    ollama_messages = OllamaModelClient()._to_ollama_messages(messages, tools=[])
    protocol = ollama_messages[0]["content"]

    assert "TOOL_ERROR" not in protocol
    assert "tool message indicates an error or failed execution" in protocol
    assert "Use request_user_input only for missing tool arguments" in protocol
    assert "Do not emit reasoning, <think> tags" in protocol
