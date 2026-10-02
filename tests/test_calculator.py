"""Covers the money-math that was hand-verified during the build session — locking
that correctness in so future changes (e.g. adding gold) can't silently break it.
"""
from datetime import date

import pytest

from src.calculator import (
    avg_cost_basis, calculate_buy, compute_holdings_state, net_shares,
    preview_reallocation_scenarios, redistribute_target_weight, temp_rest_balance, to_idr,
)
from src.models import Target, Transaction


def test_net_shares_buy_and_sell():
    txns = [
        Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR"),
        Transaction(date(2026, 2, 1), "A", "buy", 50, 12, 600, "IDR"),
        Transaction(date(2026, 3, 1), "A", "sell", 30, 15, 450, "IDR"),
    ]
    assert net_shares(txns, "A") == 120
    assert net_shares(txns, "B") == 0


def test_avg_cost_basis_weighted_across_buys():
    txns = [
        Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR"),
        Transaction(date(2026, 2, 1), "A", "buy", 100, 20, 2000, "IDR"),
    ]
    assert avg_cost_basis(txns, "A") == pytest.approx(15.0)


def test_avg_cost_basis_ignores_sells():
    txns = [
        Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR"),
        Transaction(date(2026, 2, 1), "A", "sell", 50, 999, 49950, "IDR"),
    ]
    assert avg_cost_basis(txns, "A") == pytest.approx(10.0)


def test_to_idr_conversion():
    assert to_idr(100, "IDR", 15000) == 100
    assert to_idr(10, "USD", 15000) == 150000


def test_to_idr_rejects_unknown_currency():
    with pytest.raises(ValueError):
        to_idr(10, "EUR", 15000)


