"""Holding valuation (shared by `status` and `buy`) and the monthly buy calculator.

Implements BUILD_PLAN.md's Core Logic section step by step — see the numbered comments,
they match the numbered steps in that document so the two can be read side by side.
"""
import math
from dataclasses import dataclass, field, replace

from src.models import HoldingState, Target, Transaction


def net_shares(transactions: list[Transaction], ticker: str) -> float:
    """Current shares held = Σ(buy shares) − Σ(sell shares). The only source of truth —
    never derived from any past calculator suggestion, only from what was actually logged.
    """
    total = 0.0
    for t in transactions:
        if t.ticker != ticker:
            continue
        total += t.shares if t.type == "buy" else -t.shares
    return total


def avg_cost_basis(transactions: list[Transaction], ticker: str) -> float:
    """Weighted average price of all BUYS to date for this ticker — used for realized
    gain/loss on a sell. Average cost, not FIFO/LIFO (BUILD_PLAN.md: adequate for
    tracking portfolio health, not tax-grade lot accounting).
    """
    total_shares, total_cost = 0.0, 0.0
    for t in transactions:
        if t.ticker != ticker or t.type != "buy":
            continue
        total_shares += t.shares
        total_cost += t.shares * t.price
    return (total_cost / total_shares) if total_shares > 0 else 0.0


def to_idr(amount_native: float, currency: str, fx_usdidr: float) -> float:
    if currency == "IDR":
        return amount_native
    if currency == "USD":
        return amount_native * fx_usdidr
    raise ValueError(f"Unsupported currency {currency!r} — only IDR/USD are handled")


def compute_holdings_state(
    targets: list[Target],
    transactions: list[Transaction],
    prices_by_ticker: dict,
    fx_usdidr: float,
) -> list[HoldingState]:
    """Step 1-2 of the calculator: current value/weight/drift per holding, purely from
    transactions.csv + live prices. This is what both `status` and `buy` build on.
    """
    raw = []
    total_value_idr = 0.0
    for t in targets:
        shares = net_shares(transactions, t.ticker)
        price = prices_by_ticker[t.ticker]
        value_native = shares * price
        value_idr = to_idr(value_native, t.currency, fx_usdidr)
        raw.append((t, shares, price, value_native, value_idr))
        total_value_idr += value_idr

    states = []
    for t, shares, price, value_native, value_idr in raw:
        weight = (value_idr / total_value_idr) if total_value_idr > 0 else 0.0
        drift_pp = (weight - t.target_weight) * 100
        states.append(HoldingState(
            target=t, shares=shares, avg_cost=avg_cost_basis(transactions, t.ticker),
            price=price, value_native=value_native, value_idr=value_idr,
            weight=weight, drift_pp=drift_pp,
        ))
    return states


@dataclass
class BuyLine:
    ticker: str
    name: str
    broker: str
    currency: str
    raw_target_idr: float
    price_native: float
    lot_size: int
    lot_value_idr: float
    units_to_buy: float       # shares/lots-worth of units, in native share count
    spend_idr: float
    spend_native: float
    leftover_idr: float
    below_min_lot: bool
    topped_up: bool = False        # True if funded via the leftover top-up pass below
    topup_amount_idr: float = 0.0  # how much of the 1 lot came from OTHER holdings' leftover


def calculate_buy(
    targets: list[Target],
    rebalance_band_pp: int,
    transactions: list[Transaction],
    contribution_idr: float,
    prices_by_ticker: dict,
    fx_usdidr: float,
) -> tuple[list[BuyLine], list[HoldingState]]:
    states = compute_holdings_state(targets, transactions, prices_by_ticker, fx_usdidr)
    by_ticker = {s.target.ticker: s for s in states}
    total_value_idr = sum(s.value_idr for s in states)

    # Step 3-4: plain proportional split, unless something's outside the rebalance band.
    any_outside = any(abs(s.drift_pp) > rebalance_band_pp for s in states)
    raw_target_idr = {}

    if not any_outside:
        for t in targets:
            raw_target_idr[t.ticker] = contribution_idr * t.target_weight
    else:
        new_total = total_value_idr + contribution_idr
        gaps = {}
        for t in targets:
            ideal_value = new_total * t.target_weight
            gaps[t.ticker] = max(0.0, ideal_value - by_ticker[t.ticker].value_idr)
        total_gap = sum(gaps.values())

        if total_gap <= contribution_idr:
            leftover = contribution_idr - total_gap
            for t in targets:
                raw_target_idr[t.ticker] = gaps[t.ticker] + leftover * t.target_weight
        else:
            for t in targets:
                raw_target_idr[t.ticker] = (gaps[t.ticker] / total_gap) * contribution_idr if total_gap > 0 else 0.0

    # Step 5-6: floor to whole lots, track leftover explicitly, flag below-1-lot cases.
    lines = []
    for t in targets:
        price_native = prices_by_ticker[t.ticker]
        price_idr = to_idr(price_native, t.currency, fx_usdidr)
        lot_value_idr = price_idr * t.lot_size
        target_idr = raw_target_idr[t.ticker]

        if lot_value_idr <= 0:
            n_lots, spend_idr = 0, 0.0
        else:
            n_lots = math.floor(target_idr / lot_value_idr + 1e-9)
            spend_idr = n_lots * lot_value_idr

        units_to_buy = n_lots * t.lot_size
        spend_native = units_to_buy * price_native
        leftover_idr = target_idr - spend_idr
        below_min_lot = n_lots == 0 and target_idr > 0

        lines.append(BuyLine(
            ticker=t.ticker, name=t.name, broker=t.broker, currency=t.currency,
            raw_target_idr=target_idr, price_native=price_native, lot_size=t.lot_size,
            lot_value_idr=lot_value_idr, units_to_buy=units_to_buy, spend_idr=spend_idr,
            spend_native=spend_native, leftover_idr=leftover_idr, below_min_lot=below_min_lot,
        ))

    # Step 7: leftover top-up. Left alone, a holding whose own target can't afford even
    # 1 lot buys nothing and carries the whole target forward (BUILD_PLAN.md §6's
    # original design — it self-corrects next month via the drift-redirect step above).
    # But if the OTHER holdings' combined leftover — their own in-lot residue, or
    # another below-min-lot holding's unspent target — actually covers the gap to
    # exactly 1 lot, fund it from that shared pool instead of leaving the cash idle:
    # no holding's own target ever gets reduced to pay for this, it only redirects cash
    # that would otherwise sit unspent this month. Capped at exactly 1 lot (never more,
    # even if the pool could afford it — this closes the "can't afford even 1" gap, it
    # doesn't chase further overweighting). When more than one holding is below minimum
    # and the pool can't cover all of them, the smallest gap is funded first so the pool
    # stretches across as many holdings as it can.
    pool = sum(ln.leftover_idr for ln in lines)
    below_min = sorted((ln for ln in lines if ln.below_min_lot), key=lambda ln: ln.lot_value_idr - ln.raw_target_idr)
    for ln in below_min:
        if ln.lot_value_idr <= pool + 1e-6:
            pool -= ln.lot_value_idr
            ln.topup_amount_idr = ln.lot_value_idr - ln.raw_target_idr
            ln.units_to_buy = ln.lot_size
            ln.spend_idr = ln.lot_value_idr
            ln.spend_native = ln.lot_size * ln.price_native
            ln.leftover_idr = ln.raw_target_idr - ln.lot_value_idr
            ln.below_min_lot = False
            ln.topped_up = True

    return lines, states


