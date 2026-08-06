from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .storage import AgentStore


@dataclass(frozen=True)
class ReplayScenario:
    name: str
    input: str
    source_type: str
    source_path: Path
    replay_mode: str
    requested_tools: list[str] = field(default_factory=list)
    recorded_tools: list[str] = field(default_factory=list)
    final_answer: str | None = None
    final_contains: list[str] = field(default_factory=list)
    model_profile: str = "default"


@dataclass(frozen=True)
class ReplayReport:
    run_id: str
    source_type: str
    source_path: str
    replay_mode: str
    live_model: bool
    live_tools: bool
    input: str
    model_profile: str
    status: str
    tool_sequence_match: bool
    final_answer_present: bool
    final_text_contains: dict[str, bool]
    requested_tools: list[str]
    recorded_tools: list[str]
    errors: list[str]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "replay_mode": self.replay_mode,
            "live_model": self.live_model,
            "live_tools": self.live_tools,
            "input": self.input,
            "model_profile": self.model_profile,
            "status": self.status,
            "tool_sequence_match": self.tool_sequence_match,
            "final_answer_present": self.final_answer_present,
            "final_text_contains": self.final_text_contains,
            "requested_tools": self.requested_tools,
            "recorded_tools": self.recorded_tools,
            "errors": self.errors,
        }


class OfflineReplayService:
    def __init__(self, *, store: AgentStore, report_dir: Path) -> None:
        self.store = store
        self.report_dir = report_dir
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def replay_trace(self, path: Path) -> ReplayReport:
        return self._run(load_trace_scenario(path))

    def replay_scenario(self, path: Path) -> ReplayReport:
        return self._run(load_json_scenario(path))

    def list_runs(self) -> list[dict]:
        return self.store.list_eval_runs()

    def _run(self, scenario: ReplayScenario) -> ReplayReport:
        run_id = f"eval-{uuid4().hex}"
        errors: list[str] = []
        tool_sequence_match = scenario.requested_tools == scenario.recorded_tools
        if not tool_sequence_match:
            errors.append(
                "tool sequence mismatch: "
                f"requested={scenario.requested_tools} recorded={scenario.recorded_tools}"
            )

        final_answer_present = bool((scenario.final_answer or "").strip())
        if not final_answer_present:
            errors.append("missing final answer")

        final_text_contains = {
            text: text in (scenario.final_answer or "") for text in scenario.final_contains
        }
        for text, passed in final_text_contains.items():
            if not passed:
                errors.append(f"final answer missing expected text: {text}")

        status = "passed" if not errors else "failed"
        report = ReplayReport(
            run_id=run_id,
            source_type=scenario.source_type,
            source_path=str(scenario.source_path),
            replay_mode=scenario.replay_mode,
            live_model=False,
            live_tools=False,
            input=scenario.input,
            model_profile=scenario.model_profile,
            status=status,
            tool_sequence_match=tool_sequence_match,
            final_answer_present=final_answer_present,
            final_text_contains=final_text_contains,
            requested_tools=scenario.requested_tools,
            recorded_tools=scenario.recorded_tools,
            errors=errors,
        )
        report_path = self.report_dir / f"{run_id}.json"
        report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.store.record_eval_run(
            run_id=run_id,
            source_type=scenario.source_type,
            source_path=scenario.source_path,
            status=status,
            input_text=scenario.input,
            report_path=report_path,
            summary={
                "replay_mode": scenario.replay_mode,
                "live_model": False,
                "live_tools": False,
                "tool_sequence_match": tool_sequence_match,
                "final_answer_present": final_answer_present,
                "errors": errors,
            },
        )
        return report


def load_trace_scenario(path: Path) -> ReplayScenario:
    events = _read_jsonl(path)
    input_text = ""
    requested: list[str] = []
    recorded: list[str] = []
    final_answer = None
    for event in events:
        payload = event.get("payload") or {}
        event_type = event.get("type")
        if event_type == "langgraph_run_started":
            input_text = str(payload.get("input") or "")
        elif event_type == "langgraph_model_response":
            for tool_call in payload.get("tool_calls") or []:
                if isinstance(tool_call, dict) and isinstance(tool_call.get("name"), str):
                    requested.append(tool_call["name"])
        elif event_type == "langgraph_tool_message":
            name = payload.get("name")
            if isinstance(name, str):
                recorded.append(name)
        elif event_type == "langgraph_run_finished":
            value = payload.get("final_answer")
            if isinstance(value, str):
                final_answer = value

    return ReplayScenario(
        name=path.stem,
        input=input_text,
        source_type="trace",
        source_path=path,
        replay_mode="offline_trace",
        requested_tools=requested,
        recorded_tools=recorded,
        final_answer=final_answer,
    )


def load_json_scenario(path: Path) -> ReplayScenario:
    body = json.loads(path.read_text(encoding="utf-8"))
    observations = body.get("tool_observations") or []
    recorded = [
        str(item.get("name") or item.get("tool"))
        for item in observations
        if isinstance(item, dict) and (item.get("name") or item.get("tool"))
    ]
    expect = body.get("expect") or {}
    requested = body.get("tool_requests") or expect.get("tool_sequence") or recorded
    return ReplayScenario(
        name=str(body.get("name") or path.stem),
        input=str(body.get("input") or ""),
        source_type="scenario",
        source_path=path,
        replay_mode="offline_scenario",
        requested_tools=[str(item) for item in requested],
        recorded_tools=recorded,
        final_answer=body.get("final_answer") or expect.get("final_answer"),
        final_contains=[str(item) for item in expect.get("final_contains") or []],
        model_profile=str(body.get("model_profile") or "default"),
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


TraceReplayService = OfflineReplayService
