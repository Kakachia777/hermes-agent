"""Regression tests for /loop using the skill path, not a fixed command path."""

from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = "sess-123"
    cli_obj._pending_input = MagicMock()
    return cli_obj


def test_loop_command_queues_skill_message_not_fixed_handler():
    cli_obj = _make_cli()

    with patch("cli._skill_commands", {"/loop": {"name": "loop", "description": "Loop skill"}}), \
         patch("cli.build_skill_invocation_message", return_value="LOOP_SKILL_MESSAGE") as mock_build, \
         patch("cli.get_skill_allowed_tools", return_value=["session_loop", "skill_view"]) as mock_allowed, \
         patch.object(cli_obj, "_handle_loop_command", side_effect=AssertionError("fixed loop handler should not run")):
        result = cli_obj.process_command("/loop 5m check the build")

    assert result is True
    args, kwargs = mock_build.call_args
    assert args[0] == "/loop"
    assert args[1] == "5m check the build"
    assert kwargs["task_id"] == "sess-123"
    assert "use the session_loop tool" in kwargs["runtime_note"]
    assert "Do not use cronjob" in kwargs["runtime_note"]
    mock_allowed.assert_called_once_with("/loop", task_id="sess-123")
    assert cli_obj._next_turn_allowed_tools == ["session_loop", "skill_view"]
    cli_obj._pending_input.put.assert_called_once_with("LOOP_SKILL_MESSAGE")


def test_turn_route_consumes_one_shot_skill_tool_allowlist():
    cli_obj = _make_cli()
    cli_obj.model = "test-model"
    cli_obj.api_key = None
    cli_obj.base_url = None
    cli_obj.provider = None
    cli_obj.api_mode = None
    cli_obj.acp_command = None
    cli_obj.acp_args = []
    cli_obj.service_tier = None
    cli_obj._next_turn_allowed_tools = ["session_loop", "skill_view"]

    route = cli_obj._resolve_turn_agent_config("loop message")

    assert route["allowed_tools"] == ["session_loop", "skill_view"]
    assert route["signature"][-1] == ("session_loop", "skill_view")
    assert cli_obj._next_turn_allowed_tools is None
