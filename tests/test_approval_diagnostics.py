"""Synthetic evidence only. Host processes, Telegram and Gateway remain mocked."""
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from openshell_manager.approval import diagnostics as diag, live_test as live, native
from openshell_manager import cli
from test_approval_native import setup as native_setup, callback
from test_approval_live import trial, mock_host


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    writer = diag.Diagnostics(tmp_path / 'evidence')
    monkeypatch.setattr(diag, 'ACTIVE', writer)
    return writer


def test_private_allowlist_and_no_payloads(evidence):
    for stream in diag.STREAMS:
        assert stat.S_IMODE((evidence.directory / (stream + '.jsonl')).stat().st_mode) == 0o600
    assert stat.S_IMODE(evidence.directory.stat().st_mode) == 0o700
    for fields in ({'bot_token': 'secret'}, {'code': 'Bearer secret'}, {'environment': {}},
                   {'policy_hash': 'private-key'}, {'duration_ms': float('nan')}, {'receipt': 'body'}):
        with pytest.raises(ValueError):
            evidence.emit('native', 'test', **fields)
    assert not evidence.evidence()
    unsafe = evidence.directory / 'commands.jsonl'
    unsafe.chmod(0o644)
    with pytest.raises(ValueError):
        evidence.emit('commands', 'test')


def test_native_uint64_provider_revision_is_preserved(native_setup, evidence):
    _, rpc, adapter, _, _, _, _ = native_setup
    # Native hashes provider state to an unsigned 64-bit revision, including
    # the empty-provider case. It is not a small sequential counter.
    rpc.responses['GetSandboxConfig'].provider_env_revision = 2**64 - 1
    adapter.context()
    observed = next(r for r in evidence.evidence() if r['event'] == 'configuration_observed')
    assert observed['provider_revision'] == 2**64 - 1
    with pytest.raises(ValueError):
        evidence.emit('native', 'test', provider_revision=2**64)


def test_rotation_retains_mutation_and_receipt_facts(evidence, monkeypatch):
    monkeypatch.setattr(diag, 'LIMIT', 900)
    evidence.emit('native', 'rpc_start', mutation_attempted=True, grant_possible=True)
    evidence.emit('native', 'approval_receipt', receipt_received=True)
    evidence.emit('native', 'confirmation_end', status='fail', code='receipt_hash')
    for _ in range(25):
        evidence.emit('native', 'poll', status='pass')
    summary = evidence.summary('workflow', 'failed', True)
    assert summary['mutation_attempted'] and summary['receipt_received']
    assert summary['runtime_permission_may_have_been_granted']
    assert summary['invariant'] == 'receipt_hash'
    assert (evidence.directory / 'native.jsonl').stat().st_size <= 900
    assert (evidence.directory / 'native.jsonl.1').stat().st_size <= 900


def test_shared_writers_event_identity_and_monotonic_duration(evidence):
    other = diag.Diagnostics(evidence.directory, monotonic=lambda: 123)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda i: (evidence if i % 2 else other).emit('events', 'poll', retry=i), range(30)))
    records = evidence.evidence()
    assert len(records) == len({r['event_id'] for r in records}) == 30
    assert len({r['run_ref'] for r in records}) == 1
    assert all(r['timestamp'].endswith('+00:00') and r['elapsed_ms'] >= 0 for r in records)


