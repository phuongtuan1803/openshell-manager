"""Private SQLite notification journal. No transition here can grant permission."""
import hashlib
import os
import secrets
import sqlite3
import stat
import time
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
        self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('binding', binding))
        if self.db.execute('SELECT value FROM metadata WHERE key="binding"').fetchone()[0] != binding:
            self.db.close()
            raise ValueError('Approval project/bot binding changed; manual migration required')

    def close(self):
        self.db.close()

    def audit(self, request, event):
        self.db.execute('INSERT INTO audit(request,event,at) VALUES (?,?,?)', (request, event, self.clock()))

    def enqueue(self, fingerprint, scope):
        if self.db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 10000:
            return
        self.db.execute('INSERT OR IGNORE INTO requests(fingerprint,scope,expires) VALUES (?,?,?)',
                        (fingerprint, scope, self.clock() + 900))

    def recover(self):
        with self.transaction():
            for row in self.db.execute('SELECT id FROM requests WHERE status IN ("sending", "applying")').fetchall():
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
            self.db.execute('INSERT INTO callbacks VALUES (?,?,?,NULL)',
                            (hashlib.sha256(token.encode()).hexdigest(), request, 'details'))
        return token

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

    @property
    def offset(self):
        row = self.db.execute('SELECT value FROM metadata WHERE key="offset"').fetchone()
        return int(row[0]) if row else 0

    def advance(self, offset):
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES ("offset",?)', (str(max(offset, self.offset)),))
