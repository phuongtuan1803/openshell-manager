"""Pinned protobuf fixtures, mocked authenticated RPCs, no real native services."""
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from google.protobuf.json_format import ParseDict

from openshell_manager import control
from openshell_manager.approval import native, schema, service, telegram
from openshell_manager.approval.store import Store

FIXTURE = Path(__file__).parent / 'fixtures/approval/v0.1.2.json'


class RPC:
    identity = 'fixture-gateway'

    def __init__(self):
        self.responses = {}
        for method, payload in json.loads(FIXTURE.read_text()).items():
            if method.startswith('_'):
                continue
            self.responses[method] = ParseDict(payload, schema.METHODS[method][1]())
        self.calls, self.mutations = [], []
        self.loaded = True
        self.delayed_load = False
        self.fail = None
        self.drift_on_mutate = False
        self.reject_race = False
        self.on_call = None

    def call(self, method, **fields):
        # Exercise actual schema serialization/deserialization, not a fabricated dict API.
        request, response = schema.METHODS[method]
        request = request.FromString(request(**fields).SerializeToString())
        self.calls.append((method, request))
        if self.on_call:
            self.on_call(method)
        if self.fail == 'read' and method not in {'ApproveAllDraftChunks', 'RejectDraftChunk'}:
            raise native.NativeUnavailable('unavailable')
        config = self.responses['GetSandboxConfig']
        chunk = self.responses['GetDraftPolicy'].chunks[0]
        if method in {'ApproveAllDraftChunks', 'RejectDraftChunk'}:
            self.mutations.append((method, request))
            if self.fail == 'before':
                raise native.NativeUnavailable('RPC refused or uncertain')
            if method == 'ApproveAllDraftChunks':
                assert len(request.approvals) == 1
                assert request.approvals[0].chunk_id == chunk.id
                assert request.approvals[0].review_token == chunk.review_token
                assert not request.include_security_flagged
                assert str(uuid.UUID(request.request_id)) == request.request_id
                if self.drift_on_mutate or chunk.status != 'pending':
                    return response(chunks_approved=0, chunks_skipped=1)
                chunk.status = 'approved'
                config.policy.CopyFrom(chunk.candidate_effective_policy)
                config.policy_hash = chunk.candidate_effective_policy_hash
                config.version = 2
                config.config_revision += 1
                if self.delayed_load: self.loaded = False
                result = response(policy_version=2, policy_hash='d' * 64, chunks_approved=1)
            else:
                assert request.chunk_id == chunk.id and request.reason
                chunk.status = 'rejected'
                chunk.rejection_reason = request.reason
                if self.reject_race:
                    chunk.proposed_rule.endpoints[0].rules[0].allow.path = '/different/**'
                result = response()
            if self.fail == 'after':
                raise native.NativeUnavailable('lost RPC response after mutation')
            return result
        if method == 'GetSandboxPolicyStatus':
            return response(revision={'version':config.version,'policy_hash':'d'*64,'status':2 if self.loaded else 1},
                            active_version=config.version if self.loaded else 1)
        result = response()
        result.CopyFrom(self.responses[method])
        if method == 'GetSandbox':
            admission = result.sandbox.status.configuration_admission
            admission.policy_version = config.version
            admission.policy_hash = config.policy_hash
            admission.config_revision = config.config_revision
            admission.provider_env_revision = config.provider_env_revision
            if not self.loaded: admission.state = 1
        if method == 'GetDraftPolicy' and request.status_filter:
            selected = [c for c in result.chunks if c.status == request.status_filter]
            result.ClearField('chunks')
            result.chunks.extend(selected)
        return result


class Bot:
    def __init__(self):
        self.notices, self.answers, self.statuses = [], [], []
        self.fail_send = False
        self.fail_status = False

    def notify(self, chat, sandbox, row, tokens):
        self.notices.append((row['id'], tokens))
        if self.fail_send:
            raise telegram.TelegramError('outage')
        return 100 + row['id']

    def status(self, chat, row, text):
        if self.fail_status:
            raise telegram.TelegramError('status outage')
        self.statuses.append(text)

    def call(self, method, **kwargs):
        self.answers.append(kwargs.get('text'))
        return []