@pytest.mark.parametrize('phase', ['success', 'delayed', 'receipt_failure', 'lost_response', 'hash_drift'])
def test_bridge_evidence_and_single_mutation(native_setup, evidence, phase):
    _, rpc, adapter, store, bot, bridge, _ = native_setup
    bridge.scan()
    if phase == 'delayed':
        rpc.delayed_load = True
        adapter.sleep = lambda seconds: setattr(rpc, 'loaded', True)
    if phase == 'lost_response':
        rpc.fail = 'after'
    if phase in {'receipt_failure', 'hash_drift'}:
        original = rpc.call
        def altered(method, **fields):
            response = original(method, **fields)
            if method == 'ApproveAllDraftChunks':
                if phase == 'receipt_failure':
                    response.policy_hash = 'f' * 64
                else:
                    rpc.responses['GetSandboxConfig'].policy_hash = 'f' * 64
            return response
        rpc.call = altered
    bridge.update(callback(store, bot))
    summary = evidence.summary('confirmation', 'unknown', True)
    assert summary['mutation_attempted'] and summary['receipt_received'] == (phase != 'lost_response')
    assert summary['runtime_permission_may_have_been_granted']
    records = evidence.evidence()
    mutation = next(r for r in records if r['event'] == 'rpc_start' and r['mutation_attempted'])
    decision = next(r for r in records if r['event'] == 'decision_claimed')
    assert all(mutation[key] == decision[key] for key in ('proposal_ref', 'sandbox_ref', 'decision_ref'))
    if phase in {'success', 'delayed'}:
        assert store.rows()[0]['status'] == 'approved'
        predicates = {r['code'] for r in records if r['event'] == 'predicate'}
        assert {'proposal_status', 'proposal_rule_equality', 'receipt_hash', 'receipt_version',
                'confirmation_loaded_state', 'confirmation_active_version', 'provider_revision'} <= predicates
    else:
        assert store.rows()[0]['status'] == 'needs_review'
        assert summary['invariant'] in {'receipt_hash', 'candidate_policy_hash', 'native_transport_unknown'}
    rpc.fail = None
    for _ in range(8):
        bridge.reconcile_native()
    assert len(rpc.mutations) == 1
    if phase in {'receipt_failure', 'hash_drift'}:
        assert store.rows()[0]['status'] == 'needs_review'
    if phase == 'lost_response':
        assert store.rows()[0]['status'] == 'approved'
    payload = ''.join(p.read_text() for p in evidence.directory.glob('*.json*'))
    assert rpc.responses['GetDraftPolicy'].chunks[0].review_token not in payload
    assert not any(token in payload for _, tokens in bot.notices for token in tokens.values())
    assert 'proposed_rule' not in payload


def test_transient_failure_cannot_hide_permanent_drift(native_setup, evidence):
    _, rpc, adapter, _, _, _, _ = native_setup
    snapshot = adapter.discover()[0]
    receipt = adapter.approve(snapshot, str(uuid.uuid4()), 1900)
    rpc.loaded = False
    rpc.responses['GetDraftPolicy'].chunks[0].proposed_rule.name = 'changed'
    sleeps = []
    adapter.sleep = sleeps.append
    with pytest.raises(native.NativeVerification, match='proposal_rule_equality'):
        adapter.confirm(snapshot, 'approve', receipt)
    assert not sleeps and len(rpc.mutations) == 1


def test_bounded_load_retries_and_persistent_receipt(native_setup, evidence):
    _, rpc, adapter, _, _, _, _ = native_setup
    snapshot = adapter.discover()[0]
    rpc.delayed_load = True
    receipt = adapter.approve(snapshot, str(uuid.uuid4()), 1900)
    sleeps = []
    adapter.sleep = sleeps.append
    with pytest.raises(native.NativeVerification) as failure:
        adapter.confirm(snapshot, 'approve', receipt)
    assert failure.value.retryable and len(sleeps) == 3
    assert len(rpc.mutations) == 1
    assert len([r for r in evidence.evidence() if r['event'] == 'confirmation_start']) == 4
    assert evidence.summary('native', 'failed', True)['receipt_received']


def test_loaded_revision_with_lagging_active_pointer_converges(native_setup, evidence):
    _, rpc, adapter, _, _, _, _ = native_setup
    snapshot = adapter.discover()[0]
    receipt = adapter.approve(snapshot, str(uuid.uuid4()), 1900)
    # Native ReportPolicyStatus persists LOADED before updating the sandbox's
    # active version. These independent reads can observe that short window.
    lagging = [True]
    original = rpc.call
    def call(method, **fields):
        result = original(method, **fields)
        if method == 'GetSandboxPolicyStatus' and lagging[0]:
            assert result.revision.status == 2
            result.active_version = 1
        return result
    rpc.call = call
    sleeps = []
    def converge(seconds):
        sleeps.append(seconds)
        lagging[0] = False
    adapter.sleep = converge
    assert adapter.confirm(snapshot, 'approve', receipt)['loaded']
    assert sleeps == [10] and len(rpc.mutations) == 1
    records = evidence.evidence()
    assert any(r.get('code') == 'active_policy_version' and r['status'] == 'wait' for r in records)


