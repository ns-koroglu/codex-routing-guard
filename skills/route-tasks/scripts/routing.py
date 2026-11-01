#!/usr/bin/env python3
"""Inspect or invoke an existing routing v2 installation without duplicating it."""
import json
import hashlib
import os
from pathlib import Path
import sys
import subprocess


def inspect_runtime(home):
    home = Path(home).expanduser().resolve()
    root = home / 'routing'
    required = {'helperPath':root/'dispatch.py', 'policyPath':root/'POLICY.md',
                'operatorReferencePath':root/'README.md'}
    status = {'routingInstalled':all(path.is_file() for path in required.values()),
              'runtimeHome':str(home), **{key:str(path) for key,path in required.items()},
              'recovery':{'installed':False, 'enabled':None, 'trustStatus':'not_queried'}}
    settings = home/'recovery-guard/settings.json'
    if settings.is_file():
        status['recovery']['installed'] = True
        try:
            value = json.loads(settings.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or not isinstance(value.get('enabled'),bool):
                raise ValueError('Recovery enabled state is not a boolean')
            status['recovery']['enabled'] = value.get('enabled')
        except (ValueError,OSError) as error:
            status['recovery']['settingsError'] = type(error).__name__
    return status


def main(arguments=None, runtime_home=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    home = runtime_home if runtime_home is not None else os.environ.get('CODEX_HOME') or Path.home()/'.codex'
    status = inspect_runtime(home)
    if arguments == ['--status']:
        print(json.dumps(status,ensure_ascii=True,indent=2))
        return 0 if status['routingInstalled'] else 2
    if not status['routingInstalled']:
        print('Existing routing v2 helper/policy is missing; no installation or dispatch was attempted.',file=sys.stderr)
        return 2
    helper = status['helperPath']
    if arguments and arguments[0] == 'skills':
        helper = str(Path(helper).with_name('skill_supply.py'))
        arguments = arguments[1:]
    elif arguments and arguments[0] == 'recovery':
        settings = json.loads((Path(status['runtimeHome']) / 'recovery-guard/settings.json').read_text(encoding='utf-8'))
        helper = settings['installed_script']
        if hashlib.sha256(Path(helper).read_bytes()).hexdigest() != settings['script_sha256']:
            raise ValueError('Installed recovery helper changed; inspect and reinstall before using it')
        support = Path(helper).with_name('runtime_support.py')
        if settings.get('runtime_sha256') and hashlib.sha256(support.read_bytes()).hexdigest() != settings['runtime_sha256']:
            raise ValueError('Installed recovery runtime changed; inspect and reinstall before using it')
        arguments = ['--root', str(Path(status['runtimeHome']) / 'recovery-guard'), *arguments[1:]]
        if arguments[-1] == 'daemon':
            arguments.extend(['--codex', settings['codex'], '--codex-home', status['runtimeHome']])
    return subprocess.run([sys.executable, helper, *arguments]).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