@pytest.fixture
def setup(tmp_path):
    now = [1000.0]
    root = tmp_path / 'state'
    root.mkdir(mode=0o700)
    manifest = tmp_path / 'scope.txt'
    manifest.write_text('fixture.py\n')
    cfg = {'id':'fixture', 'sandbox':'fixture-dev', 'state':root, 'manager_revision':'a'*40,
           'source_manifest':manifest, 'edit_scope':manifest, 'image_id':'sha256:'+'e'*64,
           'codex_version':'codex-cli 0.162.1'}
    config_hash = control.digest(json.dumps(cfg, sort_keys=True, default=str).encode() + manifest.read_bytes()*2)
    (root / 'state.json').write_text(json.dumps({'sandbox_id':'sandbox-uuid-0001','config_hash':config_hash,
        'tools': {'uid':1000,'version':'codex-cli 0.162.1','binaries':['/usr/local/lib/codex/bin/codex'],
                  'native_sha256':'e'*64},
        'policy': {'filesystem_policy':{'read_only':['/usr'],'read_write':['/sandbox','/tmp'],'include_workdir':True},
                   'landlock':{'compatibility':'hard_requirement'}}}))
    (root / 'state.json').chmod(0o600)
    rpc = RPC()
    adapter = native.NativeAdapter(cfg, rpc, clock=lambda: now[0], sleep=lambda seconds: None)
    store = Store(root / 'approval', 'native-fixture', clock=lambda: now[0])
    bot = Bot()
    bridge = service.Bridge(cfg, {'user_id':34,'chat_id':12}, store, bot, adapter)
    yield cfg, rpc, adapter, store, bot, bridge, now
    store.close()


def callback(store, bot, action='approve', user=34, chat=12, number=0):
    request, tokens = bot.notices[-1]
    return {'update_id':number, 'callback_query':{'id':'callback','data':tokens[action], 'from':{'id':user},
            'message':{'message_id':100+request,'chat':{'id':chat}}}}


def test_exact_descriptor_and_rpc_contract():
    assert set(schema.METHODS) == {'Health','GetSandbox','GetSandboxConfig','GetDraftPolicy',
                                 'GetSandboxPolicyStatus','ApproveAllDraftChunks','RejectDraftChunk'}
    service_descriptor = next(s for f in schema.DESCRIPTORS.file for s in f.service)
    assert service_descriptor.name == 'OpenShell'
    serialized = schema.DESCRIPTORS.SerializeToString(deterministic=True)
    assert hashlib.sha256(serialized).hexdigest() == schema.SCHEMA_SHA256
    request = schema.METHODS['ApproveAllDraftChunks'][0].DESCRIPTOR
    assert {f.name:f.number for f in request.fields} == {
        'workspace_scope':4,'include_security_flagged':2,'approvals':3,'sandbox':1,'request_id':5}
    proposal = schema.message('openshell.v1.PolicyChunk').DESCRIPTOR
    assert proposal.fields_by_name['review_token'].number == 20
    assert proposal.fields_by_name['proposed_rule'].number == 4
    assert proposal.fields_by_name['created_time'].number == 109
    assert len(schema.SOURCE_HASHES) == 4


def test_automatic_discovery_identity_and_repeated_scope(setup):
    cfg, rpc, adapter, store, bot, bridge, _ = setup
    assert not (cfg['state'] / 'inbox.json').exists()
    bridge.scan()
    bridge.scan()
    assert len(bot.notices) == 1
    assert {'approve','reject','details'} == set(bot.notices[0][1])
    assert store.rows()[0]['expires'] == 1900
    assert any(method == 'GetDraftPolicy' for method, _ in rpc.calls)
    # Agent-authored repeat after rejection: same endpoint, new server-assigned chunk ID.
    bridge.update(callback(store, bot, 'reject'))
    assert store.rows()[0]['status'] == 'rejected'
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    chunk.id = 'chunk-0002'; chunk.status = 'pending'; chunk.rejection_reason = ''
    chunk.created_time.seconds = 1001
    bridge.scan()
    assert len(bot.notices) == 2
    assert len({row['native_identity'] for row in store.rows()}) == 2


