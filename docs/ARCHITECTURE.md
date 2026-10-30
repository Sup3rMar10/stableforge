# StableForge architecture

Local-only simulation of an over-collateralized stablecoin (FORGE) backed by one simulated asset (COL). No real chain, keys, RPC or network calls: "chain data" is rows in SQLite and prices come from `feed.py`.

```
 browser (web/: HTML + vanilla JS + CSS, no build)
   |  fetch, httpOnly cookie + X-CSRF-Token
   v
 app.py  FastAPI: validation, auth, CSRF, rate limits, CSP, CORS = localhost only
   |-- engine.py  protocol rules, integer math      --reads--> feed.py (scenario prices)
   |      vaults | stability fee index | liquidation + Dutch auction | PSM | simulated keeper
   v
 db.py  SQLite (stableforge.db)   <--- sim.py seeds a demo user and three vaults

 tick (1 simulated hour):  feed -> market + oracle price
                           -> drip rate index, book fees as surplus
                           -> bark: vaults under 150% (by oracle) become auctions
                           -> keeper: buys when auction price <= market - 3%
```

## Rules in one place (all in `engine.py`)

| Parameter | Value |
|---|---|
| Mint ratio / liquidation ratio | 170% / 150% |
| Stability fee | 2% per year, compounding hourly through a 12-decimal rate index |
| Liquidation penalty | 13% added to the debt (the auction `tab`) |
| Auction | opens at 120% of oracle price, falls linearly to ~0 over 24 ticks |
| Keeper | one purchase, only at 3% or more under the market |
| Dust / debt ceiling / PSM ceiling | 100 / 5,000,000 / 1,000,000 FORGE |
| PSM fee | 0.10% each way |
| Units | micro-units (1 COL = 1,000,000), prices in micro-FORGE per COL, ratios in basis points |

## API

All routes are JSON. Everything except register/login needs the session cookie; every POST also needs `X-CSRF-Token` (returned by login/register/me). Faults return `{"detail": "..."}`.

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | /api/register | username, password | Create user + starter wallet (50 COL, 20,000 USDC); rate limited |
| POST | /api/login | username, password | Start session; rate limited |
| POST | /api/logout | - | End session |
| GET | /api/me | - | Current user + CSRF token |
| GET | /api/state | - | Prices, rate index, surplus, bad debt, peg, wallet, history, parameters |
| GET | /api/vaults | - | Your open vaults with ratio and max mint |
| POST | /api/vaults | - | Open an empty vault (max 8) |
| POST | /api/vaults/{id}/{op} | amount (micro-units) | op = deposit, withdraw, mint, repay, close |
| POST | /api/psm | side (buy/sell), amount | Swap USDC and FORGE at the PSM |
| GET | /api/auctions | - | Last 50 liquidations, live and settled |
| GET | /api/preview?shock_bps= | - | Your vault ratios at oracle price moved by -90%..+50% |
| POST | /api/sim/scenario | name (calm, crash, oracle_lag) | Switch price scenario (shared, local) |
| POST | /api/sim/advance | hours (1-72) | Run that many ticks |

## Database schema

```
users(id, username UNIQUE, pw_hash, created_at)
sessions(token_hash PK, user_id -> users, csrf, expires_at)
wallets(user_id PK -> users, col, forge, usdc)              CHECK >= 0
vaults(id, user_id -> users, ink, art, status open|closed, opened_at)
        ink = COL held; art = normalized debt (debt = ceil(art * rate / 1e12))
auctions(id, vault_id -> vaults, lot, debt, tab, top, start_tick,
         status active|done, end_tick, price, paid, returned, bad_debt, keeper_profit)
sys(id = 1, tick, step, scenario, p0, market, oracle, rate,
    surplus, bad_debt, psm_usdc, psm_forge)                 single global state row
price_history(tick PK, market, oracle)                      last ~500 ticks
```

## Simplifications

- Fees are booked to `surplus` as the index moves; the FORGE for them is not minted until someone repays, so total supply is not reconciled against debt.
- The simulation state is global: every user on the instance shares one clock and one price.
- One keeper, one purchase per auction, no competition, no gas, no congestion.
- Surplus is never auctioned and bad debt is never covered by a debt auction; both are just counters.
