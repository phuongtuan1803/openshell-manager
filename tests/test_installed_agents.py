"""Native agents share the existing sandbox; credentials never cross from the host."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openshell_manager import agent_guest, agents, cli, control, installed_agents as support


@pytest.fixture
def sandbox(tmp_path):
    data = {'sandbox_id': 'existing-sandbox', 'policy': control.base_policy()}
    calls = []
    fake = SimpleNamespace(
        STATE=tmp_path, NAME='existing-dev', HERE=control.HERE,
        PROJECT_ROOT=control.PROJECT_ROOT, require=control.require,
        read=control.read, save=control.save,
        validate_rule=control.validate_rule, network_rule=control.network_rule,
        oshell=lambda *args, **kw: calls.append(args),
    )
    return fake, data, calls


def receipt(name='claude'):
    version = agents.adapter(name).version
    return {'agent': name, 'version': version,
            'binary': f'/sandbox/tools/{name}/{version}/{name}',
            'home': f'/sandbox/{name}-home', 'sha256': 'a' * 64,
            'sandbox_id': 'existing-sandbox'}


def elf(path):
    header = bytearray(20)
    header[:4] = b'\x7fELF'
    header[18:20] = b'\x3e\x00'
    path.write_bytes(header + b'INERT NATIVE FIXTURE')
    path.chmod(0o700)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('name', ['claude', 'opencode'])
def test_prepare_only_uploads_binary_to_bound_existing_sandbox(sandbox, monkeypatch, tmp_path, name):
    c, data, calls = sandbox
    source = tmp_path / 'native'
    expected = elf(source)
    requests = []
    monkeypatch.setattr(support, 'idle_guest', lambda c: None)

    def guest(c, installed, action, **kwargs):
        requests.append((action, kwargs))
        return {k: v for k, v in installed.items() if k != 'sandbox_id'}

    monkeypatch.setattr(support, 'guest', guest)
    prepared = support.prepare(c, name, source, expected, data)
    assert prepared['sandbox_id'] == 'existing-sandbox'
    assert c.read(support.receipt_path(c, name)) == prepared
    assert [call[:3] for call in calls] == [('sandbox', 'upload', 'existing-dev')]
    assert all('create' not in call and 'provider' not in call for call in calls)
    assert [action for action, _ in requests] == ['directories', 'install']
    assert agents.adapter(name).live is False


def test_prepare_rejects_non_executable_credentials_before_guest_access(sandbox, monkeypatch, tmp_path):
    c, data, calls = sandbox
    account = tmp_path / 'auth.json'
    account.write_text('{"token":"PRIVATE_TEST_ACCOUNT"}')
    monkeypatch.setattr(support, 'guest', lambda *a, **k: pytest.fail('Host account must not transfer'))
    with pytest.raises(ValueError, match='native executable'):
        support.prepare(c, 'claude', account, hashlib.sha256(account.read_bytes()).hexdigest(), data)
    assert not calls


def test_prepare_checks_hash_and_active_task_before_upload(sandbox, monkeypatch, tmp_path):
    c, data, calls = sandbox
    source = tmp_path / 'native'
    expected = elf(source)
    with pytest.raises(ValueError, match='checksum mismatch'):
        support.prepare(c, 'claude', source, 'b' * 64, data)
    monkeypatch.setattr(support, 'idle_guest', lambda c: (_ for _ in ()).throw(ValueError('Active guest')))
    with pytest.raises(ValueError, match='Active guest'):
        support.prepare(c, 'claude', source, expected, data)
    assert not calls


def test_receipt_reuse_verifies_same_sandbox_and_executable(sandbox, monkeypatch):
    c, data, _ = sandbox
    stored = receipt()
    c.save(support.receipt_path(c, 'claude'), stored)
    monkeypatch.setattr(support, 'guest', lambda c, r, action: {k: v for k, v in r.items() if k != 'sandbox_id'})
    assert support.load(c, 'claude', data) == stored
    with pytest.raises(ValueError, match='identity drift'):
        support.load(c, 'claude', {**data, 'sandbox_id': 'replacement'})
    monkeypatch.setattr(support, 'guest', lambda *a: {'sha256': 'bad'})
    with pytest.raises(ValueError, match='differs'):
        support.load(c, 'claude', data)


def test_claude_network_plan_is_exact_and_does_not_grant_access(sandbox):
    c, data, calls = sandbox
    before = copy.deepcopy(data)
    rules = support.network_rules(c, receipt(), data)
    assert {r['host'] for r in rules} == {'claude.ai', 'platform.claude.com', 'api.anthropic.com'}
    assert all(r['port'] == 443 and r['binaries'] == [receipt()['binary']] for r in rules)
    assert not calls and data == before
    for rule in rules:
        data['policy']['network_policies'][rule['id']] = c.network_rule(rule)
    assert support.network_rules(c, receipt(), data) == []
    data['policy']['network_policies'][rules[0]['id']]['binaries'] = [{'path': '/usr/bin/node'}]
    with pytest.raises(ValueError, match='rule drift'):
        support.network_rules(c, receipt(), data)


def test_opencode_only_uses_selected_reviewed_provider(sandbox):
    c, data, calls = sandbox
    with pytest.raises(ValueError):
        support.network_rules(c, receipt('opencode'), data)
    rules = support.network_rules(c, receipt('opencode'), data, 'openai/reviewed-model')
    assert [r['host'] for r in rules] == ['api.openai.com']
    assert 'api.anthropic.com' not in json.dumps(rules)
    with pytest.raises(ValueError, match='reviewed endpoint'):
        support.network_rules(c, receipt('opencode'), data, 'unknown/model')
    assert not calls


@pytest.mark.parametrize('name', ['claude', 'opencode'])
def test_guest_accounts_are_isolated_and_host_credentials_not_inherited(name, monkeypatch):
    monkeypatch.setenv('HOME', '/host-home')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'DO_NOT_COPY_KEY')
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'DO_NOT_COPY_SUBSCRIPTION')
    monkeypatch.setenv('OPENAI_API_KEY', 'DO_NOT_COPY_OPENAI')
    monkeypatch.setenv('SSL_CERT_FILE', '/run/openshell-supervisor-ca/material/ca-bundle.crt')
    monkeypatch.setenv('GIT_SSL_CAINFO', '/run/openshell-supervisor-ca/material/ca-bundle.crt')
    env = agent_guest.environment(name)
    assert env['HOME'] == f'/sandbox/{name}-home'
    assert 'DO_NOT_COPY' not in json.dumps(env)
    assert env['GIT_SSL_CAINFO'] == env['SSL_CERT_FILE']
    if name == 'claude':
        assert env['NODE_EXTRA_CA_CERTS'] == env['SSL_CERT_FILE']
        assert env['DISABLE_AUTOUPDATER'] == '1'


def test_guest_rejects_symlinks_and_writable_executables(tmp_path):
    binary = tmp_path / 'native'
    expected = elf(binary)
    assert agent_guest.file_hash(binary) == expected
    link = tmp_path / 'link'
    link.symlink_to(binary)
    with pytest.raises(ValueError, match='Symlink'):
        agent_guest.file_hash(link)
    binary.chmod(0o777)
    with pytest.raises(ValueError, match='another user'):
        agent_guest.file_hash(binary)


def test_start_keeps_existing_checkout_sync_and_source_lock(sandbox, monkeypatch):
    c, _, _ = sandbox
    calls = []
    c.execute = lambda *a, **k: pytest.fail('Start must use shared session lock/sync')
    c.session = lambda *a, **k: calls.append((a, k))
    support.guest(c, receipt(), 'start', model_args=[])
    assert calls[0][0] == ('launch',)
    assert calls[0][1]['interactive'] is True
    assert calls[0][1]['argv'][0] == '/usr/bin/python3'


def test_task_lock_blocks_other_agent(sandbox):
    c, _, _ = sandbox
    with support.task_lease(c):
        with pytest.raises(ValueError, match='Active session'):
            with support.task_lease(c):
                pytest.fail('Two agents must not own the same task')


def test_agent_cli_dispatch_and_missing_profile(monkeypatch, capsys):
    seen = []
    original = support.main
    monkeypatch.setattr(support, 'main', lambda args: seen.append(args) or 19)
    assert cli.main(['agent', 'status', '--agent', 'claude']) == 19
    assert seen == [['status', '--agent', 'claude']]
    assert cli.main(['start', 'claude', '--model', 'reviewed-model']) == 19
    assert seen[-1] == ['start', '--agent', 'claude', '--model', 'reviewed-model']
    assert cli.main(['start', 'opencode', '--model', 'anthropic/reviewed-model']) == 19
    assert seen[-1] == ['start', '--agent', 'opencode', '--model', 'anthropic/reviewed-model']
    monkeypatch.setattr(support, 'main', original)
    monkeypatch.delenv('MANAGER_PROJECT', raising=False)
    monkeypatch.setattr(control, 'configure', lambda *a: pytest.fail('Missing profile must not configure'))
    assert support.main(['status', '--agent', 'claude']) == 1
    assert 'Supply --project' in capsys.readouterr().err


def test_guest_install_checks_upload_before_publishing(tmp_path, monkeypatch):
    binary = tmp_path / 'claude'
    upload = tmp_path / ('.upload-' + 'a' * 32)
    expected = elf(upload)
    upload.chmod(0o644)
    monkeypatch.setattr(agent_guest, 'paths', lambda _: ('claude', '2.1.296', binary, tmp_path))
    monkeypatch.setattr(agent_guest, 'verified', lambda _: {'sha256': agent_guest.file_hash(binary)})
    assert agent_guest.main({'action': 'install', 'upload': str(upload), 'sha256': expected}) == {'sha256': expected}
    assert binary.stat().st_mode & 0o777 == 0o700
    assert not upload.exists()
    bad = tmp_path / ('.upload-' + 'b' * 32)
    elf(bad)
    before = binary.read_bytes()
    with pytest.raises(ValueError, match='checksum mismatch'):
        agent_guest.main({'action': 'install', 'upload': str(bad), 'sha256': 'f' * 64})
    assert binary.read_bytes() == before
    assert not bad.exists()


def test_guest_inspection_rejects_version_mismatch(tmp_path, monkeypatch):
    binary = tmp_path / 'claude'
    expected = elf(binary)
    monkeypatch.setattr(agent_guest, 'paths', lambda _: ('claude', '2.1.296', binary, tmp_path))
    monkeypatch.setattr(agent_guest.os, 'getuid', lambda: binary.stat().st_uid)
    monkeypatch.setattr(agent_guest.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='9.9.9\n'))
    with pytest.raises(ValueError, match='version differs'):
        agent_guest.verified({'sha256': expected})