def test_compute_holdings_state_weights_and_drift():
    targets = [
        Target("A", "Alpha", 0.5, "IDR", "yfinance", 0.08, "Broker", 100, 0.10),
        Target("B", "Beta", 0.5, "USD", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    txns = [Transaction(date(2026, 1, 1), "A", "buy", 100, 100, 10000, "IDR")]
    states = compute_holdings_state(targets, txns, {"A": 100, "B": 0}, fx_usdidr=15000)
    a = next(s for s in states if s.target.ticker == "A")
    b = next(s for s in states if s.target.ticker == "B")
    assert a.value_idr == pytest.approx(10000)
    assert a.weight == pytest.approx(1.0)
    assert a.drift_pp == pytest.approx(50.0)
    assert b.weight == pytest.approx(0.0)
    assert b.drift_pp == pytest.approx(-50.0)


def test_calculate_buy_plain_dca_when_in_band():
    targets = [
        Target("A", "Alpha", 0.5, "IDR", "yfinance", 0.08, "Broker", 1, 0.10),
        Target("B", "Beta", 0.5, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    lines, _ = calculate_buy(targets, 10, [], 1000, {"A": 10, "B": 1}, fx_usdidr=1)
    assert next(l for l in lines if l.ticker == "A").raw_target_idr == pytest.approx(500)
    assert next(l for l in lines if l.ticker == "B").raw_target_idr == pytest.approx(500)


def test_calculate_buy_redirects_to_underweight_gap_equals_contribution():
    """The exact boundary case hand-verified live during the build session: total
    gap across holdings equal to the contribution — leftover should be zero and
    each target should equal its gap exactly, nothing proportional on top."""
    targets = [
        Target("A", "Alpha", 0.5, "IDR", "yfinance", 0.08, "Broker", 1, 0.10),
        Target("B", "Beta", 0.5, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    txns = [Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR")]  # A=1000, B=0
    # new_total = 1000+1000=2000; ideal A=ideal B=1000; gap A=0, gap B=1000; total gap=1000=contribution
    lines, _ = calculate_buy(targets, 10, txns, 1000, {"A": 10, "B": 10}, fx_usdidr=1)
    assert next(l for l in lines if l.ticker == "A").raw_target_idr == pytest.approx(0)
    assert next(l for l in lines if l.ticker == "B").raw_target_idr == pytest.approx(1000)


def test_calculate_buy_splits_by_gap_size_when_gaps_exceed_contribution():
    targets = [
        Target("A", "Alpha", 0.5, "IDR", "yfinance", 0.08, "Broker", 1, 0.10),
        Target("B", "Beta", 0.5, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    txns = [Transaction(date(2026, 1, 1), "A", "buy", 200, 10, 2000, "IDR")]  # A=2000, B=0, total=2000
    # new_total = 2000+100=2100; ideal A=ideal B=1050; gap A=0 (already above ideal), gap B=1050
    # total gap = 1050 > contribution(100) -> all 100 goes to B (only underweight one)
    lines, _ = calculate_buy(targets, 10, txns, 100, {"A": 10, "B": 10}, fx_usdidr=1)
    assert next(l for l in lines if l.ticker == "A").raw_target_idr == pytest.approx(0)
    assert next(l for l in lines if l.ticker == "B").raw_target_idr == pytest.approx(100)


def test_calculate_buy_floors_to_whole_lots():
    targets = [Target("A", "Alpha", 1.0, "IDR", "yfinance", 0.08, "Broker", 100, 0.10)]
    lines, _ = calculate_buy(targets, 10, [], 479_500, {"A": 4790}, fx_usdidr=1)
    a = lines[0]
    assert a.units_to_buy == 100  # 1 lot (100 shares), floored — not a fractional lot count
    assert a.spend_idr == pytest.approx(479000)
    assert a.leftover_idr == pytest.approx(500)
    assert a.below_min_lot is False


def test_calculate_buy_below_min_lot_is_flagged_not_silently_zeroed():
    targets = [Target("A", "Alpha", 1.0, "IDR", "yfinance", 0.08, "Broker", 100, 0.10)]
    lines, _ = calculate_buy(targets, 10, [], 100_000, {"A": 4790}, fx_usdidr=1)
    a = lines[0]
    assert a.units_to_buy == 0
    assert a.below_min_lot is True
    assert a.leftover_idr == pytest.approx(100000)  # not silently lost — visible in the report


def test_calculate_buy_ko_style_lot_size_one_uses_whole_shares():
    targets = [Target("KO", "Coca-Cola", 1.0, "USD", "yfinance", 0.03, "Broker", 1, 0.15)]
    # $91.10/share, USDIDR=17705 -> 1 share = Rp 1,612,925
    lines, _ = calculate_buy(targets, 10, [], 3_000_000, {"KO": 91.10}, fx_usdidr=17705)
    ko = lines[0]
    assert ko.units_to_buy == 1  # floor(3,000,000 / 1,612,925) = 1, not fractional


# --- calculate_buy leftover top-up (a below-min-lot holding borrowing from the pool) ---

def test_calculate_buy_tops_up_below_min_lot_holding_when_pool_covers_the_gap():
    """A can afford its lots with Rp 500 left over; B's own target is Rp 50 short of a
    full lot. The pool (A's Rp 500 leftover + B's own Rp 1000 target) easily covers B's
    Rp 50 gap, so B should get its 1 lot instead of carrying the whole target forward."""
    targets = [
        Target("A", "Alpha", 0.9, "IDR", "yfinance", 0.08, "Broker", 100, 0.10),
        Target("B", "Beta", 0.1, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    lines, _ = calculate_buy(targets, 10, [], 10_000, {"A": 17, "B": 1050}, fx_usdidr=1)
    a = next(l for l in lines if l.ticker == "A")
    b = next(l for l in lines if l.ticker == "B")

    assert a.spend_idr == pytest.approx(8500)
    assert a.leftover_idr == pytest.approx(500)
    assert a.topped_up is False

    assert b.raw_target_idr == pytest.approx(1000)
    assert b.below_min_lot is False  # no longer flagged — it did get bought
    assert b.topped_up is True
    assert b.topup_amount_idr == pytest.approx(50)
    assert b.units_to_buy == 1
    assert b.spend_idr == pytest.approx(1050)
    assert b.leftover_idr == pytest.approx(-50)  # borrowed 50 beyond its own target


def test_calculate_buy_does_not_top_up_when_pool_is_short():
    """Same shape, but A's target divides evenly into its lots (no leftover) — the pool
    is only B's own Rp 1000 target, short of B's Rp 1050 lot. B stays below-min-lot,
    untouched, exactly as before this feature existed."""
    targets = [
        Target("A", "Alpha", 0.9, "IDR", "yfinance", 0.08, "Broker", 100, 0.10),
        Target("B", "Beta", 0.1, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    lines, _ = calculate_buy(targets, 10, [], 10_000, {"A": 18, "B": 1050}, fx_usdidr=1)
    a = next(l for l in lines if l.ticker == "A")
    b = next(l for l in lines if l.ticker == "B")

    assert a.leftover_idr == pytest.approx(0)
    assert b.below_min_lot is True
    assert b.topped_up is False
    assert b.units_to_buy == 0
    assert b.leftover_idr == pytest.approx(1000)


def test_calculate_buy_tops_up_smallest_gap_first_when_pool_is_limited():
    """F buys fine with Rp 100 leftover. X and Y are both below-min-lot: X needs only
    Rp 50 more, Y needs Rp 200 more. The pool (100 + X's 1000 + Y's 1000 = 2100) covers
    X (1050) but not both (1050 + 1200 = 2250) — X, the smaller gap, should win so the
    pool stretches to help it rather than being spent entirely on Y."""
    targets = [
        Target("F", "Funder", 1 / 3, "IDR", "yfinance", 0.08, "Broker", 100, 0.10),
        Target("X", "Xylo", 1 / 3, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
        Target("Y", "Yankee", 1 / 3, "IDR", "yfinance", 0.03, "Broker", 1, 0.15),
    ]
    lines, _ = calculate_buy(targets, 10, [], 3_000, {"F": 9, "X": 1050, "Y": 1200}, fx_usdidr=1)
    f = next(l for l in lines if l.ticker == "F")
    x = next(l for l in lines if l.ticker == "X")
    y = next(l for l in lines if l.ticker == "Y")

    assert f.spend_idr == pytest.approx(900)
    assert f.leftover_idr == pytest.approx(100)

    assert x.topped_up is True
    assert x.units_to_buy == 1
    assert x.topup_amount_idr == pytest.approx(50)

    assert y.topped_up is False
    assert y.below_min_lot is True
    assert y.units_to_buy == 0
    assert y.leftover_idr == pytest.approx(1000)


def test_calculate_buy_topup_never_exceeds_one_lot():
    """B's lot is 10 units (Rp 150); its own target (Rp 100) is Rp 50 short. The pool
    (A's Rp 1300 leftover + B's own Rp 100) is Rp 1400 — big enough to fund 9 of B's
    lots if the top-up chased that — but it should still buy exactly 1 lot (10 units),
    never more, since this closes the "can't afford even 1" gap and nothing further."""
    targets = [
        Target("A", "Alpha", 0.999, "IDR", "yfinance", 0.08, "Broker", 100, 0.10),
        Target("B", "Beta", 0.001, "IDR", "yfinance", 0.03, "Broker", 10, 0.15),
    ]
    lines, _ = calculate_buy(targets, 10, [], 100_000, {"A": 17, "B": 15}, fx_usdidr=1)
    a = next(l for l in lines if l.ticker == "A")
    b = next(l for l in lines if l.ticker == "B")

    assert a.leftover_idr == pytest.approx(1300)
    assert b.topped_up is True
    assert b.units_to_buy == 10  # exactly 1 lot, not the ~9 lots the pool could afford
    assert b.spend_idr == pytest.approx(150)


# --- redistribute_target_weight (sav set-target — a conscious Tier 3/4 decision, PLAN.md §8.5) ---

def _mk_targets(weights: dict) -> list[Target]:
    return [Target(ticker, ticker, w, "IDR", "yfinance", 0.05, "Broker", 1, 0.10)
            for ticker, w in weights.items()]


def test_redistribute_target_weight_two_holdings_all_delta_to_the_other():
    targets = _mk_targets({"A": 0.5, "B": 0.5})
    result = redistribute_target_weight(targets, "A", 0.3)
    by_ticker = {t.ticker: t.target_weight for t in result}
    assert by_ticker["A"] == pytest.approx(0.3)
    assert by_ticker["B"] == pytest.approx(0.7)


def test_redistribute_target_weight_three_holdings_split_proportionally():
    # A shrinks by 0.2; B(0.3)/C(0.2) split that 0.2 in their own 0.3:0.2 ratio.
    targets = _mk_targets({"A": 0.5, "B": 0.3, "C": 0.2})
    result = redistribute_target_weight(targets, "A", 0.3)
    by_ticker = {t.ticker: t.target_weight for t in result}
    assert by_ticker["A"] == pytest.approx(0.3)
    assert by_ticker["B"] == pytest.approx(0.42)
    assert by_ticker["C"] == pytest.approx(0.28)
    assert sum(by_ticker.values()) == pytest.approx(1.0)


def test_redistribute_target_weight_increasing_a_holding_shrinks_the_others():
    targets = _mk_targets({"A": 0.5, "B": 0.3, "C": 0.2})
    result = redistribute_target_weight(targets, "A", 0.6)
    by_ticker = {t.ticker: t.target_weight for t in result}
    assert by_ticker["A"] == pytest.approx(0.6)
    assert by_ticker["B"] == pytest.approx(0.24)
    assert by_ticker["C"] == pytest.approx(0.16)
    assert sum(by_ticker.values()) == pytest.approx(1.0)


def test_redistribute_target_weight_full_exit_to_zero_floors_others_at_zero_not_negative():
    targets = _mk_targets({"A": 0.5, "B": 0.5})
    result = redistribute_target_weight(targets, "A", 1.0)
    by_ticker = {t.ticker: t.target_weight for t in result}
    assert by_ticker["A"] == pytest.approx(1.0)
    assert by_ticker["B"] == pytest.approx(0.0, abs=1e-9)


def test_redistribute_target_weight_original_targets_untouched():
    targets = _mk_targets({"A": 0.5, "B": 0.5})
    redistribute_target_weight(targets, "A", 0.3)
    assert targets[0].target_weight == pytest.approx(0.5)  # not mutated in place


def test_redistribute_target_weight_rejects_unknown_ticker():
    targets = _mk_targets({"A": 0.5, "B": 0.5})
    with pytest.raises(ValueError):
        redistribute_target_weight(targets, "Z", 0.3)


def test_redistribute_target_weight_rejects_out_of_range_percent():
    targets = _mk_targets({"A": 0.5, "B": 0.5})
    with pytest.raises(ValueError):
        redistribute_target_weight(targets, "A", 1.5)


def test_redistribute_target_weight_rejects_single_holding():
    targets = _mk_targets({"A": 1.0})
    with pytest.raises(ValueError):
        redistribute_target_weight(targets, "A", 0.5)


def test_redistribute_target_weight_rejects_when_others_already_zero():
    targets = _mk_targets({"A": 1.0, "B": 0.0})
    with pytest.raises(ValueError):
        redistribute_target_weight(targets, "A", 0.5)


# --- preview_reallocation_scenarios (sav alerts' impact preview / set-target --preview) ---

def test_preview_reallocation_scenarios_matches_real_portfolio_shape():
    # Mirrors the real 40/30/30 config — same numbers verified live via set-target.
    targets = _mk_targets({"ASII.JK": 0.40, "BBRI.JK": 0.30, "KO": 0.30})
    scenarios = preview_reallocation_scenarios(targets, "ASII.JK")
    labels = [label for label, _ in scenarios]
    assert labels == ["-5pp", "-10pp", "full exit (0%)"]

    by_label = {label: {t.ticker: t.target_weight for t in result} for label, result in scenarios}
    assert by_label["-5pp"]["ASII.JK"] == pytest.approx(0.35)
    assert by_label["-5pp"]["BBRI.JK"] == pytest.approx(0.325)
    assert by_label["-5pp"]["KO"] == pytest.approx(0.325)
    assert by_label["-10pp"]["ASII.JK"] == pytest.approx(0.30)
    assert by_label["full exit (0%)"]["ASII.JK"] == pytest.approx(0.0)
    assert by_label["full exit (0%)"]["BBRI.JK"] == pytest.approx(0.50)


def test_preview_reallocation_scenarios_never_mutates_input():
    targets = _mk_targets({"ASII.JK": 0.40, "BBRI.JK": 0.30, "KO": 0.30})
    preview_reallocation_scenarios(targets, "ASII.JK")
    assert next(t for t in targets if t.ticker == "ASII.JK").target_weight == pytest.approx(0.40)


def test_preview_reallocation_scenarios_dedupes_when_steps_clamp_to_the_same_floor():
    # A tiny 3% holding: both -5pp and -10pp clamp to 0%, and that's also the full-exit
    # scenario — should show one "0%" scenario, not three identical rows.
    targets = _mk_targets({"A": 0.03, "B": 0.97})
    scenarios = preview_reallocation_scenarios(targets, "A")
    assert len(scenarios) == 1
    assert scenarios[0][0] == "-5pp"


def test_preview_reallocation_scenarios_rejects_unknown_ticker():
    targets = _mk_targets({"A": 1.0})
    with pytest.raises(ValueError):
        preview_reallocation_scenarios(targets, "Z")


# --- temp_rest_balance (the "leftover cash shouldn't sit idle" parking ledger) -----------

def test_temp_rest_balance_empty_log_is_zero():
    assert temp_rest_balance([]) == 0.0


def test_temp_rest_balance_sums_deposits_minus_redeems():
    rows = [
        (date(2026, 1, 1), "deposit", 400000, ""),
        (date(2026, 1, 5), "deposit", 100000, ""),
        (date(2026, 2, 1), "redeem", 350000, ""),
    ]
    assert temp_rest_balance(rows) == pytest.approx(150000)
