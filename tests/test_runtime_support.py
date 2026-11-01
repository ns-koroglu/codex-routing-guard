import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from recovery import guard
from routing import runtime_support as support


class RuntimeSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_lock_serializes_real_processes_and_releases_after_crash(self):
        path = self.root / 'process.lock'
        script = self.root / 'lock_child.py'
        script.write_text('import sys, time\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n'
                          'from routing.runtime_support import file_lock\n'
                          'with file_lock(sys.argv[2]):\n print("locked", flush=True)\n time.sleep(30)\n', encoding='utf-8')
        child = subprocess.Popen([sys.executable, str(script), str(Path(support.__file__).parents[1]), str(path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            with self.assertRaises(TimeoutError), support.file_lock(path, timeout=0.1):
                self.fail('Another process owns this lock')
        finally:
            child.kill()
            child.communicate(timeout=5)
        with support.file_lock(path, timeout=1):
            pass

    def test_atomic_write_failure_preserves_bytes_and_removes_temporary_file(self):
        path = self.root / 'state.json'
        support.atomic_write(path, b'old')
        with patch.object(support.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                support.atomic_write(path, b'new')
        self.assertEqual(path.read_bytes(), b'old')
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_native_shell_command_roundtrips_unicode_quotes_and_metacharacters(self):
        path = self.root / "script ı & ' quote.py"
        path.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
        arguments = [sys.executable, str(path), 'dollar $HOME ; & |', "apostrophe ' and ı", 'spaces here']
        command = support.shell_command(arguments)
        # Exercise the actual platform shell; payload is inert and no input executes.
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), arguments[2:])

    def test_windows_private_acl_is_protected_and_has_no_everyone_grant(self):
        if os.name != 'nt':
            self.skipTest('Native Windows DACL check')
        path = self.root / 'private.json'
        support.atomic_write(path, b'private')
        saved = self.root / 'acl.txt'
        result = subprocess.run(['icacls', str(path), '/save', str(saved), '/q'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        sddl = saved.read_text(encoding='utf-16-le')
        self.assertIn('D:P', sddl)
        self.assertNotIn(';;;WD)', sddl)
        self.assertNotIn(';;;BU)', sddl)

    def test_shell_command_preserves_hook_exit_code(self):
        result = subprocess.run(support.shell_command([sys.executable, '-c', 'raise SystemExit(2)']),
                                shell=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 2)

    def test_native_windows_junction_is_rejected_without_python312_helpers(self):
        if os.name != 'nt':
            self.skipTest('Native Windows junction')
        outside = self.root / 'outside'
        outside.mkdir()
        junction = self.root / 'junction'
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(outside)],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(support.is_link(junction))
        from routing import skill_supply
        with self.assertRaisesRegex(ValueError, 'junction'):
            skill_supply.no_links(junction / 'skill')

    def test_rpc_reads_real_stdio_pipe_and_closes_its_own_process(self):
        path = self.root / 'fake_metadata.py'
        path.write_text('import json,sys\n'
                        'for line in sys.stdin:\n'
                        ' v=json.loads(line)\n'
                        ' if "id" in v:\n'
                        '  print(json.dumps({"id":v["id"],"result":{"method":v["method"]}}),flush=True)\n', encoding='utf-8')
        original = subprocess.Popen
        with patch.object(guard.subprocess, 'Popen', side_effect=lambda *a, **kw: original([sys.executable, '-u', str(path)], **kw)):
            rpc = guard.Rpc('fixture', self.root)
        try:
            self.assertEqual(rpc.call('thread/read', {})['method'], 'thread/read')
        finally:
            rpc.close()
        self.assertIsNotNone(rpc.proc.poll())
        self.assertFalse(rpc.reader.is_alive())

    def test_daemon_tick_disabled_or_not_due_never_opens_metadata(self):
        store = guard.Store(self.root / 'recovery')
        settings = store.root / 'settings.json'
        support.atomic_write(settings, b'{"enabled": false}')
        with patch.object(guard, 'run_recovery_check') as check:
            self.assertEqual(guard.daemon_tick(store, 'fixture', self.root), [])
            check.assert_not_called()
        support.atomic_write(settings, b'{"enabled": true}')
        guard.register(store, 'session', self.root, 100)
        with patch.object(guard, 'run_recovery_check') as check:
            self.assertEqual(guard.daemon_tick(store, 'fixture', self.root, now=101), [])
            check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
