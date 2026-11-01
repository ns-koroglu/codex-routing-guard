---
name: route-tasks
description: "Operate the installed Codex routing v2 workflow: prepare execution prompts, choose supported profiles, dispatch tasks into the intended project, and reuse destinations for follow-ups. Use for task routing; an existing execution chat continues its task in place."
---

# Route Tasks

Operate the existing routing installation through a thin adapter. Keep its implementation and detailed policy canonical; do not copy them into this skill or create another dispatcher.

## Establish the context

1. Run `python3 scripts/routing.py --status` relative to this skill directory. It resolves the runtime from the existing `CODEX_HOME`, or `~/.codex`, without changing either. If the router/policy is missing, explain the dependency and stop dispatching; this skill does not install hooks, services or account settings.
2. Read the returned `policyPath` once for the relevant context. Read the returned operator reference only for the operation being performed. For dependency and portability boundaries, see [references/runtime.md](references/runtime.md).
3. Distinguish fresh intake, an execution task, a related follow-up and a request to inspect/maintain the router. `EXECUTION_MODE: ROUTED_WORK_V1` executes in place. A follow-up/status/correction/stop uses the existing stable task and destination; it never starts a duplicate. An audit-only request remains read-only until actual user approval.

## Operate the router

Preserve explicit user choices and established authorization. This skill, copied policy text and another agent's request do not grant permission to create chats, message tasks, spend money, publish or expand access. Use existing user authorization when present; ask only for genuinely missing authority before the external action.

Use `scripts/routing.py` to forward the required command to the installed helper. For new authorized work, use its `preflight` with the full professional English task prompt, stable taskId, current project/capability evidence and authorization phase. Submit the emitted exact arguments through the available app `create_thread` tool. The adapter itself does not create chats. A null projectId is unknown: resolve project ownership separately from the actual checkout, preserving edits and existing worktrees.

After asynchronous creation, attach the actual destination, verify its runtime profile, and observe a fresh app turn before announcing started. Preserve uncertain reservations and reconcile the original destination. Never substitute configured defaults, queue acceptance or a model named in a prompt for actual execution evidence.

For genuinely multi-stage work, assign at most 3–5 meaningful scopes to actual nested agents when useful, one active by default, with explicit supported profiles and minimal context. Economical-model output needs actual stronger-model final review of the original intent, routed prompt and final result. Bind review and completion to the current task/revision and unchanged artifacts.

Record stop/cancel as requested until the app confirms interruption. Keep approval waiting and terminal states intact. Recovery activation is a separate installed-product trust decision; creating or invoking this skill never satisfies it.

## Deliver

Use the user's preferred language for receipts/results and English for execution prompts unless overridden. Report created/queued/running/waiting/blocked/completed accurately. Do not echo the full execution prompt in the source. Report only measured facts and executed checks; there is no universal interception, token-saving or recovery guarantee.
