"""Portfolio status: value, weight, drift, blended yield (gross + net), realized
yield-to-date, and annualized total return — see BUILD_PLAN.md's tracker.py section.
"""
from dataclasses import dataclass
from datetime import date as Date

from src.calculator import compute_holdings_state, to_idr


def blended_yield_gross(states) -> float:
    return sum(s.weight * s.target.researched_yield for s in states)


def blended_yield_net(states) -> float:
    return sum(s.weight * s.target.researched_yield * (1 - s.target.dividend_tax_rate) for s in states)


def realized_yield_to_date(dividends, transactions, fx_usdidr):
    """Yield-on-cost approximation: total dividends received (IDR) / total capital
    contributed to date (IDR). Not time-weighted — a deliberate simplification that's
    adequate for an early-stage portfolio (see BUILD_PLAN.md); revisit only if it turns
    out to matter once there's years of history.
    """
    total_div_idr = sum(to_idr(d.amount, d.currency, fx_usdidr) for d in dividends)
    total_contributed_idr = sum(
        to_idr(t.amount, t.currency, fx_usdidr) for t in transactions if t.type == "buy"
    )
    if total_contributed_idr <= 0:
        return None
    return total_div_idr / total_contributed_idr


def _xirr(cashflows):
    """cashflows: [(date, amount_idr), ...] — negative = money out, positive = money in.
    Dependency-free bisection solver for the standard XIRR equation. Returns None if
    there's no clean sign change to solve against (too little history, or every
    cashflow happens to be the same sign).
    """
    if len(cashflows) < 2:
        return None
    cashflows = sorted(cashflows, key=lambda cf: cf[0])
    t0 = cashflows[0][0]

    def npv(rate):
        return sum(amt / ((1 + rate) ** ((d - t0).days / 365.0)) for d, amt in cashflows)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if (f_lo > 0) == (f_mid > 0):
            lo, f_lo = mid, f_mid
        else:
            hi, f_hi = mid, f_mid
    return (lo + hi) / 2


def annualized_total_return(transactions, dividends, states, fx_usdidr):
    """Money-weighted return (XIRR) — answers PLAN.md §7.5's 7%/10-15% target directly.
    Each buy is an outflow on its actual date, each sell/dividend an inflow on its
    actual date, and today's mark-to-market value is a final inflow. Returns
    (rate_or_None, is_reliable) — is_reliable is False until ~12 months of history
    exist, an XIRR from a few weeks of contributions swings wildly on small price moves.

    Known simplification: transactions logged in USD (KO) are converted to IDR using
    TODAY's FX rate, not the rate on that transaction's actual date (which isn't
    stored). USDIDR doesn't move drastically day to day, so this is a reasonable
    approximation, not an exact one — worth knowing if the number looks slightly off.
    """
    if not transactions:
        return None, False

    flows = []
    for t in transactions:
        amt_idr = to_idr(t.amount, t.currency, fx_usdidr)
        flows.append((t.date, -amt_idr if t.type == "buy" else amt_idr))
    for d in dividends:
        flows.append((d.date, to_idr(d.amount, d.currency, fx_usdidr)))

    current_value_idr = sum(s.value_idr for s in states)
    flows.append((Date.today(), current_value_idr))

    rate = _xirr(flows)
    earliest = min(t.date for t in transactions)
    is_reliable = (Date.today() - earliest).days >= 365
    return rate, is_reliable


@dataclass
class StatusReport:
    states: list
    total_value_idr: float
    net_contributed_idr: float
    unrealized_gain_idr: float
    yield_gross: float
    yield_net: float
    realized_yield: object
    total_return_xirr: object
    total_return_reliable: bool
    drifted: list


def build_status_report(targets, rebalance_band_pp, transactions, dividends, prices_by_ticker, fx_usdidr):
    states = compute_holdings_state(targets, transactions, prices_by_ticker, fx_usdidr)
    total_value_idr = sum(s.value_idr for s in states)

    total_bought_idr = sum(to_idr(t.amount, t.currency, fx_usdidr) for t in transactions if t.type == "buy")
    total_sold_idr = sum(to_idr(t.amount, t.currency, fx_usdidr) for t in transactions if t.type == "sell")
    net_contributed_idr = total_bought_idr - total_sold_idr

    rate, reliable = annualized_total_return(transactions, dividends, states, fx_usdidr)

    return StatusReport(
        states=states,
        total_value_idr=total_value_idr,
        net_contributed_idr=net_contributed_idr,
        unrealized_gain_idr=total_value_idr - net_contributed_idr,
        yield_gross=blended_yield_gross(states),
        yield_net=blended_yield_net(states),
        realized_yield=realized_yield_to_date(dividends, transactions, fx_usdidr),
        total_return_xirr=rate,
        total_return_reliable=reliable,
        drifted=[s for s in states if abs(s.drift_pp) > rebalance_band_pp],
    )
