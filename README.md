# Codex Routing Guard

[![Tests](https://github.com/ns-koroglu/codex-routing-guard/actions/workflows/tests.yml/badge.svg)](https://github.com/ns-koroglu/codex-routing-guard/actions/workflows/tests.yml)
[Türkçe başlangıç](docs/README.tr.md) · [MIT license](LICENSE)

Keep Codex execution chats attached to the right project and checkout, select a supported model/effort, and track completion using actual execution evidence. Includes a reusable `route-tasks` skill and optional completion-aware recovery hooks.

This is an independent, agent-operated workflow. It does not intercept every app message, add missing app tools, reset quotas, or guarantee lower costs. A model named in a prompt is never evidence of a model switch.

## Requirements

- macOS or Linux, Python 3.9+ and Git. Prefer a currently maintained Python release. No third-party Python dependencies.
- Project chat dispatch needs a Codex host that exposes project/chat creation, listing and turn-inspection tools. A CLI-only setup can use the ledger and checks, but cannot create desktop project chats through this package alone.
- Optional recovery needs subscription authentication, trusted hooks, and stable Codex CLI with the metadata and queue APIs checked by `doctor.py --live`. CLI `0.166.0` was verified locally; do not assume an arbitrary stable release supports the same APIs.
- Native Windows is unsupported (`fcntl` and Unix sockets). Linux automated tests cover local logic; live desktop/recovery integration was checked on macOS only.

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

Tests use temporary homes, fake metadata and queue adapters. They cover project inference/worktrees, supported profiles, stale evidence, approval boundaries, duplicate dispatch, stronger review, quota guards, custom homes, installation failure and rollback. CI runs on Linux and macOS. See [validation scope](docs/validation.md) and [contributing](CONTRIBUTING.md).

Only source, documentation and tests belong in this repository. Do not commit hook trust, authentication files, chat transcripts, task ledgers, capability snapshots, local backups or personal paths. See [security and trust boundaries](SECURITY.md). Skill format follows the [official skill documentation](https://learn.chatgpt.com/docs/build-skills).
