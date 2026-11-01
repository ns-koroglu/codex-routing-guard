#!/usr/bin/env python3
"""Preview or install the local runtime. No services, account edits or trust bypass."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

SOURCE = Path(__file__).resolve().parent
EVENTS = ('SessionStart', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop', 'Stop', 'Interrupt')
MANIFEST = 'routing-guard-installation.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def recovery_base(data):
    value = json.loads(data)
    if not isinstance(value, dict) or not isinstance(value.get('enabled'), bool):
        raise ValueError('Invalid managed recovery settings')
    return encoded({**value, 'enabled': False})


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def target(home, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Unsafe installation path')
    path = home / relative
    for part in (path, *path.parents):
        if part == home:
            break
        if part.is_symlink():
            raise ValueError('Symlink installation target refused: ' + str(part))
    return path


def state(path):
    if path.exists() and not path.is_file():
        raise ValueError('Expected a regular file: ' + str(path))
    return {'exists': path.exists(), 'sha256': sha(path.read_bytes()) if path.exists() else None,
            'mode': path.stat().st_mode & 0o777 if path.exists() else 0o600}


def stable_cli(value):
    found = shutil.which(value)
    if not found:
        raise ValueError('Codex CLI was not found; pass --codex with a stable executable')
    path = str(Path(found).resolve())
    version = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=15)
    text = version.stdout.strip()
    if version.returncode or not text.startswith('codex-cli ') or any(x in text.lower() for x in ('alpha', 'beta', 'rc')):
        raise ValueError('Recovery requires a stable Codex CLI; detected: ' + text[:100])
    return path


def plan(home, recovery=False, codex='codex', verification_cwd=None):
    old_path = target(home, MANIFEST)
    old = json.loads(old_path.read_text()) if old_path.exists() else {}
    if not isinstance(old, dict) or (old and (old.get('schemaVersion') != 1 or not isinstance(old.get('files'), dict))):
        raise ValueError('Unrecognized existing installer manifest')
    updates = {}
    for name in ('dispatch.py', 'POLICY.md', 'README.md', 'execution-prompt.txt'):
        updates['routing/' + name] = (SOURCE / 'routing' / name).read_bytes()
    for name in ('SKILL.md', 'agents/openai.yaml', 'scripts/routing.py', 'references/runtime.md'):
        updates['skills/route-tasks/' + name] = (SOURCE / 'skills/route-tasks' / name).read_bytes()
    hook_command = old.get('hookCommand')
    settings_base = old.get('recoverySettingsBaseSha256')
    if recovery:
        executable = stable_cli(codex)
        code = (SOURCE / 'recovery/guard.py').read_bytes()
        script = home / 'recovery-guard/bin' / ('guard-' + sha(code)[:20] + '.py')
        hook_command = shlex.join([sys.executable, str(script), '--root', str(home / 'recovery-guard'), 'hook'])
        hooks_path = target(home, 'hooks.json')
        hooks = json.loads(hooks_path.read_text()) if hooks_path.exists() else {}
        if not isinstance(hooks, dict) or not isinstance(hooks.get('hooks', {}), dict):
            raise ValueError('Invalid existing hooks.json; no files were changed')
        hooks.setdefault('hooks', {})
        for event in EVENTS:
            groups = hooks['hooks'].setdefault(event, [])
            if not isinstance(groups, list):
                raise ValueError('Invalid hook event: ' + event)
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                    raise ValueError('Invalid hook group: ' + event)
                for handler in group['hooks']:
                    if not isinstance(handler, dict):
                        raise ValueError('Invalid hook handler: ' + event)
                if old.get('hookCommand'):
                    group['hooks'] = [h for h in group['hooks'] if h.get('command') != old['hookCommand']]
            groups[:] = [g for g in groups if g['hooks']]
            groups.append({'hooks': [{'type': 'command', 'command': hook_command}]})
        settings = {'enabled': False, 'codex': executable, 'codex_home': str(home),
                    'hook_command': hook_command, 'verification_cwd': str(Path(verification_cwd or Path.cwd()).resolve()),
                    'installed_script': str(script), 'script_sha256': sha(code)}
        settings_path = target(home, 'recovery-guard/settings.json')
        if 'recovery-guard/settings.json' in old.get('files', {}) and settings_path.is_file():
            current_settings = settings_path.read_bytes()
            if recovery_base(current_settings) == recovery_base(encoded(settings)):
                settings['enabled'] = json.loads(current_settings)['enabled']
        settings_base = sha(recovery_base(encoded(settings)))
        updates.update({'hooks.json': encoded(hooks), str(script.relative_to(home)): code,
                        'recovery-guard/settings.json': encoded(settings),
                        'recovery-guard/installation.json': encoded({
                            'root': str(home / 'recovery-guard'), 'script': str(script), 'python': sys.executable,
                            'script_sha256': sha(code), 'hooks_path': str(hooks_path), 'hooks_sha256': sha(encoded(hooks))})})
    baseline = {}
    for name, data in updates.items():
        current = state(target(home, name))
        owned = old.get('files', {}).get(name)
        if owned and current['sha256'] != owned:
            mutable_enabled = (name == 'recovery-guard/settings.json' and current['exists']
                               and sha(recovery_base(target(home, name).read_bytes())) == old.get('recoverySettingsBaseSha256', owned))
            if not mutable_enabled:
                raise ValueError('Later user edit preserved; installation stopped: ' + name)
        if current['exists'] and not owned and current['sha256'] != sha(data) and name != 'hooks.json':
            raise ValueError('Unmanaged existing file preserved: ' + name)
        baseline[name] = current
    files = {**old.get('files', {}), **{name: sha(data) for name, data in updates.items()}}
    updates[MANIFEST] = encoded({'schemaVersion': 1, 'files': files, 'hookCommand': hook_command,
                                'recoverySettingsBaseSha256': settings_base})
    baseline[MANIFEST] = state(old_path)
    return updates, baseline


def verify(home, baseline):
    for name, expected in baseline.items():
        if state(target(home, name)) != expected:
            raise ValueError('File changed after planning; preserved: ' + name)


@contextlib.contextmanager
def locked(home):
    home.mkdir(parents=True, exist_ok=True)
    path = target(home, '.routing-guard-install.lock')
    with path.open('a') as stream:
        os.chmod(path, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def apply(home, updates, baseline):
    verify(home, baseline)
    backup = home / 'routing-guard-backups' / uuid.uuid4().hex
    target(home, str(backup.relative_to(home)) + '/restore.json')
    backup.mkdir(parents=True, mode=0o700)
    records = {}
    for index, (name, data) in enumerate(updates.items()):
        before = baseline[name]
        records[name] = {**before, 'afterSha256': sha(data), 'entry': str(index)}
        if before['exists']:
            atomic(backup / str(index), target(home, name).read_bytes())
    atomic(backup / 'restore.json', encoded({'home': str(home), 'files': records}))
    verify(home, baseline)
    written = []
    try:
        for name, data in updates.items():
            if state(target(home, name)) != baseline[name]:
                raise ValueError('Concurrent edit preserved: ' + name)
            atomic(target(home, name), data)
            written.append(name)
    except Exception:
        for name in reversed(written):
            path = target(home, name)
            if state(path)['sha256'] == records[name]['afterSha256']:
                restore_one(path, backup, records[name])
        raise
    return backup


def restore_one(path, backup, item):
    if item['exists']:
        data = (backup / item['entry']).read_bytes()
        if sha(data) != item['sha256']:
            raise ValueError('Corrupt backup refused')
        atomic(path, data, item['mode'])
    elif path.exists():
        path.unlink()


def rollback(home, backup, apply_changes=False):
    backup = Path(backup).expanduser().resolve()
    if backup.parent != (home / 'routing-guard-backups').resolve():
        raise ValueError('Backup must belong to this Codex home')
    value = json.loads((backup / 'restore.json').read_text())
    if value['home'] != str(home):
        raise ValueError('Backup belongs to another Codex home')
    for name, item in value['files'].items():
        current = state(target(home, name))
        if current['sha256'] not in {item['sha256'], item['afterSha256']}:
            raise ValueError('Later edit preserved; rollback stopped: ' + name)
        if item['exists']:
            entry = item['entry']
            if not isinstance(entry, str) or not entry.isdigit() or sha((backup / entry).read_bytes()) != item['sha256']:
                raise ValueError('Corrupt backup refused')
    if apply_changes:
        for name, item in reversed(list(value['files'].items())):
            path = target(home, name)
            if state(path)['sha256'] not in {item['sha256'], item['afterSha256']}:
                raise ValueError('Concurrent edit preserved; rollback stopped: ' + name)
            restore_one(path, backup, item)
    return list(value['files'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex'))
    parser.add_argument('--apply', action='store_true', help='Write the previewed changes; default is read-only')
    parser.add_argument('--with-recovery', action='store_true', help='Install disabled recovery hooks; requires product trust')
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--verification-cwd', type=Path)
    parser.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 9) or os.name != 'posix':
            raise ValueError('Python3.9+ on macOS/Linux is required')
        home = args.codex_home.expanduser().resolve()
        if args.rollback:
            if args.apply:
                with locked(home):
                    files = rollback(home, args.rollback, True)
            else:
                files = rollback(home, args.rollback)
            print(json.dumps({'rollbackApplied': args.apply, 'files': files}))
            return
        updates, baseline = plan(home, args.with_recovery, args.codex, args.verification_cwd)
        result = {'applied': False, 'codexHome': str(home), 'files': list(updates),
                  'recoveryInstalled': args.with_recovery, 'recoveryEnabled': False if args.with_recovery else None,
                  'serviceInstalled': False, 'modelDefaultsChanged': False}
        if args.apply:
            with locked(home):
                result['backup'] = str(apply(home, updates, baseline))
            result['applied'] = True
        print(json.dumps(result, indent=2))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
