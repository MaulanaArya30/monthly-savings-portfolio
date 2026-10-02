import json
from datetime import date

import pytest

from src import storage
from src.models import Target, Transaction
from src.storage import DataError


def test_append_and_load_transaction_roundtrip(tmp_path):
    txn = Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR", "note")
    storage.append_transaction(tmp_path, txn)
    loaded = storage.load_transactions(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].ticker == "A"
    assert loaded[0].shares == 100
    assert loaded[0].type == "buy"


def test_transaction_exists_detects_duplicate(tmp_path):
    storage.append_transaction(tmp_path, Transaction(date(2026, 1, 1), "A", "buy", 100, 10, 1000, "IDR"))
    assert storage.transaction_exists(tmp_path, date(2026, 1, 1), "A", "buy", 100, 10) is True
    assert storage.transaction_exists(tmp_path, date(2026, 1, 1), "A", "buy", 100, 11) is False
    assert storage.transaction_exists(tmp_path, date(2026, 1, 1), "A", "sell", 100, 10) is False


def test_load_transactions_rejects_bad_type(tmp_path):
    (tmp_path / "transactions.csv").write_text(
        "date,ticker,type,shares,price,amount,currency,note\n2026-01-01,A,hold,1,1,1,IDR,\n"
    )
    with pytest.raises(DataError):
        storage.load_transactions(tmp_path)


def test_load_transactions_rejects_negative_shares(tmp_path):
    (tmp_path / "transactions.csv").write_text(
        "date,ticker,type,shares,price,amount,currency,note\n2026-01-01,A,buy,-1,1,1,IDR,\n"
    )
    with pytest.raises(DataError):
        storage.load_transactions(tmp_path)


def test_load_transactions_rejects_non_numeric_shares(tmp_path):
    (tmp_path / "transactions.csv").write_text(
        "date,ticker,type,shares,price,amount,currency,note\n2026-01-01,A,buy,abc,1,1,IDR,\n"
    )
    with pytest.raises(DataError):
        storage.load_transactions(tmp_path)


def test_load_targets_validates_weights_sum_to_one(tmp_path):
    config = tmp_path / "targets.json"
    config.write_text(json.dumps({
        "rebalance_band_pp": 10,
        "holdings": [{
            "ticker": "A", "name": "A", "target_weight": 0.5, "currency": "IDR",
            "price_source": "yfinance", "researched_yield": 0.05, "broker": "X",
            "lot_size": 1, "dividend_tax_rate": 0.1,
        }],
    }))
    with pytest.raises(DataError):
        storage.load_targets(config)


def test_load_targets_accepts_weights_summing_to_one(tmp_path):
    config = tmp_path / "targets.json"
    config.write_text(json.dumps({
        "rebalance_band_pp": 10,
        "holdings": [
            {"ticker": "A", "name": "A", "target_weight": 0.6, "currency": "IDR",
             "price_source": "yfinance", "researched_yield": 0.05, "broker": "X",
             "lot_size": 1, "dividend_tax_rate": 0.1},
            {"ticker": "B", "name": "B", "target_weight": 0.4, "currency": "IDR",
             "price_source": "yfinance", "researched_yield": 0.05, "broker": "X",
             "lot_size": 1, "dividend_tax_rate": 0.1},
        ],
    }))
    band, targets = storage.load_targets(config)
    assert band == 10
    assert len(targets) == 2


# --- save_targets (sav set-target — PLAN.md §8.5 Tier 3/4) ---------------------------------

def test_save_targets_round_trips_through_load_targets(tmp_path):
    config = tmp_path / "targets.json"
    targets = [
        Target("A", "Alpha", 0.6, "IDR", "yfinance", 0.05, "Broker", 1, 0.10),
        Target("B", "Beta", 0.4, "USD", "yfinance", 0.03, "Broker", 1, 0.15, benchmark="^GSPC"),
    ]
    storage.save_targets(config, 10, targets)
    band, loaded = storage.load_targets(config)
    assert band == 10
    assert len(loaded) == 2
    assert loaded[0].ticker == "A"
    assert loaded[0].target_weight == pytest.approx(0.6)
    assert loaded[1].benchmark == "^GSPC"


