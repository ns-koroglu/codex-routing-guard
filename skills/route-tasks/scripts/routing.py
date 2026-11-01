#!/usr/bin/env python3
"""Inspect or invoke an existing routing v2 installation without duplicating it."""
import json
import os
from pathlib import Path
import sys


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
            value = json.loads(settings.read_text())
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
        print(json.dumps(status,ensure_ascii=False,indent=2))
        return 0 if status['routingInstalled'] else 2
    if not status['routingInstalled']:
        print('Existing routing v2 helper/policy is missing; no installation or dispatch was attempted.',file=sys.stderr)
        return 2
    os.execv(sys.executable,[sys.executable,status['helperPath'],*arguments])


if __name__ == '__main__':
    raise SystemExit(main())
