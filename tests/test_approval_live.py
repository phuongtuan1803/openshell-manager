"""Offline orchestration tests. Mocked outcomes are never live acceptance evidence."""
import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openshell_manager import cli, control
from openshell_manager.approval import live_test as live, probe, service, telegram
from openshell_manager.approval.store import private

TOKEN = '123456:' + 'x' * 30


@pytest.fixture
def trial(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    state = tmp_path / 'test-state'
    state.mkdir(mode=0o700)
    rpc = SimpleNamespace(call=lambda *a, **k: None)
    test = live.LiveTest(directory, state, 'openshell', 'default', rpc, 'a' * 40)
    test.image = 'sha256:' + 'e' * 64
    test.cfg = {'state': state / test.project_id}
    return test


def auth_file(tmp_path):
    path = tmp_path / 'telegram.json'
    control.save(path, {'bot_token': TOKEN, 'user_id': 123, 'chat_id': 123})
    return path


def test_one_command_dispatch_without_profiles(monkeypatch):
    seen = []
    monkeypatch.setenv('MANAGER_PROJECT', '/must/not/use/active-project.json')
    monkeypatch.setattr(live, 'main', lambda args: seen.append(args) or 0)
    assert cli.main(['approval', 'test']) == 0
    assert seen[0].action == 'test' and seen[0].gateway is None


def test_wait_has_real_deadline_and_interrupt_propagates():
    ticks = iter([0, 0, 1, 2])
    with pytest.raises(live.Failed, match='expired'):
        live.wait_for(lambda: False, 2, 'expired', clock=lambda: next(ticks), sleep=lambda n: None)
    with pytest.raises(KeyboardInterrupt):
        live.wait_for(lambda: (_ for _ in ()).throw(KeyboardInterrupt()), 1, 'timeout')


def test_bot_existing_reuse_no_prompt(monkeypatch, tmp_path):
    path = auth_file(tmp_path)
    calls = []
    class Bot:
        def __init__(self, token):
            assert token == TOKEN
        def call(self, method, **fields):
            calls.append(method)
            return {'is_bot': True} if method == 'getMe' else {'url': ''}
    monkeypatch.setattr(live, 'Telegram', Bot)
    monkeypatch.setattr(live.getpass, 'getpass', lambda *a: pytest.fail('Token should be reused'))
    assert live.bot_config(path)['user_id'] == 123
    assert calls == ['getMe', 'getWebhookInfo']


@pytest.mark.parametrize('confirm', ['yes', 'no'])
def test_first_run_bot_challenge_identity_confirmation(monkeypatch, tmp_path, capsys, confirm):
    path = tmp_path / 'telegram.json'
    challenge = 'a' * 32
    calls = []
    monkeypatch.setattr(live.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(live.secrets, 'token_hex', lambda n: challenge)
    monkeypatch.setattr(live.getpass, 'getpass', lambda prompt: TOKEN)
    monkeypatch.setattr('builtins.input', lambda prompt: confirm)
    class Bot:
        def __init__(self, token):
            assert token == TOKEN
        def call(self, method, **fields):
            calls.append((method, fields))
            if method == 'getMe':
                return {'is_bot': True, 'username': 'dedicated_test_bot'}
            if method == 'getWebhookInfo':
                return {'url': ''}
            return [{'update_id': 4, 'message': {'text': '/start ' + challenge,
                'from': {'id': 123, 'is_bot': False}, 'chat': {'id': 123, 'type': 'private'}}}]
    monkeypatch.setattr(live, 'Telegram', Bot)
    if confirm == 'yes':
        assert live.bot_config(path) == telegram.credentials(path)
        assert path.stat().st_mode & 0o777 == 0o600
        assert calls[-1][1]['offset'] == 5
    else:
        with pytest.raises(live.Blocked, match='not confirmed'):
            live.bot_config(path)
        assert not path.exists()
    assert TOKEN not in capsys.readouterr().out


def test_bot_bad_enrollment_and_timeout_do_not_save(monkeypatch, tmp_path):
    monkeypatch.setattr(live.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(live.getpass, 'getpass', lambda prompt: TOKEN)
    class Bot:
        def __init__(self, token): pass
        def call(self, method, **fields):
            if method == 'getMe': return {'is_bot': True, 'username': 'dedicated_test_bot'}
            if method == 'getWebhookInfo': return {'url': ''}
            return [{'update_id': 1, 'message': {'text': '/start WRONG', 'from': {'id': 5, 'is_bot': False},
                                               'chat': {'id': -99, 'type': 'group'}}}]
    monkeypatch.setattr(live, 'Telegram', Bot)
    def expire(check, *a):
        assert check() is None
        raise live.Failed('timeout')
    monkeypatch.setattr(live, 'wait_for', expire)
    with pytest.raises(live.Failed): live.bot_config(tmp_path / 'telegram.json')
    assert not (tmp_path / 'telegram.json').exists()


@pytest.mark.parametrize('failure', ['permissions', 'git', 'webhook', 'no-terminal'])
def test_bot_prerequisites_fail_closed(monkeypatch, tmp_path, failure):
    path = auth_file(tmp_path)
    if failure == 'permissions': path.chmod(0o644)
    if failure == 'git':
        (tmp_path / '.git').mkdir()
        (tmp_path / '.git/HEAD').write_text('ref: refs/heads/main\n')
    if failure == 'no-terminal':
        path.unlink()
        monkeypatch.setattr(live.sys.stdin, 'isatty', lambda: False)
    monkeypatch.setattr(live, 'Telegram', lambda token: SimpleNamespace(call=lambda *a, **k: {'is_bot': True, 'url': 'https://existing.invalid'}))
    with pytest.raises(ValueError): live.bot_config(path)


@pytest.fixture
def image_host(monkeypatch):
    image = 'sha256:' + 'e' * 64
    recipe = control.digest((live.RESOURCES / 'docker/Dockerfile').read_bytes())
    inventory = [name + '\t1.0' for name in
                 ['ca-certificates', 'curl', 'git', 'nodejs', 'npm', 'python3', 'python3-venv', 'python3-pytest']]
    calls = []
    def execute(args, timeout=60):
        calls.append(args)
        if args[:2] == ['docker', 'build']:
            assert timeout == 1800
            assert '-t' not in args and '--tag' not in args
            context = Path(args[-1])
            assert sorted(p.name for p in context.iterdir()) == ['Dockerfile']
            assert (context / 'Dockerfile').read_bytes() == (live.RESOURCES / 'docker/Dockerfile').read_bytes()
            assert args[args.index('--label') + 1] == 'manager.dockerfile=' + recipe
            Path(args[args.index('--iidfile') + 1]).write_text(image + '\n')
            return ''
        if args[:3] == ['docker', 'image', 'inspect']:
            assert args[-1] == image
            return json.dumps([{'Id': image, 'Config': {'Labels': {'manager.dockerfile': recipe}}}])
        assert args[:2] == ['docker', 'run']
        assert args[args.index('--network') + 1] == 'none'
        assert '--read-only' in args and '--rm' in args and '-v' not in args and '-e' not in args
        assert args[-2] == image
        return 'codex-cli 0.162.1\n' if args[-1] == '--version' else '\n'.join(inventory)
    monkeypatch.setattr(live, 'run', execute)
    return SimpleNamespace(image=image, recipe=recipe, inventory=sorted(inventory), calls=calls, run=execute)


def test_image_missing_receipt_builds_then_reuses_private_review(tmp_path, image_host):
    assert live.reviewed_image(tmp_path) == image_host.image
    path = tmp_path / 'approval-live/built-image-review.json'
    receipt = json.loads(private(path).read_text())
    assert receipt == {'image_id': image_host.image, 'dockerfile_sha256': image_host.recipe,
                       'codex_version': 'codex-cli 0.162.1', 'inventory': image_host.inventory}
    assert not Path(image_host.calls[0][-1]).exists()  # Empty context removed.
    image_host.calls.clear()
    assert live.reviewed_image(tmp_path) == image_host.image
    assert not any(args[1] == 'build' for args in image_host.calls)


def test_image_existing_verified_project_receipt_reused(tmp_path, image_host):
    control.save(tmp_path / 'project/built-image-review.json', {
        'image_id': image_host.image, 'dockerfile_sha256': image_host.recipe,
        'codex_version': 'codex-cli 0.162.1', 'inventory': image_host.inventory})
    assert live.reviewed_image(tmp_path) == image_host.image
    assert all(args[1] != 'build' for args in image_host.calls)


@pytest.mark.parametrize('mismatch', ['recipe', 'digest', 'label', 'version', 'inventory', 'missing'])
def test_image_incompatible_existing_receipt_builds_without_mutating_existing(monkeypatch, tmp_path, image_host, mismatch):
    old = 'sha256:' + 'a' * 64
    path = tmp_path / 'old-project/built-image-review.json'
    receipt = {'image_id': old, 'dockerfile_sha256': image_host.recipe,
               'codex_version': 'codex-cli 0.162.1', 'inventory': image_host.inventory}
    if mismatch == 'recipe': receipt['dockerfile_sha256'] = 'outdated'
    control.save(path, receipt)
    original = path.read_bytes()
    def execute(args, timeout=60):
        if old in args:
            if args[1] == 'image':
                if mismatch == 'missing': raise live.Blocked('not found')
                return json.dumps([{'Id': 'sha256:' + '0' * 64 if mismatch == 'digest' else old,
                                   'Config': {'Labels': {} if mismatch == 'label' else {'manager.dockerfile': image_host.recipe}}}])
            return ('codex-cli wrong' if mismatch == 'version' else 'codex-cli 0.162.1') if args[-1] == '--version' else 'curl\t1'
        return image_host.run(args, timeout)
    monkeypatch.setattr(live, 'run', execute)
    assert live.reviewed_image(tmp_path) == image_host.image
    assert path.read_bytes() == original
    assert any(args[1] == 'build' for args in image_host.calls)
    assert all(args[1] in {'build', 'image', 'run'} for args in image_host.calls)


@pytest.mark.parametrize('failure', ['build', 'timeout', 'iid', 'label', 'digest', 'version', 'inventory', 'empty-inventory'])
def test_image_build_failure_never_records_review(monkeypatch, tmp_path, image_host, failure):
    def execute(args, timeout=60):
        if args[1] == 'build':
            if failure == 'build': raise live.Blocked('private command output must not leak')
            if failure == 'timeout': raise subprocess.TimeoutExpired(args, timeout)
            result = image_host.run(args, timeout)
            if failure == 'iid': Path(args[args.index('--iidfile') + 1]).write_text('invented')
            return result
        if args[1] == 'image' and failure in {'label', 'digest'}:
            return json.dumps([{'Id': 'wrong' if failure == 'digest' else image_host.image,
                               'Config': {'Labels': {}}}])
        if args[-1] == '--version' and failure == 'version': return 'codex-cli 0.999'
        if args[-1] == '-W' and failure in {'inventory', 'empty-inventory'}:
            return 'unstructured output' if failure == 'inventory' else ''
        return image_host.run(args, timeout)
    monkeypatch.setattr(live, 'run', execute)
    with pytest.raises(live.Blocked, match='build/verification failed') as error:
        live.reviewed_image(tmp_path)
    assert 'private command output' not in str(error.value)
    assert not (tmp_path / 'approval-live/built-image-review.json').exists()



def sandbox(test, *, id='owned-uuid', marker=None, name=None):
    meta = SimpleNamespace(id=id, name=name or test.name, workspace='default', labels={
        'manager.approval-test': marker or test.nonce, 'manager.project': test.project_id,
        'manager.phase': '2', 'manager.image': 'e' * 32})
    return SimpleNamespace(metadata=meta, status=SimpleNamespace(phase=2, configuration_admission=SimpleNamespace(state=2)))


@pytest.mark.parametrize('name', ['oma-security-test', 'oma-dev-validation', 'oma-dev-phase2', 'oma-debug'])
def test_protected_sandboxes_cannot_be_exec_or_cleanup_targets(trial, monkeypatch, name):
    trial.name = name
    trial.create_attempted = True
    monkeypatch.setattr(trial, 'cli', lambda *a, **k: pytest.fail('No protected sandbox operation allowed'))
    monkeypatch.setattr(trial, 'listing', lambda: [{'name': name, 'id': 'existing'}])
    with pytest.raises(live.Blocked): trial.execute('/bin/true')
    assert trial.cleanup() is False


@pytest.mark.parametrize('change', ['marker', 'uuid', 'name'])
def test_ownership_drift_prevents_exec_and_delete(trial, monkeypatch, change):
    trial.resource_id = 'owned-uuid'
    trial.create_attempted = True
    changed = {'marker': {'marker': 'foreign'}, 'uuid': {'id': 'replaced'}, 'name': {'name': 'wrong'}}[change]
    trial.rpc.call = lambda *a, **k: SimpleNamespace(sandbox=sandbox(trial, **changed))
    monkeypatch.setattr(trial, 'listing', lambda: [{'name': trial.name}])
    monkeypatch.setattr(trial, 'cli', lambda *a, **k: pytest.fail('Refuse exec/delete on ownership drift'))
    with pytest.raises(live.Blocked): trial.execute('/bin/true')
    assert not trial.cleanup()


def test_exec_uses_supported_explicit_pinned_flags(trial, monkeypatch):
    trial.rpc.call = lambda *a, **k: SimpleNamespace(sandbox=sandbox(trial))
    calls = []
    monkeypatch.setattr(trial, 'cli', lambda *a, **k: calls.append((a,k)) or '{}')
    trial.execute('/usr/bin/python3', '-I', '-c', 'print(1)')
    args = calls[0][0]
    assert '--no-login-shell' in args and '--no-tty' in args and '--timeout' in args
    assert args[args.index('--name') + 1] == trial.name
    assert len(trial.name) <= 19 and args[-3:] == ('-I','-c','print(1)')


def test_cleanup_owned_only_and_confirm_absence(trial, monkeypatch):
    trial.create_attempted = True
    trial.resource_id = 'owned-uuid'
    trial.grant_possible = True
    trial.rpc.call = lambda *a, **k: SimpleNamespace(sandbox=sandbox(trial))
    listings = iter([[{'name': trial.name, 'id': 'owned-uuid'}, {'name': 'oma-security-test'}], [{'name': 'oma-security-test'}]])
    monkeypatch.setattr(trial, 'listing', lambda: next(listings))
    calls = []
    monkeypatch.setattr(trial, 'cli', lambda *a, **k: calls.append(a))
    trial.child = SimpleNamespace(terminate=lambda: calls.append(('terminate',)), wait=lambda timeout: 0)
    assert trial.cleanup() and trial.cleaned
    assert calls == [('terminate',), ('sandbox','delete',trial.name)]
    assert json.loads((trial.directory / 'result.json').read_text())['native_resource_absent']


def test_partial_create_failure_can_clean_only_its_marker(trial, monkeypatch):
    trial.create_attempted = True  # lost create response before UUID persisted
    trial.rpc.call = lambda *a, **k: SimpleNamespace(sandbox=sandbox(trial))
    entries = [{'name': trial.name, 'id': 'owned-uuid'}]
    monkeypatch.setattr(trial, 'listing', lambda: list(entries))
    monkeypatch.setattr(trial, 'cli', lambda *a, **k: entries.clear())
    assert trial.cleanup() and trial.resource_id == 'owned-uuid'


def test_cleanup_failure_never_claims_success(trial, monkeypatch):
    trial.create_attempted = True
    monkeypatch.setattr(trial, 'listing', lambda: (_ for _ in ()).throw(live.Blocked('offline')))
    assert not trial.cleanup() and not trial.cleaned


def test_guest_probe_real_payload_and_only_sanitized_output(monkeypatch):
    calls = []
    def curl(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(returncode=0, stdout='{"accepted_chunk_ids":["native-id"],"rejection_reasons":[]}\n202', stderr=TOKEN)
    monkeypatch.setattr(probe.subprocess, 'run', curl)
    assert probe.main({'action': 'submit', 'name': 'telegram_probe_a', 'path': '/'}) == {'accepted_chunk_ids':['native-id'], 'refused':False}
    body = json.loads(calls[0][1]['input'])
    rule = body['operations'][0]['addRule']['rule']
    assert rule['binaries'] == [{'path':'/usr/bin/curl'}]
    assert rule['endpoints'][0]['rules'] == [{'allow':{'method':'GET','path':'/'}}]
    assert 'http://policy.local/v1/proposals' in calls[0][0]
    assert TOKEN not in json.dumps(body)


@pytest.mark.parametrize('error,code,expected', [('policy_denied','403',True), ('upstream_error','502',False)])
def test_denial_evidence_is_not_arbitrary_network_failure(monkeypatch, error, code, expected):
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'error':error})+'\n'+code))
    assert probe.main({'action':'probe','path':'/'})['policy_denied'] is expected


