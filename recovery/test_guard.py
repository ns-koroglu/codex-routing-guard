import copy
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from recovery import guard


class FakeRuntime:
    def __init__(self):
        self.snap = {'archived': False, 'thread': {'status': {'type': 'idle'}, 'modelProvider': 'openai'},
                     'turn': {'id': 'failed-turn', 'status': 'failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}}
        self.bucket = {'limitId': 'codex', 'primary': {'usedPercent': 10, 'resetsAt': 30000, 'windowDurationMins': 300},
                       'secondary': {'usedPercent': 50, 'resetsAt': 90000, 'windowDurationMins': 10080},
                       'credits': {'hasCredits': False, 'unlimited': False}, 'individualLimit': None}
        self.sent = []
        self.calls = 0
        self.before_second = None
        self.fail = False
        self.hooks_trusted = True
        self.after_trusted = None
        self.after_snapshot = {}
        self.after_allowance = None
        self.on_enqueue = None
        self.rpc = self
        self.closed = False

    def close(self):
        self.closed = True

    def trusted(self, cwd, command):
        result = self.hooks_trusted
        if self.after_trusted:
            self.after_trusted()
        return result

    def snapshot(self, sid):
        self.calls += 1
        if self.calls == 2 and self.before_second:
            self.before_second()
        result = copy.deepcopy(self.snap)
        if self.calls in self.after_snapshot:
            self.after_snapshot[self.calls]()
        return result

    def allowance(self):
        result = copy.deepcopy(self.bucket)
        if self.after_allowance:
            self.after_allowance()
        return result

    def enqueue(self, sid, message):
        self.sent.append(message)
        if self.on_enqueue:
            self.on_enqueue()
        if self.fail:
            raise TimeoutError()
        return 'queued'


class GuardTest(unittest.TestCase):
    def test_supported_new_strong_profile_and_unknown_profiles_fail_conservatively(self):
        self.assertFalse(guard.is_lower('gpt-6.1-terra'))
        for model in ('gpt-6-luna','gpt-5.6-sol','unknown'):
            self.assertTrue(guard.is_lower(model))
        self.assertFalse(guard.is_lower(None))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = guard.Store(self.root / 'guard')
        (self.store.root / 'settings.json').write_text(json.dumps({'enabled': True, 'hook_command': 'guard hook'}))
        self.sid = 'session-test'
        self.now = guard.DELAY + 1
        self.runtime = FakeRuntime()
        self.event('SessionStart', source='startup', cwd=str(self.root), now=0)
        self.event('UserPromptSubmit', prompt='Implement the task', model='gpt-6.1-sol')
        self.cp()

    def tearDown(self):
        self.temp.cleanup()

    def event(self, name, now=None, **kw):
        return guard.hook(self.store, dict(session_id=self.sid, hook_event_name=name, **kw), now=now)

    def cp(self, **kw):
        value = dict(objective='Implement and validate the requested work', status='in_progress',
                     next_action='Run the meaningful test', completed=['Prepared implementation'],
                     pending=['Verify behavior'], validation=[], artifacts=[])
        value.update(kw)
        guard.checkpoint(self.store, self.sid, value)

    def recover(self):
        return guard.recover_one(self.store, self.sid, self.runtime, self.now)

    def test_deadline_and_resume_do_not_rearm(self):
        self.event('SessionStart', source='compact', now=50000)
        self.event('SessionStart', source='resume', now=50001)
        self.event('SessionStart', source='startup', cwd=str(self.root), now=50002)
        self.assertEqual(self.store.load(self.sid)['first_check_at'], 18120)
        self.now = 18119
        self.assertEqual(self.recover(), 'not_due')
        self.assertEqual(self.runtime.calls, 0)

    def test_complete_cancels_without_runtime_read(self):
        self.cp(status='complete', pending=[])
        self.assertEqual(self.event('Stop', model='gpt-6.1-sol'), {})
        self.assertEqual(self.recover(), 'not_due')
        self.assertEqual(self.runtime.calls, 0)

    def test_waiting_and_interrupt_cancel(self):
        self.cp(status='waiting_for_user')
        self.assertEqual(self.recover(), 'not_due')
        self.event('UserPromptSubmit', prompt='Proceed')
        self.cp()
        self.event('Interrupt')
        self.assertEqual(self.recover(), 'not_due')

    def test_archived_cancels(self):
        self.runtime.snap['archived'] = True
        self.assertEqual(self.recover(), 'cancelled')
        self.assertFalse(self.store.load(self.sid)['armed'])

    def test_active_unknown_and_nonquota_do_not_queue(self):
        self.runtime.snap['thread']['status']['type'] = 'active'
        self.assertEqual(self.recover(), 'busy_or_unknown')
        self.runtime.snap['thread']['status']['type'] = 'idle'
        self.now += 60
        self.runtime.snap['turn']['error']['codexErrorInfo'] = 'other'
        self.assertEqual(self.recover(), 'not_quota_interrupted')
        self.assertFalse(self.runtime.sent)

    def test_weekly_and_primary_reset_obey_latest_blocked_window(self):
        self.runtime.bucket['primary']['usedPercent'] = 100
        self.runtime.bucket['secondary']['usedPercent'] = 100
        self.assertEqual(self.recover(), 'waiting_for_allowance')
        self.assertEqual(self.store.load(self.sid)['next_check_at'], 90120)
        self.assertFalse(self.runtime.sent)

    def test_verified_five_hour_cause_and_one_dispatch(self):
        self.runtime.bucket['primary']['usedPercent'] = 100
        self.assertEqual(self.recover(), 'waiting_for_allowance')
        self.now = 30120
        self.runtime.bucket['primary'].update(usedPercent=0, resetsAt=48000)
        self.assertEqual(self.recover(), 'queued')
        self.assertIn('the five-hour usage limit', self.runtime.sent[0])
        self.now += 60
        self.assertEqual(self.recover(), 'already_attempted')
        self.assertEqual(len(self.runtime.sent), 1)

    def test_unobserved_limit_cause_is_generic(self):
        self.assertEqual(self.recover(), 'queued')
        self.assertIn('an account usage limit', self.runtime.sent[0])
        self.assertNotIn('the five-hour usage limit', self.runtime.sent[0])

    def test_uncertain_dispatch_is_not_retried(self):
        self.runtime.fail = True
        self.assertEqual(self.recover(), 'dispatch_uncertain')
        self.now += 60
        self.assertEqual(self.recover(), 'already_attempted')
        self.assertEqual(len(self.runtime.sent), 1)

    def test_completion_between_runtime_reads_prevents_dispatch(self):
        def complete():
            self.cp(status='complete', pending=[])
            self.event('Stop', model='gpt-6.1-sol')
        self.runtime.before_second = complete
        self.assertEqual(self.recover(), 'state_changed')
        self.assertFalse(self.runtime.sent)

    def test_runtime_change_defers_without_spin(self):
        self.runtime.before_second = lambda: self.runtime.snap['thread']['status'].update(type='active')
        self.assertEqual(self.recover(), 'runtime_changed')
        self.assertEqual(self.store.load(self.sid)['next_check_at'], self.now + 60)

    def test_provider_and_credits_fail_closed(self):
        self.runtime.snap['thread']['modelProvider'] = 'custom'
        self.assertEqual(self.recover(), 'provider_not_supported')
        self.assertFalse(self.store.load(self.sid)['armed'])
        self.event('UserPromptSubmit', prompt='Proceed')
        self.cp()
        self.runtime.snap['thread']['modelProvider'] = 'openai'
        self.runtime.bucket['credits']['hasCredits'] = True
        with self.assertRaisesRegex(RuntimeError, 'credit fallback'):
            self.recover()
        self.assertFalse(self.runtime.sent)

    def test_exact_one_use_recovery_and_stale_pause_block(self):
        self.assertEqual(self.recover(), 'queued')
        event = dict(session_id=self.sid, hook_event_name='UserPromptSubmit', prompt=self.runtime.sent[0])
        self.assertNotIn('decision', guard.hook(self.store, event, recovery_archived=False))
        self.assertEqual(guard.hook(self.store, event, recovery_archived=False)['decision'], 'block')
        self.event('Interrupt')
        event['prompt'] = 'RECOVERY_RESUME:' + self.sid + ':old\nstale'
        self.assertEqual(guard.hook(self.store, event, recovery_archived=False)['decision'], 'block')
        self.assertFalse(self.store.load(self.sid)['armed'])

    def test_new_revision_rejects_old_dispatch(self):
        self.assertEqual(self.recover(), 'queued')
        self.event('UserPromptSubmit', prompt='A different request')
        event = dict(session_id=self.sid, hook_event_name='UserPromptSubmit', prompt=self.runtime.sent[0])
        self.assertEqual(guard.hook(self.store, event, recovery_archived=False)['decision'], 'block')

    def test_missing_state_recovery_blocks(self):
        event = dict(session_id='missing', hook_event_name='UserPromptSubmit', prompt='RECOVERY_RESUME:missing:old')
        self.assertEqual(guard.hook(self.store, event)['decision'], 'block')

    def test_missing_disabled_or_untrusted_settings_never_dispatch(self):
        settings = self.store.root / 'settings.json'
        settings.unlink()
        self.assertEqual(self.recover(), 'settings_missing')
        settings.write_text(json.dumps({'enabled': False, 'hook_command': 'guard hook'}))
        self.assertEqual(self.recover(), 'hooks_not_activated_or_trusted')
        self.now += 900
        settings.write_text(json.dumps({'enabled': True, 'hook_command': 'guard hook'}))
        self.runtime.hooks_trusted = False
        self.assertEqual(self.recover(), 'hooks_not_activated_or_trusted')
        self.assertFalse(self.runtime.sent)

    def test_recovery_hook_cli_fails_closed_on_bad_settings(self):
        (self.store.root / 'settings.json').write_text('{bad json')
        event = dict(session_id=self.sid, hook_event_name='UserPromptSubmit', prompt='RECOVERY_RESUME:invalid')
        result = subprocess.run([sys.executable, str(Path(guard.__file__)), '--root', str(self.store.root), 'hook'], input=json.dumps(event), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['decision'], 'block')

    def test_repeated_stop_exception_pauses_without_loop(self):
        self.cp(status='complete', pending=[])
        with self.store.edit(self.sid) as state:
            state['checkpoint']['artifacts'] = [{'path': str(self.root / 'missing'), 'sha256': 'old'}]
        event = dict(session_id=self.sid, hook_event_name='Stop', model='gpt-6.1-sol', stop_hook_active=True)
        result = subprocess.run([sys.executable, str(Path(guard.__file__)), '--root', str(self.store.root), 'hook'], input=json.dumps(event), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['continue'])
        self.assertFalse(self.store.load(self.sid)['armed'])

    def test_cold_interrupted_turn_defers_without_resuming(self):
        self.runtime.snap['turn'].update(status='interrupted', error=None)
        self.assertEqual(self.recover(), 'not_quota_interrupted')
        self.assertTrue(self.store.load(self.sid)['armed'])
        self.assertEqual(self.store.load(self.sid)['next_check_at'], self.now + 900)

    def test_child_start_requires_final_review_even_if_child_crashes(self):
        self.event('SubagentStart', agent_id='child')
        self.cp(status='complete', pending=[])
        self.assertTrue(self.store.load(self.sid)['weak_work'])
        self.assertEqual(self.event('Stop', model='gpt-6.1-sol')['decision'], 'block')

    def test_public_trust_event_names_and_all_hooks_required(self):
        runtime = object.__new__(guard.Runtime)
        names = ['sessionStart', 'userPromptSubmit', 'subagentStart', 'subagentStop', 'stop', 'interrupt']
        data = {'data': [{'hooks': [dict(command='guard hook', enabled=True, trustStatus='trusted', eventName=n) for n in names]}]}
        class FakeRpc:
            def call(self, method, params): return data
        runtime.rpc = FakeRpc()
        self.assertTrue(runtime.trusted('/tmp', 'guard hook'))
        data['data'][0]['hooks'][0]['trustStatus'] = 'untrusted'
        self.assertFalse(runtime.trusted('/tmp', 'guard hook'))

    def test_luna_cannot_self_approve(self):
        self.event('UserPromptSubmit', prompt='Small task', model='gpt-6-luna')
        self.cp(status='complete', pending=[])
        guard.review_primary(self.store, self.sid, 'approved', 'Claimed review')
        self.assertEqual(self.event('Stop', model='gpt-6-luna')['decision'], 'block')
        self.assertFalse(self.store.load(self.sid)['completion_confirmed'])
        self.assertEqual(self.event('Stop', model='gpt-6.1-sol'), {})
        self.assertTrue(self.store.load(self.sid)['completion_confirmed'])

    def test_actual_strong_child_receipt_accepted_config_alone_rejected(self):
        self.event('UserPromptSubmit', prompt='Task', model='gpt-6-luna')
        self.event('SubagentStart', agent_id='child')
        self.cp(status='complete', pending=[])
        receipt = 'RECOVERY_REVIEW:' + json.dumps(dict(verdict='approved', summary='Reviewed artifacts and tests', checkpoint_digest=self.store.load(self.sid)['checkpoint_digest']))
        event = dict(session_id=self.sid, hook_event_name='SubagentStop', model='gpt-6.1-sol', agent_id='child', last_assistant_message=receipt)
        child = dict(id='child', parentThreadId=self.sid, model='gpt-6.1-sol', reasoningEffort='high')
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertNotIn('verified_review', self.store.load(self.sid))
        child.update(execution_model='gpt-6.1-sol', execution_effort='high')
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertEqual(self.event('Stop', model='gpt-6-luna'), {})

    def test_artifact_change_invalidates_review(self):
        path = self.root / 'artifact.txt'
        path.write_text('before')
        self.event('UserPromptSubmit', prompt='Task', model='gpt-6-luna')
        self.cp(status='complete', pending=[], artifacts=[str(path)])
        guard.review_primary(self.store, self.sid, 'approved', 'Reviewed actual file')
        path.write_text('after')
        self.assertEqual(self.event('Stop', model='gpt-6.1-sol')['decision'], 'block')
        self.assertFalse(self.store.load(self.sid)['completion_confirmed'])

    def test_pending_and_bounded_stop_loop(self):
        self.cp(status='complete')
        self.assertEqual(self.event('Stop')['decision'], 'block')
        self.assertEqual(self.event('Stop', stop_hook_active=True)['decision'], 'block')
        self.assertFalse(self.event('Stop', stop_hook_active=True)['continue'])
        self.event('UserPromptSubmit', prompt='Resolve blocker')
        self.assertEqual(self.store.load(self.sid)['stop_blocks'], 0)

    def test_checkpoint_size_bound_and_no_whole_history_rpc(self):
        with self.assertRaisesRegex(ValueError, '16 KiB'):
            self.cp(objective='x' * 17000)
        class FakeRpc:
            def __init__(self): self.calls = []
            def call(self, method, params):
                self.calls.append((method, params))
                return {'thread': {}, 'data': []}
        runtime = object.__new__(guard.Runtime)
        runtime.rpc = FakeRpc();runtime.home = str(self.root)
        runtime.snapshot(self.sid)
        self.assertEqual(runtime.rpc.calls, [('thread/read', {'threadId': self.sid, 'includeTurns': False}), ('thread/turns/list', {'threadId': self.sid, 'limit': 1, 'itemsView': 'notLoaded'})])

    def test_api_account_disallowed(self):
        runtime = object.__new__(guard.Runtime)
        class FakeRpc:
            def call(self, method, params): return {'account': {'type': 'apiKey'}}
        runtime.rpc = FakeRpc()
        with self.assertRaisesRegex(RuntimeError, 'no API-credit fallback'):
            runtime.allowance()

    def test_bounded_execution_context_and_wrong_turn_rejected(self):
        sessions = self.root / 'sessions';sessions.mkdir()
        path = sessions / 'rollout-child.jsonl'
        path.write_text(json.dumps({'type': 'turn_context', 'payload': {'turn_id': 't', 'model': 'gpt-6.1-sol', 'effort': 'high'}}) + '\n')
        child = {'path': str(path)}
        event = dict(agent_id='child', agent_transcript_path=str(path), turn_id='t')
        self.assertEqual(guard.reviewer_execution(child, event, self.root)['execution_model'], 'gpt-6.1-sol')
        event['turn_id'] = 'wrong'
        self.assertNotIn('execution_model', guard.reviewer_execution(child, event, self.root))

    def repair_reason(self):
        self.event('UserPromptSubmit', prompt='New task without a checkpoint')
        return self.event('Stop')['reason']

    def test_fresh_repair_is_one_use_and_preserves_task_checkpoint(self):
        reason = self.repair_reason()
        before = self.store.load(self.sid)
        result = self.event('UserPromptSubmit', prompt=reason)
        self.assertNotIn('decision', result)
        after = self.store.load(self.sid)
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(after['request_id'], before['request_id'])
        self.assertEqual(after['checkpoint'], before['checkpoint'])
        self.assertEqual(self.event('UserPromptSubmit', prompt=reason)['decision'], 'block')
        self.cp(status='complete', pending=[])
        self.assertEqual(self.event('Stop', model='gpt-6.1-sol'), {})
        self.assertTrue(self.store.load(self.sid)['completion_confirmed'])

    def test_interrupt_invalidates_repair_and_stop_does_not_rearm(self):
        reason = self.repair_reason()
        self.event('Interrupt')
        self.assertEqual(self.event('UserPromptSubmit', prompt=reason)['decision'], 'block')
        self.assertEqual(self.event('Stop'), {})
        state = self.store.load(self.sid)
        self.assertEqual(state['status'], 'paused')
        self.assertFalse(state['armed'])
        self.assertNotIn('pending_continuation', state)

    def test_new_request_rejects_old_repair_without_new_revision(self):
        reason = self.repair_reason()
        self.event('UserPromptSubmit', prompt='Different user request')
        revision = self.store.load(self.sid)['revision']
        self.assertEqual(self.event('UserPromptSubmit', prompt=reason)['decision'], 'block')
        self.assertEqual(self.store.load(self.sid)['revision'], revision)

    def test_terminal_checkpoint_invalidates_repair(self):
        for status in ('complete', 'waiting_for_user', 'paused'):
            with self.subTest(status=status):
                reason = self.repair_reason()
                self.cp(status=status, pending=[])
                self.assertEqual(self.event('UserPromptSubmit', prompt=reason)['decision'], 'block')
                self.assertNotIn('pending_continuation', self.store.load(self.sid))

    def test_missing_and_legacy_repair_tokens_fail_closed(self):
        missing = dict(session_id='missing', hook_event_name='UserPromptSubmit', prompt='RECOVERY_GUARD_CONTINUE:missing:1')
        self.assertEqual(guard.hook(self.store, missing)['decision'], 'block')
        reason = 'RECOVERY_GUARD_CONTINUE:' + self.sid + ':1\nold unbound reason'
        with self.store.edit(self.sid) as state:
            state['continuation_reason'] = reason
        self.assertEqual(self.event('UserPromptSubmit', prompt=reason)['decision'], 'block')

    def test_repeated_repair_stops_without_stop_hook_flag(self):
        first = self.repair_reason()
        self.event('UserPromptSubmit', prompt=first)
        second = self.event('Stop')['reason']
        self.assertNotEqual(first, second)
        self.assertEqual(self.event('UserPromptSubmit', prompt=first)['decision'], 'block')
        self.event('UserPromptSubmit', prompt=second)
        self.assertFalse(self.event('Stop')['continue'])
        self.assertFalse(self.store.load(self.sid)['armed'])
        self.assertEqual(self.event('UserPromptSubmit', prompt=second)['decision'], 'block')

    def child_receipt(self, agent_id='child', **metadata):
        receipt = 'RECOVERY_REVIEW:' + json.dumps(dict(verdict='approved', summary='Reviewed actual final artifacts',
            checkpoint_digest=self.store.load(self.sid)['checkpoint_digest']))
        event = dict(session_id=self.sid, hook_event_name='SubagentStop', agent_id=agent_id, last_assistant_message=receipt)
        child = dict(id=agent_id, parentThreadId=self.sid, model='gpt-6.1-sol',
                     execution_model='gpt-6.1-sol', execution_effort='high')
        child.update(metadata)
        return event, child

    def test_late_child_cannot_review_identical_checkpoint_in_new_request(self):
        self.event('UserPromptSubmit', prompt='First request', model='gpt-6-luna', turn_id='request-A')
        self.event('SubagentStart', agent_id='old-child')
        self.cp(status='complete', pending=[])
        event, child = self.child_receipt('old-child')
        old_digest = self.store.load(self.sid)['checkpoint_digest']
        self.event('UserPromptSubmit', prompt='Second request', model='gpt-6-luna', turn_id='request-B')
        self.cp(status='complete', pending=[])
        self.assertEqual(self.store.load(self.sid)['checkpoint_digest'], old_digest)
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertNotIn('verified_review', self.store.load(self.sid))
        self.assertEqual(self.event('Stop', model='gpt-6-luna')['decision'], 'block')

    def test_missing_child_start_and_legacy_request_identity_reject_review(self):
        self.event('UserPromptSubmit', prompt='Weak work', model='gpt-6-luna')
        self.cp(status='complete', pending=[])
        event, child = self.child_receipt()
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertNotIn('verified_review', self.store.load(self.sid))
        with self.store.edit(self.sid) as state:
            state.pop('request_id', None)
        self.event('SubagentStart', agent_id='child')
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertNotIn('verified_review', self.store.load(self.sid))
        guard.review_primary(self.store, self.sid, 'approved', 'Actual stronger primary fallback review')
        self.assertEqual(self.event('Stop', model='gpt-6.1-sol'), {})

    def test_child_receipt_checks_parent_effort_and_is_consumed(self):
        self.event('UserPromptSubmit', prompt='Task', model='gpt-6-luna')
        self.event('SubagentStart', agent_id='child')
        self.cp(status='complete', pending=[])
        event, child = self.child_receipt()
        for field, value in [('parentThreadId', 'different'), ('execution_effort', None), ('execution_model', 'gpt-6-luna')]:
            with self.subTest(field=field):
                bad = dict(child, **{field: value})
                guard.hook(self.store, event, reviewer_metadata=bad)
                self.assertNotIn('verified_review', self.store.load(self.sid))
        guard.hook(self.store, event, reviewer_metadata=child)
        state = self.store.load(self.sid)
        self.assertTrue(state['child_starts']['child']['consumed'])
        self.assertEqual(state['verified_review']['revision'], state['revision'])
        with self.store.edit(self.sid) as state:
            state.pop('verified_review')
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertNotIn('verified_review', self.store.load(self.sid))

    def test_missing_execution_turn_is_not_runtime_evidence(self):
        sessions = self.root / 'sessions'
        sessions.mkdir()
        path = sessions / 'rollout-child.jsonl'
        path.write_text(json.dumps({'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'high'}}) + '\n')
        event = dict(agent_id='child', agent_transcript_path=str(path))
        self.assertNotIn('execution_model', guard.reviewer_execution({'path': str(path)}, event, self.root))

    def test_current_child_cannot_review_prior_request_checkpoint(self):
        self.event('UserPromptSubmit', prompt='New request without a current checkpoint', model='gpt-6-luna')
        self.event('SubagentStart', agent_id='child')
        event, child = self.child_receipt()
        guard.hook(self.store, event, reviewer_metadata=child)
        self.assertNotIn('verified_review', self.store.load(self.sid))
        self.cp(status='complete', pending=[])
        self.assertEqual(self.event('Stop', model='gpt-6-luna')['decision'], 'block')

    def test_quota_cause_is_cleared_on_new_request(self):
        self.runtime.bucket['primary']['usedPercent'] = 100
        self.assertEqual(self.recover(), 'waiting_for_allowance')
        self.event('UserPromptSubmit', prompt='Different task')
        self.assertNotIn('limit_provenance', self.store.load(self.sid))
        self.cp()
        self.runtime.snap['turn']['id'] = 'new-failed-turn'
        self.now = 30120
        self.runtime.bucket['primary'].update(usedPercent=0, resetsAt=48000)
        self.assertEqual(self.recover(), 'queued')
        self.assertIn('an account usage limit', self.runtime.sent[-1])

    def test_five_hour_wording_requires_same_failure_and_window_provenance(self):
        for mutation in ('failed_turn', 'primary_window', 'secondary_window', 'limit_id', 'legacy_unbound'):
            with self.subTest(mutation=mutation):
                self.setUp_isolated_cause_fixture()
                if mutation == 'failed_turn':
                    self.runtime.snap['turn']['id'] = 'different-failed-turn'
                elif mutation == 'primary_window':
                    self.runtime.bucket['primary']['resetsAt'] = 90000
                elif mutation == 'secondary_window':
                    self.runtime.bucket['secondary']['resetsAt'] = 100000
                elif mutation == 'limit_id':
                    self.runtime.bucket['limitId'] = 'another-bucket'
                else:
                    with self.store.edit(self.sid) as state:
                        state.pop('limit_provenance')
                        state['limit_cause'] = 'five_hour'
                self.assertEqual(self.recover(), 'queued')
                self.assertNotIn('the five-hour usage limit', self.runtime.sent[-1])

    def setUp_isolated_cause_fixture(self):
        self.event('UserPromptSubmit', prompt='Task for provenance test')
        self.cp()
        self.runtime = FakeRuntime()
        self.runtime.snap['turn']['id'] = 'failed-revision-' + str(self.store.load(self.sid)['revision'])
        self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
        self.runtime.bucket['primary']['resetsAt'] = self.now + 100
        self.runtime.bucket['primary']['usedPercent'] = 100
        self.assertEqual(self.recover(), 'waiting_for_allowance')
        self.now = self.runtime.bucket['primary']['resetsAt'] + 120
        self.runtime.bucket['primary'].update(usedPercent=0, resetsAt=self.now + 17880)

    def test_failed_turn_identity_must_match_current_turn_when_known(self):
        self.event('UserPromptSubmit', prompt='Current turn', turn_id='new-turn')
        self.cp()
        self.assertEqual(self.recover(), 'failed_turn_not_current')
        self.assertFalse(self.runtime.sent)

    def test_checkpoint_approval_phase_unarms_and_invalidates_internal_requests(self):
        self.assertEqual(self.recover(), 'queued')
        queued = self.runtime.sent[-1]
        reason = self.repair_reason()
        self.cp(authorization_phase='awaiting_approval')
        self.assertEqual(self.recover(), 'not_due')
        self.assertEqual(self.event('Stop'), {})
        self.assertEqual(self.event('UserPromptSubmit', prompt=reason)['decision'], 'block')
        event = dict(session_id=self.sid, hook_event_name='UserPromptSubmit', prompt=queued)
        self.assertEqual(guard.hook(self.store, event, recovery_archived=False)['decision'], 'block')
        self.assertEqual(self.store.load(self.sid)['status'], 'awaiting_approval')
        self.assertFalse(self.store.load(self.sid)['armed'])

    def test_audit_only_and_legacy_scope_are_explicit_in_recovery(self):
        self.cp(authorization_phase='audit_only')
        self.assertEqual(self.recover(), 'queued')
        self.assertIn('audit_only', self.runtime.sent[-1])
        self.assertIn('do not implement, install', self.runtime.sent[-1])
        self.assertIn('grants no new implementation authority', guard.authorization_context({}))
        self.assertIn('already established scope', guard.authorization_context({}))
        with self.assertRaisesRegex(ValueError, 'authorization phase'):
            self.cp(authorization_phase='implicit_full_access')

    def test_revision_change_during_allowance_cannot_save_stale_cause(self):
        self.runtime.bucket['primary']['usedPercent'] = 100
        allowance = self.runtime.allowance
        def changed():
            self.event('UserPromptSubmit', prompt='New request during old allowance read')
            self.cp()
            return allowance()
        self.runtime.allowance = changed
        self.assertEqual(self.recover(), 'state_changed')
        self.assertNotIn('limit_provenance', self.store.load(self.sid))

    def test_same_turn_id_becoming_complete_during_recheck_does_not_queue(self):
        self.runtime.before_second = lambda: self.runtime.snap['turn'].update(status='completed', error=None)
        self.assertEqual(self.recover(), 'runtime_changed')
        self.assertFalse(self.runtime.sent)

    def test_stop_exception_repair_is_bound_and_bounded_without_flag(self):
        self.cp(status='complete', pending=[])
        with self.store.edit(self.sid) as state:
            state['checkpoint']['artifacts'] = [{'path': str(self.root / 'missing'), 'sha256': 'old'}]
        event = dict(session_id=self.sid, hook_event_name='Stop', model='gpt-6.1-sol')
        def cli_stop():
            result = subprocess.run([sys.executable, str(Path(guard.__file__)), '--root', str(self.store.root), 'hook'],
                                    input=json.dumps(event), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            return json.loads(result.stdout)
        revision = self.store.load(self.sid)['revision']
        for _ in range(2):
            reason = cli_stop()['reason']
            self.assertTrue(reason.startswith('RECOVERY_GUARD_CONTINUE:'))
            self.assertNotIn('decision', self.event('UserPromptSubmit', prompt=reason))
            self.assertEqual(self.store.load(self.sid)['revision'], revision)
        self.assertFalse(cli_stop()['continue'])
        self.assertFalse(self.store.load(self.sid)['armed'])

    def replace_request_during_observation(self):
        self.event('UserPromptSubmit', prompt='New user request during old recovery metadata',
                   turn_id='new-request-turn-' + str(self.store.load(self.sid)['revision']))
        self.cp(objective='New request scope', authorization_phase='execution')
        self.expected_after_race = self.store.load(self.sid)

    def test_stale_early_observations_never_mutate_new_request(self):
        cases = ('untrusted', 'archived', 'child', 'busy', 'unknown_archive', 'completed',
                 'interrupted', 'wrong_turn', 'checkpoint_required', 'provider')
        for case in cases:
            with self.subTest(case=case):
                self.event('UserPromptSubmit', prompt='Old task for ' + case)
                self.cp()
                self.runtime = FakeRuntime()
                if case == 'untrusted':
                    self.runtime.hooks_trusted = False
                    self.runtime.after_trusted = self.replace_request_during_observation
                else:
                    self.runtime.after_snapshot[1] = self.replace_request_during_observation
                if case == 'archived':
                    self.runtime.snap['archived'] = True
                elif case == 'child':
                    self.runtime.snap['thread']['parentThreadId'] = 'a-parent'
                elif case == 'busy':
                    self.runtime.snap['thread']['status']['type'] = 'active'
                elif case == 'unknown_archive':
                    self.runtime.snap['archived'] = None
                elif case in ('completed', 'interrupted'):
                    self.runtime.snap['turn'].update(status=case, error=None)
                elif case == 'wrong_turn':
                    self.runtime.snap['turn']['id'] = None
                elif case == 'checkpoint_required':
                    self.cp(status='complete', pending=[])
                elif case == 'provider':
                    self.runtime.snap['thread']['modelProvider'] = 'custom'
                self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
                self.assertEqual(self.recover(), 'state_changed')
                self.assertEqual(self.store.load(self.sid), self.expected_after_race)
                self.assertFalse(self.runtime.sent)

    def test_stale_allowance_and_final_snapshot_never_reschedule_new_request(self):
        for case in ('blocked', 'already_attempted', 'runtime_changed', 'dispatch_claim'):
            with self.subTest(case=case):
                self.event('UserPromptSubmit', prompt='Old task for ' + case)
                self.cp()
                self.runtime = FakeRuntime()
                if case in ('blocked', 'already_attempted'):
                    self.runtime.after_allowance = self.replace_request_during_observation
                    if case == 'blocked':
                        self.runtime.bucket['primary']['usedPercent'] = 100
                    else:
                        with self.store.edit(self.sid) as state:
                            state['attempts'].append({'turn_id': 'failed-turn'})
                else:
                    def second_snapshot():
                        self.replace_request_during_observation()
                    self.runtime.after_snapshot[2] = second_snapshot
                    if case == 'runtime_changed':
                        self.runtime.before_second = lambda: self.runtime.snap['thread']['status'].update(type='active')
                    with self.store.edit(self.sid) as state:
                        state['attempts'] = []
                self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
                self.assertEqual(self.recover(), 'state_changed')
                self.assertEqual(self.store.load(self.sid), self.expected_after_race)
                self.assertFalse(self.runtime.sent)

    def test_pause_and_checkpoint_change_during_observation_remain_untouched(self):
        for case in ('interrupt', 'awaiting_approval', 'complete'):
            with self.subTest(case=case):
                self.event('UserPromptSubmit', prompt='Task for ' + case)
                self.cp()
                self.runtime = FakeRuntime()
                self.runtime.snap['turn'].update(status='completed', error=None)
                def change_checkpoint():
                    if case == 'interrupt':
                        self.event('Interrupt')
                    elif case == 'awaiting_approval':
                        self.cp(authorization_phase='awaiting_approval')
                    else:
                        self.cp(status='complete', pending=[])
                    self.expected_after_race = self.store.load(self.sid)
                self.runtime.after_snapshot[1] = change_checkpoint
                self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
                self.assertEqual(self.recover(), 'state_changed')
                self.assertEqual(self.store.load(self.sid), self.expected_after_race)

    def test_enqueue_outcomes_never_update_new_request_attempts(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.event('UserPromptSubmit', prompt='Old task before enqueue outcome')
                self.cp()
                with self.store.edit(self.sid) as state:
                    state['attempts'] = []
                self.runtime = FakeRuntime()
                self.runtime.fail = fail
                self.runtime.on_enqueue = self.replace_request_during_observation
                self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
                self.assertEqual(self.recover(), 'state_changed')
                self.assertEqual(self.store.load(self.sid), self.expected_after_race)
                self.assertEqual(len(self.runtime.sent), 1)
                self.assertEqual(self.store.load(self.sid)['attempts'][-1]['status'], 'dispatching')

    def test_enqueue_outcome_targets_its_exact_attempt(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.event('UserPromptSubmit', prompt='Same request attempt targeting')
                self.cp()
                with self.store.edit(self.sid) as state:
                    state['attempts'] = []
                self.runtime = FakeRuntime()
                self.runtime.fail = fail
                def add_later_attempt():
                    with self.store.edit(self.sid) as state:
                        state['attempts'].append({'dispatch_token': 'later-token', 'status': 'later-attempt'})
                self.runtime.on_enqueue = add_later_attempt
                self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
                self.assertEqual(self.recover(), 'dispatch_uncertain' if fail else 'queued')
                attempts = self.store.load(self.sid)['attempts']
                self.assertEqual(attempts[0]['status'], 'uncertain_or_failed' if fail else 'queued')
                self.assertEqual(attempts[-1]['status'], 'later-attempt')

    def test_daemon_exception_paths_do_not_reschedule_newer_work(self):
        for stage in ('constructor', 'trusted', 'snapshot', 'allowance', 'enqueue'):
            with self.subTest(stage=stage):
                self.event('UserPromptSubmit', prompt='Old task before ' + stage + ' exception')
                self.cp()
                with self.store.edit(self.sid) as state:
                    state['attempts'] = []
                self.runtime = FakeRuntime()
                def replace_and_raise():
                    self.replace_request_during_observation()
                    raise RuntimeError('Old metadata operation failed')
                factory = lambda *args: self.runtime
                if stage == 'constructor':
                    def factory(*args):
                        replace_and_raise()
                elif stage == 'trusted':
                    self.runtime.after_trusted = replace_and_raise
                elif stage == 'snapshot':
                    self.runtime.after_snapshot[1] = replace_and_raise
                elif stage == 'allowance':
                    self.runtime.after_allowance = replace_and_raise
                else:
                    self.runtime.on_enqueue = replace_and_raise
                self.now = max(self.now, self.store.load(self.sid)['next_check_at'])
                with patch.object(guard, 'Runtime', side_effect=factory):
                    result = guard.run_recovery_check(self.store, self.sid, 'fake-codex', self.root, self.now)
                self.assertEqual(result, 'state_changed')
                self.assertEqual(self.store.load(self.sid), self.expected_after_race)
                self.assertEqual(self.runtime.closed, stage != 'constructor')

    def test_current_daemon_exception_still_defers_with_cleanup(self):
        self.runtime.after_allowance = lambda: (_ for _ in ()).throw(RuntimeError('Metadata unavailable'))
        with patch.object(guard, 'Runtime', return_value=self.runtime):
            self.assertEqual(guard.run_recovery_check(self.store, self.sid, 'fake-codex', self.root, self.now), 'deferred:RuntimeError')
        self.assertEqual(self.store.load(self.sid)['next_check_at'], self.now + 60)
        self.assertTrue(self.runtime.closed)

    def test_stale_daemon_due_entry_never_opens_runtime_for_paused_work(self):
        self.event('Interrupt')
        expected = self.store.load(self.sid)
        with patch.object(guard, 'Runtime', side_effect=AssertionError('Inactive work must not open runtime')) as runtime:
            self.assertEqual(guard.run_recovery_check(self.store, self.sid, 'fake-codex', self.root, self.now), 'not_due')
        runtime.assert_not_called()
        self.assertEqual(self.store.load(self.sid), expected)

    def test_stop_exception_handler_cannot_rearm_newer_request(self):
        original_hook = guard.hook
        def changed_then_failed(*args, **kwargs):
            original_hook(self.store, dict(session_id=self.sid, hook_event_name='UserPromptSubmit',
                prompt='New user request during old Stop exception', turn_id='new-stop-race-turn'))
            self.cp(authorization_phase='execution')
            self.expected_after_race = self.store.load(self.sid)
            raise RuntimeError('Old Stop verification failed')
        event = dict(session_id=self.sid, hook_event_name='Stop', model='gpt-6.1-sol')
        output = io.StringIO()
        with patch.object(guard, 'hook', side_effect=changed_then_failed), \
             patch.object(sys, 'argv', ['guard.py', '--root', str(self.store.root), 'hook']), \
             patch.object(sys, 'stdin', io.StringIO(json.dumps(event))), patch.object(sys, 'stdout', output):
            guard.main()
        self.assertFalse(json.loads(output.getvalue())['continue'])
        self.assertEqual(self.store.load(self.sid), self.expected_after_race)


if __name__ == '__main__':
    unittest.main(verbosity=2)