@pytest.mark.parametrize('action', ['approve','reject'])
def test_full_native_decision_confirmed_and_replay_safe(setup, action):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    event = callback(store, bot, action)
    bridge.update(event)
    bridge.update(event | {'update_id':1})
    assert len(rpc.mutations) == 1
    assert store.rows()[0]['status'] == ('approved' if action == 'approve' else 'rejected')
    assert store.db.execute('SELECT actor FROM audit WHERE event=?', ('native_'+store.rows()[0]['status'],)).fetchone()[0] == 34
    assert bot.statuses and ('agent may retry' in bot.statuses[0] if action == 'approve' else 'narrower' in bot.statuses[0])
    assert 'native_result' in store.rows()[0].keys()


@pytest.mark.parametrize('user,chat', [(35,12),(34,13),('34',12),(34,'12'),(True,12)])
def test_unauthorized_never_touches_native_mutation(setup, user, chat):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    before = len(rpc.calls)
    bridge.update(callback(store, bot, user=user, chat=chat))
    assert rpc.mutations == [] and len(rpc.calls) == before
    assert store.rows()[0]['status'] == 'notified'


def test_expiry_fresh_generation_does_not_reuse_callback(setup):
    _, rpc, _, store, bot, bridge, now = setup
    bridge.scan()
    old = callback(store, bot)
    now[0] = 1900
    bridge.update(old)
    assert not rpc.mutations and 'expired' in bot.answers[-1]
    bridge.scan()
    assert len(bot.notices) == 2
    assert store.rows()[0]['status'] == 'expired'
    assert store.rows()[1]['generation'] == 2
    bridge.update(old | {'update_id':1})
    assert not rpc.mutations
    bridge.update(callback(store, bot, number=2))
    assert len(rpc.mutations) == 1


@pytest.mark.parametrize('changed', ['sandbox','instance','provider','policy','risk','rule','token','project','created'])
def test_full_identity_and_policy_drift_refused(setup, changed):
    cfg, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    config = rpc.responses['GetSandboxConfig']
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    if changed == 'sandbox': rpc.responses['GetSandbox'].sandbox.metadata.id = 'another'
    if changed == 'instance': config.configuration_instance_id = 'another'
    if changed == 'provider': config.provider_env_revision += 1
    if changed == 'policy': config.policy_hash = 'f'*64
    if changed == 'risk': chunk.validation_result = 'prover: credential_reach_expansion'
    if changed == 'rule': chunk.proposed_rule.endpoints[0].rules[0].allow.path = '/other/**'
    if changed == 'token': chunk.review_token = 'f'*64
    if changed == 'project': cfg['manager_revision'] = 'f'*40
    if changed == 'created': chunk.created_time.seconds += 1
    bridge.update(callback(store, bot))
    assert rpc.mutations == [] and store.rows()[0]['status'] != 'approved'


