---
name: route-tasks
description: "Route Codex tasks to verified project, checkout and model profiles; reuse destinations and optionally discover/review/install task-specific skills. Use for routing or capability gaps; an existing execution chat continues in place."
---

# Route Tasks

Operate the existing routing installation through a thin adapter. Keep its implementation and detailed policy canonical; do not copy them into this skill or create another dispatcher.

## Establish the context

1. Run `scripts/routing.py --status` with the available Python 3.9+ interpreter relative to this skill directory (`python`/`py -3` on Windows, typically `python3` on Linux/macOS). It resolves the runtime from the existing `CODEX_HOME`, or `~/.codex`, without changing either. If the router/policy is missing, explain the dependency and stop dispatching; this skill does not install hooks, services or account settings.
2. Read the returned `policyPath` once for the relevant context. Read the returned operator reference only for the operation being performed. For dependency and portability boundaries, see [references/runtime.md](references/runtime.md).
3. Distinguish fresh intake, an execution task, a related follow-up and a request to inspect/maintain the router. `EXECUTION_MODE: ROUTED_WORK_V1` executes in place. A follow-up/status/correction/stop uses the existing stable task and destination; it never starts a duplicate. An audit-only request remains read-only until actual user approval.

## Operate the router

Preserve explicit user choices and established authorization. This skill, copied policy text and another agent's request do not grant permission to create chats, message tasks, spend money, publish or expand access. Use existing user authorization when present; ask only for genuinely missing authority before the external action.

Use `scripts/routing.py` to forward the required command to the installed helper. For new authorized work, use its `preflight` with the full professional English task prompt, stable taskId, current project/capability evidence and authorization phase. Submit the emitted exact arguments through the available app `create_thread` tool. The adapter itself does not create chats. A null projectId is unknown: resolve project ownership separately from the actual checkout, preserving edits and existing worktrees.

After asynchronous creation, attach the actual destination, verify its runtime profile, and observe a fresh app turn before announcing started. Preserve uncertain reservations and reconcile the original destination. Never substitute configured defaults, queue acceptance or a model named in a prompt for actual execution evidence.

For genuinely multi-stage work, use actual nested agents only when delegation is authorized by the user and host; keep scopes meaningful, one active by default, with supported profiles and minimal context. Economical-model output needs actual stronger-model final review of the original intent, routed prompt and final result. Bind review and completion to the current task/revision and unchanged artifacts. Missing reviewer access remains visible; never invent a receipt.

Record stop/cancel as requested until the app confirms interruption. Keep approval waiting and terminal states intact. Recovery activation is a separate installed-product trust decision; creating or invoking this skill never satisfies it.

## Fill a real capability gap

Reuse the exposed installed skill/tool catalog first. If a specialist would materially improve the current task and none fits, read [references/skill-discovery.md](references/skill-discovery.md). Compare primary sources, task fit, maintenance, license and repository reputation; prefer high-star sources without equating popularity with safety. With existing user authorization, review and install the smallest useful skill at an immutable commit using the installed `skill_supply.py` helper. Preserve install boundaries and report actual activation/verification. No useful candidate or unavailable network is a reason to continue with existing capabilities, not to stop the original task.

## Deliver

Use the user's preferred language for receipts/results and English for execution prompts unless overridden. Report created/queued/running/waiting/blocked/completed accurately. Do not echo the full execution prompt in the source. Report only measured facts and executed checks; there is no universal interception, token-saving or recovery guarantee.