def test_save_targets_refuses_to_write_if_weights_dont_sum_to_one(tmp_path):
    config = tmp_path / "targets.json"
    targets = [Target("A", "Alpha", 0.6, "IDR", "yfinance", 0.05, "Broker", 1, 0.10)]
    with pytest.raises(DataError):
        storage.save_targets(config, 10, targets)
    assert not config.exists()  # refused before writing anything


# --- target_changes_log.csv ----------------------------------------------------------------

def test_append_and_load_target_change_log_roundtrip(tmp_path):
    storage.append_target_change_log(tmp_path, "A", 0.40, 0.30, "payout ratio too high", date(2026, 1, 1))
    rows = storage.load_target_changes_log(tmp_path)
    assert len(rows) == 1
    d, ticker, old_w, new_w, reason = rows[0]
    assert d == date(2026, 1, 1)
    assert ticker == "A"
    assert old_w == pytest.approx(0.40)
    assert new_w == pytest.approx(0.30)
    assert reason == "payout ratio too high"


# --- temp_rest.json / temp_rest_log.csv / temp_rest_fund_log.csv ---------------------------

def test_temp_rest_config_round_trips(tmp_path):
    config = tmp_path / "temp_rest.json"
    storage.save_temp_rest_config(config, 100000, "Fund X", "picked for liquidity")
    loaded = storage.load_temp_rest_config(config)
    assert loaded["significant_threshold_idr"] == pytest.approx(100000)
    assert loaded["current_fund_name"] == "Fund X"
    assert loaded["current_fund_note"] == "picked for liquidity"


def test_temp_rest_config_missing_file_raises(tmp_path):
    with pytest.raises(DataError):
        storage.load_temp_rest_config(tmp_path / "does_not_exist.json")


def test_temp_rest_config_rejects_negative_threshold(tmp_path):
    config = tmp_path / "temp_rest.json"
    config.write_text(json.dumps({"significant_threshold_idr": -5, "current_fund_name": ""}))
    with pytest.raises(DataError):
        storage.load_temp_rest_config(config)


def test_append_and_load_temp_rest_log_roundtrip(tmp_path):
    storage.append_temp_rest_log(tmp_path, date(2026, 1, 1), "deposit", 400000, "leftover from ASII")
    storage.append_temp_rest_log(tmp_path, date(2026, 2, 1), "redeem", 400000, "")
    rows = storage.load_temp_rest_log(tmp_path)
    assert len(rows) == 2
    assert rows[0] == (date(2026, 1, 1), "deposit", 400000.0, "leftover from ASII")
    assert rows[1][1] == "redeem"


def test_load_temp_rest_log_rejects_bad_action(tmp_path):
    (tmp_path / "temp_rest_log.csv").write_text(
        "date,action,amount_idr,note\n2026-01-01,withdraw,1000,\n"
    )
    with pytest.raises(DataError):
        storage.load_temp_rest_log(tmp_path)


def test_append_and_load_temp_rest_fund_log_roundtrip(tmp_path):
    storage.append_temp_rest_fund_log(tmp_path, date(2026, 1, 1), "", "Fund X", "initial pick")
    rows = storage.load_temp_rest_fund_log(tmp_path)
    assert len(rows) == 1
    assert rows[0] == (date(2026, 1, 1), "", "Fund X", "initial pick")


# --- deep_review_log.csv (the unified yearly live-session review clock) --------------------

def test_append_and_load_deep_review_log_roundtrip(tmp_path):
    storage.append_deep_review_log(tmp_path, date(2026, 1, 1), "annual review: no changes needed")
    rows = storage.load_deep_review_log(tmp_path)
    assert len(rows) == 1
    assert rows[0] == (date(2026, 1, 1), "annual review: no changes needed")