def test_native_precondition_race_never_reports_success(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    rpc.drift_on_mutate = True
    bridge.update(callback(store, bot))
    assert store.rows()[0]['status'] == 'stale'
    assert not bot.statuses
    assert 'stale' in bot.answers[-1]
    bridge.scan()
    assert len(rpc.mutations) == 1 and len(bot.notices) == 2


@pytest.mark.parametrize('fault', ['review_token','proposed_rule','created_time','current_effective_policy','candidate_effective_policy'])
def test_incomplete_native_contract_cannot_grant(setup, fault):
    _, rpc, adapter, store, bot, bridge, _ = setup
    rpc.responses['GetDraftPolicy'].chunks[0].ClearField(fault)
    with pytest.raises(native.NativeUnavailable): adapter.discover()
    bridge.scan()
    assert not bot.notices and not rpc.mutations


@pytest.mark.parametrize('fault', ['auto','version','global','admission','unknown'])
def test_wrong_native_version_mode_or_unknown_data_fail_closed(setup, fault):
    _, rpc, adapter, _, _, _, _ = setup
    if fault == 'auto': rpc.responses['GetSandboxConfig'].settings['proposal_approval_mode'].value.string_value = 'auto'
    if fault == 'version': rpc.responses['Health'].version = '0.1.3'
    if fault == 'global': rpc.responses['GetSandboxConfig'].policy_source = 2
    if fault == 'admission': rpc.responses['GetSandboxConfig'].configuration_admitted = False
    if fault == 'unknown':
        value = rpc.responses['GetSandboxConfig']
        value.ParseFromString(value.SerializeToString() + b'\xf8\x07\x01')
    with pytest.raises(native.NativeUnavailable): adapter.discover()
    assert rpc.mutations == []


@pytest.mark.parametrize('unsafe', ['filesystem','process','landlock','existing_rule','github','credential','broad','binary','security','unavailable_prover'])
def test_scope_or_risk_outside_p1_cannot_approve(setup, unsafe):
    _, rpc, adapter, store, bot, bridge, _ = setup
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    if unsafe == 'filesystem': chunk.candidate_effective_policy.filesystem.read_write.append('/etc')
    if unsafe == 'process': chunk.candidate_effective_policy.process.run_as_user = 'root'
    if unsafe == 'landlock': chunk.candidate_effective_policy.landlock.compatibility = 'best_effort'
    if unsafe == 'existing_rule': chunk.candidate_effective_policy.network_policies['unreviewed'].name = 'unreviewed'
    if unsafe == 'github': chunk.proposed_rule.endpoints[0].host = 'api.github.com'
    if unsafe == 'credential': chunk.proposed_rule.endpoints[0].credential_binding.provider = 'secret-provider'
    if unsafe == 'broad': chunk.proposed_rule.endpoints[0].rules[0].allow.path = ''
    if unsafe == 'binary': chunk.proposed_rule.binaries[0].path = '/bin/sh'
    if unsafe == 'security': chunk.security_notes = 'wildcard host'
    if unsafe == 'unavailable_prover': chunk.validation_result = 'validation unavailable'
    assert adapter.discover()[0]['eligible'] is False
    bridge.scan()
    assert set(bot.notices[0][1]) == {'reject','details'}
    assert not rpc.mutations


def test_atomic_concurrent_callbacks_single_mutation(setup):
    _, rpc, adapter, store, bot, bridge, _ = setup
    bridge.scan()
    event = callback(store, bot)
    directory = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    def apply(_):
        other = Store(directory, 'native-fixture', clock=store.clock)
        worker = service.Bridge(bridge.cfg, bridge.auth, other, bot, adapter)
        try:
            return worker.native_callback(event['callback_query'], event['callback_query']['message'], 34)
        finally: other.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(apply, range(4)))
    assert len(rpc.mutations) == 1 and store.rows()[0]['status'] == 'approved'


@pytest.mark.parametrize('phase', ['before','after'])
def test_uncertain_mutation_restart_never_repeats(setup, phase):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    rpc.fail = phase
    bridge.update(callback(store, bot))
    assert store.rows()[0]['status'] == 'needs_review'
    assert not bot.statuses
    rpc.fail = None
    store.recover(native=True)
    bridge.scan()
    assert len(rpc.mutations) == 1
    assert store.rows()[0]['status'] == ('approved' if phase == 'after' else 'needs_review')


