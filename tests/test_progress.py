from __future__ import annotations

from vermay.progress import ProgressReporter


def test_input_summary_collapses_multiline_input():
    reporter = ProgressReporter(enabled=False)

    summary = reporter._input_summary("apply manifest:\nkind: ConfigMap\nmetadata:\n  name: test")

    assert summary == "apply manifest: <54 chars, 4 lines>"


def test_args_summary_collapses_multiline_manifest():
    reporter = ProgressReporter(enabled=False)

    summary = reporter._args_summary(
        {"manifest": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test", "dry_run": True}
    )

    assert summary == "{manifest=<53 chars, 4 lines>, dry_run=true}"


def test_content_summary_collapses_multiline_model_response():
    reporter = ProgressReporter(enabled=False)

    summary = reporter._content_summary("line 1\nline 2\nline 3")

    assert summary == "line 1 <20 chars, 3 lines>"


def test_progress_transcript_is_supported(capsys):
    reporter = ProgressReporter(enabled=True)

    reporter.event(1, "tool_call", payload={"name": "kubectl_apply", "arguments": {"manifest": "a\nb"}})

    captured = capsys.readouterr()
    assert "loop 1" in captured.err
    assert '  tool_call  name=kubectl_apply  args={manifest=<3 chars, 2 lines>}' in captured.err


def test_progress_transcript_groups_events_by_loop(capsys):
    reporter = ProgressReporter(enabled=True)

    reporter.event(None, "run_started", input="check k8s", max_steps=5)
    reporter.event(1, "model_call_start")
    reporter.event(1, "model_response", tool="ssh_kubectl_get", content="Calling tool.")
    reporter.event(2, "model_call_start")

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        "> check k8s",
        "max_steps: 5",
        "loop 1",
        "  model_call  status=calling",
        "  model_decision  action=tool_call  tool=ssh_kubectl_get",
        "",
        "loop 2",
        "  model_call  status=calling",
    ]
