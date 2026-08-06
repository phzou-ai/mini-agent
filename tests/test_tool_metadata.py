import pytest

from vermay.tool_metadata import (
    ApprovalPolicy,
    ExecutionScope,
    SideEffectLevel,
    ToolSource,
    metadata_from_legacy,
)


def test_metadata_from_legacy_maps_safe_tool_to_auto_policy():
    metadata = metadata_from_legacy({"dangerous": False})

    assert metadata.dangerous is False
    assert metadata.read_only is True
    assert metadata.approval_policy == ApprovalPolicy.AUTO
    assert metadata.source == ToolSource.BUILTIN
    assert metadata.output_visibility == "internal"
    assert metadata.output_redaction_status == "unknown"
    assert metadata.output_max_chars == 1000


def test_metadata_from_legacy_maps_dangerous_tool_to_approval_required():
    metadata = metadata_from_legacy({"dangerous": True})

    assert metadata.dangerous is True
    assert metadata.read_only is False
    assert metadata.approval_policy == ApprovalPolicy.APPROVAL_REQUIRED


def test_metadata_from_legacy_accepts_explicit_policy_fields():
    metadata = metadata_from_legacy(
        {
            "dangerous": False,
            "source": "mcp",
            "execution_scope": "remote",
            "side_effect_level": "none",
            "credential_sensitive": True,
            "redaction_required": True,
        }
    )

    assert metadata.source == ToolSource.MCP
    assert metadata.execution_scope == ExecutionScope.REMOTE
    assert metadata.side_effect_level == SideEffectLevel.NONE
    assert metadata.credential_sensitive is True
    assert metadata.redaction_required is True


def test_metadata_rejects_invalid_output_visibility():
    with pytest.raises(ValueError, match="output_visibility"):
        metadata_from_legacy({"output_visibility": "everyone"})


def test_metadata_rejects_invalid_redaction_status():
    with pytest.raises(ValueError, match="output_redaction_status"):
        metadata_from_legacy({"output_redaction_status": "safe"})


def test_metadata_rejects_invalid_output_max_chars():
    with pytest.raises(ValueError, match="output_max_chars"):
        metadata_from_legacy({"output_max_chars": 0})


def test_metadata_rejects_read_only_side_effect_conflict():
    with pytest.raises(ValueError, match="read_only=true"):
        metadata_from_legacy({"read_only": True, "side_effect_level": "remote"})


def test_metadata_rejects_artifact_kinds_without_artifact_production():
    with pytest.raises(ValueError, match="artifact_kinds"):
        metadata_from_legacy({"artifact_kinds": ["tool_observation"]})
