# StableForge
> 🏦 Print money, but make it collateralized.
## What it does
A tiny MakerDAO-style stablecoin in Python: vaults, stability fees, descending-price liquidation auctions, a peg module, and crash scenarios you can scrub through.
## Run it
`pip install -r requirements.txt`, `python sim.py`, `uvicorn app:app --host 127.0.0.1 --port 8004`, then open localhost:8004 (demo / demo-pass-1234).
## Honest disclaimer ⚠️
Untested prototype. Simulated data, one fake keeper, no real chain. Do not use it to reason about real money. Download and use at your own discretion.
## Things that went wrong while building this
- It was written without ever being run, so expect a typo or two on first launch.
- The crash scenario is a lot more fun to watch than it is to be a vault owner in.

Built with caution and an unreasonable fondness for ratios. Mind the 150% line.
