"""
db.py — Wrapper SQLite para jurimetria_pge.db
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

_SQLITE_DEFAULT = Path(__file__).parent / "jurimetria_pge.db"


def connect() -> sqlite3.Connection:
    return sqlite3.connect(_SQLITE_DEFAULT)


def ph() -> str:
    return "?"


def adapt_sql(sql: str) -> str:
    return sql


def executemany(cur: sqlite3.Cursor, sql: str, rows: list[tuple]) -> None:
    if rows:
        cur.executemany(sql, rows)


def read_sql(sql: str, con: sqlite3.Connection, params=None) -> pd.DataFrame:
    if params is not None:
        return pd.read_sql(sql, con, params=params)
    return pd.read_sql(sql, con)


def add_column_if_not_exists(cur: sqlite3.Cursor, table: str, col: str, tipo: str) -> None:
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {tipo}")
    except sqlite3.OperationalError:
        pass


def db_exists() -> bool:
    return _SQLITE_DEFAULT.exists()


def sqlite_path() -> Path:
    return _SQLITE_DEFAULT
