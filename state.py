# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior

import json
import os
import sqlite3
from pathlib import Path


DB_PATH = Path.home() / ".config" / "odsync" / "state.db"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(DB_PATH)
    os.chmod(DB_PATH, 0o600)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS observed (
            path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS uploaded (
            path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL, uploaded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL, upload_url TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (path, scope)
        );
    """)
    if "scope" not in {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}:
        # Sessões antigas não informam o destino: preservar sem autorizar retomada.
        connection.executescript("""
            BEGIN;
            ALTER TABLE sessions RENAME TO sessions_legacy;
            CREATE TABLE sessions (
                path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL, upload_url TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (path, scope)
            );
            INSERT INTO sessions (path, size, mtime_ns, sha256, upload_url)
                SELECT path, size, mtime_ns, sha256, upload_url FROM sessions_legacy;
            DROP TABLE sessions_legacy;
            COMMIT;
        """)
    return connection


def observation(connection, path):
    return connection.execute("SELECT size, mtime_ns FROM observed WHERE path=?", (path,)).fetchone()


def observe(connection, path, size, mtime_ns):
    connection.execute("INSERT OR REPLACE INTO observed VALUES (?, ?, ?)", (path, size, mtime_ns))
    connection.commit()


def uploaded(connection, path):
    return connection.execute("SELECT size, mtime_ns, sha256 FROM uploaded WHERE path=?", (path,)).fetchone()


def mark_uploaded(connection, path, size, mtime_ns, sha256):
    connection.execute(
        "INSERT OR REPLACE INTO uploaded VALUES (?, ?, ?, ?, datetime('now'))",
        (path, size, mtime_ns, sha256),
    )
    connection.commit()


def session_scope(tenant_id, client_id, remote_path):
    return json.dumps([tenant_id, client_id, remote_path], ensure_ascii=True)


def session(connection, path, scope=""):
    return connection.execute(
        "SELECT size, mtime_ns, sha256, upload_url FROM sessions WHERE path=? AND scope=?", (path, scope)
    ).fetchone()


def save_session(connection, path, size, mtime_ns, sha256, upload_url, scope=""):
    connection.execute(
        "INSERT OR REPLACE INTO sessions (path, size, mtime_ns, sha256, upload_url, scope) VALUES (?, ?, ?, ?, ?, ?)",
        (path, size, mtime_ns, sha256, upload_url, scope),
    )
    connection.commit()


def clear_session(connection, path, scope=""):
    connection.execute("DELETE FROM sessions WHERE path=? AND scope=?", (path, scope))
    connection.commit()


def status(connection):
    return connection.execute(
        "SELECT count(*), coalesce(sum(size), 0), max(uploaded_at) FROM uploaded"
    ).fetchone()


def forget_file(connection, remote_path, local_path):
    with connection:
        connection.execute("DELETE FROM uploaded WHERE path=?", (remote_path,))
        connection.execute("DELETE FROM observed WHERE path=?", (remote_path,))
        connection.execute("DELETE FROM sessions WHERE path=?", (local_path,))
