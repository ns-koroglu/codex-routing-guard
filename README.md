# Codex Routing Guard

[![Tests](https://github.com/ns-koroglu/codex-routing-guard/actions/workflows/tests.yml/badge.svg)](https://github.com/ns-koroglu/codex-routing-guard/actions/workflows/tests.yml)
[Türkçe başlangıç](docs/README.tr.md) · [MIT license](LICENSE)

Keep Codex execution chats attached to the right project and checkout, select a supported model/effort, and track completion using actual execution evidence. Includes a reusable `route-tasks` skill, reviewed task-specific skill discovery/installation, and optional completion-aware recovery hooks. Standard-library runtime for Windows, Linux and macOS.

This is an independent, agent-operated workflow. It does not intercept every app message, add missing app tools, reset quotas, or guarantee lower costs. A model named in a prompt is never evidence of a model switch.

## Requirements

- Windows, Linux or macOS, Python 3.9+ and Git. Prefer a currently maintained Python release. No third-party Python dependencies. Windows hook commands use the built-in Windows PowerShell; no WSL, Bash or Unix sockets are required.
- Project chat dispatch needs a Codex host that exposes project/chat creation, listing and turn-inspection tools. A CLI-only setup can use the ledger and checks, but cannot create desktop project chats through this package alone.
- Optional recovery needs subscription authentication, trusted hooks, and stable Codex CLI with the metadata and queue APIs checked by `doctor.py --live`. CLI `0.166.0` was verified locally; do not assume an arbitrary stable release supports the same APIs.
- OS support does not add missing host capabilities: project-chat dispatch still needs the app's tools, and recovery still needs the CLI metadata/queue interfaces and product trust. See the [platform and validation matrix](docs/validation.md).

## Install routing and the skill

Clone and review the code. Installation previews changes by default:

```bash
git clone https://github.com/ns-koroglu/codex-routing-guard.git
cd codex-routing-guard
python3 install.py
python3 install.py --apply
python3 doctor.py
```

This writes runtime files and the skill under `${CODEX_HOME:-~/.codex}`. It does not change `AGENTS.md`, model defaults, account instructions or permissions. Existing conflicting files are preserved; there is no force-overwrite option. An apply creates private backups and returns their exact directory. See [installation and rollback](docs/installation.md).

Open a new Codex chat so the installed skill is discovered. A realistic first request is:

> Use $route-tasks to inspect the installed router and prepare routing for this project. Do not create an execution chat until I explicitly request one.

For a dispatch, explicitly ask for a new execution chat in the intended project. The skill resolves project ownership separately from the real checkout, prepares a complete prompt, and checks current supported profiles. The host still performs the actual creation. Follow-ups reuse the same destination.

On Windows PowerShell, use `python` (or `py -3`) in place of `python3`; the same commands and flags apply:

```powershell
python install.py
python install.py --apply
python doctor.py
```

The installer retains private POSIX modes or protected Windows DACLs, serializes concurrent writes with native process locks, and preserves original Windows DACLs in rollback backups.

## Find a useful skill for the task

Use installed capabilities first. If a specialist would measurably improve the task, `$route-tasks` can find and compare reputable sources, review their contents and install the smallest useful candidate when the user has authorized skill installation. The default repository popularity floor is 1,000 stars; source identity, task fit, maintenance and license still need review. Stars alone do not establish safety or skill-level quality.

The helper also works directly from a checkout:

```text
python routing/skill_supply.py search --query "react performance" --min-stars 1000
python routing/skill_supply.py catalog --repo OWNER/REPO
python routing/skill_supply.py prepare --repo OWNER/REPO --path PATH/TO/SKILL --stage NEW_REVIEW_DIRECTORY --task "task" --benefit "specific improvement"
python routing/skill_supply.py inspect --stage REVIEW_DIRECTORY
python routing/skill_supply.py install --stage REVIEW_DIRECTORY --reviewed-digest EXACT_DIGEST --apply
```

`prepare` pins a full commit, preserves source license text and downloads a bounded inventory for inspection. Read the staged instructions/scripts before supplying the returned digest. `install` defaults to a read-only preview; `--apply` installs the exact reviewed bytes and writes provenance without executing downloaded code or overwriting existing skills. The digest binds the review to bytes; it does not determine their safety. The host usually discovers a new skill on the next turn. See the [discovery contract](skills/route-tasks/references/skill-discovery.md), [source-based comparison](docs/comparison.tr.md) and [validation scope](docs/validation.md).

## Configure your actual supported profiles

No capability snapshot from the author's account is shipped. Before dispatch, have the agent save the relevant **current host `create_thread` tool metadata** to a local text file, then refresh:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/routing/dispatch.py" refresh-capabilities \
  --metadata HOST_METADATA.txt --host local
```

Snapshots expire after 24 hours. Defaults use Luna for bounded/routine work and Sol for complex/critical work, only if those exact pairs are supported. For another supported model set, supply a local JSON file with four pairs:

```json
{
  "bounded_read": ["YOUR_SUPPORTED_MODEL", "YOUR_SUPPORTED_EFFORT"],
  "routine": ["YOUR_SUPPORTED_MODEL", "YOUR_SUPPORTED_EFFORT"],
  "complex": ["YOUR_SUPPORTED_MODEL", "YOUR_SUPPORTED_EFFORT"],
  "critical": ["YOUR_SUPPORTED_MODEL", "YOUR_SUPPORTED_EFFORT"]
}
```

Pass `--profiles YOUR_PROFILES.json` to `refresh-capabilities`. All four pairs are validated against the captured host metadata. Placeholder strings are not usable profiles. Economical-model completion requires an actual supported stronger review; unsupported or unverifiable reviewers block completion. The recognized stronger model identities are explicit in the source and must be reviewed when host model families change.

## Optional recovery

```bash
python3 install.py --with-recovery --codex /absolute/path/to/stable/codex
python3 install.py --with-recovery --codex /absolute/path/to/stable/codex --apply
```

Hooks are installed **disabled for automatic recovery**. Review and trust their current definitions through the CLI `/hooks` interface before activation. Changed definitions require renewed product trust; the installer never edits trust records. See [official hook documentation](https://learn.chatgpt.com/docs/hooks).

Run `python3 doctor.py --live`, then follow [recovery activation and operation](docs/recovery.md). No background service is installed automatically. Recovery requires compact checkpoints, waits for the original five-hour-plus-two-minute check, respects primary/weekly allowances and never opts into paid credits. Queue acceptance is not proof of execution. Real quota-expiry recovery of the public release has not been exercised.

## Test and contribute

```bash
python3 -m unittest discover -v
```

Tests use temporary homes, real local subprocesses for OS boundaries, and fake metadata/queue adapters for model operations. They cover project inference/worktrees, supported profiles, stale evidence, approval boundaries, duplicate dispatch, stronger review, quota guards, custom homes, native locks, Unicode shell arguments, RPC pipes, pinned skill installation, installation failure and rollback. CI runs on Windows, Linux and macOS. See [validation scope](docs/validation.md) and [contributing](CONTRIBUTING.md).

Only source, documentation and tests belong in this repository. Do not commit hook trust, authentication files, chat transcripts, task ledgers, capability snapshots, local backups or personal paths. See [security and trust boundaries](SECURITY.md). Skill format follows the [official skill documentation](https://learn.chatgpt.com/docs/build-skills).