def test_load_deep_review_log_sorted_ascending(tmp_path):
    storage.append_deep_review_log(tmp_path, date(2026, 3, 1), "later")
    storage.append_deep_review_log(tmp_path, date(2026, 1, 1), "earlier")
    rows = storage.load_deep_review_log(tmp_path)
    assert [r[0] for r in rows] == [date(2026, 1, 1), date(2026, 3, 1)]


# --- Risk Pot: risk_transactions.csv / risk_extensions_log.csv / risk_alerts_log.csv /
# config/risk_config.json — a completely separate ledger/config from everything above,
# see RISK_POT_PLAN.md.

def test_append_and_load_risk_transaction_roundtrip(tmp_path):
    txn = Transaction(date(2026, 8, 26), "EMAS.JK", "buy", 100, 6850, 685000, "IDR", "stop_loss=5822.5")
    storage.append_risk_transaction(tmp_path, txn)
    loaded = storage.load_risk_transactions(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].ticker == "EMAS.JK"
    assert loaded[0].note == "stop_loss=5822.5"


def test_risk_transaction_exists_is_separate_from_core_transactions(tmp_path):
    """The risk ledger and the core ledger must never see each other's rows."""
    storage.append_transaction(tmp_path, Transaction(date(2026, 8, 26), "A", "buy", 100, 10, 1000, "IDR"))
    assert storage.risk_transaction_exists(tmp_path, date(2026, 8, 26), "A", "buy", 100, 10) is False
    storage.append_risk_transaction(tmp_path, Transaction(date(2026, 8, 26), "A", "buy", 100, 10, 1000, "IDR"))
    assert storage.risk_transaction_exists(tmp_path, date(2026, 8, 26), "A", "buy", 100, 10) is True
    # and the core ledger is untouched
    assert len(storage.load_transactions(tmp_path)) == 1


def test_append_and_load_risk_extensions_log_roundtrip(tmp_path):
    storage.append_risk_extension_log(tmp_path, date(2026, 9, 25), "EMAS.JK", "beat JKSE, thesis intact")
    rows = storage.load_risk_extensions_log(tmp_path)
    assert rows == [(date(2026, 9, 25), "EMAS.JK", "beat JKSE, thesis intact")]


def test_append_and_load_risk_alerts_log_roundtrip(tmp_path):
    storage.append_risk_alerts_log(
        tmp_path,
        [("EMAS.JK", "stop_loss_breach", "price at/below stop-loss"), ("POT", "cap_breach", "over cap")],
        date(2026, 8, 26),
    )
    rows = storage.load_risk_alerts_log(tmp_path)
    assert len(rows) == 2
    assert rows[0][1:3] == ("EMAS.JK", "stop_loss_breach")


_RISK_CONFIG_FIELDS = {
    "pot_cap_idr": 1000000, "stop_loss_pct": 0.15,
    "breakeven_lock_pct": 0.10, "trailing_stop_arm_pct": 0.20, "trailing_stop_pct": 0.10,
    "min_review_days": 5, "stale_review_days": 21,
    "target_monthly_return_pct": 0.15, "benchmark": "^JKSE", "candidate_universe": "kompas100",
}


def test_load_risk_config_reads_all_fields(tmp_path):
    config_path = tmp_path / "risk_config.json"
    config_path.write_text(json.dumps(_RISK_CONFIG_FIELDS))
    config = storage.load_risk_config(config_path)
    assert config["pot_cap_idr"] == 1_000_000
    assert config["breakeven_lock_pct"] == 0.10
    assert config["trailing_stop_arm_pct"] == 0.20
    assert config["trailing_stop_pct"] == 0.10
    assert config["min_review_days"] == 5
    assert config["stale_review_days"] == 21
    assert config["benchmark"] == "^JKSE"


def test_load_risk_config_missing_file_raises(tmp_path):
    with pytest.raises(DataError):
        storage.load_risk_config(tmp_path / "does_not_exist.json")


def test_load_risk_config_missing_required_field_raises(tmp_path):
    config_path = tmp_path / "risk_config.json"
    config_path.write_text(json.dumps({"pot_cap_idr": 1000000}))
    with pytest.raises(DataError):
        storage.load_risk_config(config_path)


