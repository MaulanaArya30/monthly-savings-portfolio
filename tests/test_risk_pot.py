"""Covers src/risk_pot.py — the Risk Pot's pure logic, kept separate from and never
imported by the core sleeve's calculator.py/tracker.py/alerts.py. See RISK_POT_PLAN.md
§4 (event-driven review + two-stage profit-lock, revised 2026-09-02).
"""
from datetime import date

import pytest

from src import risk_pot
from src.models import Transaction

_CONFIG = {
    "breakeven_lock_pct": 0.10,
    "trailing_stop_arm_pct": 0.20,
    "trailing_stop_pct": 0.10,
    "min_review_days": 5,
    "stale_review_days": 21,
}


def _buy(d, ticker, shares, price, note=""):
    return Transaction(date(*d), ticker, "buy", shares, price, shares * price, "IDR", note)


def _sell(d, ticker, shares, price, note=""):
    return Transaction(date(*d), ticker, "sell", shares, price, shares * price, "IDR", note)


# --- stop-loss note encoding ---------------------------------------------------------------

def test_encode_and_extract_stop_loss_roundtrip():
    note = risk_pot.encode_note(5822.5, "gold momentum play")
    assert note == "stop_loss=5822.5 gold momentum play"
    assert risk_pot.extract_stop_loss(note) == pytest.approx(5822.5)


def test_encode_note_with_no_stop_loss_or_note_text():
    assert risk_pot.encode_note(None, "") == ""
    assert risk_pot.extract_stop_loss("") is None


def test_extract_stop_loss_ignores_unrelated_note():
    assert risk_pot.extract_stop_loss("just a plain note") is None


# --- open_tickers / entry_date_for / stop_loss_for ------------------------------------------

def test_open_tickers_excludes_fully_sold_positions():
    txns = [
        _buy((2026, 8, 1), "A", 100, 100),
        _buy((2026, 8, 1), "B", 100, 100),
        _sell((2026, 8, 10), "B", 100, 110),
    ]
    assert risk_pot.open_tickers(txns) == ["A"]


def test_entry_date_for_uses_earliest_buy():
    txns = [
        _buy((2026, 8, 5), "A", 50, 100),
        _buy((2026, 8, 1), "A", 50, 90),  # earlier, out of order in the file
    ]
    assert risk_pot.entry_date_for(txns, "A") == date(2026, 8, 1)


def test_entry_date_for_raises_when_no_buy_exists():
    with pytest.raises(ValueError):
        risk_pot.entry_date_for([], "A")


def test_stop_loss_for_uses_most_recent_buy():
    txns = [
        _buy((2026, 8, 1), "A", 50, 100, "stop_loss=85"),
        _buy((2026, 8, 5), "A", 50, 110, "stop_loss=93.5"),
    ]
    assert risk_pot.stop_loss_for(txns, "A") == pytest.approx(93.5)


def test_stop_loss_for_none_when_never_set():
    txns = [_buy((2026, 8, 1), "A", 100, 100)]
    assert risk_pot.stop_loss_for(txns, "A") is None


# --- last_review_date ------------------------------------------------------------------------

def test_last_review_date_defaults_to_entry():
    assert risk_pot.last_review_date(date(2026, 8, 1), [], "A") == date(2026, 8, 1)


def test_last_review_date_uses_most_recent_extension():
    reviews = [
        (date(2026, 8, 31), "A", "beat benchmark"),
        (date(2026, 9, 15), "B", "unrelated ticker"),
    ]
    assert risk_pot.last_review_date(date(2026, 8, 1), reviews, "A") == date(2026, 8, 31)


def test_last_review_date_never_returns_earlier_than_entry():
    """A stale/out-of-order review row shouldn't push the anchor backward."""
    reviews = [(date(2026, 7, 1), "A", "should not count")]
    assert risk_pot.last_review_date(date(2026, 8, 1), reviews, "A") == date(2026, 8, 1)


