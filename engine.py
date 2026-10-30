"""StableForge protocol simulation. Integer math only, no floats anywhere in here.

Units: amounts are micro-units (1 COL = 1_000_000), prices are micro-FORGE per COL, the stability-fee
rate index has 12 decimals, ratios are basis points. One tick is one simulated hour.
"""
import feed

UNIT = 10**6
RATE = 10**12
MINT_BPS = 17000          # most you can mint against collateral: 170%
LIQ_BPS = 15000           # below this (by the oracle) a vault is auctioned: 150%
PENALTY_BPS = 1300        # liquidation penalty added to the debt: 13%
BUF_BPS = 12000           # auction opens at 120% of the oracle price
AUCTION_TICKS = 24        # price falls linearly to zero over this many ticks
KEEPER_MARGIN_BPS = 300   # the simulated keeper only buys at 3% under the market
FEE_BPS_YEAR = 200
FEE_PER_TICK = RATE * FEE_BPS_YEAR // (10000 * 8760)
DUST = 100 * UNIT
DEBT_CEIL = 5_000_000 * UNIT
PSM_CEIL = 1_000_000 * UNIT
TIN_BPS = 10              # PSM fee on USDC -> FORGE
TOUT_BPS = 10             # PSM fee on FORGE -> USDC
MAX_VAULTS = 8
START_PRICE = 2000 * UNIT
START_COL = 50 * UNIT
START_USDC = 20000 * UNIT
SYS_COLS = ("tick", "step", "scenario", "p0", "market", "oracle", "rate", "surplus", "bad_debt", "psm_usdc", "psm_forge")


class Fault(Exception):
    """A protocol rule said no. The message is safe to show the user."""

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def need(ok, msg, status=400):
    if not ok:
        raise Fault(msg, status)


# ---- math ----------------------------------------------------------------

