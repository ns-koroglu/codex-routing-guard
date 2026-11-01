#!/usr/bin/env python3
"""Local completion-aware recovery and stronger-model review guard.

Only documented app-server metadata methods and the public `codex queue` CLI
are used. No chat-history hydration, credential reads or database writes occur.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

DELAY = 5 * 3600 + 2 * 60
MAX_CHECKPOINT = 16384
STRONG = {'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-sol', 'gpt-6-astra'}
QUOTA_ERRORS = {'usageLimitExceeded', 'rateLimitExceeded'}
SID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
AUTHORIZATION_PHASES = {'audit_only', 'execution', 'awaiting_approval'}
INACTIVE = {'complete', 'paused', 'waiting_for_user', 'awaiting_approval'}


def same_task(live, observed):
    return bool(observed) and all(live.get(key) == observed.get(key) for key in ('revision', 'request_id'))


def same_recovery_view(live, observed):
    return same_task(live, observed) and all(live.get(key) == observed.get(key) for key in (
        'armed', 'completion_confirmed', 'turn_id', 'status', 'checkpoint_revision',
        'checkpoint_digest', 'checkpoint', 'next_check_at'))


def invalidate_continuations(state):
    for key in ('continuation_reason', 'pending_continuation', 'pending_dispatch'):
        state.pop(key, None)


def authorization_context(checkpoint):
    phase = checkpoint.get('authorization_phase')
    if phase == 'audit_only':
        return 'Authorization phase: audit_only. Continue read-only audit/report work only; do not implement, install or expand authority. '
    if phase == 'execution':
        return 'Authorization phase: execution. Preserve the explicitly approved task scope. '
    if phase == 'awaiting_approval':
        return 'Authorization phase: awaiting_approval. Stop and wait for explicit user approval; do not resume work. '
    return 'Authorization phase is unspecified. This checkpoint grants no new implementation authority; preserve only already established scope. '


def repair_continuation(state, sid, reason, force_pause=False):
    invalidate_continuations(state)
    if force_pause or state.get('stop_blocks', 0) >= 2:
        state.update(status='waiting_for_user', armed=False, completion_confirmed=False)
        return {'continue': False, 'systemMessage': 'Completion was not verified. Resolve the checkpoint/review gate; automatic recovery is paused.'}
    state.update(status='review_or_checkpoint_required', completion_confirmed=False, armed=True)
    reason = 'RECOVERY_GUARD_CONTINUE:' + sid + ':' + str(state['revision']) + ':' + uuid.uuid4().hex + '\n' + reason
    state['pending_continuation'] = {'message': reason, 'revision': state['revision'],
                                     'request_id': state.get('request_id'), 'consumed': False}
    state['stop_blocks'] = state.get('stop_blocks', 0) + 1
    return {'decision': 'block', 'reason': reason}


def is_lower(model):
    return bool(model) and model not in STRONG


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def content_digest(checkpoint):
    return digest({k: v for k, v in checkpoint.items() if k not in {'status', 'next_action', 'updated_at'}})


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.states = self.root / 'states'
        self.states.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.states, 0o700)

    def path(self, sid):
        if not SID.fullmatch(sid):
            raise ValueError('Invalid session identifier')
        return self.states / (sid + '.json')

    @contextlib.contextmanager
    def edit(self, sid):
        path = self.path(sid)
        with open(str(path) + '.lock', 'a+') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(path.read_text()) if path.exists() else {}
            yield state
            raw = json.dumps(state, ensure_ascii=False, sort_keys=True)
            with tempfile.NamedTemporaryFile('w', dir=self.states, delete=False) as temp:
                temp.write(raw)
                name = temp.name
            os.chmod(name, 0o600)
            os.replace(name, path)

    def load(self, sid):
        path = self.path(sid)
        return json.loads(path.read_text()) if path.exists() else {}

    def notify(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.sendto(b'changed', str(self.root / 'notify.sock'))
        except OSError:
            pass  # The persistent state is authoritative; the daemon also polls.


def register(store, sid, cwd, started_at):
    with store.edit(sid) as state:
        if not state:
            state.update(session_id=sid, cwd=str(Path(cwd).resolve()), started_at=started_at,
                         first_check_at=started_at + DELAY, next_check_at=started_at + DELAY,
                         status='idle', armed=False, revision=0, weak_work=False,
                         completion_confirmed=False, attempts=[], checkpoint={})
    store.notify()


def fingerprints(paths):
    if not isinstance(paths, list) or len(paths) > 20:
        raise ValueError('At most twenty artifact paths are supported')
    result = []
    total = 0
    for item in paths:
        path = Path(item).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError('Review artifacts must be regular files')
        total += path.stat().st_size
        if total > 64 * 1024 * 1024:
            raise ValueError('Review artifacts exceed the bounded 64 MiB hashing budget')
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                h.update(chunk)
        result.append({'path': str(path), 'sha256': h.hexdigest()})
    return result


def checkpoint(store, sid, value):
    raw = json.dumps(value, ensure_ascii=False)
    if len(raw.encode()) > MAX_CHECKPOINT:
        raise ValueError('Checkpoint exceeds 16 KiB; do not copy chat history')
    if value.get('status') not in {'in_progress', 'ready_for_review', 'complete', 'waiting_for_user', 'paused'}:
        raise ValueError('Unsupported checkpoint status')
    if 'authorization_phase' in value and value['authorization_phase'] not in AUTHORIZATION_PHASES:
        raise ValueError('Unsupported checkpoint authorization phase')
    if not isinstance(value.get('objective'), str) or not value['objective'].strip():
        raise ValueError('Checkpoint objective is required')
    for field in ['completed', 'pending', 'validation']:
        entries = value.get(field, [])
        if not isinstance(entries, list) or len(entries) > 20 or any(not isinstance(x, str) or len(x) > 1000 for x in entries):
            raise ValueError('Checkpoint lists must be short strings')
    value = dict(value)
    value['artifacts'] = fingerprints(value.get('artifacts', []))
    with store.edit(sid) as state:
        if not state:
            raise ValueError('Session is not registered')
        state['checkpoint'] = value
        state['checkpoint_revision'] = state['revision']
        state['checkpoint_digest'] = content_digest(value)
        state['completion_confirmed'] = False
        if value.get('authorization_phase') == 'awaiting_approval':
            state.update(status='awaiting_approval', armed=False)
            invalidate_continuations(state)
        elif value['status'] in {'waiting_for_user', 'paused'}:
            state.update(status=value['status'], armed=False)
            invalidate_continuations(state)
        else:
            state.update(status='in_progress', armed=True)
            if value['status'] == 'complete':
                invalidate_continuations(state)
    store.notify()


def review_primary(store, sid, verdict, summary):
    if verdict not in {'approved', 'changes_required'} or not summary.strip():
        raise ValueError('A review verdict and substantive summary are required')
    with store.edit(sid) as state:
        if not state.get('checkpoint_digest'):
            raise ValueError('Write the checkpoint before reviewing')
        state['primary_review_candidate'] = {'verdict': verdict, 'summary': summary[:2000],
                                            'digest': state['checkpoint_digest'], 'revision': state['revision']}


def context(store, sid):
    path = store.path(sid)
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--root', str(store.root.resolve())])
    return (
        'Recovery guard: maintain a compact checkpoint at ' + str(path) + '. '
        'Before long work and at milestones, run ' + command + ' checkpoint --session ' + sid +
        ' --file <compact JSON>. JSON: objective, status, next_action, completed[], pending[], '
        'validation[], artifacts[] (file paths). Do not paste the chat transcript. '
        'At completion use status=complete with pending=[]; the Stop hook confirms cancellation. '
        'Luna work requires an actual Sol/Astra final review. A stronger primary may review the '
        'artifacts and run ' + command + ' review-primary --session ' + sid +
        ' --verdict approved --summary <evidence>. A Luna primary must delegate a final review to '
        'gpt-6.1-sol/high; the reviewer returns RECOVERY_REVIEW: followed by one JSON object with '
        'verdict, summary and checkpoint_digest. Never fabricate a review. '
        'For missing tools/model access set waiting_for_user. Resume from this checkpoint, not full history. '
        + authorization_context(store.load(sid).get('checkpoint', {}))
    )


def hook(store, event, now=None, reviewer_metadata=None, recovery_archived=None):
    now = time.time() if now is None else now
    sid = event['session_id']
    name = event['hook_event_name']
    model = event.get('model', '')
    if name == 'SessionStart':
        if event.get('source') in {'startup', 'fork'}:
            register(store, sid, event['cwd'], now)
        if not store.load(sid):
            return {}  # Do not invent the start time of an older chat.
        return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context(store, sid)}}
    state = store.load(sid)
    if not state:
        if name == 'UserPromptSubmit' and event.get('prompt', '').startswith(('RECOVERY_RESUME:', 'RECOVERY_GUARD_CONTINUE:')):
            return {'decision': 'block', 'reason': 'Recovery state is missing; automatic continuation is not authorized.'}
        return {}
    if name == 'UserPromptSubmit':
        with store.edit(sid) as state:
            prompt = event.get('prompt', '')
            recovery = prompt.startswith('RECOVERY_RESUME:')
            continuation = prompt.startswith('RECOVERY_GUARD_CONTINUE:')
            if recovery:
                dispatch = state.get('pending_dispatch', {})
                if (prompt != dispatch.get('message') or dispatch.get('revision') != state.get('revision')
                    or dispatch.get('request_id') != state.get('request_id')
                    or dispatch.get('consumed') or not state.get('armed') or state.get('completion_confirmed')
                    or state.get('status') in INACTIVE
                    or state.get('checkpoint', {}).get('authorization_phase') == 'awaiting_approval'
                    or recovery_archived is not False):
                    return {'decision': 'block', 'reason': 'Stale, duplicate, paused, archived or unverified recovery request; do not restart work.'}
                dispatch['consumed'] = True
            if continuation:
                repair = state.get('pending_continuation', {})
                if (prompt != repair.get('message') or repair.get('revision') != state.get('revision')
                    or repair.get('request_id') != state.get('request_id') or repair.get('consumed')
                    or state.get('status') != 'review_or_checkpoint_required' or not state.get('armed')
                    or state.get('completion_confirmed')
                    or state.get('checkpoint', {}).get('authorization_phase') == 'awaiting_approval'):
                    return {'decision': 'block', 'reason': 'Stale, duplicate or inactive repair continuation; do not restart work.'}
                repair['consumed'] = True
            internal = continuation or recovery
            if not internal:
                if state.get('completion_confirmed'):
                    state['weak_work'] = False
                state['revision'] += 1
                state['request_id'] = event.get('turn_id') or digest([sid, state['revision'], prompt])
                invalidate_continuations(state)
                state.pop('limit_cause', None)
                state.pop('limit_provenance', None)
                state.pop('child_starts', None)
                state.pop('primary_review_candidate', None)
                state.pop('verified_review', None)
                state['stop_blocks'] = 0
            state.update(turn_id=event.get('turn_id'), status='in_progress', armed=True,
                         completion_confirmed=False, weak_work=state.get('weak_work', False) or is_lower(model))
        store.notify()
        return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context(store, sid)}}
    if name == 'Interrupt':
        with store.edit(sid) as state:
            state.update(status='paused', armed=False, interruption='user')
            invalidate_continuations(state)
        store.notify()
        return {}
    if name == 'SubagentStart':
        with store.edit(sid) as state:
            state['weak_work'] = True  # Also covers a child that crashes before SubagentStop.
            state.pop('primary_review_candidate', None)
            state.pop('verified_review', None)
            agent_id = event.get('agent_id')
            if agent_id and state.get('request_id'):
                children = state.setdefault('child_starts', {})
                children.setdefault(agent_id, {'revision': state['revision'], 'request_id': state['request_id'],
                                               'parent_turn_id': state.get('turn_id'), 'consumed': False})
                while len(children) > 40:
                    del children[next(iter(children))]
        return {}
    if name == 'SubagentStop':
        with store.edit(sid) as state:
            child = reviewer_metadata or {}
            authenticated = child.get('parentThreadId') == sid and child.get('id') == event.get('agent_id')
            child_model = child.get('model') if authenticated else None
            # The common hook model may be the parent. Unknown child profiles
            # conservatively require a stronger primary review.
            if not authenticated or is_lower(child_model):
                state['weak_work'] = True
                state.pop('primary_review_candidate', None)
                state.pop('verified_review', None)
            text = event.get('last_assistant_message') or ''
            execution_model = child.get('execution_model')
            start = state.get('child_starts', {}).get(event.get('agent_id'), {})
            current_start = (start.get('revision') == state.get('revision') and bool(start.get('request_id'))
                             and start.get('request_id') == state.get('request_id') and not start.get('consumed')
                             and state.get('checkpoint_revision') == state.get('revision')
                             and bool(state.get('checkpoint_digest')))
            if (authenticated and current_start and execution_model in STRONG
                and child.get('execution_effort') in {'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}):
                for line in text.splitlines():
                    if line.startswith('RECOVERY_REVIEW:'):
                        receipt = json.loads(line.split(':', 1)[1].strip())
                        if receipt.get('checkpoint_digest') == state.get('checkpoint_digest') and receipt.get('summary'):
                            state['verified_review'] = {'model': execution_model, 'effort': child.get('execution_effort'), 'source': 'bounded child turn_context', 'agent_id': event.get('agent_id'),
                                'digest': receipt['checkpoint_digest'], 'verdict': receipt.get('verdict'),
                                'revision': state['revision'], 'request_id': start['request_id'],
                                'parent_turn_id': start.get('parent_turn_id'), 'summary': receipt['summary'][:2000]}
                            start['consumed'] = True
                            break
        return {}
    if name != 'Stop':
        return {}
    with store.edit(sid) as state:
        cp = state.get('checkpoint', {})
        valid_cp = state.get('checkpoint_revision') == state.get('revision') and bool(cp)
        reason = None
        if state.get('status') in {'paused', 'waiting_for_user', 'awaiting_approval'} or cp.get('authorization_phase') == 'awaiting_approval':
            state.update(armed=False)
            invalidate_continuations(state)
        elif not valid_cp:
            reason = 'Write the compact current-task checkpoint before ending. ' + context(store, sid)
        elif cp.get('status') == 'complete':
            if cp.get('pending'):
                reason = 'The task still has pending actions; do not mark it complete.'
            fresh = fingerprints([x['path'] for x in cp.get('artifacts', [])])
            if fresh != cp.get('artifacts', []):
                reason = 'Artifacts changed after the checkpoint/review; refresh the checkpoint and obtain the required review.'
            if state.get('weak_work'):
                candidate = state.get('primary_review_candidate', {})
                if model in STRONG and candidate.get('digest') == state.get('checkpoint_digest') and candidate.get('revision') == state['revision']:
                    state['verified_review'] = dict(candidate, model=model, source='primary')
                review = state.get('verified_review', {})
                if review.get('verdict') != 'approved' or review.get('digest') != state.get('checkpoint_digest') or review.get('revision') != state['revision']:
                    reason = 'A verified stronger-model final review is required before completion. ' + context(store, sid)
            if not reason:
                state.update(status='complete', completion_confirmed=True, armed=False)
                invalidate_continuations(state)
        elif cp.get('status') in {'waiting_for_user', 'paused'}:
            state.update(status=cp['status'], armed=False)
            invalidate_continuations(state)
        if reason:
            return repair_continuation(state, sid, reason)
    store.notify()
    return {}


class Rpc:
    def __init__(self, codex, codex_home=None):
        env = os.environ.copy()
        if codex_home is not None:
            env['CODEX_HOME'] = str(Path(codex_home).expanduser().resolve())
        self.proc = subprocess.Popen([codex, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        self.buffer = b''
        self.seq = 0
        try:
            self.call('initialize', {'clientInfo': {'name': 'recovery_guard', 'version': '1.0'}, 'capabilities': {'experimentalApi': True}})
            self.proc.stdin.write(b'{"method":"initialized"}\n')
            self.proc.stdin.flush()
        except Exception:
            self.close()
            raise

    def call(self, method, params):
        self.seq += 1
        request_id = self.seq
        self.proc.stdin.write((json.dumps({'id': request_id, 'method': method, 'params': params}) + '\n').encode())
        self.proc.stdin.flush()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if b'\n' not in self.buffer:
                if not select.select([self.proc.stdout], [], [], max(0, end - time.monotonic()))[0]:
                    break
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.buffer += chunk
                continue
            line, self.buffer = self.buffer.split(b'\n', 1)
            if not line:
                continue
            value = json.loads(line)
            if value.get('id') == request_id:
                if 'error' in value:
                    raise RuntimeError('Metadata RPC failed: ' + method)
                return value['result']
        raise TimeoutError('Metadata RPC timed out: ' + method)

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        self.proc.stdin.close()
        self.proc.stdout.close()


def archived_status(codex_home, sid):
    files = sorted(Path(codex_home).glob('state_*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                row = db.execute('SELECT archived FROM threads WHERE id=?', (sid,)).fetchone()
            if row is not None:
                return bool(row[0])
        except sqlite3.Error:
            continue
    return None  # Fail closed if archive metadata cannot be established.


def reviewer_execution(child, event, codex_home):
    """Bounded execution-record fallback, never a full transcript/history load.

    Transcript records are not a stable public API. Unknown layouts fail closed.
    The app-server's current configured model alone is not execution evidence.
    """
    source = event.get('agent_transcript_path')
    if not event.get('turn_id') or not source or source != child.get('path'):
        return child
    path = Path(source).resolve()
    sessions = (Path(codex_home) / 'sessions').resolve()
    if not path.is_relative_to(sessions) or str(event.get('agent_id')) not in path.name:
        return child
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        start = max(0, size - 262144)
        stream.seek(start)
        raw = stream.read(262144)
    lines = raw.splitlines()[1:] if start else raw.splitlines()
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get('type') == 'turn_context':
            payload = record.get('payload', {})
            if payload.get('turn_id') == event.get('turn_id'):
                child = dict(child, execution_model=payload.get('model'), execution_effort=payload.get('effort'))
            break
    return child


class Runtime:
    def __init__(self, codex, codex_home):
        self.codex = codex
        self.home = codex_home
        self.rpc = Rpc(codex, codex_home)

    def trusted(self, cwd, command):
        entries = self.rpc.call('hooks/list', {'cwds': [cwd]})['data']
        required = {'sessionStart', 'userPromptSubmit', 'subagentStart', 'subagentStop', 'stop', 'interrupt'}
        found = set()
        for entry in entries:
            if entry.get('errors'):
                return False
            for item in entry.get('hooks', []):
                if item.get('command') == command and item.get('enabled') and item.get('trustStatus') in {'trusted', 'managed'}:
                    found.add(item.get('eventName'))
        return required <= found

    def snapshot(self, sid):
        thread = self.rpc.call('thread/read', {'threadId': sid, 'includeTurns': False})['thread']
        turns = self.rpc.call('thread/turns/list', {'threadId': sid, 'limit': 1, 'itemsView': 'notLoaded'})['data']
        return {'thread': thread, 'turn': turns[0] if turns else {}, 'archived': archived_status(self.home, sid)}

    def allowance(self):
        account = self.rpc.call('account/read', {'refreshToken': False}).get('account') or {}
        if account.get('type') != 'chatgpt':
            raise RuntimeError('Recovery requires subscription authentication; no API-credit fallback is permitted')
        value = self.rpc.call('account/rateLimits/read', {})
        limits = value.get('rateLimitsByLimitId') or {}
        bucket = limits.get('codex') or value.get('rateLimits')
        if not bucket:
            raise RuntimeError('Usage allowance is unavailable')
        return bucket

    def enqueue(self, sid, message):
        # Supported CLI daemon management; starts infrastructure, never a model
        # turn. Needed because queue --resume only warns if no daemon exists.
        env = {**os.environ, 'CODEX_HOME': str(Path(self.home).expanduser().resolve())}
        started = subprocess.run([self.codex, 'app-server', 'daemon', 'start'],
                                 capture_output=True, text=True, timeout=25, env=env)
        if started.returncode:
            raise RuntimeError('Local resume daemon is unavailable')
        result = subprocess.run([self.codex, 'queue', '--thread', sid, '--message', message, '--resume'],
                                capture_output=True, text=True, timeout=25, env=env)
        if result.returncode:
            raise RuntimeError('Queue command failed; no automatic retry will be made for this dispatch')
        return 'queued'  # Queue success is not proof of model execution.


def recover_one(store, sid, runtime, now):
    state = store.load(sid)
    if not state.get('armed') or state.get('completion_confirmed') or now < state.get('next_check_at', float('inf')):
        return 'not_due'
    revision = state['revision']
    settings_path = store.root / 'settings.json'
    if not settings_path.exists():
        return 'settings_missing'
    settings = json.loads(settings_path.read_text())
    if not settings.get('enabled') or not runtime.trusted(state['cwd'], settings['hook_command']):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live['next_check_at'] = now + 900
        return 'hooks_not_activated_or_trusted'
    snap = runtime.snapshot(sid)
    thread, turn = snap['thread'], snap['turn']
    if snap.get('archived') is True or thread.get('parentThreadId'):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live.update(armed=False, status='archived_or_subagent')
        return 'cancelled'
    if snap.get('archived') is not False or thread.get('status', {}).get('type') not in {'idle', 'notLoaded'}:
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live['next_check_at'] = now + 60
        return 'busy_or_unknown'
    error = turn.get('error') or {}
    if turn.get('status') != 'failed' or error.get('codexErrorInfo') not in QUOTA_ERRORS:
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            if turn.get('status') == 'interrupted' and live.get('status') == 'in_progress':
                # A metadata-only cold app-server cannot observe another
                # process's active turn. Do not mistake an open transcript
                # for an explicit user interruption (that has its own hook).
                live['next_check_at'] = now + 900
            else:
                live.update(armed=False, status='suspended_nonquota_terminal')
        return 'not_quota_interrupted'
    if not turn.get('id') or (state.get('turn_id') and state['turn_id'] != turn['id']):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live['next_check_at'] = max(live.get('next_check_at', 0), now + 60)
        return 'failed_turn_not_current'
    cp = state.get('checkpoint', {})
    if (not cp or state.get('checkpoint_revision') != revision or cp.get('status') in INACTIVE
        or cp.get('authorization_phase') == 'awaiting_approval'):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live.update(armed=False, status='checkpoint_required')
            live['last_guard_result'] = 'checkpoint_not_eligible'
        return 'checkpoint_not_eligible'
    if thread.get('modelProvider') != 'openai':
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live.update(armed=False, status='unsupported_provider')
        return 'provider_not_supported'
    bucket = runtime.allowance()
    windows = [bucket.get('primary'), bucket.get('secondary')]
    if any(not isinstance(x, dict) or not isinstance(x.get('usedPercent'), (int, float)) or not isinstance(x.get('resetsAt'), (int, float)) for x in windows):
        raise RuntimeError('Incomplete usage-window metadata')
    if windows[0].get('windowDurationMins') != 300 or windows[1].get('windowDurationMins') != 10080 or bucket.get('individualLimit'):
        raise RuntimeError('Unsupported usage-window layout; recovery is deferred')
    credits = bucket.get('credits') or {}
    if credits.get('hasCredits') is not False or credits.get('unlimited') is not False:
        raise RuntimeError('Automatic credit fallback cannot be excluded; recovery is deferred')
    blocked = [x for x in windows if x['usedPercent'] >= 100]
    window_key = str(bucket.get('limitId')) + ':' + ':'.join(str(x['resetsAt']) for x in windows)
    if blocked or bucket.get('spendControlReached'):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live['next_check_at'] = max([now + 60] + [x['resetsAt'] + 120 for x in blocked])
            live.pop('limit_cause', None)  # Legacy unbound attribution is never execution evidence.
            live['limit_provenance'] = {'revision': revision, 'request_id': state.get('request_id'),
                'turn_id': turn['id'], 'limit_id': bucket.get('limitId'), 'window_key': window_key,
                'primary_resets_at': windows[0]['resetsAt'], 'secondary_resets_at': windows[1]['resetsAt'],
                'observed_at': now,
                'cause': 'five_hour' if windows[0]['usedPercent'] >= 100 and windows[1]['usedPercent'] < 100 else 'account'}
        return 'waiting_for_allowance'
    if any(x.get('turn_id') == turn.get('id') or x.get('window_key') == window_key for x in state.get('attempts', [])):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live['next_check_at'] = max(now + 60, windows[0]['resetsAt'] + 120)
        return 'already_attempted'
    # Recheck runtime state immediately before claiming a bounded, at-most-once dispatch.
    fresh = runtime.snapshot(sid)
    if (fresh['thread'].get('status', {}).get('type') not in {'idle', 'notLoaded'}
        or fresh['turn'].get('id') != turn.get('id') or fresh['turn'].get('status') != 'failed'
        or (fresh['turn'].get('error') or {}).get('codexErrorInfo') not in QUOTA_ERRORS
        or fresh.get('archived') is not False):
        with store.edit(sid) as live:
            if not same_recovery_view(live, state):
                return 'state_changed'
            live['next_check_at'] = max(live.get('next_check_at', 0), now + 60)
        return 'runtime_changed'
    provenance = state.get('limit_provenance', {})
    prior_reset = provenance.get('primary_resets_at', 0)
    matching_window = (windows[0]['resetsAt'] == prior_reset or
                       prior_reset <= now < windows[0]['resetsAt'] <= prior_reset + 300 * 60)
    matching_cause = (provenance.get('cause') == 'five_hour' and provenance.get('revision') == revision
        and provenance.get('request_id') == state.get('request_id') and provenance.get('turn_id') == turn['id']
        and provenance.get('limit_id') == bucket.get('limitId')
        and provenance.get('secondary_resets_at') == windows[1]['resetsAt']
        and provenance.get('observed_at', now) <= prior_reset and matching_window)
    cause = 'the five-hour usage limit' if matching_cause else 'an account usage limit'
    token = digest([sid, revision, turn.get('id'), window_key, now])[:24]
    message = ('RECOVERY_RESUME:' + sid + ':' + token + '\nThe previous run was interrupted because ' + cause +
               ' was reached before this task was complete. Continue from the compact checkpoint at ' + str(store.path(sid)) +
               '. Read only that checkpoint and minimal recent metadata, not the entire conversation. '
               'If the task is complete, paused or waiting for user input, stop without a recovery message. '
               'Preserve the authorized scope, permissions and stronger-model final-review requirement. '
               + authorization_context(cp))
    with store.edit(sid) as live:
        if (not same_recovery_view(live, state)
            or not live.get('armed') or live.get('completion_confirmed')
            or live.get('checkpoint', {}).get('status') in INACTIVE
            or live.get('checkpoint', {}).get('authorization_phase') == 'awaiting_approval'
            or live.get('checkpoint_digest') != state.get('checkpoint_digest')):
            return 'state_changed'
        if any(x.get('turn_id') == turn.get('id') or x.get('window_key') == window_key for x in live.get('attempts', [])):
            live['next_check_at'] = max(now + 60, windows[0]['resetsAt'] + 120)
            return 'already_attempted'
        attempt = {'dispatch_token': token, 'revision': revision, 'request_id': state.get('request_id'),
                   'turn_id': turn.get('id'), 'window_key': window_key, 'at': now, 'status': 'dispatching'}
        live['attempts'] = (live.get('attempts', []) + [attempt])[-20:]
        live['pending_dispatch'] = {'message': message, 'revision': revision, 'request_id': state.get('request_id'),
                                    'turn_id': turn['id'], 'window_key': window_key, 'consumed': False}
        live['next_check_at'] = now + 60
    try:
        result = runtime.enqueue(sid, message)
    except Exception:
        with store.edit(sid) as live:
            if not same_task(live, state):
                return 'state_changed'
            for attempt in live.get('attempts', []):
                if attempt.get('dispatch_token') == token:
                    attempt['status'] = 'uncertain_or_failed'
                    break
        return 'dispatch_uncertain'  # Never blindly resend after an uncertain side effect.
    with store.edit(sid) as live:
        if not same_task(live, state):
            return 'state_changed'
        for attempt in live.get('attempts', []):
            if attempt.get('dispatch_token') == token:
                attempt['status'] = result
                break
    return 'queued'


def run_recovery_check(store, sid, codex, codex_home, now):
    observed = store.load(sid)
    if (not observed.get('armed') or observed.get('completion_confirmed')
        or now < observed.get('next_check_at', float('inf'))):
        return 'not_due'
    runtime = None
    try:
        runtime = Runtime(codex, codex_home)
        return recover_one(store, sid, runtime, now)
    except Exception as exc:
        with store.edit(sid) as live:
            if not same_recovery_view(live, observed):
                return 'state_changed'
            live['next_check_at'] = now + 60
        return 'deferred:' + type(exc).__name__
    finally:
        if runtime:
            runtime.rpc.close()


def daemon(store, codex, codex_home):
    with open(store.root / 'daemon.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path = store.root / 'notify.sock'
        if path.exists():
            path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
            listener.bind(str(path))
            os.chmod(path, 0o600)
            while True:
                now = time.time()
                settings = json.loads((store.root / 'settings.json').read_text())
                if not settings.get('enabled'):
                    if select.select([listener], [], [], 60)[0]:
                        listener.recv(64)
                    continue
                due = []
                for file in store.states.glob('*.json'):
                    try:
                        state = json.loads(file.read_text())
                        if state.get('armed'):
                            due.append((state['next_check_at'], state['session_id']))
                    except (ValueError, KeyError, OSError):
                        continue
                for when, sid in due:
                    if when <= now:
                        result = run_recovery_check(store, sid, codex, codex_home, now)
                        log_path = store.root / 'events.jsonl'
                        if log_path.exists() and log_path.stat().st_size > 1024 * 1024:
                            os.replace(log_path, store.root / 'events.previous.jsonl')
                        with open(log_path, 'a') as log:
                            os.chmod(log.name, 0o600)
                            log.write(json.dumps({'time': now, 'session_id': sid, 'result': result}) + '\n')
                latest = [store.load(sid) for _, sid in due]
                future = [state.get('next_check_at', now + 60) for state in latest if state.get('armed')]
                timeout = max(0.1, min([60] + [x - time.time() for x in future]))
                if select.select([listener], [], [], timeout)[0]:
                    listener.recv(64)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    parser.add_argument('--root', default=str(home / 'recovery-guard'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('hook')
    p = sub.add_parser('arm');p.add_argument('--session', required=True);p.add_argument('--cwd', required=True);p.add_argument('--started-at', type=float, required=True)
    p = sub.add_parser('checkpoint');p.add_argument('--session', required=True);p.add_argument('--file', required=True)
    p = sub.add_parser('review-primary');p.add_argument('--session', required=True);p.add_argument('--verdict', required=True);p.add_argument('--summary', required=True)
    p = sub.add_parser('status');p.add_argument('--session', required=True)
    sub.add_parser('activate')
    sub.add_parser('deactivate')
    p = sub.add_parser('daemon');p.add_argument('--codex', required=True);p.add_argument('--codex-home', default=str(home))
    args = parser.parse_args();store = Store(args.root)
    if args.command == 'hook':
        event = {}
        rpc = None
        observed = None
        try:
            event = json.load(sys.stdin)
            observed = store.load(event['session_id'])
            child = None
            archived = None
            if event.get('hook_event_name') == 'SubagentStop' and store.load(event['session_id']):
                settings = json.loads((store.root / 'settings.json').read_text())
                rpc = Rpc(settings['codex'], settings['codex_home'])
                child = rpc.call('thread/read', {'threadId': event['agent_id'], 'includeTurns': False})['thread']
                child = reviewer_execution(child, event, settings['codex_home'])
            if event.get('hook_event_name') == 'UserPromptSubmit' and event.get('prompt', '').startswith('RECOVERY_RESUME:'):
                settings = json.loads((store.root / 'settings.json').read_text())
                archived = archived_status(settings['codex_home'], event['session_id'])
            result = hook(store, event, reviewer_metadata=child, recovery_archived=archived)
        except Exception as exc:
            if event.get('hook_event_name') == 'SubagentStop':
                result = {'systemMessage': 'Child review execution could not be verified (' + type(exc).__name__ + '). The stronger-model completion gate remains pending.'}
            elif event.get('hook_event_name') == 'Stop':
                with store.edit(event['session_id']) as state:
                    if not same_recovery_view(state, observed):
                        result = {'continue': False, 'systemMessage': 'Task state changed during completion verification; stale verification did not modify current work.'}
                    elif not state or state.get('status') in INACTIVE:
                        result = {'continue': False, 'systemMessage': 'Completion verification failed; automatic recovery remains inactive.'}
                    else:
                        result = repair_continuation(state, event['session_id'],
                            'Recovery/review verification failed (' + type(exc).__name__ + '). Resolve it before claiming completion.',
                            force_pause=bool(event.get('stop_hook_active')))
            elif event.get('hook_event_name') == 'UserPromptSubmit' and event.get('prompt', '').startswith(('RECOVERY_RESUME:', 'RECOVERY_GUARD_CONTINUE:')):
                result = {'decision': 'block', 'reason': 'Recovery/review verification failed (' + type(exc).__name__ + '). Resolve it before claiming completion.'}
            else:
                result = {'systemMessage': 'Recovery hook failed (' + type(exc).__name__ + '); automatic recovery is not confirmed.'}
        finally:
            if rpc:
                rpc.close()
        print(json.dumps(result))
    elif args.command == 'arm':
        register(store, args.session, args.cwd, args.started_at)
    elif args.command == 'checkpoint':
        path = Path(args.file)
        if path.stat().st_size > MAX_CHECKPOINT:
            raise ValueError('Checkpoint input exceeds 16 KiB')
        checkpoint(store, args.session, json.loads(path.read_text()))
    elif args.command == 'review-primary':
        review_primary(store, args.session, args.verdict, args.summary)
    elif args.command == 'status':
        print(json.dumps(store.load(args.session), ensure_ascii=False, indent=2))
    elif args.command in {'activate', 'deactivate'}:
        settings_path = store.root / 'settings.json'
        settings = json.loads(settings_path.read_text())
        settings['enabled'] = False
        if args.command == 'activate':
            runtime = Runtime(settings['codex'], settings['codex_home'])
            try:
                settings['enabled'] = runtime.trusted(settings['verification_cwd'], settings['hook_command'])
            finally:
                runtime.rpc.close()
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n')
        os.chmod(settings_path, 0o600)
        store.notify()
        reason = 'disabled' if args.command == 'deactivate' else ('trusted' if settings['enabled'] else 'Review and trust all six hooks with /hooks first.')
        print(json.dumps({'enabled': settings['enabled'], 'reason': reason}))
    elif args.command == 'daemon':
        daemon(store, args.codex, args.codex_home)


if __name__ == '__main__':
    main()
