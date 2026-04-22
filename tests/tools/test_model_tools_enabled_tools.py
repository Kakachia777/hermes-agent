"""Tests for explicit tool-name allowlisting in get_tool_definitions()."""

from unittest.mock import patch

from model_tools import get_tool_definitions


def test_enabled_tools_intersects_after_toolset_resolution():
    fake_defs = [
        {"type": "function", "function": {"name": "cronjob"}},
        {"type": "function", "function": {"name": "session_loop"}},
        {"type": "function", "function": {"name": "skill_view"}},
    ]

    with patch("model_tools.registry.get_definitions", return_value=fake_defs) as mock_get_defs:
        tools = get_tool_definitions(
            enabled_toolsets=["hermes-cli"],
            enabled_tools=["session_loop", "skill_view"],
            quiet_mode=True,
        )

    requested_names = mock_get_defs.call_args.args[0]
    assert "session_loop" in requested_names
    assert "skill_view" in requested_names
    assert "cronjob" not in requested_names
    assert [tool["function"]["name"] for tool in tools] == [
        "cronjob",
        "session_loop",
        "skill_view",
    ]