def debt_of(art, rate):
    return -(-art * rate // RATE)


def ratio_bps(ink, debt, price):
    return None if debt == 0 else ink * price * 10000 // (UNIT * debt)


def covers(ink, debt, price, bps):
    return ink * price * 10000 >= debt * UNIT * bps


def auction_price(a, tick):
    return max(1, a["top"] * max(0, AUCTION_TICKS - (tick - a["start_tick"])) // AUCTION_TICKS)


# ---- state ---------------------------------------------------------------

def boot(c):
    c.execute("INSERT OR IGNORE INTO sys VALUES(1, 0, 0, 'calm', ?, ?, ?, ?, 0, 0, 0, 0)",
              (START_PRICE, START_PRICE, START_PRICE, RATE))
    c.execute("INSERT OR IGNORE INTO price_history VALUES(0, ?, ?)", (START_PRICE, START_PRICE))


def sysrow(c):
    return dict(c.execute("SELECT * FROM sys WHERE id = 1").fetchone())


def save(c, s):
    c.execute("UPDATE sys SET " + ", ".join(f"{k} = :{k}" for k in SYS_COLS) + " WHERE id = 1", s)


def new_wallet(c, uid):
    c.execute("INSERT INTO wallets(user_id, col, forge, usdc) VALUES(?,?,0,?)", (uid, START_COL, START_USDC))


def wallet(c, uid):
    return dict(c.execute("SELECT col, forge, usdc FROM wallets WHERE user_id = ?", (uid,)).fetchone())


def save_wallet(c, uid, w):
    c.execute("UPDATE wallets SET col = ?, forge = ?, usdc = ? WHERE user_id = ?", (w["col"], w["forge"], w["usdc"], uid))


def total_debt(c, rate):
    art = c.execute("SELECT COALESCE(SUM(art), 0) FROM vaults WHERE status = 'open'").fetchone()[0]
    queued = c.execute("SELECT COALESCE(SUM(debt), 0) FROM auctions WHERE status = 'active'").fetchone()[0]
    return debt_of(art, rate) + queued


def peg(c, s):
    """Implied market price of FORGE. Unbacked bad debt pulls it down; a funded PSM puts a floor under it."""
    supply = c.execute("SELECT COALESCE(SUM(forge), 0) FROM wallets").fetchone()[0]
    deficit = max(0, s["bad_debt"] - s["surplus"])
    raw = UNIT * supply // (supply + deficit) if supply else UNIT
    return max(raw, UNIT * (10000 - TOUT_BPS) // 10000) if s["psm_usdc"] > 0 else raw


def view(v, s, price=None):
    price = s["oracle"] if price is None else price
    debt = debt_of(v["art"], s["rate"])
    return {"id": v["id"], "status": v["status"], "ink": v["ink"], "debt": debt,
            "ratio_bps": ratio_bps(v["ink"], debt, price),
            "max_mint": max(0, v["ink"] * price * 10000 // (UNIT * MINT_BPS) - debt),
            "liquidatable": debt > 0 and not covers(v["ink"], debt, price, LIQ_BPS)}


# ---- vaults --------------------------------------------------------------

def open_vault(c, uid):
    s = sysrow(c)
    n = c.execute("SELECT COUNT(*) FROM vaults WHERE user_id = ? AND status = 'open'", (uid,)).fetchone()[0]
    need(n < MAX_VAULTS, f"at most {MAX_VAULTS} open vaults", 409)
    return c.execute("INSERT INTO vaults(user_id, ink, art, status, opened_at) VALUES(?,0,0,'open',?)",
                     (uid, s["tick"])).lastrowid


def vault_op(c, uid, vid, op, amount):
    s = sysrow(c)
    v = c.execute("SELECT * FROM vaults WHERE id = ? AND user_id = ?", (vid, uid)).fetchone()
    need(v, "vault not found", 404)
    need(v["status"] == "open", "vault is closed", 409)
    need(not c.execute("SELECT 1 FROM auctions WHERE vault_id = ? AND status = 'active'", (vid,)).fetchone(),
         "vault is being liquidated", 409)
    w, ink, art, status = wallet(c, uid), v["ink"], v["art"], "open"
    debt = debt_of(art, s["rate"])
    if op == "deposit":
        need(amount <= w["col"], "not enough COL in your wallet")
        ink += amount
        w["col"] -= amount
    elif op == "withdraw":
        need(amount <= ink, "the vault holds less COL than that")
        ink -= amount
        w["col"] += amount
        need(debt == 0 or covers(ink, debt, s["oracle"], MINT_BPS), "that would drop the vault below the 170% minting ratio")
    elif op == "mint":
        need(total_debt(c, s["rate"]) + amount <= DEBT_CEIL, "global debt ceiling reached")
        art += -(-amount * RATE // s["rate"])
        debt = debt_of(art, s["rate"])
        need(covers(ink, debt, s["oracle"], MINT_BPS), "that would drop the vault below the 170% minting ratio")
        need(debt >= DUST, "minimum debt is 100 FORGE")
        w["forge"] += amount
    elif op == "repay":
        pay = min(amount, debt)
        need(pay <= w["forge"], "not enough FORGE in your wallet; buy some at the PSM")
        art -= min(art, pay * RATE // s["rate"])
        left = debt_of(art, s["rate"])
        need(left == 0 or left >= DUST, "leave either no debt or at least 100 FORGE")
        w["forge"] -= pay
    else:
        need(debt <= w["forge"], "not enough FORGE to repay the debt; buy some at the PSM")
        w["forge"] -= debt
        w["col"] += ink
        ink = art = 0
        status = "closed"
    c.execute("UPDATE vaults SET ink = ?, art = ?, status = ? WHERE id = ?", (ink, art, status, vid))
    save_wallet(c, uid, w)


# ---- peg stability module ------------------------------------------------

def psm_swap(c, uid, side, amount):
    s, w = sysrow(c), None
    w = wallet(c, uid)
    fee = amount * (TIN_BPS if side == "buy" else TOUT_BPS) // 10000
    out = amount - fee
    if side == "buy":
        need(amount <= w["usdc"], "not enough USDC")
        need(s["psm_forge"] + out <= PSM_CEIL, "PSM ceiling reached")
        w["usdc"] -= amount
        w["forge"] += out
        s["psm_usdc"] += amount
        s["psm_forge"] += out
    else:
        need(amount <= w["forge"], "not enough FORGE")
        need(out <= s["psm_usdc"], "the PSM is out of USDC")
        w["forge"] -= amount
        w["usdc"] += out
        s["psm_usdc"] -= out
        s["psm_forge"] = max(0, s["psm_forge"] - out)
    s["surplus"] += fee
    save(c, s)
    save_wallet(c, uid, w)


# ---- simulation ----------------------------------------------------------

def set_scenario(c, name):
    need(name in feed.SCENARIOS, "unknown scenario")
    s = sysrow(c)
    s["scenario"], s["step"], s["p0"] = name, 0, s["market"]
    s["oracle"] = feed.oracle(name, 0, s["p0"])
    save(c, s)


def advance(c, hours):
    for _ in range(hours):
        tick(c)


def tick(c):
    s = sysrow(c)
    s["tick"] += 1
    s["step"] += 1
    s["market"] = feed.market(s["scenario"], s["step"], s["p0"])
    s["oracle"] = feed.oracle(s["scenario"], s["step"], s["p0"])
    rate = s["rate"] * (RATE + FEE_PER_TICK) // RATE
    art = c.execute("SELECT COALESCE(SUM(art), 0) FROM vaults WHERE status = 'open'").fetchone()[0]
    s["surplus"] += art * (rate - s["rate"]) // RATE
    s["rate"] = rate
    c.execute("INSERT OR REPLACE INTO price_history VALUES(?,?,?)", (s["tick"], s["market"], s["oracle"]))
    c.execute("DELETE FROM price_history WHERE tick < ?", (s["tick"] - 500,))
    bark(c, s)
    for a in c.execute("SELECT * FROM auctions WHERE status = 'active' ORDER BY id").fetchall():
        take(c, s, dict(a))
    save(c, s)


def bark(c, s):
    """Seize every vault the oracle says is under 150% and put its collateral up for auction."""
    for v in c.execute("SELECT * FROM vaults WHERE status = 'open' AND art > 0").fetchall():
        debt = debt_of(v["art"], s["rate"])
        if covers(v["ink"], debt, s["oracle"], LIQ_BPS):
            continue
        c.execute("INSERT INTO auctions(vault_id, lot, debt, tab, top, start_tick, status) VALUES(?,?,?,?,?,?,'active')",
                  (v["id"], v["ink"], debt, debt + debt * PENALTY_BPS // 10000, s["oracle"] * BUF_BPS // 10000, s["tick"]))
        c.execute("UPDATE vaults SET ink = 0, art = 0 WHERE id = ?", (v["id"],))


def take(c, s, a):
    """The simulated keeper buys once the falling auction price is 3% under the market. One purchase ends the auction."""
    price = auction_price(a, s["tick"])
    if price > s["market"] * (10000 - KEEPER_MARGIN_BPS) // 10000:
        return
    sold = min(a["lot"], -(-a["tab"] * UNIT // price))
    paid = min(a["tab"], sold * price // UNIT)
    left = a["lot"] - sold
    covered = min(paid, a["debt"])
    s["surplus"] += paid - covered
    s["bad_debt"] += a["debt"] - covered
    c.execute("UPDATE auctions SET status = 'done', end_tick = ?, price = ?, paid = ?, returned = ?, bad_debt = ?, "
              "keeper_profit = ? WHERE id = ?",
              (s["tick"], price, paid, left, a["debt"] - covered, sold * s["market"] // UNIT - paid, a["id"]))
    if left:
        c.execute("UPDATE vaults SET ink = ink + ? WHERE id = ?", (left, a["vault_id"]))


def preview(c, uid, shock_bps):
    s = sysrow(c)
    price = s["oracle"] * (10000 + shock_bps) // 10000
    rows = c.execute("SELECT * FROM vaults WHERE user_id = ? AND status = 'open' ORDER BY id", (uid,)).fetchall()
    return {"price": price, "vaults": [view(dict(v), s, price) for v in rows]}
