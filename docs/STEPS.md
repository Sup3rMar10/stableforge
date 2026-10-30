# Build steps and interview Q&A

## Steps

1. Fix the units first: micro-units for amounts, a 12-decimal rate index, basis points for ratios. No floats in `engine.py`.
2. `db.py`: schema, with CHECK constraints as a backstop and a `write=True` mode that takes the lock up front.
3. `feed.py`: scenarios as pure functions of (name, step, start price), so every run replays exactly. The oracle is the market delayed by N ticks.
4. `engine.py` vaults: deposit, withdraw, mint, repay, close. Debt is stored normalized (`art`) and multiplied by the rate index, so one number moves every vault's fee.
5. Stability fee: each tick compounds the index and books the increase as surplus.
6. Liquidation: each tick, vaults under 150% by the oracle become auctions with a 13% penalty on the debt. A linear descending price auction, one simulated keeper buying at 3% under the market, leftover collateral returned, any shortfall recorded as bad debt.
7. PSM: swap USDC and FORGE 1:1 minus a fee, with a ceiling, and a peg estimate that a funded PSM floors.
8. `app.py`: validation, argon2, httpOnly cookie sessions, CSRF token, localhost-only CORS and hosts, rate limits, CSP.
9. `web/`: dashboard, SVG ratio gauge, scenario picker and what-if slider (server computes the preview), liquidation log.
10. `sim.py`: seed a demo user with vaults at 250%, ~180% and ~171%.

Nothing here was run or tested; the rules for this build said not to.

## Interview Q&A

**1. CDP stablecoin vs algorithmic stablecoin?**
A CDP coin like this one is backed by excess collateral that anyone can inspect, and the peg is defended by liquidating weak vaults. An algorithmic coin relies on a reflexive token and market confidence to restore the peg. When confidence breaks there is nothing underneath, which is how Terra/UST failed. CDPs fail more gently: bad debt, not a death spiral, unless the collateral itself collapses.

**2. Why over-collateralize at all?**
Price moves faster than liquidations can happen. The gap between the mint ratio (170%) and the liquidation ratio (150%) is the buffer for users, and the gap between 150% and 100% is the buffer for the protocol, covering the penalty, keeper margin and slippage.

**3. How do liquidation incentives work?**
Keepers need a reason to show up during a crash. They get collateral at a discount (here at least 3% under the market, since the auction price keeps falling until it is attractive), and the vault owner pays a 13% penalty. Too small a discount and nobody bids; too large and liquidations are needlessly punishing. The penalty also pays the protocol, so the system earns on risky vaults.

**4. Why a descending-price (Dutch) auction?**
It needs one action instead of a bidding war: the first keeper who finds the price acceptable takes it. It resolves in a predictable time and works with atomic composability, so keepers do not need to hold FORGE upfront. The cost is that price discovery is only as good as the starting price and decay curve.

**5. What is bad debt and who pays?**
Bad debt is debt that the collateral sale could not cover, because the price fell past the vault before it was liquidated. In this sim it is a counter that surplus (stability fees, penalties, PSM fees) is meant to absorb. Real MakerDAO covers any remainder by minting and auctioning MKR (debt auction), which dilutes MKR holders.

**6. What are the peg mechanisms?**
Arbitrage through the PSM: if FORGE trades under $1, buy it and redeem for USDC; if above, mint it with USDC. The fee sets the band the price can wander in. The stability fee works as a monetary-policy lever (raising it reduces demand for debt) and has no hard guarantee. The PSM's weakness is that it imports USDC risk, and an empty PSM means no floor.

**7. What happened on Black Thursday (12 March 2020)?**
ETH fell by around 40% in a day. The oracle lagged, so liquidations triggered late, and network congestion kept most keepers from bidding. Some auctions were won with zero-DAI bids, leaving several million dollars of bad debt that MKR holders had to cover. The `oracle_lag` scenario is a toy model of the first half of that story. It does not model congestion or zero bids. Maker later moved to Dutch auctions and added safeguards.

**8. Why integer math and a rate index?**
Floats make money rounding unpredictable and non-reproducible. Integers with fixed scales are exact, and every rounding is chosen to favor the protocol (debt rounds up, repayments round down). Storing normalized debt times a global index means accruing interest is one multiplication for the whole system, not a loop over vaults.