def snapshot(name='telegram_probe_a', path='/'):
    return {'eligible':True, 'scope':json.dumps({'name':name, 'binaries':[{'path':'/usr/bin/curl'}],
            'endpoints':[{'host':'example.com','rules':[{'allow':{'method':'GET','path':path}}]}]})}


def test_workflow_approval_rejection_retry_and_repeated_request(trial, monkeypatch):
    calls = []
    ids = iter(['native-a', 'native-b'])
    monkeypatch.setattr(trial, 'submit', lambda name,path: next(ids))
    monkeypatch.setattr(trial, 'decision', lambda chunk,action: calls.append((chunk,action)))
    def guest(action, **fields):
        calls.append((action, fields))
        if action == 'submit': return {'accepted_chunk_ids':['native-c'], 'refused':False}
        if fields['path'] == '/': return {'http_status':'200','exit_code':0,'policy_denied':False}
        return {'http_status':'403','exit_code':0,'policy_denied':True}
    monkeypatch.setattr(trial, 'probe', guest)
    trial.adapter = SimpleNamespace(get=lambda *a: snapshot('telegram_probe_b','/pr3b-'+trial.nonce))
    monkeypatch.setattr(trial, 'request', lambda chunk: {'status':'notified'})
    trial.workflow()
    assert ('native-a','approve') in calls and ('native-b','reject') in calls
    assert trial.results['repeated_request']['chunk_id'] == 'native-c'
    assert trial.results['live_replay'].startswith('SKIP')


