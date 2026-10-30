"""StableForge API. Local only: meant for 127.0.0.1:8004, talks to nobody."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import engine
import feed

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
COOKIE = "sf_session"
TTL = 8 * 3600
MAX_BODY = 4 * 1024
ORIGINS = ["http://localhost:8004", "http://127.0.0.1:8004"]
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")

ph = PasswordHasher()
DUMMY = ph.hash("not-a-real-password")
db.init()
with db.conn() as _c:
    engine.boot(_c)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-CSRF-Token"])


@app.middleware("http")
async def harden(request: Request, call_next):
    size = request.headers.get("content-length", "0")
    if size.isascii() and size.isdigit() and int(size) > MAX_BODY:
        return JSONResponse({"detail": "payload too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(engine.Fault)
def fault(request: Request, exc: engine.Fault):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


class Creds(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,32}$")
    password: str = Field(min_length=10, max_length=128)


class Amount(BaseModel):
    amount: int = Field(0, ge=0, le=10**13)


class Swap(BaseModel):
    side: Literal["buy", "sell"]
    amount: int = Field(ge=1, le=10**13)


class Scenario(BaseModel):
    name: Literal["calm", "crash", "oracle_lag"]


class Hours(BaseModel):
    hours: int = Field(ge=1, le=72)


# ---- auth plumbing -------------------------------------------------------

_hits = defaultdict(deque)


def throttle(key, limit, window=60):
    q, now = _hits[key], time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "too many requests, wait a minute", headers={"Retry-After": str(window)})
    q.append(now)


def ip_of(request: Request):
    return request.client.host if request.client else "?"


def digest(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def open_session(c, uid, response):
    tok, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    c.execute("INSERT INTO sessions(token_hash, user_id, csrf, expires_at) VALUES(?,?,?,?)",
              (digest(tok), uid, csrf, int(time.time()) + TTL))
    # No Secure flag on purpose: this only ever runs on plain-http localhost.
    response.set_cookie(COOKIE, tok, max_age=TTL, httponly=True, samesite="strict", path="/")
    return csrf


def current(request: Request):
    tok, row = request.cookies.get(COOKIE), None
    if tok:
        with db.conn() as c:
            row = c.execute("SELECT u.id, u.username, s.csrf, s.expires_at FROM sessions s "
                            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (digest(tok),)).fetchone()
    if not row or row["expires_at"] < time.time():
        raise HTTPException(401, "not logged in")
    return dict(row)


def mutating(request: Request, user=Depends(current)):
    sent = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(sent.encode(), user["csrf"].encode()):
        raise HTTPException(403, "bad CSRF token")
    origin = request.headers.get("origin")
    if origin and origin not in ORIGINS:
        raise HTTPException(403, "bad origin")
    throttle(f"mut:{user['id']}", 120)
    return user


@app.post("/api/register")
def register(body: Creds, request: Request, response: Response):
    throttle("auth:" + ip_of(request), 8)
    with db.conn(write=True) as c:
        try:
            uid = c.execute("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                            (body.username, ph.hash(body.password), db.now())).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "username taken")
        engine.new_wallet(c, uid)
        csrf = open_session(c, uid, response)
    return {"username": body.username, "csrf": csrf}


@app.post("/api/login")
def login(body: Creds, request: Request, response: Response):
    throttle("auth:" + ip_of(request), 8)
    with db.conn() as c:
        u = c.execute("SELECT id, pw_hash FROM users WHERE username = ?", (body.username,)).fetchone()
        try:
            ph.verify(u["pw_hash"] if u else DUMMY, body.password)
            ok = u is not None
        except (VerificationError, InvalidHashError):
            ok = False
        if not ok:
            raise HTTPException(401, "wrong username or password")
        csrf = open_session(c, u["id"], response)
    return {"username": body.username, "csrf": csrf}


@app.post("/api/logout")
def logout(request: Request, response: Response, user=Depends(mutating)):
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(request.cookies[COOKIE]),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current)):
    return {"username": user["username"], "csrf": user["csrf"]}


# ---- protocol ------------------------------------------------------------

@app.get("/api/state")
def state(user=Depends(current)):
    with db.conn() as c:
        s = engine.sysrow(c)
        hist = c.execute("SELECT tick, market, oracle FROM price_history ORDER BY tick DESC LIMIT 120").fetchall()
        return {**s, "peg": engine.peg(c, s), "wallet": engine.wallet(c, user["id"]),
                "total_debt": engine.total_debt(c, s["rate"]),
                "history": [dict(h) for h in reversed(hist)],
                "scenarios": feed.SCENARIOS,
                "params": {"mint_bps": engine.MINT_BPS, "liq_bps": engine.LIQ_BPS, "penalty_bps": engine.PENALTY_BPS,
                           "fee_bps_year": engine.FEE_BPS_YEAR, "tin_bps": engine.TIN_BPS, "tout_bps": engine.TOUT_BPS,
                           "dust": engine.DUST, "unit": engine.UNIT}}


@app.get("/api/vaults")
def vaults(user=Depends(current)):
    with db.conn() as c:
        s = engine.sysrow(c)
        rows = c.execute("SELECT * FROM vaults WHERE user_id = ? AND status = 'open' ORDER BY id", (user["id"],)).fetchall()
        return [engine.view(dict(v), s) for v in rows]


@app.post("/api/vaults")
def open_vault(user=Depends(mutating)):
    with db.conn(write=True) as c:
        return {"id": engine.open_vault(c, user["id"])}


@app.post("/api/vaults/{vid}/{op}")
def vault_op(vid: int, op: Literal["deposit", "withdraw", "mint", "repay", "close"], body: Amount, user=Depends(mutating)):
    if op != "close" and body.amount < 1:
        raise HTTPException(422, "amount must be at least 1")
    with db.conn(write=True) as c:
        engine.vault_op(c, user["id"], vid, op, body.amount)
    return {"ok": True}


@app.post("/api/psm")
def psm(body: Swap, user=Depends(mutating)):
    with db.conn(write=True) as c:
        engine.psm_swap(c, user["id"], body.side, body.amount)
    return {"ok": True}


@app.get("/api/auctions")
def auctions(user=Depends(current)):
    with db.conn() as c:
        s = engine.sysrow(c)
        rows = c.execute("SELECT a.*, v.user_id FROM auctions a JOIN vaults v ON v.id = a.vault_id "
                         "ORDER BY a.id DESC LIMIT 50").fetchall()
    out = []
    for r in rows:
        a = dict(r)
        a["mine"] = a.pop("user_id") == user["id"]
        a["price_now"] = engine.auction_price(a, s["tick"]) if a["status"] == "active" else None
        out.append(a)
    return out


@app.get("/api/preview")
def preview(shock_bps: int = Query(0, ge=-9000, le=5000), user=Depends(current)):
    with db.conn() as c:
        return engine.preview(c, user["id"], shock_bps)


# ---- simulation controls (shared by everyone on this local instance) -----

@app.post("/api/sim/scenario")
def sim_scenario(body: Scenario, user=Depends(mutating)):
    with db.conn(write=True) as c:
        engine.set_scenario(c, body.name)
    return {"ok": True}


@app.post("/api/sim/advance")
def sim_advance(body: Hours, user=Depends(mutating)):
    with db.conn(write=True) as c:
        engine.advance(c, body.hours)
    return {"ok": True}


# ---- frontend ------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/static", StaticFiles(directory=WEB), name="static")
