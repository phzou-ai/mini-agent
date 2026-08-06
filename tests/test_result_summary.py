from __future__ import annotations

from vermay.result_summary import observation_summary, tool_command_summary, tool_exit_code


def test_tool_command_summary_extracts_kubectl_command():
    output = {
        "command": (
            "ssh host PATH=/snap/bin:$PATH; if command -v kubectl; then "
            "kubectl get pods -A -o wide; fi"
        )
    }

    assert tool_command_summary(output) == "kubectl get pods -A -o wide"


def test_tool_exit_code_reads_dict_exit_code():
    assert tool_exit_code({"exit_code": 0}) == 0
    assert tool_exit_code({"stdout": "ok"}) is None


def test_observation_summary_prefers_stdout_preview():
    output = {"stdout": "\n".join(str(index) for index in range(10)), "stderr": ""}

    summary = observation_summary(output, "fallback")

    assert summary.startswith("stdout_lines: 10")
    assert "... (2 more lines in JSONL trace)" in summary


def test_observation_summary_falls_back_to_content():
    assert observation_summary("plain", "content") == "content"


def test_observation_summary_prefers_status_from_output():
    assert observation_summary({"status": "placeholder_not_applied", "manifest": "apiVersion: v1"}, "fallback") == (
        "status=placeholder_not_applied"
    )


def test_observation_summary_prefers_status_from_json_content():
    assert observation_summary(None, '{"status":"placeholder_not_applied","manifest":"apiVersion: v1"}') == (
        "status=placeholder_not_applied"
    )