@pytest.mark.parametrize('failure', ['upstream', 'denial'])
def test_workflow_never_treats_network_failure_as_live_success(trial, monkeypatch, failure):
    monkeypatch.setattr(trial, 'submit', lambda *a: 'chunk')
    monkeypatch.setattr(trial, 'decision', lambda *a: None)
    def guest(action, **fields):
        if failure == 'denial' and fields.get('path') == '/':
            return {'http_status':'200','exit_code':0,'policy_denied':False}
        return {'http_status':'000','exit_code':35,'policy_denied':False}
    monkeypatch.setattr(trial, 'probe', guest)
    with pytest.raises(live.Failed): trial.workflow()


@pytest.mark.parametrize('state', ['expired', 'stale', 'needs_review', 'rejected'])
def test_decision_refuses_expired_uncertain_or_wrong_result(trial, monkeypatch, state):
    monkeypatch.setattr(trial, 'request', lambda chunk: {'status':state})
    with pytest.raises(live.Failed): trial.decision('native-a','approve')


def test_submit_requires_exact_native_scope_not_just_cli_id(trial, monkeypatch):
    monkeypatch.setattr(trial, 'probe', lambda *a, **k: {'accepted_chunk_ids':['native-a'], 'refused':False})
    trial.adapter = SimpleNamespace(get=lambda *a: snapshot())
    assert trial.submit('telegram_probe_a','/') == 'native-a'
    with pytest.raises(live.Blocked): trial.submit('telegram_probe_b','/pr3b-'+trial.nonce)


