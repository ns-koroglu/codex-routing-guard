#!/usr/bin/env python3
"""Check installation and optional live metadata without enqueueing model work."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def inspect(home, codex='codex', live=False):
    checks = {'platformSupported': os.name == 'posix' and sys.version_info >= (3, 9)}
    checks['routingInstalled'] = all((home / 'routing' / p).is_file() for p in ('dispatch.py', 'POLICY.md', 'README.md'))
    checks['skillInstalled'] = (home / 'skills/route-tasks/SKILL.md').is_file()
    result = {'checks': checks, 'liveRequested': live, 'modelWorkEnqueued': False,
              'recovery': {'installed': False, 'enabled': None, 'trusted': 'not_queried'}}
    path = home / 'recovery-guard/settings.json'
    if path.is_file():
        settings = json.loads(path.read_text())
        if not isinstance(settings, dict) or not isinstance(settings.get('enabled'), bool):
            raise ValueError('Recovery enabled value must be boolean')
        result['recovery'].update(installed=True, enabled=settings['enabled'])
    else:
        settings = None
    if live:
        from recovery.guard import Runtime
        executable = shutil.which(settings['codex'] if settings else codex)
        if not executable:
            raise ValueError('Codex CLI is not available')
        version = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=15)
        result['cliVersion'] = version.stdout.strip()
        checks['stableCli'] = version.returncode == 0 and result['cliVersion'].startswith('codex-cli ') and not any(
            tag in result['cliVersion'].lower() for tag in ('alpha', 'beta', 'rc'))
        for name, args, required in [('queueResumeApi', ['queue', '--help'], '--resume'),
                                     ('daemonStartApi', ['app-server', 'daemon', 'start', '--help'], 'Start')]:
            response = subprocess.run([executable, *args], capture_output=True, text=True, timeout=15,
                                      env={**os.environ, 'CODEX_HOME': str(home)})
            checks[name] = response.returncode == 0 and required in response.stdout
        runtime = Runtime(executable, str(home))
        try:
            runtime.allowance()
            checks['subscriptionLimitsApi'] = True
            if settings:
                trusted = runtime.trusted(settings['verification_cwd'], settings['hook_command'])
                result['recovery']['trusted'] = trusted
                checks['allSixHooksTrusted'] = trusted
        finally:
            runtime.rpc.close()
    result['healthy'] = all(checks.values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex'))
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--live', action='store_true', help='Start metadata app-server and read actual trust/subscription limits; never enqueue')
    args = parser.parse_args()
    try:
        result = inspect(args.codex_home.expanduser().resolve(), args.codex, args.live)
    except (ValueError, KeyError, OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print(json.dumps({'healthy': False, 'error': str(error), 'modelWorkEnqueued': False}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['healthy'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
