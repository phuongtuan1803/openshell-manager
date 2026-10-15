"""Private SQLite queue, atomic callback claims and durable decision audit."""
import hashlib
import os
import secrets
import sqlite3
import stat
import time
import json
import uuid
from pathlib import Path

from ..control import require


def private(path, directory=False, create=False):
    path = Path(path).absolute()
    require(path.resolve() == path, 'Symlink approval path')
    if create:
        if directory:
            path.mkdir(mode=0o700, exist_ok=True)
        else:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass  # Another opener won; validate its ownership/type/mode below.
            else:
                os.close(fd)
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
            'Approval paths must be owned by the host user and private (0700/0600)')
    require(directory or info.st_nlink == 1, 'Hard-linked approval file')
    return path


class Store:
    def __init__(self, directory, binding, clock=time.time):
        private(directory, directory=True, create=not directory.exists())
        path = directory / 'queue.sqlite3'
        private(path, create=not path.exists())
        self.clock = clock
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # DELETE journal plus private directory avoids public sidecars.
        self.db.executescript('''
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (
          id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, scope TEXT NOT NULL,
          expires REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', message INTEGER);
        CREATE TABLE IF NOT EXISTS callbacks (
          hash TEXT PRIMARY KEY, request INTEGER NOT NULL, action TEXT NOT NULL, processed REAL);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, request INTEGER, event TEXT NOT NULL, at REAL NOT NULL);
        ''')
        # Additive migration preserves all PR3b fallback notices, tokens and audit.
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(requests)')}
        for name, declaration in {'kind': "TEXT NOT NULL DEFAULT 'compatibility'", 'snapshot': 'TEXT',
                'native_identity': 'TEXT', 'generation': 'INTEGER NOT NULL DEFAULT 0',
                'action': 'TEXT', 'operation_id': 'TEXT', 'decision_user': 'INTEGER',
                'native_result': 'TEXT', 'native_receipt': 'TEXT', 'status_sent': 'INTEGER NOT NULL DEFAULT 0'}.items():
            if name not in columns:
                self.db.execute(f'ALTER TABLE requests ADD COLUMN {name} {declaration}')
        audit_columns = {row[1] for row in self.db.execute('PRAGMA table_info(audit)')}
        for name, declaration in {'actor': 'INTEGER', 'before_hash': 'TEXT', 'after_hash': 'TEXT'}.items():
            if name not in audit_columns:
                self.db.execute(f'ALTER TABLE audit ADD COLUMN {name} {declaration}')
        self.db.execute('CREATE INDEX IF NOT EXISTS native_identity ON requests(native_identity,generation)')
        self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('binding', binding))
        if self.db.execute('SELECT value FROM metadata WHERE key="binding"').fetchone()[0] != binding:
            self.db.close()
            raise ValueError('Approval project/bot binding changed; manual migration required')

    def close(self):
        self.db.close()

    def audit(self, request, event, actor=None, before=None, after=None):
        self.db.execute('INSERT INTO audit(request,event,at,actor,before_hash,after_hash) VALUES (?,?,?,?,?,?)',
                        (request, event, self.clock(), actor, before, after))

    def enqueue(self, fingerprint, scope):
        if self.db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 10000:
            return
        self.db.execute('INSERT OR IGNORE INTO requests(fingerprint,scope,expires) VALUES (?,?,?)',
                        (fingerprint, scope, self.clock() + 900))

    def recover(self, native=False):
        with self.transaction():
            query = 'SELECT id FROM requests WHERE status="sending" OR (status="applying" AND (kind!="native" OR ?=0))'
            for row in self.db.execute(query, (int(native),)).fetchall():
                self.db.execute('UPDATE requests SET status="needs_review" WHERE id=?', (row['id'],))
                self.audit(row['id'], 'uncertain_after_restart_no_retry')

    def transaction(self):
        from contextlib import contextmanager
        @contextmanager
        def transaction():
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        return transaction()

    def rows(self):
        self.db.execute('UPDATE requests SET status="expired" WHERE expires<=? AND status IN ("pending","notified")',
                        (self.clock(),))
        return self.db.execute('SELECT * FROM requests ORDER BY id').fetchall()

    def claim_notice(self, request):
        token = secrets.token_urlsafe(24)
        with self.transaction():
            changed = self.db.execute('UPDATE requests SET status="sending" WHERE id=? AND status="pending" AND expires>?',
                                      (request, self.clock())).rowcount
            if not changed:
                return None
            row = self.db.execute('SELECT * FROM requests WHERE id=?', (request,)).fetchone()
            tokens = {'details': token}
            if row['kind'] == 'native':
                tokens['reject'] = secrets.token_urlsafe(24)
                if json.loads(row['snapshot'])['eligible']:
                    tokens['approve'] = secrets.token_urlsafe(24)
            for action, value in tokens.items():
                self.db.execute('INSERT INTO callbacks VALUES (?,?,?,NULL)',
                                (hashlib.sha256(value.encode()).hexdigest(), request, action))
        return tokens if row['kind'] == 'native' else token

    def sent(self, request, message):
        self.db.execute('UPDATE requests SET status="notified",message=? WHERE id=? AND status="sending"',
                        (message, request))

    def uncertain(self, request):
        self.db.execute('UPDATE requests SET status="needs_review" WHERE id=? AND status="sending"', (request,))
        self.audit(request, 'telegram_delivery_uncertain_no_retry')

    def callback(self, token, message):
        if not isinstance(token, str) or len(token) != 32 or type(message) is not int:
            return 'Invalid callback'
        with self.transaction():
            row = self.db.execute('''SELECT r.*,c.processed,c.hash FROM callbacks c JOIN requests r ON r.id=c.request
                                  WHERE c.hash=? AND c.action='details' ''',
                                  (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row is None or row['message'] != message:
                return 'Invalid callback'
            if row['expires'] <= self.clock():
                return 'Request expired; review on host'
            if row['processed'] is not None:
                return 'Callback already processed'
            if row['status'] != 'notified':
                return 'Request stale; review on host'
            self.db.execute('UPDATE callbacks SET processed=? WHERE hash=?', (self.clock(), row['hash']))
            self.audit(row['id'], 'details_authorized_manual_review_only')
            return 'Manual review only. Method/path and native identity unavailable. No permission changed.'


    def enqueue_native(self, snapshot):
        latest = self.db.execute('SELECT * FROM requests WHERE native_identity=? ORDER BY generation DESC LIMIT 1',
                                 (snapshot['identity'],)).fetchone()
        fingerprint = snapshot['fingerprint']
        if latest:
            previous = json.loads(latest['snapshot'])
            if latest['status'] in {'sending', 'applying', 'needs_review'}:
                return  # Native/delivery outcome unknown: never create replacement authority.
            if latest['status'] in {'pending', 'notified'}:
                if previous == snapshot:
                    return
                self.db.execute('UPDATE requests SET status="stale" WHERE id=?', (latest['id'],))
                self.audit(latest['id'], 'native_inputs_changed')
        generation = latest['generation'] + 1 if latest else 1
        if self.db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 10000:
            return
        self.db.execute('''INSERT INTO requests(fingerprint,scope,expires,kind,snapshot,native_identity,generation)
                        VALUES (?,?,?,'native',?,?,?)''',
                        (fingerprint + ':' + str(generation), snapshot['scope'], self.clock() + 900,
                         json.dumps(snapshot, sort_keys=True), snapshot['identity'], generation))

    def resolve(self, token, message):
        if not isinstance(token, str) or len(token) != 32 or type(message) is not int:
            return None
        row = self.db.execute('''SELECT r.*,c.action AS callback_action,c.processed,c.hash FROM callbacks c
                                 JOIN requests r ON r.id=c.request WHERE c.hash=?''',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row['message'] != message:
            return None
        return row

    def claim_decision(self, row, actor):
        with self.transaction():
            current = self.resolve_by_hash(row['hash'])
            if (current is None or current['processed'] is not None or current['status'] != 'notified'
                    or current['expires'] <= self.clock() or current['kind'] != 'native'
                    or row['callback_action'] not in {'approve', 'reject'}
                    or (row['callback_action'] == 'approve' and not json.loads(current['snapshot'])['eligible'])):
                return None
            operation = str(uuid.uuid4())
            self.db.execute('UPDATE callbacks SET processed=? WHERE request=?', (self.clock(), row['id']))
            self.db.execute('''UPDATE requests SET status='applying',action=?,operation_id=?,decision_user=?
                               WHERE id=? AND status='notified' ''',
                            (row['callback_action'], operation, actor, row['id']))
            snapshot = json.loads(row['snapshot'])
            self.audit(row['id'], 'native_' + row['callback_action'] + '_claimed', actor, snapshot['policy_hash'])
            return operation

    def resolve_by_hash(self, token_hash):
        return self.db.execute('''SELECT r.*,c.processed FROM callbacks c JOIN requests r ON r.id=c.request
                                  WHERE c.hash=?''', (token_hash,)).fetchone()

    def finish(self, request, status, result=None):
        with self.transaction():
            row = self.db.execute('SELECT * FROM requests WHERE id=?', (request,)).fetchone()
            self.db.execute('UPDATE requests SET status=?,native_result=COALESCE(?,native_result) WHERE id=? AND status IN ("applying","needs_review")',
                            (status, json.dumps(result, sort_keys=True) if result else None, request))
            snapshot = json.loads(row['snapshot'])
            self.audit(request, 'native_' + status, row['decision_user'], snapshot['policy_hash'],
                       result.get('policy_hash') if result else None)

    def stale(self, request):
        self.db.execute('UPDATE requests SET status="stale" WHERE id=? AND status IN ("pending","notified")', (request,))
        self.audit(request, 'native_stale_no_mutation')

    @property
    def offset(self):
        row = self.db.execute('SELECT value FROM metadata WHERE key="offset"').fetchone()
        return int(row[0]) if row else 0

    def advance(self, offset):
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES ("offset",?)', (str(max(offset, self.offset)),))