# --- pct_return / beats_benchmark / review_needed ---------------------------------------------

def test_pct_return_basic():
    assert risk_pot.pct_return(100, 115) == pytest.approx(15.0)
    assert risk_pot.pct_return(100, 85) == pytest.approx(-15.0)


def test_pct_return_rejects_non_positive_old_price():
    with pytest.raises(ValueError):
        risk_pot.pct_return(0, 100)


def test_beats_benchmark_requires_positive_and_ahead():
    assert risk_pot.beats_benchmark(15.0, 5.0) is True
    assert risk_pot.beats_benchmark(3.0, 5.0) is False  # positive, but trailing the benchmark
    assert risk_pot.beats_benchmark(-2.0, -10.0) is False  # ahead of benchmark, but still negative


def test_review_needed_gated_by_min_review_days():
    # Failing the bar, but too fresh to react to — day-1/day-2 noise floor.
    assert risk_pot.review_needed(-2.0, 5.0, days_since_review=2, min_review_days=5) is False


def test_review_needed_fires_once_min_days_passed_and_bar_fails():
    assert risk_pot.review_needed(-2.0, 5.0, days_since_review=5, min_review_days=5) is True


def test_review_needed_false_when_bar_still_clears():
    assert risk_pot.review_needed(15.0, 5.0, days_since_review=10, min_review_days=5) is False


# --- capital_at_work -----------------------------------------------------------------------

def test_capital_at_work_sums_only_open_positions():
    txns = [
        _buy((2026, 8, 1), "A", 100, 6850),           # open: cost basis 685,000
        _buy((2026, 8, 1), "B", 200, 1500),            # fully closed below
        _sell((2026, 8, 15), "B", 200, 1600),
    ]
    assert risk_pot.capital_at_work(txns) == pytest.approx(685_000)


def test_capital_at_work_zero_when_nothing_open():
    assert risk_pot.capital_at_work([]) == 0.0


# --- effective_stop_loss (the two-stage profit-lock ladder / take-profit mechanism) -----------

def test_effective_stop_loss_none_when_no_base_stop_set():
    assert risk_pot.effective_stop_loss(1000, None, 1150, 1150, 0.10, 0.20, 0.10) is None


def test_effective_stop_loss_stays_at_base_below_breakeven_threshold():
    # Peak +5% — under the 10% breakeven-lock trigger, base stop still governs.
    stop = risk_pot.effective_stop_loss(1000, 850, 1050, 1050, 0.10, 0.20, 0.10)
    assert stop == pytest.approx(850)


def test_effective_stop_loss_locks_breakeven_past_threshold():
    # Peak +10% — breakeven-lock arms, floor rises to avg_cost. Trailing not armed yet.
    stop = risk_pot.effective_stop_loss(1000, 850, 1100, 1100, 0.10, 0.20, 0.10)
    assert stop == pytest.approx(1000)


def test_effective_stop_loss_trails_highest_price_past_arm_threshold():
    # Peak +30% (1300) — trailing-lock arms: 1300 * (1 - 0.10) = 1170.
    stop = risk_pot.effective_stop_loss(1000, 850, 1300, 1300, 0.10, 0.20, 0.10)
    assert stop == pytest.approx(1170)


def test_effective_stop_loss_stays_armed_after_pulling_back_from_peak():
    # Peak was +30% (1300, arming the trailing lock); price has since pulled back to
    # +17% (1170) — a trailing stop must not silently disarm on a pullback, it should
    # still be trailing the 1300 peak, not falling back to breakeven.
    stop = risk_pot.effective_stop_loss(1000, 850, 1300, 1170, 0.10, 0.20, 0.10)
    assert stop == pytest.approx(1170)  # 1300 * (1 - 0.10), unaffected by today's pullback


