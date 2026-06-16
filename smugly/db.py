import sqlite3
import threading
from pathlib import Path

_local = threading.local()


def get_conn(db_path: Path) -> sqlite3.Connection:
    key = str(db_path)
    if getattr(_local, "db_path", None) != key:
        conn = sqlite3.connect(key, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.db_path = key
    return _local.conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id              INTEGER PRIMARY KEY,
            local_path      TEXT UNIQUE NOT NULL,
            local_hash      TEXT,
            local_mtime     REAL,
            smugmug_image_id TEXT,
            smugmug_album_id TEXT,
            smugmug_hash    TEXT,
            status          TEXT NOT NULL DEFAULT 'local_only',
            last_synced_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_files_hash   ON files(local_hash);
        CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);

        CREATE TABLE IF NOT EXISTS albums (
            id               INTEGER PRIMARY KEY,
            local_path       TEXT UNIQUE NOT NULL,
            smugmug_node_id  TEXT,
            smugmug_album_id TEXT,
            node_type        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS queue (
            id               INTEGER PRIMARY KEY,
            action           TEXT NOT NULL,
            local_path       TEXT,
            smugmug_image_id TEXT,
            smugmug_album_id TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            error_msg        TEXT,
            created_at       TEXT DEFAULT (datetime('now')),
            updated_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS history (
            id           INTEGER PRIMARY KEY,
            action       TEXT NOT NULL,
            local_path   TEXT,
            detail       TEXT,
            completed_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS worker_state (
            id     INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT NOT NULL DEFAULT 'stopped'
        );
        INSERT OR IGNORE INTO worker_state (id, status) VALUES (1, 'stopped');

        CREATE TABLE IF NOT EXISTS scan_state (
            id           INTEGER PRIMARY KEY CHECK (id = 1),
            last_scan_at TEXT,
            is_scanning  INTEGER NOT NULL DEFAULT 0,
            scan_root    TEXT
        );
        INSERT OR IGNORE INTO scan_state (id, is_scanning) VALUES (1, 0);

        CREATE TABLE IF NOT EXISTS local_dirs (
            path TEXT PRIMARY KEY
        );
    """)
    conn.commit()