def test_save_risk_config_roundtrip_changes_only_the_cap(tmp_path):
    config_path = tmp_path / "risk_config.json"
    config_path.write_text(json.dumps(_RISK_CONFIG_FIELDS))
    original = storage.load_risk_config(config_path)
    storage.save_risk_config(
        config_path, 1500000, original["stop_loss_pct"],
        original["target_monthly_return_pct"], original["benchmark"], original["candidate_universe"],
        original["breakeven_lock_pct"], original["trailing_stop_arm_pct"], original["trailing_stop_pct"],
        original["min_review_days"], original["stale_review_days"],
    )
    reloaded = storage.load_risk_config(config_path)
    assert reloaded["pot_cap_idr"] == 1_500_000
    assert reloaded["stop_loss_pct"] == 0.15
    assert reloaded["breakeven_lock_pct"] == 0.10
    assert reloaded["trailing_stop_arm_pct"] == 0.20
    assert reloaded["trailing_stop_pct"] == 0.10
    assert reloaded["min_review_days"] == 5
    assert reloaded["stale_review_days"] == 21
    assert reloaded["benchmark"] == "^JKSE"
    assert reloaded["candidate_universe"] == "kompas100"


def test_append_and_load_risk_cap_log_roundtrip(tmp_path):
    storage.append_risk_cap_log(tmp_path, date(2026, 8, 27), 1000000, 1500000, "topped up another Rp500,000")
    rows = storage.load_risk_cap_log(tmp_path)
    assert rows == [(date(2026, 8, 27), 1_000_000.0, 1_500_000.0, "topped up another Rp500,000")]


def test_load_risk_watchlist_reads_candidates(tmp_path):
    config_path = tmp_path / "risk_watchlist.json"
    config_path.write_text(json.dumps({
        "note": "test", "as_of": "2026-08-27",
        "candidates": [
            {"ticker": "GOTO.JK", "theme": "digital economy"},
            {"ticker": "ANTM.JK", "theme": "nickel/EV battery supply chain"},
        ],
    }))
    candidates = storage.load_risk_watchlist(config_path)
    assert candidates == [
        {"ticker": "GOTO.JK", "theme": "digital economy"},
        {"ticker": "ANTM.JK", "theme": "nickel/EV battery supply chain"},
    ]


def test_load_risk_watchlist_missing_file_raises(tmp_path):
    with pytest.raises(DataError):
        storage.load_risk_watchlist(tmp_path / "does_not_exist.json")


def test_load_risk_watchlist_empty_candidates_raises(tmp_path):
    config_path = tmp_path / "risk_watchlist.json"
    config_path.write_text(json.dumps({"candidates": []}))
    with pytest.raises(DataError):
        storage.load_risk_watchlist(config_path)


def test_load_risk_watchlist_missing_ticker_raises(tmp_path):
    config_path = tmp_path / "risk_watchlist.json"
    config_path.write_text(json.dumps({"candidates": [{"theme": "no ticker here"}]}))
    with pytest.raises(DataError):
        storage.load_risk_watchlist(config_path)


# --- risk_screen_log.csv (the wide-screen recurring-cadence clock, RISK_POT_PLAN.md §3.12) --

def test_append_and_load_risk_screen_log_roundtrip(tmp_path):
    storage.append_risk_screen_log(tmp_path, date(2026, 9, 11), "11 candidates screened, shortlist: ERAA.JK")
    rows = storage.load_risk_screen_log(tmp_path)
    assert rows == [(date(2026, 9, 11), "11 candidates screened, shortlist: ERAA.JK")]


def test_load_risk_screen_log_sorted_ascending(tmp_path):
    storage.append_risk_screen_log(tmp_path, date(2026, 9, 11), "later")
    storage.append_risk_screen_log(tmp_path, date(2026, 9, 1), "earlier")
    rows = storage.load_risk_screen_log(tmp_path)
    assert [r[0] for r in rows] == [date(2026, 9, 1), date(2026, 9, 11)]


