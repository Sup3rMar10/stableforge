"""Price feed simulator. Pure functions of (scenario, step, p0): no clock, no randomness, so a run replays exactly."""
CRASH_STEPS = 24
CRASH_BPS = 4000

# How many ticks behind the market the protocol's oracle runs.
LAG = {"calm": 1, "crash": 1, "oracle_lag": 12}
SCENARIOS = {
    "calm": "Price wobbles within half a percent. Boring on purpose.",
    "crash": "Down 40% over 24 hours; the oracle trails by one hour.",
    "oracle_lag": "Same crash, but the oracle is 12 hours behind the market.",
}


def wobble(step):
    return (step * 7919 % 101) - 50 if step else 0


def market(name, step, p0):
    if name == "calm":
        return p0 * (10000 + wobble(step)) // 10000
    drop = CRASH_BPS * min(step, CRASH_STEPS) // CRASH_STEPS
    return p0 * (10000 - drop + wobble(step) // 5) // 10000


def oracle(name, step, p0):
    return market(name, max(0, step - LAG[name]), p0)
