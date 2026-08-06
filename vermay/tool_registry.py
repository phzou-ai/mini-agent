from __future__ import annotations

from langchain_core.tools import BaseTool

from .tool_metadata import ApprovalPolicy, ToolMetadata, metadata_from_legacy
from .tool_schema import (
    DANGEROUS_METADATA_KEY,
    tool_schemas_from_tools,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def schemas(self) -> list[dict]:
        return tool_schemas_from_tools(self.tools())

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools(self) -> list[BaseTool]:
        return [self._tools[name] for name in self.names()]

    def tools_for_model(self) -> list[BaseTool]:
        return [
            self._tools[name]
            for name in self.names()
            if self.tool_metadata(name).approval_policy != ApprovalPolicy.DENY
        ]

    def is_dangerous(self, name: str) -> bool:
        tool = self.get(name)
        return bool((tool.metadata or {}).get(DANGEROUS_METADATA_KEY, False))

    def metadata(self, name: str) -> dict:
        return dict(self.get(name).metadata or {})

    def tool_metadata(self, name: str) -> ToolMetadata:
        return metadata_from_legacy(self.metadata(name))

    def output_policy(self, name: str) -> dict:
        metadata = self.tool_metadata(name)
        return {
            "visibility": metadata.output_visibility,
            "redaction_status": metadata.output_redaction_status,
            "max_chars": metadata.output_max_chars,
        }
