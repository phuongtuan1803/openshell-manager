"""One project/bot host bridge; exact native network proposals and manual fallback."""
import argparse
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .. import configuration, control, provenance
from . import diagnostics
from .compatibility import notifications
from .native import BLOCKER, GatewayRPC, NativeAdapter, NativeUnavailable, NativeStale, NativeVerification
from .store import Store, private
from .telegram import Telegram, TelegramError, credentials


@contextmanager
def bridge_lock(state_root):
    private(state_root, directory=True)
    path = state_root / 'approval-bridge.lock'
    private(path, create=not path.exists())
    with path.open('r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another approval bridge is active on this host') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def admission(project, host):
    control.require(project is not None, 'Supply --project or MANAGER_PROJECT')
    control.require(not any(k.startswith('OPENSHELL_') for k in os.environ),
                    'OpenShell environment overrides are not allowed')
    provenance.verify(configuration.file(project).get('manager_revision'))
    cfg = configuration.load(project, host)
    for rule in cfg['rules']:
        control.validate_rule({**rule, 'binaries': [rule['binary']] if 'binary' in rule else ['/usr/bin/codex']})
    binding = {k: str(cfg[k]) for k in ('id', 'root', 'repository', 'sandbox', 'workspace', 'manager_revision')}
    control.require(control.read(cfg['state'] / 'project-binding.json') == binding,
                    'Project state binding mismatch; run explicit setup first')
    return cfg


