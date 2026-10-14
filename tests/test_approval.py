"""Offline security and integration evidence for the manual-review capability gate."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openshell_manager import cli
from openshell_manager.approval import compatibility, native, service, telegram
from openshell_manager.approval.store import Store, private


@pytest.fixture
def queue(tmp_path):
    now = [1000.0]
    store = Store(tmp_path / 'approval', 'fixture-binding', clock=lambda: now[0])
    yield store, now
    store.close()


def notice(store):
    store.enqueue('f' * 64, json.dumps({'host': 'docs.example.test', 'port': 443, 'binary': '/usr/bin/curl'}))
    row = store.rows()[0]
    token = store.claim_notice(row['id'])
    store.sent(row['id'], 17)
    return row, token


@pytest.mark.parametrize('operation,args', [('discover', ()), ('get', ('fixture-dev', 'chunk')),
                                            ('approve', ('fixture-dev', 'chunk')), ('reject', ('fixture-dev', 'chunk'))])
def test_native_gate_has_no_subprocess(operation, args):
    with pytest.raises(native.NativeUnavailable):
        getattr(native.NativeAdapter(), operation)(*args)


def test_callback_ttl_replay_message_identity(queue):
    store, now = queue
    row, token = notice(store)
    assert row['expires'] == 1900
    assert token not in str(store.db.execute('SELECT * FROM callbacks').fetchall())
    assert store.callback(token, 18) == 'Invalid callback'
    assert store.callback('Approve rule', 17) == 'Invalid callback'
    assert 'No permission changed' in store.callback(token, 17)
    assert 'already processed' in store.callback(token, 17)
    now[0] = 1900
    assert 'expired' in store.callback(token, 17)
    assert store.rows()[0]['status'] == 'expired'


def test_duplicates_and_concurrent_claims(queue):
    store, _ = queue
    store.enqueue('f', '{}')
    store.enqueue('f', '{}')
    assert len(store.rows()) == 1
    directory = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    def claim(_):
        other = Store(directory, 'fixture-binding', clock=lambda: 1000)
        try:
            return other.claim_notice(1)
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(claim, range(4)))
    assert sum(value is not None for value in tokens) == 1


def test_concurrent_callbacks(queue):
    store, _ = queue
    _, token = notice(store)
    directory = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    def callback(_):
        other = Store(directory, 'fixture-binding', clock=lambda: 1000)
        try:
            return other.callback(token, 17)
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(callback, range(4)))
    assert sum('No permission changed' in value for value in replies) == 1
    assert sum('already processed' in value for value in replies) == 3


@pytest.mark.parametrize('state', ['sending', 'applying'])
def test_restart_uncertain_never_retries(queue, state):
    store, _ = queue
    row, _ = notice(store)
    store.db.execute('UPDATE requests SET status=?', (state,))
    directory = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    other = Store(directory, 'fixture-binding', clock=lambda: 1000)
    try:
        other.recover()
        assert other.rows()[0]['status'] == 'needs_review'
        assert other.claim_notice(row['id']) is None
        assert other.db.execute('SELECT event FROM audit').fetchone()[0] == 'uncertain_after_restart_no_retry'
    finally:
        other.close()


def test_binding_drift_refused(queue):
    store, _ = queue
    directory = Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent
    with pytest.raises(ValueError, match='binding changed'):
        Store(directory, 'changed-project-or-bot')


def test_private_paths_symlinks_hardlinks_permissions(tmp_path):
    directory = tmp_path / 'approval'
    store = Store(directory, 'binding')
    store.enqueue('f', '{}')
    store.close()
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / 'queue.sqlite3').stat().st_mode & 0o777 == 0o600
    target = tmp_path / 'secret'
    target.write_text('{}')
    target.chmod(0o600)
    alias = tmp_path / 'alias'
    alias.symlink_to(target)
    with pytest.raises(ValueError, match='Symlink'):
        private(alias)
    alias.unlink()
    os.link(target, alias)
    with pytest.raises(ValueError, match='Hard-linked'):
        private(alias)
    target.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        private(target)


class Bot:
    def __init__(self):
        self.notices, self.answers = [], []
        self.fail = False

    def notify(self, *args):
        self.notices.append(args)
        if self.fail:
            raise telegram.TelegramError('offline')
        return 17

    def call(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        if self.fail:
            raise telegram.TelegramError('offline')
        return []


def bridge(queue, tmp_path):
    store, _ = queue
    cfg = {'state': tmp_path, 'sandbox': 'fixture-dev'}
    bot = Bot()
    return service.Bridge(cfg, {'chat_id': 12, 'user_id': 34}, store, bot), bot


def inbox(tmp_path, **fields):
    value = {'status': 'pending', 'host': 'docs.example.test', 'port': 443, 'binaries': ['/usr/bin/curl'],
             'reason': 'PRIVATE-CONTENT', 'native_chunk_id': 'FORGED', 'method': 'POST', 'path': '/PRIVATE-CONTENT'}
    value.update(fields)
    path = tmp_path / 'inbox.json'
    path.write_text(json.dumps({'forged': value}))
    return path


def update(token, user=34, chat=12, message=17, number=0):
    return {'update_id': number, 'callback_query': {'id': 'callback', 'data': token,
            'from': {'id': user}, 'message': {'message_id': message, 'chat': {'id': chat}}}}


@pytest.mark.parametrize('user,chat', [(35, 12), (34, 13), ('34', 12), (34, '12'), (True, 12)])
def test_unauthorized_callbacks_do_not_consume_token(queue, tmp_path, user, chat):
    store, _ = queue
    _, token = notice(store)
    worker, bot = bridge(queue, tmp_path)
    worker.update(update(token, user, chat))
    assert bot.answers[0][1]['text'].startswith('Unauthorized')
    assert store.db.execute('SELECT processed FROM callbacks').fetchone()[0] is None
    assert store.rows()[0]['status'] == 'notified'


def test_delivery_once_sanitized_and_inbox_unchanged(queue, tmp_path):
    path = inbox(tmp_path)
    before = path.read_bytes()
    worker, bot = bridge(queue, tmp_path)
    worker.scan()
    worker.scan()
    assert len(bot.notices) == 1
    row = bot.notices[0][2]
    assert 'PRIVATE-CONTENT' not in row['scope'] and 'FORGED' not in row['scope']
    assert path.read_bytes() == before
    assert not (tmp_path / 'policy.yaml').exists()
    path.write_text('{}')
    worker.scan()
    assert queue[0].rows()[0]['status'] == 'stale'
    assert 'stale' in queue[0].callback(bot.notices[0][3], 17)


@pytest.mark.parametrize('fields', [{'host': '<script>secret</script>'}, {'binaries': ['/bin/sh']},
                                    {'port': True}, {'host': 'example.test?token=SECRET'},
                                    {'binaries': ['token', '/usr/bin/curl']}])
def test_forged_inbox_invalid_fields_ignored(tmp_path, fields):
    assert compatibility.notifications(inbox(tmp_path, **fields)) == []


def test_telegram_outage_and_restart_review_only(queue, tmp_path):
    inbox(tmp_path)
    worker, bot = bridge(queue, tmp_path)
    bot.fail = True
    with pytest.raises(telegram.TelegramError):
        worker.scan()
    assert queue[0].rows()[0]['status'] == 'needs_review'
    queue[0].recover()
    bot.fail = False
    worker.scan()
    assert len(bot.notices) == 1
    # Newly observed endpoints can still be notified after outage recovery.
    inbox(tmp_path, host='other.example.test')
    worker.scan()
    assert len(bot.notices) == 2


def test_poll_cursor_response_failure_replay(queue, tmp_path):
    store, _ = queue
    _, token = notice(store)
    worker, bot = bridge(queue, tmp_path)
    bot.fail = True
    with pytest.raises(telegram.TelegramError):
        worker.update(update(token))
    assert store.offset == 0
    bot.fail = False
    worker.update(update(token))
    assert 'already processed' in bot.answers[-1][1]['text']
    assert store.offset == 1
    count = len(bot.answers)
    worker.update(update(token))
    assert len(bot.answers) == count


def test_bot_payload_and_error_redaction(queue):
    store, _ = queue
    row, token = notice(store)
    calls = []
    bot = telegram.Telegram('PRIVATE-TOKEN')
    def call(method, **kwargs):
        calls.append((method, kwargs))
        return {'message_id': 18}
    bot.call = call
    assert bot.notify(12, 'fixture-dev', row, token) == 18
    payload = calls[0][1]
    assert 'parse_mode' not in payload
    assert payload['reply_markup']['inline_keyboard'][0][0]['text'] == 'Details'
    assert 'Approve' not in str(payload) and 'PRIVATE-TOKEN' not in str(payload)
    def failure(*args, **kwargs):
        raise OSError('https://api.telegram.org/botPRIVATE-TOKEN/getUpdates')
    bot = telegram.Telegram('PRIVATE-TOKEN', transport=failure)
    with pytest.raises(telegram.TelegramError) as error:
        bot.call('getUpdates', timeout=25)
    assert 'PRIVATE-TOKEN' not in str(error.value)
    assert error.value.__suppress_context__


def test_bot_api_mock_success_and_failure():
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return b'{"ok": true, "result": []}'
    calls = []
    def transport(request, **kwargs):
        calls.append(json.loads(request.data))
        return Response()
    assert telegram.Telegram('fixture', transport).call('getUpdates', offset=7, timeout=25) == []
    assert calls == [{'offset': 7, 'timeout': 25}]


def test_credentials_private_numeric_and_redacted(tmp_path):
    path = tmp_path / 'bot.json'
    path.write_text(json.dumps({'bot_token': '123456:' + 'X' * 32, 'user_id': 34, 'chat_id': 12}))
    path.chmod(0o600)
    assert telegram.credentials(path)['chat_id'] == 12
    path.write_text('{"SECRET": "malformed"}')
    with pytest.raises(ValueError) as error:
        telegram.credentials(path)
    assert 'SECRET' not in str(error.value)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        telegram.credentials(path)


def test_single_host_lock_across_projects(tmp_path):
    tmp_path.chmod(0o700)
    with service.bridge_lock(tmp_path):
        with pytest.raises(ValueError, match='Another approval bridge'):
            with service.bridge_lock(tmp_path):
                pytest.fail('second poller admitted')
    with service.bridge_lock(tmp_path):
        pass


def test_approval_cli_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(service, 'main', lambda args: calls.append(args) or 17)
    assert cli.main(['approval', 'status', '--project', '/fixture']) == 17
    assert calls == [['status', '--project', '/fixture']]


def test_status_pending_are_read_only_and_stop_is_cooperative(queue, tmp_path, monkeypatch, capsys):
    store, _ = queue
    notice(store)
    cfg = {'state': tmp_path}
    monkeypatch.setattr(service, 'admission', lambda *args: cfg)
    before = (tmp_path / 'approval/queue.sqlite3').read_bytes()
    assert service.main(['status']) == 0
    assert service.main(['pending']) == 0
    assert (tmp_path / 'approval/queue.sqlite3').read_bytes() == before
    assert service.main(['stop']) == 0
    assert (tmp_path / 'approval/stop').stat().st_mode & 0o777 == 0o600
    assert 'disabled' in capsys.readouterr().out


def test_admission_provenance_before_state_or_native(monkeypatch, tmp_path):
    project = tmp_path / 'project.json'
    project.write_text('{"manager_revision":"invalid"}')
    monkeypatch.setattr(service.provenance, 'verify', lambda pin: (_ for _ in ()).throw(ValueError('pin')))
    monkeypatch.setattr(service.configuration, 'load', lambda *args: pytest.fail('profile reached'))
    before = set(tmp_path.iterdir())
    assert service.main(['serve', '--project', str(project)]) == 1
    assert set(tmp_path.iterdir()) == before


def test_service_template_is_opt_in():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'docs/runbooks/openshell-manager-approval.service').read_text()
    assert 'approval serve' in template
    assert 'ExecStartPre' not in template and 'ExecStartPost' not in template
    assert 'Restart=on-failure' in template
    assert 'enable' not in (root / 'pyproject.toml').read_text()


def test_serve_stop_and_no_native(tmp_path, monkeypatch):
    project = tmp_path / 'project.json'
    project.write_text('{}')
    cfg = {'state': tmp_path, 'sandbox': 'fixture-dev'}
    auth = {'bot_token': 'fixture', 'user_id': 34, 'chat_id': 12}
    # Use an isolated sibling host state root for the host-wide bridge lock.
    host_root = tmp_path / 'host'
    host_root.mkdir(mode=0o700)
    state = host_root / 'fixture'
    state.mkdir(mode=0o700)
    cfg['state'] = state
    bot = Bot()
    def poll(method, **kwargs):
        assert method == 'getUpdates'
        assert kwargs['timeout'] == 25
        path = state / 'approval/stop'
        path.write_text('')
        path.chmod(0o600)
        return []
    bot.call = poll
    monkeypatch.setattr(service, 'Telegram', lambda token: bot)
    monkeypatch.setattr(service, 'admission', lambda *args: cfg)
    service.serve(cfg, auth, project, None)
    assert bot.notices == []
    with service.bridge_lock(host_root):
        pass  # lock was released, even after cooperative shutdown


@pytest.mark.parametrize('changed', ['id', 'sandbox', 'manager_revision', 'rules', 'github', 'root'])
def test_configuration_drift_stops_before_polling(tmp_path, monkeypatch, changed):
    root = tmp_path / 'host'
    root.mkdir(mode=0o700)
    state = root / 'fixture'
    state.mkdir(mode=0o700)
    cfg = {'state': state, 'sandbox': 'fixture-dev'}
    project = tmp_path / 'project.json'
    project.write_text('{}')
    monkeypatch.setattr(service, 'admission', lambda *args: cfg | {changed: 'changed'})
    bot = Bot()
    monkeypatch.setattr(service, 'Telegram', lambda token: bot)
    with pytest.raises(ValueError, match='configuration changed'):
        service.serve(cfg, {'bot_token': 'fixture', 'user_id': 34, 'chat_id': 12}, project, None)
    assert bot.notices == [] and bot.answers == []
    with service.bridge_lock(root):
        pass
