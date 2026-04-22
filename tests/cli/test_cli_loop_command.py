"""Tests for the session-scoped /loop slash command."""

import queue
import threading
import time
from unittest.mock import patch

from cli import HermesCLI, _parse_loop_command


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj._loop_jobs = {}
    cli_obj._loop_lock = threading.Lock()
    cli_obj._pending_input = queue.Queue()
    cli_obj._should_exit = False
    cli_obj._agent_running = False
    return cli_obj


def test_parse_loop_defaults_to_ten_minutes():
    parsed = _parse_loop_command("/loop check the build")
    assert parsed["action"] == "create"
    assert parsed["minutes"] == 10
    assert parsed["prompt"] == "check the build"


def test_parse_loop_supports_trailing_every_clause():
    parsed = _parse_loop_command("/loop check the build every 2 hours")
    assert parsed["action"] == "create"
    assert parsed["minutes"] == 120
    assert parsed["prompt"] == "check the build"


def test_parse_loop_supports_trailing_unit_words():
    parsed = _parse_loop_command("/loop run tests every 5 minutes")
    assert parsed["action"] == "create"
    assert parsed["minutes"] == 5
    assert parsed["prompt"] == "run tests"


def test_parse_loop_every_without_time_expression_uses_default_interval():
    parsed = _parse_loop_command("/loop check every PR")
    assert parsed["action"] == "create"
    assert parsed["minutes"] == 10
    assert parsed["prompt"] == "check every PR"


def test_loop_fires_back_into_current_session():
    cli_obj = _make_cli()
    with patch("cli.build_skill_invocation_message", return_value="LOOP_SKILL_MESSAGE"):
        cli_obj._handle_loop_command("/loop 1m check the build")

        assert len(cli_obj._loop_jobs) == 1
        job_id, job = next(iter(cli_obj._loop_jobs.items()))
        assert job["prompt"] == "check the build"

        with cli_obj._loop_lock:
            cli_obj._loop_jobs[job_id]["next_run_at"] = time.time() - 1

        fired = cli_obj._pending_input.get(timeout=2.5)
        assert fired == "LOOP_SKILL_MESSAGE"

        cli_obj._handle_loop_command("/loop clear")


def test_loop_falls_back_to_raw_prompt_when_skill_load_fails():
    cli_obj = _make_cli()
    with patch("cli.build_skill_invocation_message", return_value=None):
        cli_obj._handle_loop_command("/loop 1m check the build")

        job_id = next(iter(cli_obj._loop_jobs))
        with cli_obj._loop_lock:
            cli_obj._loop_jobs[job_id]["next_run_at"] = time.time() - 1

        fired = cli_obj._pending_input.get(timeout=2.5)
        assert fired == "check the build"

        cli_obj._handle_loop_command("/loop clear")


def test_loop_clear_removes_all_jobs():
    cli_obj = _make_cli()
    cli_obj._handle_loop_command("/loop 1m first job")
    cli_obj._handle_loop_command("/loop second job")

    assert len(cli_obj._loop_jobs) == 2

    cli_obj._handle_loop_command("/loop clear")

    assert cli_obj._loop_jobs == {}