class Bridge:
    def __init__(self, cfg, auth, store, telegram, native=None, stopping=lambda: False):
        self.cfg, self.auth, self.store, self.telegram = cfg, auth, store, telegram
        self.native = native
        self.stopping = stopping

    def scan(self):
        if self.stopping():
            return
        if self.native is not None:
            try:
                self.scan_native()
                self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES ("native_health",?)',
                    (json.dumps({'mode': 'native', 'status': 'verified', 'observed_at': self.store.clock()}),))
                self.reconcile_native()
                self.announce_results()
                return
            except NativeUnavailable:
                self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES ("native_health",?)',
                    (json.dumps({'mode': 'manual_review', 'status': 'blocked', 'observed_at': self.store.clock()}),))
                print(BLOCKER, file=sys.stderr)
        self.scan_compatibility()

    def scan_compatibility(self):
        current = dict(notifications(self.cfg['state'] / 'inbox.json'))
        with self.store.transaction():
            for row in self.store.rows():
                if row['kind'] == 'compatibility' and row['fingerprint'] not in current and row['status'] in {'pending', 'notified'}:
                    self.store.db.execute('UPDATE requests SET status="stale" WHERE id=?', (row['id'],))
                    self.store.audit(row['id'], 'compatibility_notice_no_longer_pending')
            for fingerprint, scope in current.items():
                self.store.enqueue(fingerprint, scope)
        self.deliver('compatibility')

    def deliver(self, kind):
        sent = 0
        for row in self.store.rows():
            if self.stopping() or sent >= 10:
                return
            if row['kind'] != kind:
                continue
            token = self.store.claim_notice(row['id'])
            if token is None:
                continue
            refs = self.refs(row)
            diagnostics.emit('telegram', 'notification_start', **refs)
            try:
                message = self.telegram.notify(self.auth['chat_id'], self.cfg['sandbox'], row, token)
                self.store.sent(row['id'], message)
                diagnostics.emit('telegram', 'notification_end', status='pass', message_ref=diagnostics.reference(message), **refs)
                sent += 1
            except (TelegramError, ValueError):
                # The HTTP result could be lost after delivery. Never send blindly twice.
                self.store.uncertain(row['id'])
                diagnostics.emit('telegram', 'notification_end', status='unknown', code='delivery_uncertain', **refs)
                raise TelegramError('Telegram delivery uncertain; inspect approval pending') from None


    def scan_native(self):
        proposals = self.native.discover()
        identities = {p['identity'] for p in proposals}
        with self.store.transaction():
            for row in self.store.rows():
                if row['kind'] == 'native' and row['native_identity'] not in identities and row['status'] in {'pending', 'notified'}:
                    self.store.stale(row['id'])
            for proposal in proposals:
                self.store.enqueue_native(proposal)
        self.deliver('native')

    @contextmanager
    def operation_lock(self):
        path = self.cfg['state'] / 'operation.lock'
        private(path, create=not path.exists())
        with path.open('r+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise NativeUnavailable('Manager operation busy; no mutation attempted') from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def refs(self, row):
        fields = {'message_ref': diagnostics.reference(row['message'])} if row['message'] is not None else {}
        if row['kind'] == 'native':
            snapshot = json.loads(row['snapshot'])
            fields.update(sandbox_ref=diagnostics.reference(snapshot['sandbox_id']),
                          proposal_ref=diagnostics.reference(snapshot['chunk_id']))
        return fields

    def native_callback(self, callback, message, actor):
        if self.stopping():
            return 'Bridge stopping; no action attempted'
        row = self.store.resolve(callback.get('data'), message.get('message_id'))
        if row is None or row['kind'] != 'native':
            diagnostics.emit('telegram', 'callback_refused', code='invalid_callback', status='fail')
            return 'Invalid callback'
        if row['expires'] <= self.store.clock():
            diagnostics.emit('telegram', 'callback_refused', code='expired_callback', status='fail', **self.refs(row))
            return 'Request expired; wait for a fresh native review notification'
        if row['processed'] is not None or row['status'] != 'notified':
            diagnostics.emit('telegram', 'callback_refused', code='stale_callback', status='fail', **self.refs(row))
            return 'Already processed or stale; no action repeated'
        if self.native is None:
            return BLOCKER
        snapshot = json.loads(row['snapshot'])
        try:
            with self.operation_lock():
                self.native.verify(snapshot)
                if row['callback_action'] == 'details':
                    with self.store.transaction():
                        changed = self.store.db.execute('UPDATE callbacks SET processed=? WHERE hash=? AND processed IS NULL',
                                                         (self.store.clock(), row['hash'])).rowcount
                        if not changed:
                            return 'Callback already processed'
                        self.store.audit(row['id'], 'native_details', actor, snapshot['policy_hash'])
                    return 'Exact native proposal verified. Scope/risk are in this message; approval is a runtime rule, not one-time permission.'
                operation = self.store.claim_decision(row, actor)
                if operation is None:
                    return 'Already processed or expired; no action repeated'
                diagnostics.emit('telegram', 'decision_claimed', action=row['callback_action'],
                                 decision_ref=diagnostics.reference(operation), **self.refs(row))
                started = time.monotonic()
                try:
                    receipt = self.native.decide(snapshot, row['callback_action'], operation, row['expires'])
                    self.store.db.execute('UPDATE requests SET native_result=?,native_receipt=? WHERE id=?',
                                          (json.dumps(receipt), json.dumps(receipt), row['id']))
                    result = self.native.confirm(snapshot, row['callback_action'], receipt)
                except NativeStale:
                    self.store.finish(row['id'], 'stale')
                    return 'Native refused stale review; no grant confirmed. Fresh review required.'
                except NativeUnavailable as error:
                    code = getattr(error, 'code', 'native_outcome_unknown')
                    self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                        ('confirmation_error_' + str(row['id']), json.dumps({'code': code, 'permanent': isinstance(error, NativeVerification) and not error.retryable})))
                    diagnostics.emit('telegram', 'decision_end', status='unknown', code=code,
                                     duration_ms=(time.monotonic() - started) * 1000, **self.refs(row))
                    self.store.finish(row['id'], 'needs_review')
                    return 'Native outcome unconfirmed; needs host review. No automatic retry.'
                diagnostics.emit('telegram', 'decision_end', status='pass', action=row['callback_action'],
                                 duration_ms=(time.monotonic() - started) * 1000, **self.refs(row))
                self.store.finish(row['id'], result['status'], result)
                text = 'Approved and loaded by OpenShell; agent may retry.' if result['status'] == 'approved' else 'Rejected by OpenShell; agent may submit a narrower proposal.'
                self.announce_result(row, text)
                return text
        except NativeStale:
            self.store.stale(row['id'])
            return 'Request stale; no native action attempted. Review a fresh proposal.'
        except NativeUnavailable:
            return 'Native verification unavailable or Manager busy; no action attempted.'

    def reconcile_native(self):
        for row in self.store.rows():
            if self.stopping():
                return
            if row['kind'] != 'native' or row['status'] not in {'applying', 'needs_review'} or not row['action']:
                continue
            previous = self.store.db.execute('SELECT value FROM metadata WHERE key=?', ('confirmation_error_' + str(row['id']),)).fetchone()
            if previous and json.loads(previous[0])['permanent']:
                continue
            key = 'confirmation_reads_' + str(row['id'])
            reads = self.store.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            count = int(reads[0]) if reads else 0
            if count >= 4:
                continue
            self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, str(count + 1)))
            try:
                # Read-only reconciliation. request_id is never replayed to a mutation.
                result = self.native.confirm(json.loads(row['snapshot']), row['action'],
                                             json.loads(row['native_receipt'] or row['native_result']) if row['native_receipt'] or row['native_result'] else None)
            except NativeUnavailable as error:
                if isinstance(error, NativeVerification) and not error.retryable:
                    self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                        ('confirmation_error_' + str(row['id']), json.dumps({'code': error.code, 'permanent': True})))
                if row['status'] == 'applying':
                    self.store.finish(row['id'], 'needs_review')
                continue
            self.store.finish(row['id'], result['status'], result)

    def announce_result(self, row, text):
        self.telegram.status(self.auth['chat_id'], row, text)
        self.store.db.execute('UPDATE requests SET status_sent=1 WHERE id=?', (row['id'],))

    def announce_results(self):
        for row in self.store.rows():
            if row['kind'] == 'native' and row['status'] in {'approved', 'rejected'} and not row['status_sent']:
                self.announce_result(row, 'OpenShell decision confirmed: ' + row['status'])

    def update(self, update):
        if not isinstance(update, dict) or type(update.get('update_id')) is not int:
            return
        if update['update_id'] < self.store.offset:
            return
        callback_id = None
        callback = update.get('callback_query')
        if isinstance(callback, dict):
            sender, message = callback.get('from'), callback.get('message')
            chat = message.get('chat') if isinstance(message, dict) else None
            authorized = (isinstance(sender, dict) and type(sender.get('id')) is int
                          and sender['id'] == self.auth['user_id'] and isinstance(chat, dict)
                          and type(chat.get('id')) is int and chat['id'] == self.auth['chat_id'])
            diagnostics.emit('telegram', 'callback_received', user_match=isinstance(sender, dict) and type(sender.get('id')) is int and sender['id'] == self.auth['user_id'],
                             chat_match=isinstance(chat, dict) and type(chat.get('id')) is int and chat['id'] == self.auth['chat_id'])
            if authorized:
                row = self.store.resolve(callback.get('data'), message.get('message_id'))
                diagnostics.emit('telegram', 'callback_resolved', message_match=row is not None,
                                 action=row['callback_action'] if row is not None and row['kind'] == 'native' else 'invalid',
                                 **(self.refs(row) if row is not None else {}))
                if row is not None and row['kind'] == 'native':
                    result = self.native_callback(callback, message, sender['id'])
                else:
                    result = self.store.callback(callback.get('data'), message.get('message_id'))
            else:
                diagnostics.emit('telegram', 'callback_refused', code='unauthorized_callback', status='fail')
                result = 'Unauthorized; no permission changed'
            callback_id = callback.get('id')
        # Handling is durable before acknowledging Telegram. An expired query
        # can never be acknowledged successfully; replaying it would block all
        # later decisions behind the same failed answerCallbackQuery forever.
        self.store.advance(update['update_id'] + 1)
        if isinstance(callback_id, str) and len(callback_id) <= 256:
            self.telegram.call('answerCallbackQuery', callback_query_id=callback_id, text=result, show_alert=True)