def test_gateway_preference_and_failed_mtls_no_fallback(monkeypatch):
    base = Path.home()/'.config/openshell/gateways/openshell'
    base.mkdir(parents=True)
    seen = []
    def rpc(name):
        seen.append(name)
        raise live.NativeUnavailable(TOKEN)
    monkeypatch.setattr(live,'GatewayRPC',rpc)
    with pytest.raises(live.Blocked) as error: live.select_gateway()
    assert seen == ['openshell'] and TOKEN not in str(error.value)


def test_missing_gateway_no_native_operations(monkeypatch):
    monkeypatch.setattr(live,'GatewayRPC',lambda *a: pytest.fail('No Gateway selected'))
    with pytest.raises(live.Blocked, match='Gateway'): live.select_gateway()

# Exercise orchestration against the actual bridge/queue/adapter with typed RPC fixtures.
from test_approval_native import setup as native_setup, callback as native_callback


@pytest.mark.parametrize('action', ['approve','reject'])
def test_decision_confirmed_by_existing_engine_and_guest_wait(trial, native_setup, monkeypatch, action):
    cfg, rpc, adapter, store, bot, bridge, now = native_setup
    trial.cfg, trial.adapter = cfg, adapter
    trial.name = cfg['sandbox']
    trial.child = SimpleNamespace(poll=lambda: None)
    bridge.scan()
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    original_request = trial.request
    acted = []
    def request(identity):
        if not acted:
            bridge.update(native_callback(store,bot,action))
            acted.append(True)
        return original_request(identity)
    monkeypatch.setattr(trial,'request',request)
    monkeypatch.setattr(trial,'probe',lambda operation,**k: {'status':chunk.status,
        'policy_reloaded':action=='approve','has_rejection_reason':action=='reject'})
    row = trial.decision(chunk.id,action)
    assert row['status'] == ('approved' if action=='approve' else 'rejected')
    assert len(rpc.mutations) == 1 and trial.results[action]['chunk_id'] == chunk.id
    assert store.db.execute('SELECT count(*) FROM callbacks WHERE processed IS NULL').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['expired', 'child-exit', 'timeout'])
