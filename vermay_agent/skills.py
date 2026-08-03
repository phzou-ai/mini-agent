from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .storage import AgentStore


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: list[str]
    version: str
    content: str
    path: Path


class SkillStore:
    def __init__(self, *, authored_dir: Path, proposals_dir: Path, store: AgentStore) -> None:
        self.authored_dir = authored_dir
        self.proposals_dir = proposals_dir
        self.store = store
        self.authored_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[Skill]:
        skills = []
        for path in sorted(self.authored_dir.glob("*.md")):
            skill = parse_skill_file(path)
            skills.append(skill)
        return skills

    def show(self, name: str) -> Skill:
        for skill in self.list_skills():
            if skill.name == name or skill.path.stem == name:
                return skill
        raise KeyError(f"unknown skill: {name}")

    def retrieve(self, query: str, *, limit: int = 3) -> list[Skill]:
        terms = _terms(query)
        scored: list[tuple[int, Skill]] = []
        for skill in self.list_skills():
            score = 0
            haystack = " ".join([skill.name, skill.description, " ".join(skill.triggers), skill.content]).lower()
            for term in terms:
                if term in skill.triggers:
                    score += 4
                if term in skill.name.lower():
                    score += 3
                if term in skill.description.lower():
                    score += 2
                if term in haystack:
                    score += 1
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda pair: (pair[0], pair[1].name), reverse=True)
        return [skill for _, skill in scored[:limit]]

    def propose_from_trace(self, trace_path: Path) -> Path:
        events = _read_trace_events(trace_path)
        tool_names = []
        input_text = ""
        for event in events:
            payload = event.get("payload") or {}
            if event.get("type") == "langgraph_run_started":
                input_text = str(payload.get("input") or "")
            if event.get("type") == "langgraph_tool_message":
                name = payload.get("name")
                if isinstance(name, str) and name not in tool_names:
                    tool_names.append(name)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        base_name = _slug(input_text) or "trace-skill"
        proposal_id = f"{timestamp}-{base_name}"
        path = self.proposals_dir / f"{proposal_id}.md"
        triggers = ", ".join(tool_names or _terms(input_text))
        body = [
            "---",
            f"name: {base_name}",
            "description: Proposed skill extracted from a trace.",
            f"triggers: {triggers}",
            "version: 0.1.0",
            "---",
            "",
            f"Source trace: `{trace_path}`",
            "",
            "## Candidate Procedure",
            "",
            f"- Original request: {input_text or 'unknown'}",
        ]
        if tool_names:
            body.append(f"- Observed tools: {', '.join(tool_names)}")
        body.append("- Review and edit this proposal before approval.")
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path

    def approve(self, proposal_id: str) -> Skill:
        proposal_path = self._proposal_path(proposal_id)
        if not proposal_path.exists():
            raise KeyError(f"unknown skill proposal: {proposal_id}")
        skill = parse_skill_file(proposal_path)
        target = self.authored_dir / f"{_slug(skill.name)}.md"
        if target.exists():
            raise FileExistsError(f"skill already exists: {target.name}")
        shutil.move(str(proposal_path), target)
        approved = parse_skill_file(target)
        self.store.upsert_skill_index(
            name=approved.name,
            path=approved.path,
            description=approved.description,
            triggers=approved.triggers,
            version=approved.version,
        )
        return approved

    def _proposal_path(self, proposal_id: str) -> Path:
        path = Path(proposal_id)
        if path.suffix == ".md":
            candidate = self.proposals_dir / path.name
        else:
            candidate = self.proposals_dir / f"{proposal_id}.md"
        return candidate


def parse_skill_file(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    metadata, content = _parse_front_matter(text)
    name = metadata.get("name") or path.stem
    description = metadata.get("description") or ""
    triggers = _parse_list(metadata.get("triggers") or "")
    version = metadata.get("version") or "0.1.0"
    if not name.strip():
        raise ValueError(f"skill missing name: {path}")
    return Skill(
        name=name.strip(),
        description=description.strip(),
        triggers=triggers,
        version=version.strip(),
        content=content.strip(),
        path=path,
    )


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("skill file must start with front matter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("skill front matter is not closed")
    raw = text[4:end].strip()
    content = text[end + 4 :].strip()
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid front matter line: {line}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata, content


def _parse_list(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            loaded = []
        if isinstance(loaded, list):
            return [str(item).strip().lower() for item in loaded if str(item).strip()]
    return [item.strip().lower() for item in stripped.split(",") if item.strip()]


def _read_trace_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _terms(value: str) -> set[str]:
    return {part.strip(".,:;()[]{}'\"`").lower() for part in value.split() if part.strip()}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return normalized[:80]
