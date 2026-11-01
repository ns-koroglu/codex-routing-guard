import json
import base64
import contextlib
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from recovery import guard
from routing import dispatch


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'home with spaces'

    def test_recovery_instructions_keep_the_registered_custom_root(self):
        store = guard.Store(self.root / 'guard')
        text = guard.context(store, 'example-session')
        command = text.split('run ', 1)[1].split(' checkpoint ', 1)[0]
        if os.name == 'nt':
            self.assertIn("'--root' '" + str(store.root) + "'", command)
        else:
            self.assertEqual(shlex.split(command)[-2:], ['--root', str(store.root)])

    def test_rpc_child_receives_explicit_codex_home(self):
        process = Mock()
        with patch.object(guard.subprocess, 'Popen', return_value=process) as popen, patch.object(guard.Rpc, 'call', return_value={}):
            rpc = guard.Rpc('codex', self.root)
            self.assertEqual(popen.call_args.kwargs['env']['CODEX_HOME'], str(self.root))
            rpc.close()

    def test_enqueue_uses_same_home_for_daemon_and_queue(self):
        runtime = object.__new__(guard.Runtime)
        runtime.codex = 'codex'
        runtime.home = str(self.root)
        with patch.object(guard.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(runtime.enqueue('example-session', 'fixture'), 'queued')
            self.assertEqual(len(run.call_args_list), 2)
            self.assertTrue(all(call.kwargs['env']['CODEX_HOME'] == str(self.root) for call in run.call_args_list))

    def test_cli_uses_codex_home_for_default_state_root(self):
        response = subprocess.run([sys.executable, str(Path(guard.__file__)), 'status', '--session', 'example-session'],
                                  env={**os.environ, 'CODEX_HOME': str(self.root)}, capture_output=True, text=True)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertTrue((self.root / 'recovery-guard/states').is_dir())

    def test_metrics_on_missing_installation_does_not_create_a_ledger_or_lock(self):
        response = subprocess.run([sys.executable, str(Path(dispatch.__file__)), '--ledger', str(self.root / 'ledger.json'), 'metrics'], capture_output=True, text=True)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(json.loads(response.stdout)['records'], 0)
        self.assertFalse(self.root.exists())

    def test_supported_profiles_can_be_configured_without_model_switch_claims(self):
        metadata = 'gpt-example (supported reasoning efforts: low, high)'
        profiles = {kind: ['gpt-example', 'high'] for kind in ('bounded_read', 'routine', 'complex', 'critical')}
        catalog = dispatch.refresh_capabilities(metadata, 'local', profiles)
        choice = dispatch.recommend('complex', catalog)
        self.assertEqual((choice['model'], choice['effort']), ('gpt-example', 'high'))
        with self.assertRaisesRegex(ValueError, 'not supported'):
            dispatch.refresh_capabilities(metadata, 'local', {**profiles, 'critical': ['gpt-example', 'ultra']})

    def test_incomplete_or_malformed_profiles_fail_closed(self):
        metadata = 'gpt-example (supported reasoning efforts: low, high)'
        with self.assertRaises(ValueError):
            dispatch.refresh_capabilities(metadata, 'local', {'routine': ['gpt-example', 'low']})
        profiles = {kind: ['gpt-example', 'high'] for kind in ('bounded_read', 'routine', 'complex', 'critical')}
        with self.assertRaises(ValueError):
            dispatch.refresh_capabilities(metadata, 'local', {**profiles, 'routine': 'bad'})

    def test_archive_metadata_handles_reserved_characters_in_home(self):
        home = self.root / ('codex home#unicode-ı' if os.name == 'nt' else 'codex?home#unicode-ı')
        home.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(home / 'state_1.sqlite')) as db:
            db.execute('CREATE TABLE threads (id TEXT, archived INTEGER)')
            db.execute('INSERT INTO threads VALUES (?, ?)', ('example-session', 0))
            db.commit()
        self.assertIs(guard.archived_status(home, 'example-session'), False)


if __name__ == '__main__':
    unittest.main()