def test_wait_refuses_unverified_native_or_bridge_state(trial, monkeypatch, failure):
    if failure == 'child-exit':
        trial.child = SimpleNamespace(poll=lambda: 1)
        with pytest.raises(live.Failed): trial.request('chunk')
    else:
        monkeypatch.setattr(trial,'request',lambda chunk: {'status':failure} if failure=='expired' else None)
        def bounded(check,seconds,message):
            check()
            raise live.Failed(message)
        monkeypatch.setattr(live,'wait_for',bounded)
        with pytest.raises(live.Failed): trial.decision('chunk','approve')


def mock_host(monkeypatch, tmp_path):
    config = Path(__import__('os').environ['XDG_CONFIG_HOME'])/'openshell'
    config.mkdir(parents=True,exist_ok=True)
    driver = tmp_path/'openshell-driver-vm'
    driver.write_text('not executable in tests')
    (config/'gateway.toml').write_text('[openshell]\nversion=2\n[openshell.gateway]\ncompute_driver="vm"\nbind_address="127.0.0.1:17670"\n[openshell.drivers.vm]\ndriver_dir="'+str(tmp_path)+'"\n')
    monkeypatch.setattr(live.provenance,'identity',lambda: ('a'*40,'fixture'))
    monkeypatch.setattr(live.provenance,'verify',lambda revision: revision)
    monkeypatch.setattr(live.platform,'system',lambda: 'Linux')
    monkeypatch.setattr(live.platform,'machine',lambda: 'x86_64')
    monkeypatch.setattr(live.shutil,'which',lambda name: '/mock/'+name)
    monkeypatch.setattr(live.os,'access',lambda *a: True)
    def command(args,timeout=60):
        if args[:2] == ['openshell','--version']: return 'openshell 0.1.2'
        if args[:3] == ['openshell','sandbox','exec']: return '--no-login-shell --no-tty --timeout --name'
        if args[0].endswith('openshell-gateway') or args[0].endswith('openshell-driver-vm'): return '0.1.2'
        if 'workspace' in args: return json.dumps({'workspaces':[{'name':'default'}],'next_page_token':''})
        return 'mock-host-result'
    monkeypatch.setattr(live,'run',command)
    calls=[]
    monkeypatch.setattr(live,'select_gateway',lambda requested: ('openshell',SimpleNamespace(close=lambda: calls.append('rpc-close'))))
    monkeypatch.setattr(live,'reviewed_image',lambda state: 'sha256:'+'e'*64)
    monkeypatch.setattr(live,'bot_config',lambda path: {'bot_token':TOKEN,'user_id':123,'chat_id':123})
    return calls