def test_effective_stop_loss_trailing_never_drops_below_breakeven():
    # Both locks armed (peak +13%, past both the 10% breakeven and 12% trailing arm
    # thresholds here), but a wide 20% trail puts the raw trailing math below breakeven
    # (1130 * (1 - 0.20) = 904 < breakeven 1000) — the max() floor must win.
    stop = risk_pot.effective_stop_loss(1000, 850, 1130, 1130, 0.10, 0.12, 0.20)
    assert stop == pytest.approx(1000)


# --- build_position / RiskPosition -----------------------------------------------------------

def test_build_position_computes_unrealized_pl_and_days():
    txns = [_buy((2026, 8, 1), "A", 100, 1000, "stop_loss=850")]
    pos = risk_pot.build_position(txns, [], "A", current_price=1150,
                                   highest_price_since_entry=1150, config=_CONFIG)

    assert pos.shares == 100
    assert pos.avg_cost == pytest.approx(1000)
    assert pos.cost_basis == pytest.approx(100_000)
    assert pos.value_idr == pytest.approx(115_000)
    assert pos.unrealized_pl == pytest.approx(15_000)
    assert pos.unrealized_pl_pct == pytest.approx(15.0)
    assert pos.stop_loss == pytest.approx(850)
    assert pos.entry_date == date(2026, 8, 1)
    assert pos.review_anchor == date(2026, 8, 1)  # never reviewed


def test_risk_position_stale_backstop():
    pos = risk_pot.build_position(
        [_buy((2026, 8, 1), "A", 100, 1000)], [], "A",
        current_price=1000, highest_price_since_entry=1000, config=_CONFIG,
    )
    assert pos.stale(date(2026, 8, 21), 21) is False   # 20 days
    assert pos.stale(date(2026, 8, 22), 21) is True    # exactly 21 days
    assert pos.days_since_entry(date(2026, 8, 22)) == 21


def test_risk_position_stop_loss_breached():
    pos = risk_pot.build_position(
        [_buy((2026, 8, 1), "A", 100, 1000, "stop_loss=850")], [], "A",
        current_price=849, highest_price_since_entry=1000, config=_CONFIG,
    )
    assert pos.stop_loss_breached() is True


def test_risk_position_stop_loss_not_breached_when_none_set():
    pos = risk_pot.build_position(
        [_buy((2026, 8, 1), "A", 100, 1000)], [], "A",
        current_price=1, highest_price_since_entry=1000, config=_CONFIG,
    )
    assert pos.stop_loss_breached() is False


def test_risk_position_take_profit_via_trailing_lock():
    # Ran up to +30% (peak 1300) then pulled back to the trailing level itself.
    pos = risk_pot.build_position(
        [_buy((2026, 8, 1), "A", 100, 1000, "stop_loss=850")], [], "A",
        current_price=1170, highest_price_since_entry=1300, config=_CONFIG,
    )
    assert pos.effective_stop == pytest.approx(1170)
    assert pos.stop_loss_breached() is True  # exit at/below the trailing floor — banks the gain


def test_build_position_after_review_uses_new_anchor():
    txns = [_buy((2026, 8, 1), "A", 100, 1000)]
    reviews = [(date(2026, 8, 31), "A", "beat benchmark, extended")]
    pos = risk_pot.build_position(txns, reviews, "A", current_price=1000,
                                   highest_price_since_entry=1000, config=_CONFIG)

    assert pos.entry_date == date(2026, 8, 1)
    assert pos.review_anchor == date(2026, 8, 31)
    assert pos.days_since_review(date(2026, 9, 5)) == 5
    assert pos.days_since_entry(date(2026, 9, 5)) == 35


# --- Track B: early special situations with their own exit ladder (RISK_POT_PLAN.md §3.1b) ---

def test_encode_note_track_b_roundtrip():
    note = risk_pot.encode_note(3880.0, "leg-2 breakout", track="B", pivot=4000.0)
    assert note == "stop_loss=3880.0 track=B pivot=4000.0 leg-2 breakout"
    assert risk_pot.extract_stop_loss(note) == 3880.0
    assert risk_pot.extract_track(note) == "B"
    assert risk_pot.extract_pivot(note) == 4000.0