def test_confirmation_waits_for_supervisor_policy_poll(native_setup, evidence):
    _, rpc, adapter, _, _, _, _ = native_setup
    snapshot = adapter.discover()[0]
    rpc.delayed_load = True
    receipt = adapter.approve(snapshot, str(uuid.uuid4()), 1900)
    original = rpc.call
    elapsed = [0]

    def call(method, **fields):
        result = original(method, **fields)
        if method == 'GetSandbox' and not rpc.loaded:
            admission = result.sandbox.status.configuration_admission
            admission.policy_version = snapshot['version']
            admission.policy_hash = snapshot['policy_hash']
            admission.config_revision = snapshot['config_revision']
        return result

    def supervisor_poll(seconds):
        elapsed[0] += seconds
        if elapsed[0] >= 10:
            rpc.loaded = True

    rpc.call = call
    adapter.sleep = supervisor_poll
    assert adapter.confirm(snapshot, 'approve', receipt)['loaded']
    assert 10 <= elapsed[0] <= 30
    assert len(rpc.mutations) == 1
    records = evidence.evidence()
    assert any(r.get('code') == 'admission_policy_revision' and r['status'] == 'wait' for r in records)


@pytest.mark.parametrize('unauthorized', [True, False])
def test_callback_authorization_and_staleness_evidence(native_setup, evidence, unauthorized):
    _, rpc, _, store, bot, bridge, now = native_setup
    bridge.scan()
    if not unauthorized:
        now[0] = 2000
    bridge.update(callback(store, bot, user=99 if unauthorized else 34))
    records = evidence.evidence()
    received = next(r for r in records if r['event'] == 'callback_received')
    assert received['user_match'] == (not unauthorized) and received['chat_match']
    assert any(r.get('code') == ('unauthorized_callback' if unauthorized else 'expired_callback') for r in records)
    assert not rpc.mutations


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'interrupt'])
def test_command_evidence_excludes_arguments_output_environment(evidence, monkeypatch, failure):
    secret = 'PRIVATE_SECRET_DO_NOT_DISCLOSE'
    def process(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args[0], 2, output=secret, stderr=secret)
        if failure == 'interrupt':
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(args[0], 19, secret, secret)
    monkeypatch.setattr(live.subprocess, 'run', process)
    with pytest.raises((live.Blocked, subprocess.TimeoutExpired, KeyboardInterrupt)):
        live.run(['docker', 'exec', secret], timeout=2, env={'TOKEN': secret})
    records = evidence.evidence()
    assert any(r['event'] == 'command_end' and r['status'] == 'fail' for r in records)
    assert secret not in json.dumps(records)


def test_failure_and_cleanup_keep_diagnostics(trial, evidence, monkeypatch):
    trial.diagnostics = evidence
    evidence.emit('native', 'confirmation_end', status='fail', code='receipt_hash', receipt_received=True)
    trial.create_attempted = True
    monkeypatch.setattr(trial, 'listing', lambda: [])
    assert trial.cleanup() and trial.cleaned
    assert evidence.summary('confirmation', 'failed', trial.cleaned)['invariant'] == 'receipt_hash'
    assert all((evidence.directory / (stream + '.jsonl')).exists() for stream in diag.STREAMS)


def test_guest_wait_optional_output_is_not_persisted(trial, native_setup, evidence, monkeypatch):
    cfg, rpc, adapter, store, bot, bridge, _ = native_setup
    trial.cfg, trial.adapter, trial.name = cfg, adapter, cfg['sandbox']
    bridge.scan()
    bridge.update(callback(store, bot, action='reject'))
    secret = 'UNTRUSTED_GUEST_OUTPUT'
    monkeypatch.setattr(trial, 'probe', lambda *args, **kwargs: {
        'status': 'rejected', 'has_rejection_reason': True, 'policy_reloaded': secret})
    trial.decision(rpc.responses['GetDraftPolicy'].chunks[0].id, 'reject')
    assert secret not in (trial.directory / 'result.json').read_text()


