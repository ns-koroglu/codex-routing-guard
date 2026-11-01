# Existing runtime contract

This skill is a portable instruction/adapter package, not a standalone installer. It requires an existing compatible routing v2 runtime at the current `CODEX_HOME` (otherwise `~/.codex`) and the app tools needed for the requested operation. Its adapter uses Python 3.9+ and standard-library modules only. Git is used by the installed router only when existing worktree identity needs verification.

The current runtime supports Windows, Linux and macOS. Use an available Python 3 interpreter and native argument lists. Windows uses standard-library native locks and protected DACLs; POSIX uses advisory process locks and private modes. The adapter waits for the actual child exit status on every platform. `routing.py skills ...` forwards to the reviewed skill-supply helper, and `routing.py recovery ...` resolves and verifies the installed recovery bundle from settings.

`scripts/routing.py --status` is read-only. It returns actual local helper/policy/reference paths and the stored recovery enabled flag. `trustStatus: not_queried` means the adapter has not verified product trust. A missing runtime yields exit code 2 without creating directories, installing services, changing account settings or dispatching a task. On another machine, establish the compatible runtime through a separately authorized installation; do not infer portability of this Mac's hooks or saved account permissions.

Other arguments are passed as a list directly to the existing helper using the current Python executable. No shell interpolation or duplicate routing logic is used. Read its current operator reference for schemas and required evidence. Typical read-only checks are:

```
python3 scripts/routing.py recommend --kind bounded_read
python3 scripts/routing.py metrics
```

Recommendations are supported choices, not proof of the executing model. Fresh dispatch uses `preflight`; retries/follow-ups use the persisted task mapping. Stop and approval commands record workflow state, while actual app actions and their confirmations still require the supported app tools and user authorization.

The current product may expose metadata and creation tools without an interruption control. Preserve stop_requested instead of claiming a successful stop. Missing or mismatched profile/project/checkout evidence blocks a new dispatch rather than causing source execution or a new worktree.

Hook trust, recovery activation, saved account instructions, background service installation and default-model changes are outside ordinary skill invocation. Handle them only when explicitly authorized. Never enable recovery simply because a skill status check found it disabled. Exact recovery lifecycle and review rules remain in the canonical installed policy; do not maintain a second copy here.
