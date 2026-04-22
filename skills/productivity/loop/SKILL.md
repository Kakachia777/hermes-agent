---
name: loop
description: Create or manage recurring prompts in this same Hermes session. /loop is for same-session loops only; use /cron separately when the user wants persistent detached scheduling.
version: 2.1.0
author: Hermes Agent
license: MIT
allowedTools:
  - session_loop
  - skills_list
  - skill_view
metadata:
  hermes:
    tags: [loop, session-loop, scheduling, polling, reminders]
    related_skills: [plan, blogwatcher]
---

# /loop

Use this skill to create or manage recurring prompts.

Hard rule:

- `/loop` uses `session_loop`.
- Do not use `cronjob` for `/loop`.
- If the user wants persistent detached scheduling, tell them to use `/cron` instead.

Current live session id:

- `${HERMES_SESSION_ID}`

Do not ask the user for the session id.

- Use the current live session id above when you need to mention it explicitly.
- It is also valid to omit `session_id` in the `session_loop` tool call when Hermes already provides the current session context automatically.
- Asking the user to paste a session id for normal `/loop` usage is a bug.

## What `/loop` means in Hermes

In Hermes, `/loop` is for same-session recurring prompts.

- Jobs are in-memory and session-scoped.
- They fire back into this exact chat as future user turns.
- They are gone when the session is cleared, switched, or exited.

That is different from `cronjob`, which creates persistent jobs that run in fresh sessions. `/loop` must not silently switch to that behavior.

## Subcommands

If the input after `/loop` is exactly one of these:

- `list`
  Call `session_loop` with `action="list"` and `session_id="${HERMES_SESSION_ID}"`.
  Summarize the jobs briefly.
  Prefer the tool's timing fields directly:
  - `next_run_display`
  - `next_run_in_display`

- `clear`
  Call `session_loop` with `action="clear"` and `session_id="${HERMES_SESSION_ID}"`.
  Confirm how many jobs were cleared.

If the user explicitly asks to remove one specific loop by id, call `session_loop` with `action="remove"`.

Treat natural-language management requests as the matching subcommand when the intent is clear.

- "what loops do we have"
- "show my loops"
- "what is scheduled here"

These should behave like `list`.

- "clear the loops"
- "stop all loops"
- "cancel every loop"

These should behave like `clear`.

## Parsing rules

Parse the user instruction into an interval plus prompt using this order:

1. Leading token:
   If the first token matches a time interval like `5m`, `2h`, `1d`, or `45s`, that is the interval and the rest is the prompt.

2. Trailing every clause:
   Otherwise, if the instruction ends with `every <time expression>`, use that as the interval and strip it from the prompt.
   Match only real time expressions such as:
   - `every 20m`
   - `every 5 minutes`
   - `every 2 hours`

3. Default:
   Otherwise default to `10m`.

If the resulting prompt is empty, reply with short usage help and stop.

Examples:

- `5m /review-pr 1234` -> interval `5m`, prompt `/review-pr 1234`
- `check the deploy every 20m` -> interval `20m`, prompt `check the deploy`
- `run tests every 5 minutes` -> interval `5m`, prompt `run tests`
- `check every PR` -> default `10m`, prompt `check every PR`

## Interval conversion

Convert to whole minutes for `session_loop.interval_minutes`.

- `Ns`: round up to the nearest minute, minimum 1
- `Nm`: use N minutes
- `Nh`: use N * 60 minutes
- `Nd`: use N * 1440 minutes

If seconds were rounded up, tell the user briefly.

## Prompt design

Do not schedule vague prompts when you can make them better first.

Before creating the loop:

- Rewrite the prompt into a cleaner recurring instruction when that helps.
- Preserve the user's meaning.
- Preserve slash commands verbatim. If the prompt starts with `/something`, do not rewrite the slash command itself.
- Prefer delta-oriented wording like "tell me only what changed" for repeated checks.

Examples:

- Weak: `check deployment`
- Better scheduled prompt: `check whether the deployment finished, inspect logs if it failed, and tell me only what changed since the last check`

- Weak: `check PR`
- Better scheduled prompt: `check the PR for new review comments, failing CI, or merge conflicts, and summarize only what changed`

## Create action

For normal `/loop` scheduling:

1. Parse the interval.
2. Produce a clean recurring prompt.
3. Call `session_loop` with:
   - `action="create"`
   - `session_id="${HERMES_SESSION_ID}"`
   - `prompt=<final recurring prompt>`
   - `interval_minutes=<converted whole minutes>`
   - `run_now=true`
4. Briefly confirm:
   - the final scheduled prompt
   - the cadence
   - the job id
   - the next scheduled fire using the tool's `job.next_run_display`
   - how long until it fires using `job.next_run_in_display`
   - that it is tied to this live session only
   - that `/loop list` and `/loop clear` manage it

## When the scheduled prompt is itself a slash command

If the final scheduled prompt starts with `/`:

- Preserve it exactly when scheduling.
- Do not rewrite the slash command token.
- If you also need to run the first iteration now in the current turn, interpret it as a command/skill-style workflow:
  - If it refers to a skill command, load that skill with `skill_view` if needed and follow it.
  - Otherwise, follow the underlying intent directly.

## Persistent scheduling

If the user wants scheduling that survives restarts or runs outside the current live chat:

- do not use `/loop`
- tell them to use `/cron`
- keep `/loop` behavior same-session only
