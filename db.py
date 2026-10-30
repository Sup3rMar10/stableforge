"""SQLite helpers shared by the API, the engine and sim.py."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("SF_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "stableforge.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wallets(
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  col INTEGER NOT NULL CHECK(col >= 0), forge INTEGER NOT NULL CHECK(forge >= 0), usdc INTEGER NOT NULL CHECK(usdc >= 0));
CREATE TABLE IF NOT EXISTS vaults(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ink INTEGER NOT NULL CHECK(ink >= 0), art INTEGER NOT NULL CHECK(art >= 0),
  status TEXT NOT NULL CHECK(status IN ('open','closed')), opened_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS auctions(
  id INTEGER PRIMARY KEY, vault_id INTEGER NOT NULL REFERENCES vaults(id),
  lot INTEGER NOT NULL, debt INTEGER NOT NULL, tab INTEGER NOT NULL, top INTEGER NOT NULL, start_tick INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','done')), end_tick INTEGER, price INTEGER,
  paid INTEGER, returned INTEGER, bad_debt INTEGER, keeper_profit INTEGER);
CREATE TABLE IF NOT EXISTS sys(
  id INTEGER PRIMARY KEY CHECK(id = 1), tick INTEGER NOT NULL, step INTEGER NOT NULL, scenario TEXT NOT NULL,
  p0 INTEGER NOT NULL, market INTEGER NOT NULL, oracle INTEGER NOT NULL, rate INTEGER NOT NULL,
  surplus INTEGER NOT NULL, bad_debt INTEGER NOT NULL, psm_usdc INTEGER NOT NULL, psm_forge INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS price_history(tick INTEGER PRIMARY KEY, market INTEGER NOT NULL, oracle INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_vaults_user ON vaults(user_id);
CREATE INDEX IF NOT EXISTS idx_auctions_status ON auctions(status, vault_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn(write=False):
    """write=True takes the write lock up front so read-modify-write sequences can't interleave."""
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        if write:
            c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
