from __future__ import annotations

import json

import pytest

from vermay.skills import SkillStore, parse_skill_file
from vermay.storage import AgentStore


def test_skill_front_matter_parser_and_retrieval(tmp_path):
    skills_dir = tmp_path / "skills"
    proposals_dir = tmp_path / "proposals"
    skills_dir.mkdir()
    path = skills_dir / "k8s.md"
    path.write_text(
        """---
name: k8s-debug
description: Inspect Kubernetes safely.
triggers: k8s, kubernetes, pods
version: 1.0.0
---

Use read-only tools first.
""",
        encoding="utf-8",
    )
    store = AgentStore(tmp_path / "agent.sqlite")
    skill_store = SkillStore(authored_dir=skills_dir, proposals_dir=proposals_dir, store=store)

    skill = parse_skill_file(path)
    assert skill.name == "k8s-debug"
    assert skill.triggers == ["k8s", "kubernetes", "pods"]
    assert skill_store.retrieve("check kubernetes pods")[0].name == "k8s-debug"
    assert skill_store.show("k8s-debug").description == "Inspect Kubernetes safely."
    store.close()


def test_skill_parser_rejects_missing_front_matter(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("missing metadata", encoding="utf-8")

    with pytest.raises(ValueError, match="front matter"):
        parse_skill_file(path)


def test_skill_proposal_from_trace_can_be_approved(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps({"type": "langgraph_run_started", "payload": {"input": "check k8s pods"}}),
                json.dumps({"type": "langgraph_tool_message", "payload": {"name": "ssh_kubectl_get"}}),
            ]
        ),
        encoding="utf-8",
    )
    store = AgentStore(tmp_path / "agent.sqlite")
    skill_store = SkillStore(
        authored_dir=tmp_path / "skills",
        proposals_dir=tmp_path / "proposals",
        store=store,
    )

    proposal = skill_store.propose_from_trace(trace)
    approved = skill_store.approve(proposal.stem)

    assert approved.name == "check-k8s-pods"
    assert approved.path.exists()
    assert not proposal.exists()
    store.close()
