# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior

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
            path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL, upload_url TEXT NOT NULL
        );
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


def session(connection, path):
    return connection.execute(
        "SELECT size, mtime_ns, sha256, upload_url FROM sessions WHERE path=?", (path,)
    ).fetchone()


def save_session(connection, path, size, mtime_ns, sha256, upload_url):
    connection.execute(
        "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?)",
        (path, size, mtime_ns, sha256, upload_url),
    )
    connection.commit()


def clear_session(connection, path):
    connection.execute("DELETE FROM sessions WHERE path=?", (path,))
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
