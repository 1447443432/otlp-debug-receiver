"""SQLite 持久化。单 Pod / 单副本；不支持多个 Pod 同时读写同一数据库文件。"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from app import config

SCHEMA = '''
CREATE TABLE IF NOT EXISTS spans (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 received_ns INTEGER NOT NULL, trace_id TEXT NOT NULL, span_id TEXT NOT NULL,
 parent_span_id TEXT, service TEXT NOT NULL, name TEXT,
 start_ns INTEGER, end_ns INTEGER, duration_ms REAL, status INTEGER,
 attrs TEXT NOT NULL, resource_attrs TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS spans_received ON spans(received_ns);
CREATE INDEX IF NOT EXISTS spans_service ON spans(service);
CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, received_ns INTEGER NOT NULL,
 event_ns INTEGER, service TEXT NOT NULL, severity TEXT, body TEXT,
 trace_id TEXT, span_id TEXT, attrs TEXT NOT NULL, resource_attrs TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_received ON logs(received_ns);
CREATE INDEX IF NOT EXISTS logs_trace ON logs(trace_id);
CREATE INDEX IF NOT EXISTS logs_service ON logs(service);
'''

class Store:
    def __init__(self, path=None):
        path = str(path or config.DB_PATH)
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA busy_timeout=10000')
            self.conn.executescript(SCHEMA)

    def insert(self, table, records):
        if not records:
            return 0
        if table not in ('spans', 'logs'):
            raise ValueError('invalid table')
        cols = list(records[0])
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
        with self.lock, self.conn:
            self.conn.executemany(sql, [tuple(row[key] for key in cols) for row in records])
        return len(records)

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def cleanup(self, hours):
        cutoff = time.time_ns() - hours * 3600 * 1_000_000_000
        with self.lock, self.conn:
            for table in ('spans', 'logs'):
                self.conn.execute(f'DELETE FROM {table} WHERE received_ns < ?', (cutoff,))

    def close(self):
        with self.lock:
            self.conn.close()


def parse_json_fields(rows):
    for row in rows:
        for key in ('attrs', 'resource_attrs'):
            if key in row:
                row[key] = json.loads(row[key])
    return rows