@pytest.mark.parametrize('failure,code', [(None,0),('interrupt',130),('workflow',1),('cleanup',1),('prepare',2)])
def test_main_single_terminal_outcomes_and_cleanup(monkeypatch,tmp_path,capsys,failure,code):
    calls = mock_host(monkeypatch,tmp_path)
    class Test:
        def __init__(self,directory,*args):
            self.directory,self.results,self.grant_possible = directory,{},False
        def prepare(self,image):
            calls.append('prepare')
            if failure=='prepare': raise live.Blocked('missing reviewed prerequisite')
        def start_bridge(self,path): calls.append('start-bridge')
        def workflow(self):
            calls.append('workflow')
            self.grant_possible=True
            if failure=='interrupt': raise KeyboardInterrupt
            if failure=='workflow': raise live.Failed('native failed')
        def cleanup(self): calls.append('cleanup'); return failure!='cleanup'
        def journal(self): calls.append('journal')
        def remove_cli_registry(self): calls.append('registry-cleanup')
    monkeypatch.setattr(live,'LiveTest',Test)
    assert cli.main(['approval','test']) == code
    assert calls.count('cleanup')==1 and calls[-1]=='rpc-close'
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize('missing', ['provenance','tool','kvm','gateway','image','bot'])
def test_missing_prerequisite_blocks_before_test_creation(monkeypatch,tmp_path,capsys,missing):
    mock_host(monkeypatch,tmp_path)
    def fail(*a): raise live.Blocked('missing prerequisite')
    if missing=='provenance': monkeypatch.setattr(live.provenance,'identity',fail)
    elif missing=='tool': monkeypatch.setattr(live.shutil,'which',lambda name: None)
    elif missing=='kvm': monkeypatch.setattr(live.os,'access',lambda *a: False)
    else: monkeypatch.setattr(live,{'gateway':'select_gateway','image':'reviewed_image','bot':'bot_config'}[missing],fail)
    monkeypatch.setattr(live,'LiveTest',lambda *a: pytest.fail('Must block before any test VM creation'))
    assert cli.main(['approval','test'])==2
    assert 'RESULT: BLOCKED' in capsys.readouterr().out