# --- config/buyback_calendar.json (RISK_POT_PLAN.md §3.10/§3.12 — catch a renewal early) ---

def test_load_buyback_calendar_reads_dated_programs(tmp_path):
    config_path = tmp_path / "buyback_calendar.json"
    config_path.write_text(json.dumps({
        "note": "test", "as_of": "2026-09-11",
        "programs": [
            {"ticker": "ERAA.JK", "max_value_idr": 500_000_000_000, "start_date": "2026-09-04",
             "end_date": "2026-12-04", "confirmed": True, "note": "renewal case study"},
        ],
    }))
    programs = storage.load_buyback_calendar(config_path)
    assert len(programs) == 1
    p = programs[0]
    assert p["ticker"] == "ERAA.JK"
    assert p["end_date"] == date(2026, 12, 4)
    assert p["start_date"] == date(2026, 9, 4)
    assert p["confirmed"] is True


def test_load_buyback_calendar_skips_undated_entries(tmp_path):
    """An entry with no end_date has nothing to watch a countdown against — skip it
    rather than error, since config/buyback_calendar.json deliberately keeps
    unconfirmed/undated programs around for reference."""
    config_path = tmp_path / "buyback_calendar.json"
    config_path.write_text(json.dumps({
        "programs": [
            {"ticker": "ERAA.JK", "end_date": "2026-12-04", "start_date": "2026-09-04"},
            {"ticker": "CBDK.JK", "end_date": None, "start_date": "2026-08-20"},
        ],
    }))
    programs = storage.load_buyback_calendar(config_path)
    assert len(programs) == 1
    assert programs[0]["ticker"] == "ERAA.JK"


def test_load_buyback_calendar_missing_file_raises(tmp_path):
    with pytest.raises(DataError):
        storage.load_buyback_calendar(tmp_path / "does_not_exist.json")


def test_load_buyback_calendar_empty_programs_raises(tmp_path):
    config_path = tmp_path / "buyback_calendar.json"
    config_path.write_text(json.dumps({"programs": []}))
    with pytest.raises(DataError):
        storage.load_buyback_calendar(config_path)


def test_load_risk_config_track_b_block_is_optional(tmp_path):
    config_path = tmp_path / "risk_config.json"
    config_path.write_text(json.dumps(_RISK_CONFIG_FIELDS))
    assert storage.load_risk_config(config_path)["track_b"] is None
    config_path.write_text(json.dumps(dict(_RISK_CONFIG_FIELDS, track_b={
        "stop_loss_pct": 0.15, "pivot_buffer_pct": 0.03, "breakeven_lock_pct": 0.3,
        "trailing_stop_arm_pct": 0.5, "trailing_stop_pct": 0.25})))
    assert storage.load_risk_config(config_path)["track_b"]["trailing_stop_pct"] == 0.25


def test_save_risk_config_preserves_track_b_block(tmp_path):
    """risk-set-cap rewrites the whole file; the Track B ladder must survive it."""
    config_path = tmp_path / "risk_config.json"
    tb = {"stop_loss_pct": 0.15, "pivot_buffer_pct": 0.03, "breakeven_lock_pct": 0.3,
          "trailing_stop_arm_pct": 0.5, "trailing_stop_pct": 0.25}
    config_path.write_text(json.dumps(dict(_RISK_CONFIG_FIELDS, track_b=tb)))
    original = storage.load_risk_config(config_path)
    storage.save_risk_config(
        config_path, 2000000, original["stop_loss_pct"],
        original["target_monthly_return_pct"], original["benchmark"], original["candidate_universe"],
        original["breakeven_lock_pct"], original["trailing_stop_arm_pct"], original["trailing_stop_pct"],
        original["min_review_days"], original["stale_review_days"], track_b=original["track_b"],
    )
    reloaded = storage.load_risk_config(config_path)
    assert reloaded["pot_cap_idr"] == 2_000_000
    assert reloaded["track_b"] == tb
