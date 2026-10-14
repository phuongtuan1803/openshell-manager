"""One host/project/bot poller, cooperative stop, no native service calls."""
import argparse
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from .. import configuration, control, provenance
from .compatibility import notifications
from .native import BLOCKER
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
    def __init__(self, cfg, auth, store, telegram):
        self.cfg, self.auth, self.store, self.telegram = cfg, auth, store, telegram

    def scan(self):
        current = dict(notifications(self.cfg['state'] / 'inbox.json'))
        with self.store.transaction():
            for row in self.store.rows():
                if row['fingerprint'] not in current and row['status'] in {'pending', 'notified'}:
                    self.store.db.execute('UPDATE requests SET status="stale" WHERE id=?', (row['id'],))
                    self.store.audit(row['id'], 'compatibility_notice_no_longer_pending')
            for fingerprint, scope in current.items():
                self.store.enqueue(fingerprint, scope)
        for row in self.store.rows():
            token = self.store.claim_notice(row['id'])
            if token is None:
                continue
            try:
                message = self.telegram.notify(self.auth['chat_id'], self.cfg['sandbox'], row, token)
                self.store.sent(row['id'], message)
            except (TelegramError, ValueError):
                # The HTTP result could be lost after delivery. Never send blindly twice.
                self.store.uncertain(row['id'])
                raise TelegramError('Telegram delivery uncertain; inspect approval pending') from None

    def update(self, update):
        if not isinstance(update, dict) or type(update.get('update_id')) is not int:
            return
        if update['update_id'] < self.store.offset:
            return
        callback = update.get('callback_query')
        if isinstance(callback, dict):
            sender, message = callback.get('from'), callback.get('message')
            chat = message.get('chat') if isinstance(message, dict) else None
            authorized = (isinstance(sender, dict) and type(sender.get('id')) is int
                          and sender['id'] == self.auth['user_id'] and isinstance(chat, dict)
                          and type(chat.get('id')) is int and chat['id'] == self.auth['chat_id'])
            if authorized:
                result = self.store.callback(callback.get('data'), message.get('message_id'))
            else:
                result = 'Unauthorized; no permission changed'
            callback_id = callback.get('id')
            if isinstance(callback_id, str) and len(callback_id) <= 256:
                self.telegram.call('answerCallbackQuery', callback_query_id=callback_id, text=result, show_alert=True)
        # Persist after handling; replay after a response failure is harmless.
        self.store.advance(update['update_id'] + 1)


def serve(cfg, auth, project, host):
    directory = cfg['state'] / 'approval'
    with bridge_lock(cfg['state'].parent):
        private(directory, directory=True, create=not directory.exists())
        stop = directory / 'stop'
        if stop.exists():
            private(stop)
            stop.unlink()
        binding = hashlib.sha256(json.dumps({
            'project': configuration.file(project), 'user': auth['user_id'], 'chat': auth['chat_id'],
            'bot': hashlib.sha256(auth['bot_token'].encode()).hexdigest()
        }, sort_keys=True).encode()).hexdigest()
        store = Store(directory, binding)
        shutdown = threading.Event()
        previous = {sig: signal.signal(sig, lambda *args: shutdown.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            store.recover()
            bridge = Bridge(cfg, auth, store, Telegram(auth['bot_token']))
            delay = 1
            while not shutdown.is_set() and not stop.exists():
                # Revalidate binding/pin/scopes every cycle; configuration drift stops polling.
                control.require(admission(project, host) == cfg, 'Project configuration changed; bridge stopped')
                try:
                    bridge.scan()
                    updates = bridge.telegram.call('getUpdates', offset=store.offset, timeout=25, limit=100,
                                                   allowed_updates=['callback_query'])
                    control.require(isinstance(updates, list), 'Invalid Telegram updates')
                    # Re-read inbox before callbacks; a stale notice has no authority.
                    bridge.scan()
                    for update in updates[:100]:
                        bridge.update(update)
                    delay = 1
                except TelegramError:
                    print('Telegram unavailable; manual host review remains required', file=sys.stderr)
                    shutdown.wait(delay)
                    delay = min(delay * 2, 30)
        finally:
            store.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv):
    parser = argparse.ArgumentParser(description=BLOCKER)
    parser.add_argument('action', choices=['setup', 'serve', 'status', 'pending', 'stop'])
    parser.add_argument('--project', type=Path, default=os.environ.get('MANAGER_PROJECT'))
    parser.add_argument('--host', type=Path, default=os.environ.get('MANAGER_HOST'))
    parser.add_argument('--bot-config', type=Path, help='Private host JSON; needed only for serve')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == 'setup':
            control.require(args.project is not None, 'Supply --project or MANAGER_PROJECT')
            control.require(not any(k.startswith('OPENSHELL_') for k in os.environ), 'OpenShell environment overrides are not allowed')
            control.configure(args.project, args.host)
            print(BLOCKER)
            print('Use a dedicated bot and a private 0600 host JSON with bot_token, user_id and chat_id.')
            print('Serve requires --bot-config. Service activation is an explicit operator step.')
            return 0
        cfg = admission(args.project, args.host)
        directory = cfg['state'] / 'approval'
        if args.action == 'serve':
            control.require(args.bot_config is not None, 'Supply private --bot-config')
            path = args.bot_config.absolute()
            control.require(not path.is_relative_to(Path(cfg['root'])), 'Bot credentials must be outside the project')
            serve(cfg, credentials(path), args.project, args.host)
        elif args.action == 'stop':
            private(directory, directory=True)
            path = directory / 'stop'
            private(path, create=not path.exists())
            print('Stop requested; bridge exits after its current poll (up to 35 seconds).')
        else:
            print(BLOCKER)
            path = directory / 'queue.sqlite3'
            if not path.exists():
                print('No approval queue; bridge has not initialized.')
                return 0
            private(directory, directory=True)
            private(path)
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                if args.action == 'status':
                    print('Queue states:', dict(db.execute('SELECT status,count(*) FROM requests GROUP BY status')))
                    print('Bridge health: use systemctl --user status openshell-manager-approval.service')
                else:
                    for row in db.execute('SELECT id,status,expires,scope FROM requests ORDER BY id LIMIT 1000'):
                        print(json.dumps(row))
        return 0
    except (ValueError, OSError, sqlite3.Error):
        # Never expose exception strings from credential or network-bearing operations.
        print('FAIL: approval operation refused; check private paths, project pin/binding and runbook', file=sys.stderr)
        return 1
