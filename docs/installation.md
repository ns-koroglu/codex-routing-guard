# Installation and rollback

`install.py` uses the standard library. The default invocation only reads source/target files and prints a plan. `--apply` is the write boundary. Custom homes, including paths with spaces, are supported:

```bash
python3 install.py --codex-home "/path/to/custom codex home"
python3 install.py --codex-home "/path/to/custom codex home" --apply
```

Use the same `CODEX_HOME` for the Codex host. Passing a custom home to this installer does not reconfigure the app's home. Runtime commands and optional recovery child processes retain their explicitly selected home.

The installer copies only its explicit runtime/skill source allowlist. It does not install a live capability snapshot. It creates an ownership manifest and a private backup under `routing-guard-backups` inside the selected home. Existing files with different content and no matching ownership are refused. Later edits to owned files are refused rather than overwritten. Identical files can be adopted while preserving their original contents in the backup.

With `--with-recovery`, existing unrelated hook groups are retained, six guard handlers are merged, and automatic recovery is disabled. This changes hook definitions and requires product trust. No launch agent, systemd service, account setting, model default or global instruction file is installed.

Each apply prints a `backup` directory. Preview rollback, then apply only after inspecting it:

```bash
python3 install.py --codex-home "/path/to/codex-home" --rollback "/path/to/codex-home/routing-guard-backups/BACKUP_ID"
python3 install.py --codex-home "/path/to/codex-home" --rollback "/path/to/codex-home/routing-guard-backups/BACKUP_ID" --apply
```

Rollback verifies every target and backup before restoring any file. It stops if a later user edit would be lost. It restores pre-existing bytes/modes, removes only files newly installed by that apply, and retains unrelated files, backups and empty directories. Preview does not create an installer lock. A write failure during installation attempts to restore already-written files without overwriting concurrent edits; the saved backup remains available.

Before rolling back recovery, stop its foreground daemon or any service you configured yourself, then run the installed helper's `deactivate` command (same `--root` as activation). Restoring hook bytes can change product trust again; inspect `/hooks` afterward. Restoring files does not stop external processes. Installer upgrades accept the validated runtime `enabled` toggle and retain activation only when the helper, hook and settings are unchanged; a changed helper/configuration resets recovery to disabled for renewed trust. Other user settings edits are preserved by refusing the upgrade. Rollback requires the original recorded settings bytes, so deactivate before reverting the original install. Do not delete the ownership manifest or trust records to force an upgrade; review and preserve your changes first.

Every rollback target is checked again immediately before restoration. A concurrent edit stops the rollback while retaining that edit; earlier files may already have been restored. Reconcile the partial result against the retained backup before continuing. These comparisons are local workflow safeguards, not an atomic filesystem transaction against uncooperative external writers.

The public installer intentionally does not adopt an unrelated pre-existing recovery installation. Keep that installation or migrate it through an explicit reviewed plan. This also keeps the author's already-trusted live installation separate from development of the public release.