def test_event_recovers_facts_when_fact_write_was_interrupted(evidence):
    evidence.emit('native', 'rpc_start', mutation_attempted=True, grant_possible=True)
    evidence.emit('native', 'approval_receipt', receipt_received=True)
    # Model interruption after the fsynced event but before the sticky update.
    diag.save(evidence.directory / 'facts.json', {})
    summary = evidence.summary('native', 'interrupted', True)
    assert summary['mutation_attempted'] and summary['receipt_received']
    assert summary['runtime_permission_may_have_been_granted']


def test_retention_prunes_only_old_diagnostics(tmp_path):
    root = tmp_path / 'runs'
    root.mkdir(mode=0o700)
    old = diag.Diagnostics(root / ('a' * 32))
    current = diag.Diagnostics(root / ('b' * 32))
    marker = old.directory / 'ownership.json'
    marker.write_text('keep me')
    diag.save(old.directory / 'result.json', {'create_attempted': True, 'native_resource_absent': False})
    diag.Diagnostics.retain(root, current.directory, keep=1)
    assert marker.exists() and (old.directory / 'result.json').exists()
    assert not (old.directory / 'events.jsonl').exists()
    assert (current.directory / 'events.jsonl').exists()


def test_symlink_and_hardlink_refused(tmp_path):
    target = tmp_path / 'target'
    writer = diag.Diagnostics(target)
    linked = tmp_path / 'linked'
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        diag.Diagnostics(linked)
    os.link(target / 'events.jsonl', tmp_path / 'hardlink')
    with pytest.raises(ValueError):
        writer.emit('events', 'test')


@pytest.mark.parametrize('failure', ['receipt', 'interrupt', 'cleanup'])
def test_cli_failure_summary_persists_after_cleanup(tmp_path, monkeypatch, capsys, failure):
    mock_host(monkeypatch, tmp_path)
    class Test:
        def __init__(self, directory, *args):
            self.directory, self.results = directory, {}
            self.grant_possible, self.cleaned = False, False
        def prepare(self, image):
            pass
        def start_bridge(self, path):
            pass
        def workflow(self):
            diag.emit('native', 'rpc_start', mutation_attempted=True, grant_possible=True)
            if failure == 'interrupt':
                raise KeyboardInterrupt
            diag.emit('native', 'approval_receipt', receipt_received=True)
            if failure == 'receipt':
                diag.emit('native', 'confirmation_end', status='fail', code='receipt_hash')
                raise live.Failed('Native invariant receipt_hash')
        def cleanup(self):
            self.cleaned = failure != 'cleanup'
            return self.cleaned
        def remove_cli_registry(self):
            pass
        def journal(self):
            diag.save(self.directory / 'result.json', dict(self.results, diagnostics=getattr(self, 'diagnostic_summary', None)))
    monkeypatch.setattr(live, 'LiveTest', Test)
    assert cli.main(['approval', 'test']) == (130 if failure == 'interrupt' else 1)
    output = capsys.readouterr().out
    assert all(label in output for label in ('Failure stage:', 'invariant/RPC:', 'Native mutation attempted: True',
               'receipt received:', 'Runtime permission may have been granted: True',
               'owned sandbox cleanup confirmed:', 'Private diagnostics:'))
    root = Path.home() / '.local/state/openshell-manager/approval-live'
    result = json.loads(next(root.glob('*/result.json')).read_text())
    summary = result['diagnostics']
    assert summary['mutation_attempted'] and summary['receipt_received'] == (failure != 'interrupt')
    assert summary['owned_sandbox_cleanup_confirmed'] == (failure != 'cleanup')
    assert set(summary['root_cause']) == {'confirmed', 'suspected', 'unknown'}