def test_prepare_generates_valid_binding_without_full_setup(trial, monkeypatch):
    for name in ('PROFILE','REPO','STATE','CONFIG','NAME','WORKSPACE','SOURCE_MANIFEST','EDIT_SCOPE','PROVIDER_PROFILE'):
        monkeypatch.setattr(control,name,getattr(control,name))
    for module in (control.github_provider,control.workflow):
        for name in ('PROVIDER','REPOSITORY','RULE'):
            if hasattr(module,name): monkeypatch.setattr(module,name,getattr(module,name))
    original_run = live.run
    commands=[]
    def command(args,timeout=60):
        if args[0]=='git': return original_run(args,timeout)
        commands.append(args)
        assert args[:3]==['docker','image','inspect']
        return trial.image+'\n'
    monkeypatch.setattr(live,'run',command)
    monkeypatch.setattr(control,'image',lambda check: trial.image)
    monkeypatch.setattr(control.provenance,'verify',lambda revision: revision)
    # All native lifecycle/exec operations are mocked, but real profile/Git/binding validation runs.
    monkeypatch.setattr(trial,'listing',lambda: [])
    monkeypatch.setattr(trial,'isolate_cli_registry',lambda: None)
    calls=[]
    monkeypatch.setattr(trial,'cli',lambda *a,**k: calls.append(a) or '{}')
    monkeypatch.setattr(trial,'owned',lambda: sandbox(trial))
    tools={'uid':1000,'version':'codex-cli 0.162.1','binaries':['/usr/local/lib/codex/bin/codex'], 'native_sha256':'e'*64}
    monkeypatch.setattr(trial,'execute',lambda *a: json.dumps(tools))
    monkeypatch.setattr(live,'NativeAdapter',lambda *a: SimpleNamespace(context=lambda **kwargs: (SimpleNamespace(policy=SimpleNamespace(network_policies={})),{})))
    trial.prepare(trial.image)
    profile = json.loads(trial.project.read_text())
    assert profile['rules']==[] and profile['manager_revision']=='a'*40
    assert profile['root'] != str(Path.cwd())
    binding = json.loads((trial.cfg['state']/'project-binding.json').read_text())
    assert binding['sandbox']==trial.name and binding['repository']=='phuongtuan1803/openshell-manager'
    create = next(c for c in calls if c[:2]==('sandbox','create'))
    assert '--no-auto-providers' in create and create[create.index('--approval-mode')+1]=='manual'
    assert create[create.index('--from')+1]==trial.image
    assert json.loads((trial.directory/'bootstrap-policy.json').read_text())['process'] == {'run_as_user':'sandbox','run_as_group':'sandbox'}
    assert not any('policy' in c[:2] for c in calls)  # no generic policy mutation
    assert ('settings','set',trial.name,'--key','agent_policy_proposals_enabled','--value','true') in calls


def test_bridge_is_managed_child_without_terminal_or_token(trial,monkeypatch,tmp_path):
    calls=[]
    class Child:
        def poll(self): return None
    monkeypatch.setattr(live.subprocess,'Popen',lambda argv,**kwargs: calls.append((argv,kwargs)) or Child())
    monkeypatch.setattr(trial,'queue_metadata',lambda: {'native_health':json.dumps({'status':'verified'})})
    trial.start_bridge(tmp_path/'telegram.json')
    argv,options=calls[0]
    assert argv[:6]==[live.sys.executable,'-I','-m','openshell_manager','approval','serve']
    assert '--gateway' in argv and '--project' in argv and TOKEN not in ' '.join(argv)
    assert options['start_new_session'] is True and options['stdout']==subprocess.DEVNULL
    assert options['cwd'] == trial.directory


