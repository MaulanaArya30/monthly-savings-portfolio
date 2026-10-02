from datetime import date

import pytest

from src.models import Dividend, HoldingState, Target, Transaction
from src.tracker import _xirr, blended_yield_gross, blended_yield_net, realized_yield_to_date


def make_state(ticker, weight, yield_, tax_rate):
    t = Target(ticker, ticker, weight, "IDR", "yfinance", yield_, "Broker", 100, tax_rate)
    return HoldingState(target=t, shares=0, avg_cost=0, price=0, value_native=0, value_idr=0,
                         weight=weight, drift_pp=0)


def test_blended_yield_gross_and_net():
    states = [make_state("A", 0.6, 0.08, 0.10), make_state("B", 0.4, 0.03, 0.15)]
    gross = blended_yield_gross(states)
    net = blended_yield_net(states)
    assert gross == pytest.approx(0.6 * 0.08 + 0.4 * 0.03)
    assert net == pytest.approx(0.6 * 0.08 * 0.9 + 0.4 * 0.03 * 0.85)
    assert net < gross  # tax always reduces the net figure — this is §7.3's core point,
                          # made structurally impossible to get backwards


def test_realized_yield_to_date():
    txns = [Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR")]
    divs = [Dividend(date(2026, 6, 1), "A", 50, "IDR")]
    assert realized_yield_to_date(divs, txns, fx_usdidr=1) == pytest.approx(50 / 1000)


def test_realized_yield_none_when_nothing_contributed():
    assert realized_yield_to_date([], [], fx_usdidr=1) is None


def test_xirr_simple_known_case():
    # Rp1000 invested, worth Rp1100 exactly 1 year later -> ~10% annualized
    flows = [(date(2026, 1, 1), -1000), (date(2027, 1, 1), 1100)]
    assert _xirr(flows) == pytest.approx(0.10, abs=0.002)


def test_xirr_returns_none_with_no_sign_change():
    # all inflows, no outflow — can't solve a rate of return for this
    flows = [(date(2026, 1, 1), 100), (date(2026, 6, 1), 200)]
    assert _xirr(flows) is None


def test_xirr_returns_none_with_single_cashflow():
    assert _xirr([(date(2026, 1, 1), 100)]) is None


def test_xirr_returns_none_when_all_same_date():
    # Exactly the case hit during live testing: two buys, a small dividend, and the
    # mark-to-market value, all logged on the same day. Every flow's exponent is 0
    # (no time spread), so npv(rate) is constant regardless of rate — same sign at
    # both ends of the search range, no rate can be solved. Net flow is nonzero here
    # (unlike a perfectly balanced net-zero case, which is a different degenerate
    # edge this solver doesn't specially handle — not realistic enough to matter).
    flows = [(date(2026, 1, 1), -1916000), (date(2026, 1, 1), -1272000),
             (date(2026, 1, 1), 45000), (date(2026, 1, 1), 3188000)]
    assert _xirr(flows) is None
