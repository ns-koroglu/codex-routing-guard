# Routing v2 operator reference

Read POLICY.md once per relevant execution context. It is the canonical policy. This file describes the helper interface; it creates no chats and grants no permissions.

## Prepare and dispatch

Save current `list_projects` as PROJECTS.json. The capability snapshot is copied from the current host's `create_thread` supported-pair metadata, includes source/hostId/observedAt/maxAgeSeconds/models, and expires after 24 hours. Refresh only that relevant metadata when needed; do not change model defaults.

REQUEST.json fields: source (actual chat ID), requestId (originating message/turn), stable taskId (source + original request, reused across retries), sourceCwd, optional non-null projectId/executionCwd, kind (bounded_read/routine/complex/critical), authorization_phase (audit_only/execution/awaiting_approval), optional explicit model/effort/title, and explicitProjectless only for a user override. PROMPT.txt is the complete task-specific English prompt body using execution-prompt.txt. No unresolved placeholders.

```
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" preflight --request REQUEST.json --projects PROJECTS.json --prompt PROMPT.txt --output CREATE_REQUEST.json
```

Read CREATE_REQUEST.json through the normal local tool, then call the app's `create_thread` with its exact createThreadArguments. Do not print the entire prompt in the source. A null createThreadArguments means approval is pending, so do not create. After asynchronous creation and current `list_threads` confirmation:

```
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" attach --key KEY --destination THREAD_ID --host local --project-id VERIFIED_PROJECT_ID
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" verify --key KEY --runtime DESTINATION_ROLLOUT.jsonl
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" observe --key KEY --snapshot APP_SNAPSHOT.json
```

Omit --project-id only for actual projectless membership. APP_SNAPSHOT.json comes from actual `wait_threads`/`read_thread` plus `list_threads` evidence: threadId, hostId, projectId (including null), turnId, turnStatus, threadStatus, observedAt epoch seconds, optional error and finalMessageSha256. Copy real values; fixture/self-written claims are not app evidence. A snapshot is fresh for five minutes. Runtime metadata is profile evidence, never configured-default evidence.

## Existing tasks, uncertainty and approval

```
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" lookup --source SOURCE_OR_DEST_ID --task-id STABLE_TASK_ID
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" related --key KEY --request-id NEW_MESSAGE_ID --intent status
```

Related intents: followup advances the same task revision; record it before forwarding the new user message. For a message already received directly by the destination, pass --request-evidence with kind=actual_user_request, sourceThreadId and requestedAt from the actual user/turn timestamp. This keeps the revision boundary tied to the real request instead of falsely rejecting that already-started current turn. Old task/reviewer turns before the boundary are rejected. stop/cancel record a request pending actual interruption evidence; wait_approval blocks further work. The caller uses authorized supported app tools for actual follow-up/stop and must then observe the real result. There is no fake stop success. Completed/cancelled records cannot reopen. Approval/resume uses authorize --key KEY --evidence USER_APPROVAL.json, with kind=explicit_user_approval, taskId, current revision, messageId, sourceThreadId and approvedPhase. Source provenance must come from the actual user, never another agent's suggestion.

For uncertain creation use block --key KEY --evidence REASON; discover the existing chat through app metadata, then reconcile --key KEY --destination DISCOVERED_ID --host HOST --project-id PROJECT_ID --evidence APP_DISCOVERY. This preserves identity/profile/context and never authorizes a second create. A timeout may still have created the chat. For generated source cwd, resolve-project accepts --source ID and uses prior dispatch lineage to retain the actual checkout. Existing Git worktrees are checked through common-dir identity without creating or moving them.

## Review and completion

`review-start --key KEY --evidence REVIEW_START.json` registers actual stronger reviewer runtimeFile/turnId/reviewerThreadId and final artifactSha256 map, or resultSha256 for a text-only result. A child runtime must show this destination as parent. Capture the returned reviewRequest/reviewId. The actual review must inspect these final artifacts, task intent, scope and checks. Do not manufacture a receipt.

`finish --key KEY --evidence COMPLETION.json` requires app (fresh actual successful completed snapshot) and review (matching current registration + verdict=approved). Changed artifacts, prior revisions, wrong reviewer or missing completion fail closed. `metrics` reports actual stored states/latencies and unavailable token data honestly. The tests include a representative recommendation/override matrix; they are deterministic routing checks, not model-performance benchmarks.

Legacy ledger entries are retained unchanged. Their older statuses/evidence are historical; use lookup --source SOURCE --request-id ORIGINAL_REQUEST to reconcile legacy mappings; use current observation and explicit task identity for new work rather than silently certifying/migrating them as v2. Guarded installation backups and rollback preserve later unrelated edits. Local files are workflow evidence, not tamperproof signatures.
