"""Private bounded evidence. Values are admitted by field type, never redacted.

No payloads, command arguments, environments or exception prose enter this API.
Each append is locked across the parent/bridge and fsynced before returning.
"""
import fcntl
import functools
import hashlib
import json
import os
import re
import time
import uuid
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .store import private

STREAMS = ('events', 'native', 'telegram', 'sandbox', 'commands')
LIMIT = 1024 * 1024
ACTIVE = None
CLEANING = False
BOOLS = {'mutation_attempted', 'receipt_received', 'grant_possible', 'cleanup_confirmed',
         'user_match', 'chat_match', 'message_match', 'cache_reused', 'verified', 'timeout', 'retryable'}
NUMBERS = {'duration_ms', 'deadline_seconds', 'retry', 'exit_code', 'version', 'active_version',
           'provider_revision', 'receipt_version', 'receipt_bytes'}
CODES = {'stage', 'status', 'code', 'method', 'operation', 'action', 'category', 'executable'}
HASHES = {'sandbox_ref', 'proposal_ref', 'message_ref', 'decision_ref', 'revision',
          'policy_hash', 'receipt_hash', 'run_ref', 'rpc_ref'}


def reference(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def emit(stream, event, **fields):
    if ACTIVE is not None:
        if CLEANING:
            ACTIVE.try_emit(stream, event, **fields)
        else:
            ACTIVE.emit(stream, event, **fields)


def soft_emit(stream, event, **fields):
    if ACTIVE is not None:
        ACTIVE.try_emit(stream, event, **fields)


def command(runner, args, **kwargs):
    """Observe a subprocess without accessing output, arguments or its environment."""
    if ACTIVE is None:
        return runner(args, **kwargs)
    executable = Path(args[0]).name
    executable = executable.replace('-', '_') if executable in {
        'openshell', 'openshell-gateway', 'openshell-driver-vm', 'docker', 'git'} else 'native_tool'
    operation = next((word for word in ('exec', 'create', 'delete', 'inspect', 'build', 'settings',
                                       'list', 'info', 'commit', 'init') if word in args), 'verify')
    started = time.monotonic()
    emit('commands', 'command_start', executable=executable, operation=operation,
         deadline_seconds=kwargs.get('timeout', 120))
    try:
        result = runner(args, **kwargs)
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        category = 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'interrupted' if isinstance(error, KeyboardInterrupt) else 'spawn_failed'
        emit('commands', 'command_end', executable=executable, operation=operation,
             status='fail', category=category, code=category, timeout=category == 'timeout',
             duration_ms=(time.monotonic() - started) * 1000)
        raise
    emit('commands', 'command_end', executable=executable, operation=operation,
         exit_code=result.returncode, timeout=False,
         status='pass' if result.returncode == 0 else 'fail',
         code='ok' if result.returncode == 0 else 'command_exit_nonzero',
         duration_ms=(time.monotonic() - started) * 1000)
    return result


def save(path, value):
    """Private from creation, atomic replacement, durable before returning."""
    private(path.parent, directory=True)
    private(path, create=True)
    temporary = private(path.with_name(path.name + '.' + uuid.uuid4().hex), create=True)
    try:
        with temporary.open('w') as output:
            json.dump(value, output, sort_keys=True, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


class Diagnostics:
    def __init__(self, directory, monotonic=time.monotonic):
        self.directory = private(directory, directory=True, create=True)
        self.clock = monotonic
        self.started = monotonic()
        self.run_ref = reference(self.directory.name)
        for stream in STREAMS:
            private(self.directory / (stream + '.jsonl'), create=True)
        private(self.directory / 'diagnostics.lock', create=True)
        private(self.directory / 'facts.json', create=True)

    def try_emit(self, stream, event, **fields):
        try:
            self.emit(stream, event, **fields)
            return True
        except (OSError, ValueError):
            return False

    def emit(self, stream, event, **fields):
        if stream not in STREAMS or not re.fullmatch(r'[a-z][a-z0-9_]{0,100}', event):
            raise ValueError('Invalid diagnostic event')
        for key, value in fields.items():
            valid = ((key in BOOLS and type(value) is bool)
                     or (key in NUMBERS and ((type(value) is int and -(2**63) <= value < 2**64)
                                            or (type(value) is float and abs(value) < 1e15)))
                     or (key in CODES and isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,120}', value))
                     or (key in HASHES and isinstance(value, str) and re.fullmatch(r'[a-f0-9]{40,64}', value)))
            if not valid:
                raise ValueError('Invalid diagnostic field')
        fields.setdefault('stage', stream)
        record = dict(event=event, event_id=str(uuid.uuid4()), run_ref=self.run_ref,
                      timestamp=datetime.now(timezone.utc).isoformat(),
                      elapsed_ms=round((self.clock() - self.started) * 1000, 3), **fields)
        data = (json.dumps(record, sort_keys=True, allow_nan=False) + '\n').encode()
        with private(self.directory / 'diagnostics.lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = private(self.directory / (stream + '.jsonl'))
            if path.stat().st_size + len(data) > LIMIT:
                rotated = self.directory / (stream + '.jsonl.1')
                if rotated.exists():
                    private(rotated).unlink()
                path.rename(rotated)
                path = private(path, create=True)
            with path.open('ab') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            facts_path = private(self.directory / 'facts.json')
            facts = json.loads(facts_path.read_text() or '{}')
            changed = False
            for field in ('mutation_attempted', 'receipt_received', 'grant_possible'):
                if record.get(field) and not facts.get(field):
                    facts[field] = True
                    changed = True
            if (record.get('status') in {'fail', 'timeout', 'unknown'} and record.get('code')
                    and not (event == 'predicate' and record['status'] == 'unknown')):
                facts['last_failure'] = record
                if event in {'predicate', 'confirmation_end', 'rpc_response', 'transport_response', 'command_end', 'invariant_failed'}:
                    generic = {'workflow_failed', 'prerequisite_blocked', 'host_or_configuration_failed',
                               'preflight_failed', 'stage_failed', 'validation_failed'}
                    if record['code'] not in generic or 'specific_failure' not in facts:
                        facts['specific_failure'] = record
                changed = True
            if changed:
                save(facts_path, facts)

    @staticmethod
    def retain(root, current, keep=20, days=30):
        """Expire only diagnostic files; ownership journals/credentials stay intact."""
        candidates = sorted((p for p in root.iterdir() if p.is_dir()
                             and re.fullmatch(r'[a-f0-9]{32}', p.name) and p != current),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        for index, directory in enumerate(candidates):
            if index < keep - 1 and time.time() - directory.stat().st_mtime < days * 86400:
                continue
            private(directory, directory=True)
            for stream in STREAMS:
                for suffix in ('.jsonl', '.jsonl.1'):
                    path = directory / (stream + suffix)
                    if path.exists():
                        private(path).unlink()
            for name in ('result.json', 'facts.json', 'diagnostics.lock'):
                path = directory / name
                if path.exists():
                    if name == 'result.json':
                        result = json.loads(private(path).read_text())
                        if result.get('create_attempted') and not result.get('native_resource_absent'):
                            continue
                    private(path).unlink()
            # Other state, including any unconfirmed ownership/credentials, is untouched.

    def evidence(self):
        records = []
        for stream in STREAMS:
            for suffix in ('.jsonl.1', '.jsonl'):
                path = self.directory / (stream + suffix)
                if path.exists():
                    records.extend(json.loads(line) for line in private(path).read_text().splitlines())
        return records

    def summary(self, stage, code, cleanup):
        records = self.evidence()
        facts = json.loads(private(self.directory / 'facts.json').read_text() or '{}')
        attempted = bool(facts.get('mutation_attempted')) or any(r.get('mutation_attempted') for r in records)
        receipt = bool(facts.get('receipt_received')) or any(r.get('receipt_received') for r in records)
        granted = bool(facts.get('grant_possible')) or any(r.get('grant_possible') for r in records)
        failures = [r for r in records if r.get('status') in {'fail', 'timeout', 'unknown'}]
        specific = [r for r in failures if r['event'] in {'predicate', 'confirmation_end', 'rpc_response'}]
        failure = facts.get('specific_failure') or (specific[-1] if specific else facts.get('last_failure', {}))
        if code == 'interrupted':
            failure = {'code': code, 'stage': stage}
        code = failure.get('code', code)
        stage = failure.get('stage', stage)
        return dict(failing_stage=stage, invariant=code, mutation_attempted=attempted,
                    receipt_received=receipt, runtime_permission_may_have_been_granted=granted,
                    owned_sandbox_cleanup_confirmed=cleanup,
                    root_cause={'confirmed': [failure.get('code', code)],
                                'suspected': ['policy_application_converging'] if failure.get('retryable') else [],
                                'unknown': (['eventual_policy_load'] if failure.get('retryable') else [])
                                + ([] if not attempted or receipt else ['native_mutation_outcome'])})


def step(stage):
    """Record lifecycle without allowing exception prose into evidence."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            global CLEANING
            previous_cleaning = CLEANING
            if stage == 'cleanup':
                CLEANING = True
            started = time.monotonic()
            emit('events', 'step_start', stage=stage, operation=function.__name__)
            try:
                result = function(*args, **kwargs)
            except BaseException as error:
                code = getattr(error, 'code', 'interrupted' if isinstance(error, KeyboardInterrupt) else 'stage_failed')
                emit('events', 'step_end', stage=stage, operation=function.__name__, status='fail',
                     code=code, duration_ms=(time.monotonic() - started) * 1000)
                CLEANING = previous_cleaning
                raise
            emit('events', 'step_end', stage=stage, operation=function.__name__, status='pass',
                 duration_ms=(time.monotonic() - started) * 1000)
            CLEANING = previous_cleaning
            return result
        return wrapped
    return decorate
