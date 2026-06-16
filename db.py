"""
db.py — Camada de abstração de banco de dados
SQLite (local, padrão) ↔ PostgreSQL (Supabase/remoto)

Configuração — prioridade:
  1. Variável de ambiente  DATABASE_URL  (PostgreSQL) ou  DB_TYPE=sqlite
  2. .streamlit/secrets.toml  [database]  type / url
  3. SQLite local  jurimetria_pge.db  (padrão quando nada configurado)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd

# Caminho padrão do SQLite — mesmo diretório do módulo
_SQLITE_DEFAULT = Path(__file__).parent / "jurimetria_pge.db"


# =============================================================================
# LEITURA DE CONFIGURAÇÃO
# =============================================================================

def _cfg() -> dict[str, str]:
    """
    Retorna dict com chaves 'type' e 'url'.
    Nunca lança exceção — silencia erros de Secrets e retorna SQLite.
    """
    # 1. Variável de ambiente
    db_url = os.getenv("DATABASE_URL", "").strip()
    db_type = os.getenv("DB_TYPE", "").strip().lower()
    if db_url or db_type == "postgresql":
        return {"type": "postgresql", "url": db_url}

    # 2. Streamlit secrets (opcional — falha silenciosa fora do contexto Streamlit)
    try:
        import streamlit as st
        cfg = dict(st.secrets.get("database", {}))
        if str(cfg.get("type", "")).lower() == "postgresql":
            return {"type": "postgresql", "url": str(cfg.get("url", ""))}
    except Exception:
        pass

    return {"type": "sqlite", "url": ""}


def is_postgres() -> bool:
    return _cfg()["type"] == "postgresql"


# =============================================================================
# ADAPTAÇÃO DE SQL
# =============================================================================

def ph() -> str:
    """Placeholder de parâmetros: '?' (SQLite) ou '%s' (PostgreSQL)."""
    return "%s" if is_postgres() else "?"


def adapt_sql(sql: str) -> str:
    """
    Adapta SQL do dialeto SQLite para PostgreSQL quando necessário.
    Substitui '?' por '%s' e 'AUTOINCREMENT' pela sintaxe serial.
    """
    if not is_postgres():
        return sql
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    sql = sql.replace("?", "%s")
    return sql


# =============================================================================
# CONEXÃO
# =============================================================================

def connect():
    """Retorna conexão DBAPI2 para o banco configurado (SQLite ou PostgreSQL)."""
    cfg = _cfg()
    if cfg["type"] == "postgresql":
        try:
            import psycopg2  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "Pacote 'psycopg2' não encontrado.\n"
                "Instale via:  pip install psycopg2-binary"
            ) from exc
        url = cfg["url"]
        if not url:
            raise ValueError(
                "URL do PostgreSQL não configurada.\n"
                "Defina DATABASE_URL no ambiente ou\n"
                "[database] url em .streamlit/secrets.toml"
            )
        return psycopg2.connect(url)
    else:
        return sqlite3.connect(_SQLITE_DEFAULT)


# =============================================================================
# INSERÇÃO EM LOTE
# =============================================================================

def executemany(cur, sql: str, rows: list[tuple]) -> None:
    """
    Executa inserções/atualizações em lote.
    SQLite: usa cursor.executemany nativo (rápido, sem rede).
    PostgreSQL: usa psycopg2.extras.execute_batch, que agrupa múltiplas linhas
    por viagem de rede — executemany puro do psycopg2 envia uma instrução por
    linha e fica inviável para milhares de registros num banco remoto.
    """
    if not rows:
        return
    if is_postgres():
        from psycopg2.extras import execute_batch
        execute_batch(cur, sql, rows, page_size=1000)
    else:
        cur.executemany(sql, rows)


# =============================================================================
# LEITURA COMO DATAFRAME
# =============================================================================

def read_sql(sql: str, con) -> pd.DataFrame:
    """
    Executa SELECT e retorna DataFrame sem exigir SQLAlchemy.
    pandas.read_sql requer SQLAlchemy para conexões psycopg2 (pandas >= 2.0);
    esta função usa cursor diretamente no caso PostgreSQL.
    """
    if is_postgres():
        cur = con.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)
    return pd.read_sql(sql, con)


# =============================================================================
# MIGRAÇÃO SEGURA DE COLUNAS
# =============================================================================

def add_column_if_not_exists(cur, table: str, col: str, tipo: str) -> None:
    """
    Adiciona coluna sem falhar se ela já existir.
    PostgreSQL usa 'ADD COLUMN IF NOT EXISTS'; SQLite usa try/except.
    """
    if is_postgres():
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {tipo}")
    else:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {tipo}")
        except sqlite3.OperationalError:
            pass


# =============================================================================
# VERIFICAÇÃO DE EXISTÊNCIA DO BANCO
# =============================================================================

def db_exists() -> bool:
    """
    Verifica se o banco está acessível.
    SQLite: checa se o arquivo existe.
    PostgreSQL: tenta abrir uma conexão.
    """
    if is_postgres():
        try:
            con = connect()
            con.close()
            return True
        except Exception:
            return False
    return _SQLITE_DEFAULT.exists()


def sqlite_path() -> Path:
    """Caminho do arquivo SQLite (útil para display/backup)."""
    return _SQLITE_DEFAULT
