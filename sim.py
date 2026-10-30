"""Seed script: wipes the local database and builds a demo user with three vaults at different risk levels.

    python sim.py
"""
import os

from argon2 import PasswordHasher

import db
import engine

DEMO = ("demo", "demo-pass-1234")   # local-only demo login, documented in the README


def main():
    for suffix in ("", "-journal", "-wal", "-shm"):
        if os.path.exists(db.DB_PATH + suffix):
            os.remove(db.DB_PATH + suffix)
    db.init()
    with db.conn(write=True) as c:
        engine.boot(c)
        uid = c.execute("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                        (DEMO[0], PasswordHasher().hash(DEMO[1]), db.now())).lastrowid
        engine.new_wallet(c, uid)
        # 10 COL each at $2000 is $20,000 of collateral: 250%, ~180% and ~171% against the 150% liquidation line.
        for debt_forge in (8000, 11100, 11700):
            vid = engine.open_vault(c, uid)
            engine.vault_op(c, uid, vid, "deposit", 10 * engine.UNIT)
            engine.vault_op(c, uid, vid, "mint", debt_forge * engine.UNIT)
    print(f"seeded {db.DB_PATH}: log in as {DEMO[0]} / {DEMO[1]}")


if __name__ == "__main__":
    main()
