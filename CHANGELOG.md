# Changelog

## 0.2.0

- Native Windows, Linux and macOS runtime: process locks, durable private writes, Windows DACL backup/restore, Unicode paths, shell exit-code preservation and stdio RPC without POSIX pipe assumptions.
- Portable foreground recovery polling, content-addressed helper/runtime bundles and a verified adapter command for activation, daemon operation and deactivation.
- Task-specific public GitHub skill discovery, reputation signals, exact-commit review staging, source-license retention and hash-bound installation. Existing skills and user edits are preserved; downloaded code is never executed by the installer.
- Windows/POSIX behavioral tests for subprocesses, lock contention/crash release, shell quoting, installation drift, source filtering, rollback and capability boundaries; three-OS CI matrix and source-based competitive comparison.

## 0.1.0

- Public routing runtime and reusable skill with explicit authorization, actual profile/project evidence, duplicate protection and stronger final review.
- Portable preview-first installer, custom Codex homes, unrelated-hook preservation, private backups, guarded rollback and a read-only/live diagnostic command.
- Recovery retains the selected home across RPC, queue and checkpoint instructions; Unicode/reserved-character paths are handled consistently.
- Supported profiles can be configured from actual host metadata; read-only metrics do not create a ledger. Recovery avoids repeated state loads per scheduling pass.
- Regression coverage for activation-compatible upgrades, changed-helper trust renewal, concurrent rollback edits and deactivation before restore.
- English/Turkish onboarding, MIT license, contributor guidance and Linux/macOS CI. Real quota-expiry recovery and measured model-cost savings remain unvalidated.
