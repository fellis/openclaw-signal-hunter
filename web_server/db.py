"""
Database connection helper for the web server.
Uses psycopg2 with RealDictCursor - same pattern as the main skill storage.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras


def get_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


@contextmanager
def get_conn():
    conn = psycopg2.connect(get_db_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def fetchall(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def fetchone(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
