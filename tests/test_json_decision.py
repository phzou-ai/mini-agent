import pytest

from vermay.model_clients.json_decision import parse_json_decision


@pytest.mark.parametrize(
    "content",
    [
        '{"action":"tool_call","name":"weather","arguments":{"city":"Beijing"}}',
        '```json\n{"action":"tool_call","name":"weather","arguments":{}}\n```',
        '<think>private reasoning</think>{"action":"final","answer":"done"}',
        'Model preface: {"action":"final","answer":"done"} trailing text',
    ],
)
def test_parse_json_decision_accepts_supported_model_wrappers(content):
    decision = parse_json_decision(content)

    assert decision is not None
    assert "action" in decision


def test_parse_json_decision_normalizes_request_user_input_shorthand():
    decision = parse_json_decision(
        '{"action":"request_user_input","prompt":"Continue?",'
        '"choices":["yes","no"]}'
    )

    assert decision == {
        "action": "tool_call",
        "name": "request_user_input",
        "arguments": {"prompt": "Continue?", "choices": ["yes", "no"]},
    }


def test_parse_json_decision_preserves_explicit_request_arguments():
    decision = parse_json_decision(
        '{"action":"request_user_input","prompt":"outer",'
        '"arguments":{"prompt":"inner","custom":true}}'
    )

    assert decision == {
        "action": "tool_call",
        "name": "request_user_input",
        "arguments": {"prompt": "inner", "custom": True},
    }


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        "Model preface: {\"answer\":\"missing action\"}",
        "```json\n{broken}\n```",
    ],
)
def test_parse_json_decision_rejects_invalid_or_non_action_content(content):
    assert parse_json_decision(content) is None