def redistribute_target_weight(targets: list[Target], ticker: str, new_weight: float) -> list[Target]:
    """Used by `sav set-target` for a conscious Tier 3/4 reallocation (PLAN.md §8.5).

    Changes one holding's target weight and spreads the delta across every other
    holding in proportion to their *current* weights, so their relative sizes to each
    other don't change — e.g. trimming one holding by 5pp with two others left doesn't
    silently favor whichever one happens to be listed first.

    Deliberately does NOT compute this weight itself from alert history — PLAN.md §8.5
    requires the full §8.1 scorecard (including Tier C qualitative research) before a
    Trim/Exit decision, which is not data this function has. The new_weight is always
    a human decision, passed in.

    Returns new Target objects (originals untouched). Raises ValueError — never
    silently clamps or normalizes — if the result would be invalid.
    """
    old_weights = {t.ticker: t.target_weight for t in targets}
    if ticker not in old_weights:
        raise ValueError(f"Unknown ticker {ticker!r}")
    if not (0.0 <= new_weight <= 1.0):
        raise ValueError(f"new_weight must be between 0 and 1 (got {new_weight})")

    others = [t for t in targets if t.ticker != ticker]
    if not others:
        raise ValueError("Only one holding on record — nothing to redistribute to")

    others_old_total = sum(old_weights[t.ticker] for t in others)
    delta = old_weights[ticker] - new_weight  # weight being freed up (or absorbed, if negative)

    if others_old_total <= 1e-9 and abs(delta) > 1e-9:
        raise ValueError("Every other holding is already at 0% weight — can't redistribute")

    result = []
    for t in targets:
        if t.ticker == ticker:
            w = new_weight
        else:
            w = old_weights[t.ticker] + delta * (old_weights[t.ticker] / others_old_total)
        if w < -1e-9:
            raise ValueError(f"Resulting weight for {t.ticker} would go negative ({w:.4f})")
        result.append(replace(t, target_weight=w))
    return result


def preview_reallocation_scenarios(
    targets: list[Target], ticker: str, reduction_steps_pp=(5.0, 10.0)
) -> list[tuple[str, list[Target]]]:
    """Illustrative-only "what if" scenarios for `sav alerts`' impact preview and
    `sav set-target --preview` — shows the ripple effect of a hypothetical trim on
    every holding's weight, without writing anything. Never a suggestion of the
    "correct" size: the step sizes are arbitrary round numbers chosen purely so
    scenarios are comparable to each other, not derived from any severity formula
    (see redistribute_target_weight's docstring for why this tool never computes a
    reallocation number from alert data alone — that's always a human decision).
    """
    current = next((t for t in targets if t.ticker == ticker), None)
    if current is None:
        raise ValueError(f"Unknown ticker {ticker!r}")
    current_weight = current.target_weight

    scenarios = []
    seen_weights = {round(current_weight, 6)}
    for step_pp in reduction_steps_pp:
        new_weight = round(max(0.0, current_weight - step_pp / 100.0), 6)
        if new_weight in seen_weights:
            continue  # already at/below this step (or duplicates an earlier step) — skip
        seen_weights.add(new_weight)
        scenarios.append((f"-{step_pp:.0f}pp", redistribute_target_weight(targets, ticker, new_weight)))
    if 0.0 not in seen_weights:
        scenarios.append(("full exit (0%)", redistribute_target_weight(targets, ticker, 0.0)))
    return scenarios


def temp_rest_balance(log_rows: list[tuple]) -> float:
    """Sum of deposits minus redemptions from data/temp_rest_log.csv. A plain ledger
    balance, not a live NAV — no price API covers Indonesian money-market funds, so no
    yield is assumed or estimated here. The real current value (ledger balance plus
    whatever the fund has quietly accrued) lives at your broker, not in this number.
    """
    balance = 0.0
    for _, action, amount, _ in log_rows:
        balance += amount if action == "deposit" else -amount
    return balance
