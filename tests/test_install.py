import json
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import doctor
import install


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'custom codex home'
        self.cli = self.base / ('stable codex.cmd' if os.name == 'nt' else 'stable codex')
        self.cli.write_text('@echo codex-cli 0.166.0\n' if os.name == 'nt' else
                            '#!' + sys.executable + '\nprint("codex-cli 0.166.0")\n')
        self.cli.chmod(0o700)

    def do_install(self, recovery=False):
        updates, baseline = install.plan(self.home, recovery, str(self.cli), self.base)
        with install.locked(self.home):
            backup = install.apply(self.home, updates, baseline)
        return backup

    def test_preview_never_creates_home(self):
        updates, _ = install.plan(self.home)
        self.assertIn('skills/route-tasks/SKILL.md', updates)
        self.assertFalse(self.home.exists())

    def test_cli_preview_respects_custom_home_without_creating_it(self):
        value = subprocess.run([sys.executable, str(install.SOURCE / 'install.py'), '--codex-home', str(self.home)], capture_output=True, text=True)
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertFalse(json.loads(value.stdout)['applied'])
        self.assertFalse(self.home.exists())

    def test_routing_only_install_and_skill_discovery(self):
        self.do_install()
        self.assertFalse((self.home / 'hooks.json').exists())
        self.assertFalse((self.home / 'config.toml').exists())
        self.assertFalse((self.home / 'routing/capabilities.json').exists())
        self.assertTrue(doctor.inspect(self.home)['healthy'])
        value = subprocess.run([sys.executable, str(self.home / 'skills/route-tasks/scripts/routing.py'), '--status'],
                               env={**os.environ, 'CODEX_HOME': str(self.home)}, capture_output=True, text=True)
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertEqual(json.loads(value.stdout)['runtimeHome'], str(self.home))

    def test_recovery_preserves_unrelated_hooks_and_stays_disabled(self):
        self.home.mkdir()
        other = {'hooks': [{'type': 'command', 'command': 'echo user-hook'}], 'matcher': 'existing'}
        (self.home / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [other]}, 'userField': True}))
        self.do_install(True)
        hooks = json.loads((self.home / 'hooks.json').read_text(encoding='utf-8'))
        self.assertEqual(hooks['hooks']['Stop'][0], other)
        self.assertTrue(hooks['userField'])
        settings = json.loads((self.home / 'recovery-guard/settings.json').read_text(encoding='utf-8'))
        self.assertFalse(settings['enabled'])
        self.assertEqual(settings['codex_home'], str(self.home))
        command = settings['hook_command']
        decoded = base64.b64decode(command.split()[-1]).decode('utf-16-le') if os.name == 'nt' else command
        self.assertIn('--root', decoded)
        for event in install.EVENTS:
            commands = [h['command'] for group in hooks['hooks'][event] for h in group['hooks']]
            self.assertEqual(commands.count(settings['hook_command']), 1)

    def test_unmanaged_file_collision_preserves_everything(self):
        (self.home / 'routing').mkdir(parents=True)
        path = self.home / 'routing/dispatch.py'
        path.write_text('user code')
        with self.assertRaisesRegex(ValueError, 'Unmanaged'):
            install.plan(self.home)
        self.assertEqual(path.read_text(encoding='utf-8'), 'user code')
        self.assertFalse((self.home / 'skills').exists())

    def test_concurrent_edit_invalidates_whole_plan(self):
        updates, baseline = install.plan(self.home)
        (self.home / 'routing').mkdir(parents=True)
        (self.home / 'routing/POLICY.md').write_text('late user policy')
        with self.assertRaisesRegex(ValueError, 'changed after planning'):
            install.apply(self.home, updates, baseline)
        self.assertFalse((self.home / 'routing/dispatch.py').exists())

    def test_symlink_target_cannot_escape_home(self):
        self.home.mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.home / 'routing').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            install.plan(self.home)
        self.assertEqual(list(outside.iterdir()), [])

    def test_idempotent_reinstall_has_one_handler_per_event(self):
        self.do_install(True)
        self.do_install(True)
        hooks = json.loads((self.home / 'hooks.json').read_text(encoding='utf-8'))
        self.assertTrue(all(len(hooks['hooks'][event]) == 1 for event in install.EVENTS))

    def test_modified_owned_file_blocks_upgrade(self):
        self.do_install()
        (self.home / 'routing/POLICY.md').write_text('custom user policy')
        with self.assertRaisesRegex(ValueError, 'Later user edit'):
            install.plan(self.home)

    def test_clean_rollback_restores_original_hooks_and_preserves_other_files(self):
        self.home.mkdir()
        original = b'{"hooks":{},"userField":"retain"}\n'
        (self.home / 'hooks.json').write_bytes(original)
        backup = self.do_install(True)
        (self.home / 'keep.txt').write_text('unrelated')
        install.rollback(self.home, backup, True)
        self.assertEqual((self.home / 'hooks.json').read_bytes(), original)
        self.assertEqual((self.home / 'keep.txt').read_text(encoding='utf-8'), 'unrelated')
        self.assertFalse((self.home / 'routing/dispatch.py').exists())
        install.rollback(self.home, backup, True)

    def test_rollback_refuses_later_edits_before_restoring_anything(self):
        backup = self.do_install()
        protected = self.home / 'routing/README.md'
        protected.write_text('later edit')
        with self.assertRaisesRegex(ValueError, 'Later edit'):
            install.rollback(self.home, backup, True)
        self.assertTrue((self.home / 'routing/dispatch.py').is_file())
        self.assertEqual(protected.read_text(encoding='utf-8'), 'later edit')

    def test_corrupt_backup_refuses_whole_rollback(self):
        self.home.mkdir()
        (self.home / 'hooks.json').write_text('{"hooks":{}}')
        backup = self.do_install(True)
        records = json.loads((backup / 'restore.json').read_text(encoding='utf-8'))['files']
        (backup / records['hooks.json']['entry']).write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'Corrupt'):
            install.rollback(self.home, backup, True)
        self.assertTrue((self.home / 'routing/dispatch.py').is_file())

    def test_failed_write_rolls_back_prior_writes(self):
        updates, baseline = install.plan(self.home)
        original = install.atomic
        def fail_once(path, data, mode=0o600):
            if path == self.home / 'routing/README.md':
                raise OSError('injected disk failure')
            return original(path, data, mode)
        with patch.object(install, 'atomic', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                install.apply(self.home, updates, baseline)
        self.assertFalse((self.home / 'routing/dispatch.py').exists())
        self.assertFalse((self.home / install.MANIFEST).exists())

    def test_prerelease_cli_rejected_without_writes(self):
        self.cli.write_text('@echo codex-cli 0.166.0-alpha.5\n' if os.name == 'nt' else
                            '#!' + sys.executable + '\nprint("codex-cli 0.166.0-alpha.5")\n')
        with self.assertRaisesRegex(ValueError, 'stable Codex'):
            install.plan(self.home, True, str(self.cli))
        self.assertFalse(self.home.exists())

    def test_malformed_hooks_refused_without_replacing_them(self):
        self.home.mkdir()
        (self.home / 'hooks.json').write_text('{"hooks":{"Stop":"bad"}}')
        with self.assertRaisesRegex(ValueError, 'Invalid hook event'):
            install.plan(self.home, True, str(self.cli))
        self.assertFalse((self.home / 'routing').exists())

    def test_doctor_missing_home_has_no_side_effects(self):
        self.assertFalse(doctor.inspect(self.home)['healthy'])
        self.assertFalse(self.home.exists())

    def test_private_file_permissions(self):
        self.do_install(True)
        for name in ('hooks.json', 'recovery-guard/settings.json', install.MANIFEST):
            if os.name == 'nt':
                self.assertTrue((self.home / name).is_file())  # Native DACL tested separately.
            else:
                self.assertEqual((self.home / name).stat().st_mode & 0o777, 0o600)

    def test_activation_does_not_block_reinstall_or_disable_unchanged_recovery(self):
        self.do_install(True)
        path = self.home / 'recovery-guard/settings.json'
        settings = json.loads(path.read_text(encoding='utf-8'))
        settings['enabled'] = True
        path.write_text(json.dumps(settings))
        self.do_install(True)
        self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['enabled'])
        settings['verification_cwd'] = '/different/user/path'
        path.write_text(json.dumps(settings))
        with self.assertRaisesRegex(ValueError, 'Later user edit'):
            install.plan(self.home, True, str(self.cli), self.base)

    def test_changed_helper_disables_recovery_and_requires_new_trust(self):
        self.do_install(True)
        path = self.home / 'recovery-guard/settings.json'
        settings = json.loads(path.read_text(encoding='utf-8'))
        settings['enabled'] = True
        path.write_text(json.dumps(settings))
        new_source = self.base / 'new source'
        shutil.copytree(install.SOURCE, new_source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        script = new_source / 'recovery/guard.py'
        script.write_bytes(script.read_bytes() + b'\n')
        with patch.object(install, 'SOURCE', new_source):
            self.do_install(True)
        changed = json.loads(path.read_text(encoding='utf-8'))
        self.assertFalse(changed['enabled'])
        self.assertNotEqual(changed['hook_command'], settings['hook_command'])

    def test_rollback_preserves_edit_injected_after_initial_validation(self):
        backup = self.do_install()
        protected = self.home / 'skills/route-tasks/references/runtime.md'
        original = install.restore_one
        injected = False
        def concurrent_edit(path, directory, item):
            nonlocal injected
            original(path, directory, item)
            if not injected:
                protected.write_text('late edit during rollback')
                injected = True
        with patch.object(install, 'restore_one', side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError, 'Concurrent edit'):
                install.rollback(self.home, backup, True)
        self.assertEqual(protected.read_text(encoding='utf-8'), 'late edit during rollback')

    def test_corrupt_manifest_and_settings_have_clear_errors(self):
        self.home.mkdir()
        (self.home / install.MANIFEST).write_text('[]')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            install.plan(self.home)
        (self.home / 'recovery-guard').mkdir()
        (self.home / 'recovery-guard/settings.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'boolean'):
            doctor.inspect(self.home)

    def test_deactivate_then_initial_rollback_needs_no_account_or_trust_access(self):
        self.home = self.base / 'özel codex ı'
        backup = self.do_install(True)
        path = self.home / 'recovery-guard/settings.json'
        settings = json.loads(path.read_text(encoding='utf-8'))
        settings['enabled'] = True
        path.write_text(json.dumps(settings))
        value = subprocess.run([sys.executable, settings['installed_script'], '--root', str(self.home / 'recovery-guard'), 'deactivate'], capture_output=True, text=True)
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertFalse(json.loads(value.stdout)['enabled'])
        install.rollback(self.home, backup, True)
        self.assertFalse(path.exists())

    def test_installed_adapter_forwards_metrics_skills_and_recovery_and_waits(self):
        self.do_install(True)
        script = self.home / 'skills/route-tasks/scripts/routing.py'
        for args in (['metrics'], ['skills', '--help'], ['recovery', 'deactivate']):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                                        env={**os.environ, 'CODEX_HOME': str(self.home)}, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.strip())

    def test_runtime_dependency_change_also_disables_recovery_for_new_trust(self):
        self.do_install(True)
        path = self.home / 'recovery-guard/settings.json'
        settings = json.loads(path.read_text(encoding='utf-8'))
        settings['enabled'] = True
        path.write_text(json.dumps(settings), encoding='utf-8')
        new_source = self.base / 'new source'
        shutil.copytree(install.SOURCE, new_source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        support = new_source / 'routing/runtime_support.py'
        support.write_bytes(support.read_bytes() + b'\n')
        with patch.object(install, 'SOURCE', new_source):
            self.do_install(True)
        changed = json.loads(path.read_text(encoding='utf-8'))
        self.assertFalse(changed['enabled'])
        self.assertNotEqual(changed['hook_command'], settings['hook_command'])

    def test_rollback_preserves_original_windows_dacl(self):
        if os.name != 'nt':
            self.skipTest('Native Windows DACL rollback')
        self.home.mkdir()
        path = self.home / 'hooks.json'
        path.write_bytes(b'{"hooks":{}}')
        before = install.windows_dacl(path)
        backup = self.do_install(True)
        install.rollback(self.home, backup, True)
        self.assertEqual(install.windows_dacl(path), before)


if __name__ == '__main__':
    unittest.main()
