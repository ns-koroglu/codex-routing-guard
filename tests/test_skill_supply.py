import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from routing import skill_supply as supply


class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root / 'inceleme ı & quote'
        self.home = self.root / 'codex home'
        self.raw = b'---\nname: useful-skill\ndescription: Inspect task fixtures.\n---\nRead local files only.\n'
        self.source = {'SKILL.md': self.raw, 'scripts/check.py': b'print("never run during install")\n'}
        self.info = {'default_branch': 'main', 'stargazers_count': 2500, 'license': {'spdx_id': 'MIT'}, 'archived': False}
        self.commit = 'a' * 40
        self.tree = {'tree': [{'path': 'skills/useful/' + name, 'type': 'blob', 'mode': '100644', 'size': len(raw)}
                              for name, raw in self.source.items()]}
        self.tree['tree'].append({'path': 'LICENSE', 'type': 'blob', 'mode': '100644', 'size': 12})

    def prepare(self):
        with patch.object(supply, 'api', side_effect=[self.info, {'sha': self.commit}, self.tree]), \
                patch.object(supply, 'fetch', side_effect=lambda url: b'MIT fixture license' if url.endswith('/LICENSE')
                             else self.source[url.split('/skills/useful/')[1]]):
            return supply.prepare('example/project', 'skills/useful', self.stage, 'Inspect fixtures', 'Avoid a known fixture parsing bug')

    def test_prepare_pins_commit_and_preserves_entire_reviewed_bundle(self):
        result = self.prepare()
        self.assertFalse(self.home.exists())
        self.assertEqual(result['manifest']['commit'], self.commit)
        self.assertEqual(result['manifest']['starsObserved'], 2500)
        for name, raw in self.source.items():
            self.assertEqual((self.stage / name).read_bytes(), raw)
        self.assertEqual((self.stage / '.routing-guard-licenses/LICENSE').read_bytes(), b'MIT fixture license')

    def test_preview_has_no_destination_side_effects_and_apply_records_provenance(self):
        result = self.prepare()
        preview = supply.install(self.stage, self.home, result['reviewDigest'])
        self.assertFalse(preview['installed'])
        self.assertFalse(self.home.exists())
        installed = supply.install(self.stage, self.home, result['reviewDigest'], True)
        destination = Path(installed['destination'])
        self.assertTrue(installed['installed'])
        self.assertFalse(installed['codeExecuted'])
        receipt = json.loads((destination / supply.RECEIPT).read_text(encoding='utf-8'))
        self.assertEqual(receipt['commit'], self.commit)
        self.assertEqual((destination / 'scripts/check.py').read_bytes(), self.source['scripts/check.py'])

    def test_later_edits_and_unreviewed_manifests_cannot_install(self):
        result = self.prepare()
        with self.assertRaisesRegex(ValueError, 'review digest'):
            supply.install(self.stage, self.home, '0' * 64, True)
        (self.stage / 'SKILL.md').write_bytes(self.raw + b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            supply.install(self.stage, self.home, result['reviewDigest'], True)
        self.assertFalse(self.home.exists())

    def test_untracked_files_refused_and_existing_user_skill_preserved(self):
        result = self.prepare()
        surprise = self.stage / 'extra.py'
        surprise.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'untracked'):
            supply.install(self.stage, self.home, result['reviewDigest'], True)
        surprise.unlink()
        destination = self.home / 'skills/useful-skill'
        destination.mkdir(parents=True)
        (destination / 'SKILL.md').write_bytes(b'user skill')
        with self.assertRaisesRegex(ValueError, 'Existing skill preserved'):
            supply.install(self.stage, self.home, result['reviewDigest'], True)
        self.assertEqual((destination / 'SKILL.md').read_bytes(), b'user skill')

    def test_source_threshold_license_archival_and_truncation_fail_without_stage(self):
        for update in ({'archived': True}, {'stargazers_count': 1}, {'license': None}):
            with self.subTest(update=update):
                with patch.object(supply, 'api', return_value={**self.info, **update}), self.assertRaises(ValueError):
                    supply.prepare('example/project', 'skills/useful', self.stage, 'task', 'benefit')
                self.assertFalse(self.stage.exists())
        self.tree['truncated'] = True
        with self.assertRaisesRegex(ValueError, 'truncated'):
            self.prepare()
        self.assertFalse(self.stage.exists())

    def test_special_files_case_aliases_and_reserved_receipt_names_refused(self):
        baseline = list(self.tree['tree'])
        for path, mode in (('link', '120000'), ('../outside', '100644'), ('NUL.txt', '100644'),
                           ('skill.md', '100644'), ('REVIEW.JSON', '100644'), ('.ROUTING-GUARD-SOURCE.JSON', '100644')):
            with self.subTest(path=path):
                self.tree['tree'] = baseline + [{'path': 'skills/useful/' + path, 'type': 'blob', 'mode': mode, 'size': 1}]
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.stage.exists())

    def test_symlink_in_stage_or_destination_refused(self):
        result = self.prepare()
        outside = self.root / 'outside'
        outside.mkdir()
        self.home.mkdir()
        try:
            (self.home / 'skills').symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest('Host does not permit symlink creation: ' + str(error))
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            supply.install(self.stage, self.home, result['reviewDigest'], True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_search_returns_reputation_signals_without_claiming_safety(self):
        with patch.object(supply, 'api', return_value={'items': [{'full_name': 'example/project', 'description': 'Agent skills', 'stargazers_count': 2500}]}) as api:
            result = supply.search('fixture parser', 1000)
        self.assertIn('stars%3A%3E%3D1000', api.call_args.args[0])
        self.assertEqual(result['candidates'][0]['full_name'], 'example/project')
        self.assertIn('not skill-level quality', result['note'])

    def test_portable_path_validation_rejects_windows_aliases_on_all_hosts(self):
        for value in ('../x', '/absolute', 'C:/x', 'a\\b', 'aux.py', 'folder./x', 'a//b', 'a:b', 'x/CON', 'x\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                supply.safe_path(value)

    def test_metadata_folded_quoted_and_invalid_names(self):
        for value in ('description: >-\n  A useful\n  fixture skill.', 'description: "A useful fixture skill."'):
            result = supply.metadata(('---\nname: useful-skill\n' + value + '\n---\n').encode())
            self.assertEqual(result['description'], 'A useful fixture skill.')
        with self.assertRaises(ValueError):
            supply.metadata(b'---\nname: ../../escape\ndescription: unsafe\n---\n')

    def test_network_error_never_publishes_partial_review(self):
        with patch.object(supply, 'api', side_effect=[self.info, {'sha': self.commit}, self.tree]), \
                patch.object(supply, 'fetch', side_effect=OSError('offline')):
            with self.assertRaisesRegex(OSError, 'offline'):
                supply.prepare('example/project', 'skills/useful', self.stage, 'task', 'benefit')
        self.assertFalse(self.stage.exists())


if __name__ == '__main__':
    unittest.main()