def serve(cfg, auth, project, host, gateway=None, workspace="default"):
    directory = cfg['state'] / 'approval'
    with bridge_lock(cfg['state'].parent):
        private(directory, directory=True, create=not directory.exists())
        stop = directory / 'stop'
        if stop.exists():
            private(stop)
            stop.unlink()
        binding_fields = {
            'project': configuration.file(project), 'user': auth['user_id'], 'chat': auth['chat_id'],
            'bot': hashlib.sha256(auth['bot_token'].encode()).hexdigest()
        }
        binding = hashlib.sha256(json.dumps(binding_fields, sort_keys=True).encode()).hexdigest()
        store = Store(directory, binding)
        if gateway is not None:
            native_binding = json.dumps({'gateway': gateway, 'workspace': workspace}, sort_keys=True)
            store.db.execute('INSERT OR IGNORE INTO metadata VALUES ("native_binding",?)', (native_binding,))
            if store.db.execute('SELECT value FROM metadata WHERE key="native_binding"').fetchone()[0] != native_binding:
                store.close()
                raise ValueError('Native Gateway binding changed; manual migration required')
        rpc = None
        if gateway is not None:
            try:
                rpc = GatewayRPC(gateway)
                native = NativeAdapter(cfg, rpc, workspace, clock=store.clock)
            except (NativeUnavailable, ValueError, OSError):
                print(BLOCKER, file=sys.stderr)
                native = None
        else:
            native = None
        shutdown = threading.Event()
        def stop_signal(signum, frame):
            diagnostics.emit('events', 'signal_received', code='sigint' if signum == signal.SIGINT else 'sigterm')
            shutdown.set()
        previous = {sig: signal.signal(sig, stop_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            store.recover(native=native is not None)
            stopping = lambda: shutdown.is_set() or stop.exists()
            if rpc is not None:
                rpc.stopping = stopping
            bridge = Bridge(cfg, auth, store, Telegram(auth['bot_token']), native, stopping)
            delay = 1
            while not shutdown.is_set() and not stop.exists():
                # Revalidate binding/pin/scopes every cycle; configuration drift stops polling.
                control.require(admission(project, host) == cfg, 'Project configuration changed; bridge stopped')
                try:
                    bridge.scan()
                    updates = bridge.telegram.call('getUpdates', offset=store.offset, timeout=25, limit=100,
                                                   allowed_updates=['callback_query'])
                    control.require(isinstance(updates, list), 'Invalid Telegram updates')
                    if stopping():
                        break
                    # Re-read native state/inbox before callbacks; stale notices have no authority.
                    bridge.scan()
                    for update in updates[:100]:
                        if stopping():
                            break
                        bridge.update(update)
                    delay = 1
                except TelegramError:
                    print('Telegram unavailable; manual host review remains required', file=sys.stderr)
                    shutdown.wait(delay)
                    delay = min(delay * 2, 30)
        finally:
            store.close()
            if rpc is not None:
                rpc.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv):
    parser = argparse.ArgumentParser(prog='openshell-manager approval', description=BLOCKER)
    parser.add_argument('action', choices=['setup', 'serve', 'status', 'pending', 'stop', 'test'])
    parser.add_argument('--project', type=Path, default=os.environ.get('MANAGER_PROJECT'))
    parser.add_argument('--host', type=Path, default=os.environ.get('MANAGER_HOST'))
    parser.add_argument('--gateway', help='Existing local authenticated mTLS Gateway name; enables native discovery')
    parser.add_argument('--gateway-workspace', default='default', help='Exact native workspace name')
    parser.add_argument('--diagnostic-directory', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--bot-config', type=Path, help='Private host JSON; needed only for serve')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == 'test':
            from . import live_test
            return live_test.main(args)
        if args.action == 'setup':
            control.require(args.project is not None, 'Supply --project or MANAGER_PROJECT')
            control.require(not any(k.startswith('OPENSHELL_') for k in os.environ), 'OpenShell environment overrides are not allowed')
            control.configure(args.project, args.host)
            print('Native mode requires --gateway with existing local mTLS credentials. Missing native evidence blocks approval.')
            print('Use a dedicated bot and a private 0600 host JSON with bot_token, user_id and chat_id.')
            print('Serve requires --bot-config. Service activation is an explicit operator step.')
            return 0
        if args.diagnostic_directory is not None:
            diagnostics.ACTIVE = diagnostics.Diagnostics(args.diagnostic_directory)
        cfg = admission(args.project, args.host)
        directory = cfg['state'] / 'approval'
        if args.action == 'serve':
            control.require(args.bot_config is not None, 'Supply private --bot-config')
            path = args.bot_config.absolute()
            control.require(not path.is_relative_to(Path(cfg['root'])), 'Bot credentials must be outside the project')
            serve(cfg, credentials(path), args.project, args.host, args.gateway, args.gateway_workspace)
        elif args.action == 'stop':
            private(directory, directory=True)
            path = directory / 'stop'
            private(path, create=not path.exists())
            print('Stop requested; bridge exits after its current poll (up to 35 seconds).')
        else:
            print('Native approval disabled unless Gateway evidence is verified; compatibility notices cannot grant permission.')
            path = directory / 'queue.sqlite3'
            if not path.exists():
                print('No approval queue; bridge has not initialized.')
                return 0
            private(directory, directory=True)
            private(path)
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                if args.action == 'status':
                    print('Queue states:', dict(db.execute('SELECT status,count(*) FROM requests GROUP BY status')))
                    health = db.execute('SELECT value FROM metadata WHERE key="native_health"').fetchone()
                    print('Last native observation:', json.loads(health[0]) if health else 'No verified native observation')
                    print('Bridge health: use systemctl --user status openshell-manager-approval.service')
                else:
                    for row in db.execute('SELECT id,status,expires,scope,kind,snapshot FROM requests ORDER BY id LIMIT 1000'):
                        item = dict(id=row[0], status=row[1], expires=row[2], scope=row[3], kind=row[4])
                        if row[4] == 'native':
                            snapshot = json.loads(row[5])
                            item.update({k: snapshot[k] for k in ('project', 'sandbox', 'chunk_id', 'risk', 'policy_hash')})
                        print(json.dumps(item))
        return 0
    except (ValueError, OSError, sqlite3.Error):
        diagnostics.soft_emit('events', 'bridge_failed', stage='bridge', status='fail', code='bridge_configuration_or_transport')
        # Never expose exception strings from credential or network-bearing operations.
        print('FAIL: approval operation refused; check private paths, project pin/binding and runbook', file=sys.stderr)
        return 1