def test_native_load_not_confirmed_then_read_only_recovery(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan(); rpc.delayed_load = True
    bridge.update(callback(store, bot))
    assert store.rows()[0]['status'] == 'needs_review' and not bot.statuses
    rpc.loaded = True
    bridge.scan()
    assert store.rows()[0]['status'] == 'approved' and len(rpc.mutations) == 1
    assert bot.statuses


def test_status_outage_retries_status_only(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan(); bot.fail_status = True
    with pytest.raises(telegram.TelegramError): bridge.update(callback(store, bot))
    assert store.rows()[0]['status'] == 'approved' and store.rows()[0]['status_sent'] == 0
    bot.fail_status = False
    bridge.scan()
    assert store.rows()[0]['status_sent'] == 1 and len(rpc.mutations) == 1


@pytest.mark.parametrize('action', ['approve', 'reject'])
def test_acknowledgement_failure_preserves_decision_and_advances_cursor(setup, action):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    update = callback(store, bot, action)
    def expired_query(method, **kwargs):
        assert method == 'answerCallbackQuery'
        raise telegram.TelegramError('expired query')
    bot.call = expired_query
    with pytest.raises(telegram.TelegramError):
        bridge.update(update)
    assert store.rows()[0]['status'] == ('approved' if action == 'approve' else 'rejected')
    assert store.offset == update['update_id'] + 1
    bridge.update(update)
    bridge.update({'update_id': store.offset})
    assert store.offset == update['update_id'] + 2
    assert len(rpc.mutations) == 1


def test_reject_race_cannot_claim_exact_success(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan(); rpc.reject_race = True
    bridge.update(callback(store, bot, 'reject'))
    assert store.rows()[0]['status'] == 'needs_review'
    assert not bot.statuses


def test_telegram_native_render_and_redaction(setup):
    _, rpc, adapter, store, _, bridge, _ = setup
    bridge.scan()
    row = store.rows()[0]
    calls = []
    bot = telegram.Telegram('TOKEN')
    bot.call = lambda method, **fields: calls.append(fields) or {'message_id':101}
    bot.notify(12, 'fixture-dev', row, {'approve':'a'*32,'reject':'r'*32,'details':'d'*32})
    text = calls[0]['text']
    assert all(value in text for value in ('fixture-dev','fixture','chunk-0001','/usr/bin/curl','/docs/**','GET','docs.example.test','443'))
    assert [b['text'] for b in calls[0]['reply_markup']['inline_keyboard'][0]] == ['Approve rule','Reject','Details']
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    chunk.rationale = 'PRIVATE-REASON'
    chunk.security_notes = 'Bearer PRIVATE-TOKEN'
    snapshot = adapter.discover()[0]
    assert 'PRIVATE-REASON' not in str(snapshot) and 'PRIVATE-TOKEN' not in str(snapshot)
    assert not snapshot['eligible']


def test_details_then_approve_remains_possible(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    bridge.update(callback(store, bot, 'details'))
    assert not rpc.mutations and store.rows()[0]['status'] == 'notified'
    bridge.update(callback(store, bot, 'approve', number=1))
    assert store.rows()[0]['status'] == 'approved'


def test_crash_after_atomic_claim_before_rpc_holds_for_review(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    event = callback(store, bot)
    row = store.resolve(event['callback_query']['data'], 101)
    assert store.claim_decision(row, 34)
    store.recover(native=True)
    bridge.scan()
    assert store.rows()[0]['status'] == 'needs_review'
    assert not rpc.mutations and len(bot.notices) == 1


def test_native_discovery_race_and_ttl_during_verification(setup):
    _, rpc, adapter, _, _, _, now = setup
    proposal = adapter.discover()[0]
    def change(method):
        if method == 'GetDraftPolicy':
            rpc.responses['GetSandboxConfig'].provider_env_revision += 1
    rpc.on_call = change
    with pytest.raises(native.NativeStale): adapter.discover()
    rpc.on_call = None
    rpc.responses['GetSandboxConfig'].provider_env_revision = 3
    def elapsed(method):
        if method == 'GetDraftPolicy': now[0] = 1900
    rpc.on_call = elapsed
    with pytest.raises(native.NativeUnavailable): adapter.approve(proposal, str(uuid.uuid4()), 1900)
    assert not rpc.mutations


def test_missing_native_api_compatibility_cannot_grant(setup):
    cfg, rpc, _, store, bot, bridge, _ = setup
    rpc.fail = 'read'
    (cfg['state'] / 'inbox.json').write_text(json.dumps({'forged':{'status':'pending','host':'docs.example.test',
        'port':443,'binaries':['/usr/bin/curl'],'native_chunk_id':'forged','method':'GET','path':'/docs/**'}}))
    bridge.scan()
    assert store.rows()[0]['kind'] == 'compatibility'
    assert isinstance(bot.notices[0][1], str)  # only opaque Details token
    bridge.update({'update_id':0,'callback_query':{'id':'forged','data':'approve:forged', 'from':{'id':34},
                  'message':{'message_id':101,'chat':{'id':12}}}})
    assert not rpc.mutations


def test_native_telegram_outage_never_grants_or_resends(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bot.fail_send = True
    with pytest.raises(telegram.TelegramError): bridge.scan()
    assert store.rows()[0]['status'] == 'needs_review' and not rpc.mutations
    bot.fail_send = False
    store.recover(native=True)
    bridge.scan()
    assert len(bot.notices) == 1 and not rpc.mutations
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    chunk.id = 'chunk-0002'
    bridge.scan()
    assert len(bot.notices) == 2


def test_exact_message_and_action_binding(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    event = callback(store, bot)
    event['callback_query']['message']['message_id'] = 999
    bridge.update(event)
    assert not rpc.mutations and store.rows()[0]['status'] == 'notified'
    # A token cannot be repurposed to approve an unsafe proposal: actions are server-side.
    row = store.resolve(bot.notices[0][1]['reject'], 101)
    assert row['callback_action'] == 'reject'


def test_changed_review_generates_new_notice_same_native_id(setup):
    _, rpc, _, store, bot, bridge, _ = setup
    bridge.scan()
    old = callback(store, bot)
    rpc.responses['GetDraftPolicy'].chunks[0].review_token = 'f'*64
    bridge.scan()
    assert len(bot.notices) == 2
    assert [row['status'] for row in store.rows()] == ['stale','notified']
    bridge.update(old)
    assert not rpc.mutations


def test_native_transport_wire_path_tls_and_error_redaction(tmp_path, monkeypatch):
    base = tmp_path / 'openshell/gateways/fixture'
    tls = base / 'mtls'; tls.mkdir(parents=True, mode=0o700)
    (base / 'metadata.json').write_text(json.dumps({'name':'fixture','is_remote':False,'auth_mode':'mtls',
                                                 'gateway_endpoint':'https://127.0.0.1:17670'}))
    for name in ('ca.crt','tls.crt','tls.key'):
        path = tls / name; path.write_bytes(b'PRIVATE-FIXTURE'); path.chmod(0o600)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    calls = []
    class Channel:
        def unary_unary(self, path, **options):
            def invoke(request, **kwargs):
                calls.append((path, options['request_serializer'](request), kwargs))
                data = schema.METHODS['Health'][1](status=1,version='0.1.2').SerializeToString()
                return options['response_deserializer'](data)
            return invoke
        def close(self): pass
    monkeypatch.setattr(native.grpc, 'ssl_channel_credentials', lambda *args: 'tls-credentials')
    monkeypatch.setattr(native.grpc, 'secure_channel', lambda target, credentials, options:
        (calls.append((target,credentials,options)) or Channel()))
    rpc = native.GatewayRPC('fixture')
    assert rpc.call('Health').version == '0.1.2'
    assert calls[0][0] == '127.0.0.1:17670'
    assert ('grpc.enable_retries',0) in calls[0][2]
    assert calls[1][0] == '/openshell.v1.OpenShell/Health'
    assert calls[1][2] == {'timeout':10,'wait_for_ready':False}
    with pytest.raises(native.NativeUnavailable): rpc.call('UpdateConfig')
    with pytest.raises(native.NativeUnavailable): rpc.call('ApproveAllDraftChunks', approvals=[], include_security_flagged=False)
    with pytest.raises(native.NativeUnavailable): rpc.call('ApproveAllDraftChunks', approvals=[{'chunk_id':'a','review_token':'x'},{'chunk_id':'b','review_token':'y'}], include_security_flagged=False)
    def fail(*args, **kwargs): raise OSError('PRIVATE-FIXTURE key and token')
    rpc.channel.unary_unary = fail
    with pytest.raises(native.NativeUnavailable) as error: rpc.call('Health')
    assert 'PRIVATE-FIXTURE' not in str(error.value) and error.value.__suppress_context__
    rpc.close()


@pytest.mark.parametrize('endpoint,auth,remote', [('http://127.0.0.1:17670','plaintext',False),
    ('https://other.test:443','mtls',True), ('https://token@127.0.0.1:17670','mtls',False),
    ('https://127.0.0.1:17670?token=secret','mtls',False)])
def test_native_transport_unsupported_hosts_auth_fail_closed(tmp_path, monkeypatch, endpoint, auth, remote):
    base = tmp_path / 'openshell/gateways/fixture'; base.mkdir(parents=True)
    (base / 'metadata.json').write_text(json.dumps({'name':'fixture','is_remote':remote,'auth_mode':auth,
                                                 'gateway_endpoint':endpoint}))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    with pytest.raises(native.NativeUnavailable): native.GatewayRPC('fixture')


def test_additive_sqlite_migration_preserves_fallback_audit(tmp_path):
    import sqlite3
    directory = tmp_path / 'approval'; directory.mkdir(mode=0o700)
    path = directory / 'queue.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO metadata VALUES('binding','old-binding'); INSERT INTO metadata VALUES('offset','7');
        CREATE TABLE requests(id INTEGER PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,scope TEXT NOT NULL,
                              expires REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',message INTEGER);
        INSERT INTO requests VALUES(1,'old','{}',1900,'notified',101);
        CREATE TABLE callbacks(hash TEXT PRIMARY KEY,request INTEGER NOT NULL,action TEXT NOT NULL,processed REAL);
        CREATE TABLE audit(id INTEGER PRIMARY KEY,request INTEGER,event TEXT NOT NULL,at REAL NOT NULL);
        INSERT INTO audit VALUES(1,1,'old-audit',1000);''')
    path.chmod(0o600)
    store = Store(directory, 'old-binding', clock=lambda:1000)
    try:
        assert store.rows()[0]['kind'] == 'compatibility' and store.offset == 7
        assert store.db.execute('SELECT event FROM audit').fetchone()[0] == 'old-audit'
    finally: store.close()


@pytest.mark.parametrize('changed', ['phase','image','filesystem','landlock','process','middleware','unloaded','runtime_phase','admission'])
def test_existing_manager_admission_guards_preserved(setup, changed):
    _, rpc, adapter, _, _, _, _ = setup
    config = rpc.responses['GetSandboxConfig']
    if changed == 'phase': rpc.responses['GetSandbox'].sandbox.metadata.labels['manager.phase'] = 'other'
    if changed == 'image': rpc.responses['GetSandbox'].sandbox.metadata.labels['manager.image'] = 'other'
    if changed == 'filesystem': config.policy.filesystem.read_write.append('/host')
    if changed == 'landlock': config.policy.landlock.compatibility = 'best_effort'
    if changed == 'process': config.policy.process.run_as_user = 'root'
    if changed == 'middleware': config.policy.network_middlewares['unknown'].middleware = 'unknown'
    if changed == 'unloaded': rpc.loaded = False
    if changed == 'runtime_phase': rpc.responses['GetSandbox'].sandbox.status.phase = 7
    if changed == 'admission': rpc.responses['GetSandbox'].sandbox.status.configuration_admission.state = 1
    with pytest.raises(native.NativeUnavailable): adapter.discover()
    assert not rpc.mutations


@pytest.mark.parametrize('credential_host', ['docs.example.test','*.example.test',''])
def test_clean_prover_cannot_authorize_new_credential_path(setup, credential_host):
    _, rpc, adapter, _, bot, bridge, _ = setup
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    rule = schema.message('openshell.sandbox.v1.NetworkPolicyRule', name='_provider_secret',
        binaries=[{'path':'/usr/bin/curl'}], endpoints=[{'host':credential_host,'port':443,
        'protocol':'rest','enforcement':1,'provider_credentialed':True,
        'rules':[{'allow':{'method':'GET','path':'/old/**'}}]}])
    chunk.current_effective_policy.network_policies['_provider_secret'].CopyFrom(rule)
    chunk.candidate_effective_policy.network_policies['_provider_secret'].CopyFrom(rule)
    rpc.responses['GetSandboxConfig'].policy.CopyFrom(chunk.current_effective_policy)
    # A new GET path can be credential authority expansion even with a clean delta summary.
    assert chunk.validation_result == 'prover: no new findings'
    assert adapter.discover()[0]['eligible'] is False
    bridge.scan()
    assert 'approve' not in bot.notices[0][1] and not rpc.mutations


def test_existing_discovered_codex_binary_binding_and_drift(setup):
    cfg, rpc, adapter, _, bot, bridge, _ = setup
    chunk = rpc.responses['GetDraftPolicy'].chunks[0]
    path = '/usr/local/lib/codex/bin/codex'
    chunk.binary = path
    chunk.proposed_rule.binaries[0].path = path
    chunk.candidate_effective_policy.network_policies['docs_get'].CopyFrom(chunk.proposed_rule)
    assert adapter.discover()[0]['eligible'] is True
    bridge.scan()
    state_path = cfg['state'] / 'state.json'
    state = json.loads(state_path.read_text())
    state['tools']['native_sha256'] = 'f'*64
    state_path.write_text(json.dumps(state))
    bridge.update(callback(bridge.store, bot))
    assert not rpc.mutations


@pytest.mark.parametrize('field,code,retryable', [
    ('process', 'process_guard', False), ('landlock', 'landlock_guard', False),
    ('filesystem', 'filesystem_guard', False), ('mode', 'manual_approval_mode', False),
    ('provider', 'provider_revision_attachment', False), ('config', 'config_revision', False),
    ('load', 'loaded_state', True), ('rejected', 'native_admission', False),
])
def test_context_reports_exact_sanitized_invariant(setup, field, code, retryable):
    cfg, rpc, adapter, *_ = setup
    config = rpc.responses['GetSandboxConfig']
    if field in {'process', 'landlock', 'filesystem'}: config.policy.ClearField(field)
    if field == 'mode': config.settings['proposal_approval_mode'].value.string_value = 'auto'
    if field == 'provider': config.provider_attachment_epoch = ''
    if field == 'config': config.config_revision = 0
    if field == 'load':
        original = rpc.call
        def call(method, **fields):
            result = original(method, **fields)
            if method == 'GetSandboxPolicyStatus': result.revision.status = 1
            return result
        rpc.call = call
    if field == 'rejected':
        rpc.responses['GetSandbox'].sandbox.status.configuration_admission.state = 3
        rpc.responses['GetSandbox'].sandbox.status.configuration_admission.error = 'PRIVATE-PROVIDER-TOKEN'
    checkpoints = []
    with pytest.raises(native.NativeVerification) as error:
        adapter.context(checkpoint=lambda *args: checkpoints.append(args))
    assert error.value.code == code and error.value.retryable == retryable
    assert checkpoints[-1] == (code, 'wait' if retryable else 'fail')
    assert 'PRIVATE-PROVIDER-TOKEN' not in str(error.value)


def test_v012_omitted_process_is_not_an_explicit_sandbox_identity(setup):
    # parse_sandbox_policy/to_proto raw.process.map and canonicalization preserve
    # absence. The previous test bootstrap had no process section, unlike fixtures.
    cfg, rpc, adapter, *_ = setup
    policy = rpc.responses['GetSandboxConfig'].policy
    policy.ClearField('process')
    with pytest.raises(native.NativeVerification, match='process_guard'):
        adapter.context()
    policy.process.run_as_user = 'sandbox'
    policy.process.run_as_group = 'sandbox'
    adapter.context()  # Explicit creation policy satisfies the unchanged guard.
