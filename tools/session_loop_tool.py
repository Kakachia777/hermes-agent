"""Session-scoped recurring prompt tool for Hermes interactive sessions.

Unlike ``cronjob``, this tool schedules prompts back into the current live
session. Jobs are in-memory only and disappear when the session target is
cleared or the process exits.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error

_TARGETS: Dict[str, Dict[str, Callable[..., Any]]] = {}
_JOBS: Dict[str, Dict[str, Any]] = {}
_SESSION_ALIASES: Dict[str, str] = {}
_LOCK = threading.RLock()


def _loop_trace(message: str) -> None:
    """Write thin session-loop debug traces when explicitly enabled.

    Controlled by ``HERMES_LOOP_DEBUG=1`` so normal users don't pay any
    runtime or noise cost. Writes to ``~/.hermes/loop_debug.log``.
    """
    if str(os.getenv("HERMES_LOOP_DEBUG", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        path = get_hermes_home() / "loop_debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{timestamp} [session_loop_tool] {message}\n")
    except Exception:
        pass


def _format_cadence(minutes: int) -> str:
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"every {days} day{'s' if days != 1 else ''}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"every {hours} hour{'s' if hours != 1 else ''}"
    return f"every {minutes} minute{'s' if minutes != 1 else ''}"


def _format_countdown(seconds: float) -> str:
    remaining = max(0, int(math.ceil(seconds)))
    if remaining <= 0:
        return "due now"

    days, rem = divmod(remaining, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return "in " + " ".join(parts)


def _format_local_timestamp(ts: float) -> tuple[str, str]:
    dt = datetime.fromtimestamp(ts).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z"), dt.isoformat(timespec="seconds")


def _advance_next_run_at(previous_next_run_at: float, interval_seconds: int, *, now: float | None = None) -> float:
    """Return the next future fire time without accumulating busy-time drift.

    The loop should keep its original cadence even if Hermes is busy when a job
    comes due. We fire once when the session becomes idle again, then advance to
    the next future slot on the same interval grid instead of scheduling a fresh
    full interval from the delayed fire time.
    """
    current = float(time.time() if now is None else now)
    interval = max(1, int(interval_seconds))
    previous = float(previous_next_run_at or 0.0)
    if previous <= 0:
        return current + interval
    if previous > current:
        return previous
    missed = int((current - previous) // interval) + 1
    next_run_at = previous + (missed * interval)
    if next_run_at <= current:
        return current + interval
    return next_run_at


def register_session_loop_target(
    session_id: str,
    *,
    enqueue_callback: Callable[[str], None],
    is_agent_running_callback: Callable[[], bool],
    is_session_alive_callback: Callable[[], bool],
    aliases: Optional[list[str]] = None,
) -> None:
    """Register one live interactive session as a loop delivery target."""
    if not session_id:
        return
    cleaned_aliases = sorted(
        {
            str(alias or "").strip()
            for alias in (aliases or [])
            if str(alias or "").strip() and str(alias or "").strip() != session_id
        }
    )
    with _LOCK:
        _TARGETS[session_id] = {
            "enqueue": enqueue_callback,
            "is_agent_running": is_agent_running_callback,
            "is_session_alive": is_session_alive_callback,
        }
        stale_aliases = [alias for alias, target in _SESSION_ALIASES.items() if target == session_id]
        for alias in stale_aliases:
            _SESSION_ALIASES.pop(alias, None)
        for alias in cleaned_aliases:
            _SESSION_ALIASES[alias] = session_id
    _loop_trace(f"registered target session_id={session_id} aliases={cleaned_aliases}")


def unregister_session_loop_target(session_id: str) -> None:
    """Forget a live session target without mutating jobs."""
    if not session_id:
        return
    with _LOCK:
        _TARGETS.pop(session_id, None)
        stale_aliases = [alias for alias, target in _SESSION_ALIASES.items() if target == session_id]
        for alias in stale_aliases:
            _SESSION_ALIASES.pop(alias, None)
    _loop_trace(f"unregistered target session_id={session_id}")


def move_session_loop_target(old_session_id: str, new_session_id: str) -> None:
    """Rebind live jobs/target after a session_id rotation (for example compression)."""
    if not old_session_id or not new_session_id or old_session_id == new_session_id:
        return
    with _LOCK:
        target = _TARGETS.pop(old_session_id, None)
        if target is not None:
            _TARGETS[new_session_id] = target
        for alias, target_sid in list(_SESSION_ALIASES.items()):
            if target_sid == old_session_id:
                _SESSION_ALIASES[alias] = new_session_id
        for job in _JOBS.values():
            if job.get("session_id") == old_session_id:
                job["session_id"] = new_session_id
    _loop_trace(f"moved target old_session_id={old_session_id} new_session_id={new_session_id}")


def clear_session_loop_jobs(session_id: str) -> int:
    """Stop and remove all loop jobs for one live session."""
    if not session_id:
        return 0
    with _LOCK:
        job_ids = [job_id for job_id, job in _JOBS.items() if job.get("session_id") == session_id]
        jobs = [_JOBS.pop(job_id) for job_id in job_ids]
    for job in jobs:
        job["stop_event"].set()
    _loop_trace(f"cleared jobs session_id={session_id} count={len(jobs)}")
    return len(jobs)


def _resolve_live_session_id(session_id: str | None, task_id: str | None) -> str:
    """Resolve a caller-provided session id or runtime alias to a live target id."""
    candidates: list[str] = []
    for raw in (session_id, task_id):
        value = str(raw or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    with _LOCK:
        for candidate in candidates:
            if candidate in _TARGETS:
                return candidate
            aliased = _SESSION_ALIASES.get(candidate)
            if aliased and aliased in _TARGETS:
                return aliased
    return str(session_id or "").strip()


def _session_jobs(session_id: str) -> list[Dict[str, Any]]:
    with _LOCK:
        jobs = [job for job in _JOBS.values() if job.get("session_id") == session_id]
    return sorted(jobs, key=lambda item: (item.get("next_run_at", 0.0), item["id"]))


def get_next_session_loop_run_at(session_id: str | None = None, task_id: str | None = None) -> float | None:
    """Return the next scheduled fire time for one live session, if any."""
    resolved_session_id = _resolve_live_session_id(session_id, task_id)
    if not resolved_session_id:
        return None
    jobs = _session_jobs(resolved_session_id)
    if not jobs:
        return None
    try:
        return float(jobs[0].get("next_run_at"))
    except (AttributeError, TypeError, ValueError):
        return None


def _serialize_job(job: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    next_run_at = float(job.get("next_run_at", 0.0) or 0.0)
    next_run_display, next_run_iso = _format_local_timestamp(next_run_at)
    payload = {
        "job_id": job["id"],
        "name": job["name"],
        "prompt": job["prompt"],
        "interval_minutes": job["interval_minutes"],
        "cadence": job["cadence"],
        "fires": job["fires"],
        "next_run_at": next_run_at,
        "next_run_display": next_run_display,
        "next_run_iso": next_run_iso,
        "next_run_in_seconds": max(0.0, round(next_run_at - now, 3)),
        "next_run_in_display": _format_countdown(next_run_at - now),
    }
    created_at = job.get("created_at")
    if created_at:
        created_display, created_iso = _format_local_timestamp(float(created_at))
        payload["created_at"] = float(created_at)
        payload["created_at_display"] = created_display
        payload["created_at_iso"] = created_iso
    last_run_at = job.get("last_run_at")
    if last_run_at:
        last_display, last_iso = _format_local_timestamp(float(last_run_at))
        payload["last_run_at"] = float(last_run_at)
        payload["last_run_at_display"] = last_display
        payload["last_run_at_iso"] = last_iso
    return payload


def _loop_worker(job_id: str) -> None:
    while True:
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            stop_event = job["stop_event"]
            session_id = job["session_id"]
            next_run_at = job["next_run_at"]

        if stop_event.is_set():
            return

        remaining = next_run_at - time.time()
        if remaining > 0 and stop_event.wait(min(remaining, 1.0)):
            return
        if time.time() < next_run_at:
            continue

        while True:
            with _LOCK:
                job = _JOBS.get(job_id)
                if job is None:
                    return
                stop_event = job["stop_event"]
                session_id = job["session_id"]
                target = _TARGETS.get(session_id)
                if target is None:
                    _JOBS.pop(job_id, None)
                    _loop_trace(f"worker stopping job_id={job_id} reason=missing_target session_id={session_id}")
                    return
                is_alive = bool(target["is_session_alive"]())
                is_busy = bool(target["is_agent_running"]())
                if not is_alive:
                    _JOBS.pop(job_id, None)
                    _loop_trace(f"worker stopping job_id={job_id} reason=session_dead session_id={session_id}")
                    return
                if not is_busy:
                    due_at = float(job.get("next_run_at") or time.time())
                    now = time.time()
                    next_run_at = _advance_next_run_at(
                        due_at,
                        job["interval_minutes"] * 60,
                        now=now,
                    )
                    job["fires"] += 1
                    job["last_run_at"] = now
                    job["next_run_at"] = next_run_at
                    prompt = job["prompt"]
                    enqueue = target["enqueue"]
                    _loop_trace(
                        f"worker enqueue job_id={job_id} session_id={session_id} fires={job['fires']} "
                        f"due_at={due_at:.3f} next_run_at={job['next_run_at']:.3f}"
                    )
                    break
                _loop_trace(f"worker waiting_busy job_id={job_id} session_id={session_id}")
            if stop_event.wait(1.0):
                return

        enqueue(prompt)


def session_loop(
    action: str,
    session_id: Optional[str] = None,
    prompt: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    name: Optional[str] = None,
    job_id: Optional[str] = None,
    run_now: Optional[bool] = None,
    task_id: Optional[str] = None,
) -> str:
    """Manage in-memory recurring prompts for one live interactive session."""
    normalized = str(action or "").strip().lower()
    resolved_session_id = _resolve_live_session_id(session_id, task_id)

    if normalized in {"list", "clear", "create"} and not resolved_session_id:
        return tool_error(
            "session_loop needs a live session target. Pass session_id explicitly, or call it from an active session where task_id/session aliases are available.",
            success=False,
        )

    if normalized == "list":
        jobs = [_serialize_job(job) for job in _session_jobs(resolved_session_id)]
        return json.dumps({"success": True, "count": len(jobs), "jobs": jobs}, indent=2)

    if normalized == "clear":
        cleared = clear_session_loop_jobs(resolved_session_id)
        return json.dumps({"success": True, "cleared": cleared}, indent=2)

    if normalized == "remove":
        if not job_id:
            return tool_error("job_id is required for session_loop remove", success=False)
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return tool_error(f"Loop job '{job_id}' not found", success=False)
            if resolved_session_id and job.get("session_id") != resolved_session_id:
                return tool_error(f"Loop job '{job_id}' does not belong to session '{resolved_session_id}'", success=False)
            removed = _JOBS.pop(job_id)
        removed["stop_event"].set()
        return json.dumps({"success": True, "removed_job": _serialize_job(removed)}, indent=2)

    if normalized != "create":
        return tool_error(f"Unknown session_loop action '{action}'", success=False)

    if not prompt or not str(prompt).strip():
        return tool_error("prompt is required for session_loop create", success=False)
    if interval_minutes is None or int(interval_minutes) < 1:
        return tool_error("interval_minutes must be >= 1 for session_loop create", success=False)

    with _LOCK:
        target = _TARGETS.get(resolved_session_id)
        if target is None:
            _loop_trace(f"create rejected session_id={resolved_session_id} reason=missing_target")
            return tool_error(
                f"Session '{resolved_session_id}' is not a live session_loop target. "
                "This tool currently works only in an active interactive session that registered itself.",
                success=False,
            )

    minutes = int(interval_minutes)
    prompt_text = str(prompt).strip()
    effective_run_now = True if run_now is None else bool(run_now)
    created_job_id = uuid.uuid4().hex[:8]
    stop_event = threading.Event()
    created_at = time.time()
    next_run_at = created_at + (minutes * 60)
    job = {
        "id": created_job_id,
        "session_id": resolved_session_id,
        "name": str(name or f"[loop] {prompt_text[:42].rstrip() or 'scheduled prompt'}").strip(),
        "prompt": prompt_text,
        "interval_minutes": minutes,
        "cadence": _format_cadence(minutes),
        "fires": 0,
        "created_at": created_at,
        "last_run_at": None,
        "next_run_at": next_run_at,
        "stop_event": stop_event,
    }
    worker = threading.Thread(target=_loop_worker, args=(created_job_id,), daemon=True, name=f"session-loop-{created_job_id}")
    job["thread"] = worker

    with _LOCK:
        _JOBS[created_job_id] = job
    worker.start()
    _loop_trace(
        f"created job_id={created_job_id} session_id={resolved_session_id} minutes={minutes} "
        f"run_now={effective_run_now} prompt={prompt_text[:120]!r}"
    )

    if effective_run_now:
        with _LOCK:
            target = _TARGETS.get(resolved_session_id)
        if target is not None and target["is_session_alive"]():
            _loop_trace(f"run_now enqueue job_id={created_job_id} session_id={resolved_session_id}")
            target["enqueue"](prompt_text)

    return json.dumps(
        {
            "success": True,
            "job": _serialize_job(job),
            "run_now": effective_run_now,
            "message": f"Session loop '{job['name']}' created.",
        },
        indent=2,
    )


SESSION_LOOP_SCHEMA = {
    "name": "session_loop",
    "description": (
        "Manage recurring prompts inside the current live Hermes session. "
        "Use this for same-chat loops that enqueue prompts back into the current conversation. "
        "For persistent jobs that survive independently and run in fresh sessions, use cronjob instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: create, list, clear, remove",
            },
            "session_id": {
                "type": "string",
                "description": "Optional live session id. If omitted, session_loop will try to resolve the current live session from the runtime task/session context or registered aliases.",
            },
            "prompt": {
                "type": "string",
                "description": "Recurring prompt to enqueue back into the same session.",
            },
            "interval_minutes": {
                "type": "integer",
                "description": "Recurring cadence in whole minutes. Must be >= 1.",
            },
            "name": {
                "type": "string",
                "description": "Optional human-friendly label for the loop job.",
            },
            "job_id": {
                "type": "string",
                "description": "Required for remove.",
            },
            "run_now": {
                "type": "boolean",
                "description": "Optional. Defaults to true during create, so the prompt runs once immediately and then repeats on schedule. Set false only if you explicitly want to wait for the first interval.",
            },
        },
        "required": ["action"],
    },
}


def check_session_loop_requirements() -> bool:
    return bool(
        os.getenv("HERMES_INTERACTIVE")
        or os.getenv("HERMES_GATEWAY_SESSION")
        or os.getenv("HERMES_EXEC_ASK")
    )


registry.register(
    name="session_loop",
    toolset="session_loop",
    schema=SESSION_LOOP_SCHEMA,
    handler=lambda args, **kw: session_loop(
        action=args.get("action", ""),
        session_id=args.get("session_id"),
        prompt=args.get("prompt"),
        interval_minutes=args.get("interval_minutes"),
        name=args.get("name"),
        job_id=args.get("job_id"),
        run_now=args.get("run_now"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_session_loop_requirements,
    emoji="🔁",
)
