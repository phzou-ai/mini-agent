from __future__ import annotations

import json

from vermay.evaluation import OfflineReplayService
from vermay.storage import AgentStore


def test_eval_trace_replay_uses_recorded_tool_sequence(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps({"type": "langgraph_run_started", "payload": {"input": "weather"}}),
                json.dumps(
                    {
                        "type": "langgraph_model_response",
                        "payload": {"tool_calls": [{"name": "weather_forecast"}]},
                    }
                ),
                json.dumps({"type": "langgraph_tool_message", "payload": {"name": "weather_forecast"}}),
                json.dumps({"type": "langgraph_run_finished", "payload": {"final_answer": "Shanghai Weather"}}),
            ]
        ),
        encoding="utf-8",
    )
    store = AgentStore(tmp_path / "agent.sqlite")
    service = OfflineReplayService(store=store, report_dir=tmp_path / "reports")

    report = service.replay_trace(trace)

    assert report.status == "passed"
    assert report.replay_mode == "offline_trace"
    assert report.live_model is False
    assert report.live_tools is False
    assert report.tool_sequence_match is True
    assert report.final_answer_present is True
    assert len(service.list_runs()) == 1
    assert (tmp_path / "reports" / f"{report.run_id}.json").exists()
    store.close()


def test_eval_scenario_replay_marks_mismatched_tool_sequence_failed(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "input": "weather",
                "tool_observations": [{"name": "weather_forecast"}],
                "final_answer": "Shanghai Weather",
                "expect": {"tool_sequence": ["ssh_kubectl_get"], "final_contains": ["Shanghai"]},
            }
        ),
        encoding="utf-8",
    )
    store = AgentStore(tmp_path / "agent.sqlite")
    service = OfflineReplayService(store=store, report_dir=tmp_path / "reports")

    report = service.replay_scenario(scenario)

    assert report.status == "failed"
    assert report.replay_mode == "offline_scenario"
    assert report.tool_sequence_match is False
    assert "tool sequence mismatch" in report.errors[0]
    store.close()