def test_extract_track_defaults_to_a_for_rows_logged_before_tracks_existed():
    old = "stop_loss=401.0 sustained uptrend + 4/5-day foreign accumulation"
    assert risk_pot.extract_track(old) == "A"
    assert risk_pot.extract_pivot(old) is None


def test_extract_track_ignores_lookalike_free_text():
    assert risk_pot.extract_track("stop_loss=100 on the fast track=Bx plan") == "A"


def test_track_b_base_stop_uses_the_tighter_of_pivot_and_hard_floor():
    # just above the pivot: back-inside-the-base (4000 * 0.97) binds
    assert risk_pot.track_b_base_stop(4400, 4000, 0.15, 0.03) == pytest.approx(3880)
    # far above the pivot: the -15% hard floor binds
    assert risk_pot.track_b_base_stop(5000, 4000, 0.15, 0.03) == pytest.approx(4250)


def test_exit_params_track_b_defaults_and_config_override():
    assert risk_pot.exit_params(_CONFIG, "B")["breakeven_lock_pct"] == 0.30
    assert risk_pot.exit_params(_CONFIG, "A")["breakeven_lock_pct"] == 0.10
    cfg = dict(_CONFIG, track_b={"trailing_stop_pct": 0.20})
    params = risk_pot.exit_params(cfg, "B")
    assert params["trailing_stop_pct"] == 0.20
    assert params["trailing_stop_arm_pct"] == 0.50   # untouched default


def test_track_for_uses_most_recent_buy():
    txns = [_buy((2026, 1, 1), "X.JK", 100, 100, "stop_loss=85"),
            _buy((2026, 2, 1), "X.JK", 100, 120, risk_pot.encode_note(110, "", track="B", pivot=112))]
    assert risk_pot.track_for(txns, "X.JK") == "B"
    assert risk_pot.pivot_for(txns, "X.JK") == 112


def test_sini_october_2025_exits_under_track_a_but_holds_under_track_b():
    """The audit case (RISK_POT_PLAN.md §3.22): entry 4,400 on the 3 Oct 2025 breakout
    over a 4,000 pivot, peak 5,675 (+29%), then 4,610 on 21 Oct. The pot-wide ladder had
    armed its breakeven and trailing locks and exits; Track B's own ladder holds (SINI
    later reached 19,200)."""
    track_a = [_buy((2025, 10, 3), "SINI.JK", 100, 4400, "stop_loss=3740")]
    track_b = [_buy((2025, 10, 3), "SINI.JK", 100, 4400,
                    risk_pot.encode_note(3880, "", track="B", pivot=4000))]
    a = risk_pot.build_position(track_a, [], "SINI.JK", current_price=4610,
                                highest_price_since_entry=5675, config=_CONFIG)
    b = risk_pot.build_position(track_b, [], "SINI.JK", current_price=4610,
                                highest_price_since_entry=5675, config=_CONFIG)
    assert a.track == "A" and a.stop_loss_breached()
    assert b.track == "B" and b.pivot == 4000
    assert b.effective_stop == pytest.approx(3880)
    assert not b.stop_loss_breached()


def test_track_b_breakeven_then_wide_trail():
    txns = [_buy((2025, 10, 3), "SINI.JK", 100, 4400,
                 risk_pot.encode_note(3880, "", track="B", pivot=4000))]
    # peak +40%: breakeven armed (>= +30%), trail not yet (< +50%)
    p = risk_pot.build_position(txns, [], "SINI.JK", current_price=5000,
                                highest_price_since_entry=6160, config=_CONFIG)
    assert p.effective_stop == pytest.approx(4400)
    # peak +100%: 25% below the 8,800 peak
    p = risk_pot.build_position(txns, [], "SINI.JK", current_price=7000,
                                highest_price_since_entry=8800, config=_CONFIG)
    assert p.effective_stop == pytest.approx(6600)
    assert not p.stop_loss_breached()
