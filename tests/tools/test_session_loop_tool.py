"""Tests for the session_loop tool."""

import json
import types

from tools.session_loop_tool import (
    _advance_next_run_at,
    clear_session_loop_jobs,
    get_next_session_loop_run_at,
    register_session_loop_target,
    session_loop,
    unregister_session_loop_target,
)


def test_session_loop_create_list_clear_with_run_now():
    queued: list[str] = []
    session_id = "sess-loop-tool"

    register_session_loop_target(
        session_id,
        enqueue_callback=queued.append,
        is_agent_running_callback=lambda: False,
        is_session_alive_callback=lambda: True,
    )

    try:
        created = json.loads(
            session_loop(
                action="create",
                session_id=session_id,
                prompt="check the build",
                interval_minutes=1,
                run_now=True,
            )
        )
        assert created["success"] is True
        assert created["job"]["prompt"] == "check the build"
        assert queued == ["check the build"]

        listed = json.loads(session_loop(action="list", session_id=session_id))
        assert listed["success"] is True
        assert listed["count"] == 1
        assert listed["jobs"][0]["job_id"] == created["job"]["job_id"]

        cleared = json.loads(session_loop(action="clear", session_id=session_id))
        assert cleared["success"] is True
        assert cleared["cleared"] == 1
    finally:
        clear_session_loop_jobs(session_id)
        unregister_session_loop_target(session_id)


def test_session_loop_create_defaults_to_run_now():
    queued: list[str] = []
    session_id = "sess-loop-default-run-now"

    register_session_loop_target(
        session_id,
        enqueue_callback=queued.append,
        is_agent_running_callback=lambda: False,
        is_session_alive_callback=lambda: True,
    )

    try:
        created = json.loads(
            session_loop(
                action="create",
                session_id=session_id,
                prompt="check the time",
                interval_minutes=1,
            )
        )
        assert created["success"] is True
        assert created["run_now"] is True
        assert queued == ["check the time"]
    finally:
        clear_session_loop_jobs(session_id)
        unregister_session_loop_target(session_id)


def test_session_loop_create_requires_live_registered_target():
    result = json.loads(
        session_loop(
            action="create",
            session_id="missing-session",
            prompt="check the build",
            interval_minutes=1,
        )
    )

    assert result["success"] is False
    assert "not a live session_loop target" in result["error"]


def test_session_loop_create_can_resolve_live_target_from_task_id_alias():
    queued: list[str] = []
    session_id = "live-sid"
    session_alias = "db-session-key"

    register_session_loop_target(
        session_id,
        enqueue_callback=queued.append,
        is_agent_running_callback=lambda: False,
        is_session_alive_callback=lambda: True,
        aliases=[session_alias],
    )

    try:
        created = json.loads(
            session_loop(
                action="create",
                prompt="check the build",
                interval_minutes=1,
                task_id=session_alias,
            )
        )
        assert created["success"] is True
        assert created["job"]["prompt"] == "check the build"
        assert queued == ["check the build"]

        listed = json.loads(session_loop(action="list", task_id=session_alias))
        assert listed["success"] is True
        assert listed["count"] == 1
        assert listed["jobs"][0]["job_id"] == created["job"]["job_id"]
    finally:
        clear_session_loop_jobs(session_id)
        unregister_session_loop_target(session_id)


def test_session_loop_create_and_list_include_next_run_countdown(monkeypatch):
    queued: list[str] = []
    session_id = "sess-loop-countdown"
    fake_now = {"value": 1_700_000_000.0}

    class _NoopThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self._target = target
            self._args = args
            self.daemon = daemon
            self.name = name

        def start(self):
            return None

    monkeypatch.setattr("tools.session_loop_tool.threading.Thread", _NoopThread)
    monkeypatch.setattr(
        "tools.session_loop_tool.time",
        types.SimpleNamespace(time=lambda: fake_now["value"]),
    )

    register_session_loop_target(
        session_id,
        enqueue_callback=queued.append,
        is_agent_running_callback=lambda: False,
        is_session_alive_callback=lambda: True,
    )

    try:
        created = json.loads(
            session_loop(
                action="create",
                session_id=session_id,
                prompt="check the time",
                interval_minutes=1,
            )
        )
        assert created["success"] is True
        assert created["job"]["next_run_in_seconds"] == 60.0
        assert created["job"]["next_run_in_display"] == "in 1m 0s"
        assert "T" in created["job"]["next_run_iso"]

        listed = json.loads(session_loop(action="list", session_id=session_id))
        assert listed["success"] is True
        assert listed["jobs"][0]["next_run_in_display"] == "in 1m 0s"
        assert listed["jobs"][0]["created_at"] == fake_now["value"]
    finally:
        clear_session_loop_jobs(session_id)
        unregister_session_loop_target(session_id)


def test_advance_next_run_keeps_interval_grid_when_fire_is_late():
    assert _advance_next_run_at(100.0, 60, now=130.0) == 160.0
    assert _advance_next_run_at(100.0, 60, now=220.0) == 280.0


def test_get_next_session_loop_run_at_returns_shared_job_time(monkeypatch):
    queued: list[str] = []
    session_id = "sess-loop-next-run"
    fake_now = {"value": 1_700_000_000.0}

    class _NoopThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self._target = target
            self._args = args
            self.daemon = daemon
            self.name = name

        def start(self):
            return None

    monkeypatch.setattr("tools.session_loop_tool.threading.Thread", _NoopThread)
    monkeypatch.setattr(
        "tools.session_loop_tool.time",
        types.SimpleNamespace(time=lambda: fake_now["value"]),
    )

    register_session_loop_target(
        session_id,
        enqueue_callback=queued.append,
        is_agent_running_callback=lambda: False,
        is_session_alive_callback=lambda: True,
    )

    try:
        created = json.loads(
            session_loop(
                action="create",
                session_id=session_id,
                prompt="check the time",
                interval_minutes=1,
                run_now=False,
            )
        )
        assert created["success"] is True
        assert get_next_session_loop_run_at(session_id=session_id) == 1_700_000_060.0
    finally:
        clear_session_loop_jobs(session_id)
        unregister_session_loop_target(session_id)