def test_fast_telegram_decision_reuses_only_confirmed_engine_evidence(trial, monkeypatch):
    monkeypatch.setattr(trial, 'probe', lambda *a, **k: {'accepted_chunk_ids':['native-a'], 'refused':False})
    def gone(*a): raise live.NativeStale('already decided')
    confirmations=[]
    trial.adapter = SimpleNamespace(get=gone, confirm=lambda value, action: confirmations.append(action))
    monkeypatch.setattr(trial, 'request', lambda chunk: {'status':'approved','action':'approve','snapshot':json.dumps(snapshot())})
    assert trial.submit('telegram_probe_a','/') == 'native-a'
    assert confirmations == ['approve']


@pytest.mark.parametrize('drift',[False,True])
def test_cli_registry_is_private_and_preserves_real_selection(trial, monkeypatch, drift):
    source=Path(__import__('os').environ['XDG_CONFIG_HOME'])/'openshell/gateways/openshell'
    source.mkdir(parents=True, mode=0o700)
    control.save(source/'metadata.json', {'name':'openshell','gateway_endpoint':'https://localhost:17670','is_remote':False,'gateway_port':17670})
    (source/'mtls').mkdir(mode=0o700)
    for name in ('ca.crt','tls.crt','tls.key'):
        private(source/'mtls'/name,create=True).write_bytes(b'synthetic-test-material')
    (source/'last_sandbox').write_text('default\noma-security-test')
    trial.rpc.identity=live.digest({'gateway':'openshell','endpoint':'https://localhost:17670',
        'ca':live.hashlib.sha256(b'synthetic-test-material').hexdigest()})
    if drift:
        trial.rpc.identity='changed-gateway'
        with pytest.raises(live.Blocked,match='identity changed'): trial.isolate_cli_registry()
        assert trial.native_config is None
        assert (source/'last_sandbox').read_text()=='default\noma-security-test'
        return
    trial.isolate_cli_registry()
    trial.journal()
    assert 'synthetic-test-material' not in (trial.directory/'result.json').read_text()
    target=trial.native_config/'openshell/gateways/openshell'
    assert (target/'mtls/tls.key').read_bytes()==b'synthetic-test-material'
    assert (target/'mtls/tls.key').stat().st_mode & 0o777 == 0o600
    assert trial.native_config.stat().st_mode & 0o777 == 0o700
    calls=[]
    monkeypatch.setattr(live,'run',lambda argv,timeout,**kw: calls.append(kw))
    trial.cli('sandbox','list')
    assert calls[0]['env']['XDG_CONFIG_HOME']==str(trial.native_config)
    (target/'last_sandbox').write_text('default\n'+trial.name)
    trial.remove_cli_registry()
    assert not trial.native_config.exists()
    assert (source/'last_sandbox').read_text()=='default\noma-security-test'
    assert (source/'mtls/tls.key').exists()


@pytest.mark.parametrize('kind', ['permanent', 'converge', 'timeout', 'unclassified', 'network'])
def test_native_checkpoints_are_durable_before_cleanup(trial, monkeypatch, kind, capsys):
    attempts = []
    def context(checkpoint):
        checkpoint('sandbox_identity', 'pass')
        attempts.append(1)
        if kind == 'unclassified': raise live.NativeUnavailable('secret-token private-provider-data')
        if kind == 'permanent':
            checkpoint('process_guard', 'fail')
            raise live.NativeVerification('process_guard')
        if kind == 'timeout' or (kind == 'converge' and len(attempts) == 1):
            checkpoint('admission_config_revision', 'wait')
            raise live.NativeVerification('admission_config_revision', True)
        checkpoint('process_guard', 'pass')
        return SimpleNamespace(policy=SimpleNamespace(network_policies={'unexpected': 1} if kind == 'network' else {})), {}
    trial.adapter = SimpleNamespace(context=context)
    def bounded(check, seconds, message):
        assert seconds == 120
        if not check() and not check(): raise live.Failed(message)
    monkeypatch.setattr(live, 'wait_for', bounded)
    if kind == 'converge':
        trial.verify_context()
        assert len(attempts) == 2
    else:
        with pytest.raises((live.Blocked, live.Failed)):
            trial.verify_context()
    report = json.loads((trial.directory/'result.json').read_text())
    checks = report['checks']['native_verification']
    assert checks['sandbox_identity'] == 'pass'
    if kind == 'timeout': assert checks['admission_config_revision'] == 'timeout'
    if kind == 'permanent':
        assert checks['process_guard'] == 'fail' and len(attempts) == 1
    if kind == 'unclassified': assert checks['native_unclassified'] == 'fail'
    assert 'secret-token' not in json.dumps(report) + capsys.readouterr().out
