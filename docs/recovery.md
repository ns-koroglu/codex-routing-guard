# Recovery activation and operation

Recovery is optional. First install with `--with-recovery`, review/trust all six definitions using the stable CLI's `/hooks`, and run:

```bash
python3 doctor.py --live
```

Live doctor starts a local metadata app-server and reads subscription limits and hook trust. It also checks queue/daemon help interfaces; it never queues work or starts the queue daemon. A host may write its normal CLI metadata/cache files. A successful installation check is not a live quota-recovery test.

The installed settings identify the exact content-addressed helper, Codex home and CLI path. Read them without guessing the helper hash:

The installed skill adapter provides the same portable entry point on all three operating systems (substitute the actual skill directory; use `python3` on POSIX if needed):

```text
python <CODEX_HOME>/skills/route-tasks/scripts/routing.py recovery activate
python <CODEX_HOME>/skills/route-tasks/scripts/routing.py recovery daemon
python <CODEX_HOME>/skills/route-tasks/scripts/routing.py recovery deactivate
```

It verifies installed helper/runtime hashes, keeps the selected Codex home and waits for the command's real exit status. The daemon runs in the foreground; stop it with Ctrl-C. It polls persisted state once per second, uses a native single-instance process lock, and requires no Unix socket, port or firewall exception. Both the helper and its shared runtime are content-addressed; a change to either produces new hook definitions and disables automatic recovery until trust is reviewed again.

The following alternative uses POSIX shell heredocs; Windows users should use the adapter above:

```bash
python3 - <<'PY'
import json, os, pathlib, subprocess, sys
home = pathlib.Path(os.environ.get('CODEX_HOME') or pathlib.Path.home() / '.codex')
settings = json.loads((home / 'recovery-guard/settings.json').read_text())
subprocess.run([sys.executable, settings['installed_script'], '--root',
                str(home / 'recovery-guard'), 'activate'], check=True)
PY
```

Activation queries actual product trust; it cannot approve hooks for you. Inspect the printed `enabled` result. Never change trust files manually or use a bypass flag. Settings initially pin the resolved stable CLI executable so behavior cannot silently change through a different shell alias; after updating/uninstalling that CLI, review the selected path and run doctor again.

For a supervised foreground daemon after activation:

```bash
python3 - <<'PY'
import json, os, pathlib, subprocess, sys
home = pathlib.Path(os.environ.get('CODEX_HOME') or pathlib.Path.home() / '.codex')
settings = json.loads((home / 'recovery-guard/settings.json').read_text())
subprocess.run([sys.executable, settings['installed_script'], '--root',
                str(home / 'recovery-guard'), 'daemon', '--codex', settings['codex'],
                '--codex-home', str(home)], check=True)
PY
```

Keep it running while using recovery. Stop with Ctrl-C. To disable automatic recovery without account or trust queries, run the installed helper with the same `--root` and replace `activate` with `deactivate`. Do that before rolling back an initially disabled installation. Persistent service installation is deliberately outside the installer; configure your operating system's service manager explicitly if desired.

Open a fresh Codex session after hook installation. Existing sessions may not receive SessionStart registration until restarted, and the helper does not invent their original start time. The hooks provide the exact checkpoint/review commands to the agent. Save a compact checkpoint with `objective`, `status`, `authorization_phase`, `next_action`, `completed`, `pending`, `validation` and `artifacts`. Keep it below 16KiB and include only a bounded set of relevant artifacts. Audit-only stays read-only; approval waiting and user interruption pause automatic continuation.

The first check remains five hours and two minutes after actual session start. Later checks require a matching quota-failed current turn, current checkpoint/revision, supported subscription/provider, available primary/weekly allowances, a non-archived eligible chat, and trusted hooks. Credits do not authorize recovery spending. Uncertain enqueues are not retried blindly. A queued message carries a revision-bound, one-use token.

The final Stop hook checks completion and stronger-model review before cancelling recovery. A stronger primary can review directly; economical work needs an actual stronger execution receipt. Completion/checkpoint hashes detect drift, but local files and transcripts are trusted workflow inputs, not cryptographic attestations or a sandbox against the local account.
