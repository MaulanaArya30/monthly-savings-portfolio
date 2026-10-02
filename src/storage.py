"""All CSV/JSON reading and writing lives here — one place that knows the file formats,
so calculator.py/tracker.py/cli.py just deal in Target/Transaction/Dividend objects.

Every load function validates rows and raises a clear, actionable error naming the bad
row rather than crashing ungracefully or silently coercing bad data (BUILD_PLAN.md's
"CSV parsing validates rows on load" requirement).
"""
import csv
import dataclasses
import json
from datetime import date as Date
from pathlib import Path

from src.models import Dividend, Target, Transaction

TRANSACTIONS_FIELDS = ["date", "ticker", "type", "shares", "price", "amount", "currency", "note"]
DIVIDENDS_FIELDS = ["date", "ticker", "amount", "currency", "note"]
MANUAL_PRICES_FIELDS = ["date", "ticker", "price", "currency", "note"]
PRICE_HISTORY_FIELDS = ["date", "ticker", "price", "currency"]
ALERTS_LOG_FIELDS = ["date", "tier", "ticker", "check", "severity"]
TARGET_CHANGES_LOG_FIELDS = ["date", "ticker", "old_weight", "new_weight", "reason"]
TEMP_REST_LOG_FIELDS = ["date", "action", "amount_idr", "note"]
TEMP_REST_FUND_LOG_FIELDS = ["date", "old_fund", "new_fund", "reason"]
DEEP_REVIEW_LOG_FIELDS = ["date", "note"]
RISK_EXTENSIONS_LOG_FIELDS = ["date", "ticker", "reason"]
RISK_ALERTS_LOG_FIELDS = ["date", "ticker", "check", "detail"]
RISK_CAP_LOG_FIELDS = ["date", "old_cap_idr", "new_cap_idr", "reason"]
RISK_SCREEN_LOG_FIELDS = ["date", "note"]


class DataError(Exception):
    """Raised on a malformed row in one of the data files — always names the bad row."""


def _require_float(value, field, row_num, path):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise DataError(f"{path}: row {row_num}: '{field}' is not a number (got {value!r})")


def _require_positive(value, field, row_num, path):
    if value < 0:
        raise DataError(f"{path}: row {row_num}: '{field}' must not be negative (got {value})")
    return value


def _require_nonempty(value, field, row_num, path):
    if not value or not value.strip():
        raise DataError(f"{path}: row {row_num}: '{field}' is missing")
    return value.strip()


def _ensure_file(path: Path, fields: list[str]):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(fields)


# --- targets.json --------------------------------------------------------------------

def load_targets(config_path: Path) -> tuple[int, list[Target]]:
    """Returns (rebalance_band_pp, [Target, ...])."""
    with config_path.open("r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    band = raw["rebalance_band_pp"]
    targets = [Target(**h) for h in raw["holdings"]]
    total_weight = sum(t.target_weight for t in targets)
    if abs(total_weight - 1.0) > 1e-6:
        raise DataError(
            f"{config_path}: target_weight across all holdings sums to {total_weight:.4f}, "
            f"expected 1.0 — fix config/targets.json before running anything else"
        )
    return band, targets


def save_targets(config_path: Path, band: int, targets: list[Target]):
    """Writes config/targets.json back out — used by `sav set-target` when applying a
    conscious Tier 3/4 reallocation (PLAN.md §8.5). Re-validates weights sum to 1.0
    before writing, same guard load_targets applies on read, so a bug upstream can't
    silently corrupt the one file every other command depends on.
    """
    total_weight = sum(t.target_weight for t in targets)
    if abs(total_weight - 1.0) > 1e-6:
        raise DataError(
            f"Refusing to write {config_path}: target_weight would sum to {total_weight:.4f}, "
            f"expected 1.0 — nothing written"
        )
    payload = {
        "rebalance_band_pp": band,
        "holdings": [dataclasses.asdict(t) for t in targets],
    }
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


# --- transactions.csv -----------------------------------------------------------------

def load_transactions(data_dir: Path) -> list[Transaction]:
    path = data_dir / "transactions.csv"
    _ensure_file(path, TRANSACTIONS_FIELDS)
    out = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            ttype = _require_nonempty(row["type"], "type", i, path)
            if ttype not in ("buy", "sell"):
                raise DataError(f"{path}: row {i}: 'type' must be 'buy' or 'sell' (got {ttype!r})")
            out.append(Transaction(
                date=Date.fromisoformat(row["date"]),
                ticker=_require_nonempty(row["ticker"], "ticker", i, path),
                type=ttype,
                shares=_require_positive(_require_float(row["shares"], "shares", i, path), "shares", i, path),
                price=_require_positive(_require_float(row["price"], "price", i, path), "price", i, path),
                amount=_require_positive(_require_float(row["amount"], "amount", i, path), "amount", i, path),
                currency=_require_nonempty(row["currency"], "currency", i, path),
                note=row.get("note", "") or "",
            ))
    return out


def transaction_exists(data_dir: Path, date: Date, ticker: str, ttype: str, shares: float, price: float) -> bool:
    for t in load_transactions(data_dir):
        if (t.date == date and t.ticker == ticker and t.type == ttype
                and abs(t.shares - shares) < 1e-9 and abs(t.price - price) < 1e-9):
            return True
    return False


def append_transaction(data_dir: Path, txn: Transaction):
    path = data_dir / "transactions.csv"
    _ensure_file(path, TRANSACTIONS_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            txn.date.isoformat(), txn.ticker, txn.type, txn.shares, txn.price,
            txn.amount, txn.currency, txn.note,
        ])


# --- dividends.csv ----------------------------------------------------------------------

def load_dividends(data_dir: Path) -> list[Dividend]:
    path = data_dir / "dividends.csv"
    _ensure_file(path, DIVIDENDS_FIELDS)
    out = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            out.append(Dividend(
                date=Date.fromisoformat(row["date"]),
                ticker=_require_nonempty(row["ticker"], "ticker", i, path),
                amount=_require_positive(_require_float(row["amount"], "amount", i, path), "amount", i, path),
                currency=_require_nonempty(row["currency"], "currency", i, path),
                note=row.get("note", "") or "",
            ))
    return out


def append_dividend(data_dir: Path, div: Dividend):
    path = data_dir / "dividends.csv"
    _ensure_file(path, DIVIDENDS_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([div.date.isoformat(), div.ticker, div.amount, div.currency, div.note])


# --- manual_prices.csv -------------------------------------------------------------------

def load_latest_manual_price(data_dir: Path, ticker: str):
    """Returns (date, price, currency) for the most recent manual entry, or None."""
    path = data_dir / "manual_prices.csv"
    _ensure_file(path, MANUAL_PRICES_FIELDS)
    latest = None
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            if row["ticker"] != ticker:
                continue
            d = Date.fromisoformat(row["date"])
            price = _require_float(row["price"], "price", i, path)
            if latest is None or d >= latest[0]:
                latest = (d, price, row["currency"])
    return latest


def append_manual_price(data_dir: Path, ticker: str, price: float, currency: str, date: Date, note: str = ""):
    path = data_dir / "manual_prices.csv"
    _ensure_file(path, MANUAL_PRICES_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), ticker, price, currency, note])


# --- price_history.csv -------------------------------------------------------------------

def load_price_history(data_dir: Path, ticker: str) -> list[tuple]:
    """Returns [(date, price), ...] sorted ascending for one ticker."""
    path = data_dir / "price_history.csv"
    _ensure_file(path, PRICE_HISTORY_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            if row["ticker"] != ticker:
                continue
            rows.append((Date.fromisoformat(row["date"]), _require_float(row["price"], "price", i, path)))
    rows.sort(key=lambda r: r[0])
    return rows


def has_price_history(data_dir: Path, ticker: str) -> bool:
    return len(load_price_history(data_dir, ticker)) > 0


def append_price_history(data_dir: Path, ticker: str, price: float, currency: str, date: Date):
    path = data_dir / "price_history.csv"
    _ensure_file(path, PRICE_HISTORY_FIELDS)
    # avoid piling up duplicate same-day rows if the tool is run more than once in a day
    existing = load_price_history(data_dir, ticker)
    if existing and existing[-1][0] == date:
        return
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), ticker, price, currency])


def bulk_append_price_history(data_dir: Path, ticker: str, rows: list[tuple], currency: str):
    """rows: [(date, price), ...] — used for the 90-day yfinance bootstrap."""
    path = data_dir / "price_history.csv"
    _ensure_file(path, PRICE_HISTORY_FIELDS)
    existing_dates = {d for d, _ in load_price_history(data_dir, ticker)}
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for d, price in rows:
            if d in existing_dates:
                continue
            w.writerow([d.isoformat(), ticker, price, currency])


# --- alerts_log.csv ----------------------------------------------------------------------
# Lets alerts.py tell whether a finding is new or repeating a prior run — the data
# behind PLAN.md §8.5's "same flag on 2 consecutive checks forces a deep review" rule.

def load_alerts_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, tier, ticker, check, severity), ...] sorted by date ascending."""
    path = data_dir / "alerts_log.csv"
    _ensure_file(path, ALERTS_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((Date.fromisoformat(row["date"]), row["tier"], row["ticker"], row["check"], row["severity"]))
    rows.sort(key=lambda r: r[0])
    return rows


def append_alerts_log(data_dir: Path, findings, run_date: Date):
    path = data_dir / "alerts_log.csv"
    _ensure_file(path, ALERTS_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for finding in findings:
            w.writerow([run_date.isoformat(), finding.tier, finding.ticker, finding.check, finding.severity])


# --- target_changes_log.csv ---------------------------------------------------------------
# Audit trail for `sav set-target` — every conscious Tier 3/4 reallocation (PLAN.md §8.5)
# gets a permanent, dated record of the old/new weight and the stated reason, so
# targets.json never changes silently.

def load_target_changes_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, ticker, old_weight, new_weight, reason), ...] sorted ascending."""
    path = data_dir / "target_changes_log.csv"
    _ensure_file(path, TARGET_CHANGES_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((
                Date.fromisoformat(row["date"]), row["ticker"],
                float(row["old_weight"]), float(row["new_weight"]), row["reason"],
            ))
    rows.sort(key=lambda r: r[0])
    return rows


def append_target_change_log(data_dir: Path, ticker: str, old_weight: float, new_weight: float,
                              reason: str, date: Date):
    path = data_dir / "target_changes_log.csv"
    _ensure_file(path, TARGET_CHANGES_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), ticker, old_weight, new_weight, reason])


# --- temp_rest.json + temp_rest_log.csv ----------------------------------------------------
# The "leftover cash shouldn't just sit idle for a month" parking system — a money-market
# fund balance tracked as a plain ledger (deposits minus redemptions), not a live NAV pull:
# no price API covers Indonesian reksadana, so this deliberately never estimates accrued
# yield. See calculator.temp_rest_balance and cli.cmd_temp_rest_status.

def load_temp_rest_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise DataError(f"{config_path} not found — see config/temp_rest.json")
    with config_path.open("r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    threshold = raw.get("significant_threshold_idr")
    if threshold is None or float(threshold) < 0:
        raise DataError(f"{config_path}: significant_threshold_idr must be a non-negative number")
    return {
        "significant_threshold_idr": float(threshold),
        "current_fund_name": raw.get("current_fund_name", "") or "",
        "current_fund_note": raw.get("current_fund_note", "") or "",
    }


def save_temp_rest_config(config_path: Path, threshold_idr: float, fund_name: str, fund_note: str):
    payload = {
        "significant_threshold_idr": threshold_idr,
        "current_fund_name": fund_name,
        "current_fund_note": fund_note,
    }
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def load_temp_rest_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, action, amount_idr, note), ...] sorted ascending. action is
    'deposit' or 'redeem'."""
    path = data_dir / "temp_rest_log.csv"
    _ensure_file(path, TEMP_REST_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            action = _require_nonempty(row["action"], "action", i, path)
            if action not in ("deposit", "redeem"):
                raise DataError(f"{path}: row {i}: 'action' must be 'deposit' or 'redeem' (got {action!r})")
            rows.append((
                Date.fromisoformat(row["date"]), action,
                _require_positive(_require_float(row["amount_idr"], "amount_idr", i, path), "amount_idr", i, path),
                row.get("note", "") or "",
            ))
    rows.sort(key=lambda r: r[0])
    return rows


def append_temp_rest_log(data_dir: Path, date: Date, action: str, amount_idr: float, note: str = ""):
    path = data_dir / "temp_rest_log.csv"
    _ensure_file(path, TEMP_REST_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), action, amount_idr, note])


def load_temp_rest_fund_log(data_dir: Path) -> list[tuple]:
    path = data_dir / "temp_rest_fund_log.csv"
    _ensure_file(path, TEMP_REST_FUND_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((Date.fromisoformat(row["date"]), row["old_fund"], row["new_fund"], row["reason"]))
    rows.sort(key=lambda r: r[0])
    return rows


def append_temp_rest_fund_log(data_dir: Path, date: Date, old_fund: str, new_fund: str, reason: str):
    path = data_dir / "temp_rest_fund_log.csv"
    _ensure_file(path, TEMP_REST_FUND_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), old_fund, new_fund, reason])


# --- deep_review_log.csv --------------------------------------------------------------------
# One unified yearly-review clock covering BOTH the main stocks (PLAN.md §8.1 scorecard)
# and the temp_rest fund pick — neither can be mechanically re-evaluated by a plain
# script (no stable data feed for either), so both need an actual live session.
# set-target and temp-rest-set-fund append here automatically when actually used;
# `sav review-log` covers the "reviewed, nothing needed to change" case.

def load_deep_review_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, note), ...] sorted ascending."""
    path = data_dir / "deep_review_log.csv"
    _ensure_file(path, DEEP_REVIEW_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((Date.fromisoformat(row["date"]), row["note"]))
    rows.sort(key=lambda r: r[0])
    return rows


def append_deep_review_log(data_dir: Path, date: Date, note: str):
    path = data_dir / "deep_review_log.csv"
    _ensure_file(path, DEEP_REVIEW_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), note])


# --- risk_screen_log.csv ---------------------------------------------------------------
# The Risk Pot's own recurring-cadence clock (RISK_POT_PLAN.md §3.12/§3.13's "catch it
# early" discussion) — a wide IHSG-wide screen only helps if it actually reruns
# periodically, not just when asked. Same shape as deep_review_log.csv: `risk-screen`
# stamps this automatically every time it runs (informational, excluded from `sav undo`
# for the same reason `alerts`/`status`/`risk-alert` are), and the daily routine reads
# the last row to decide whether a fresh wide screen is due (default: every 7 days).

def load_risk_screen_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, note), ...] sorted ascending."""
    path = data_dir / "risk_screen_log.csv"
    _ensure_file(path, RISK_SCREEN_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((Date.fromisoformat(row["date"]), row["note"]))
    rows.sort(key=lambda r: r[0])
    return rows


def append_risk_screen_log(data_dir: Path, date: Date, note: str):
    path = data_dir / "risk_screen_log.csv"
    _ensure_file(path, RISK_SCREEN_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), note])


# --- risk_transactions.csv --------------------------------------------------------------
# The Risk Pot's own ledger — see RISK_POT_PLAN.md. Same row shape as
# transactions.csv (reuses TRANSACTIONS_FIELDS) but a completely separate file on purpose:
# net_shares/avg_cost_basis math must never mix the core DCA book with a 1-month trade.

def load_risk_transactions(data_dir: Path) -> list[Transaction]:
    path = data_dir / "risk_transactions.csv"
    _ensure_file(path, TRANSACTIONS_FIELDS)
    out = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            ttype = _require_nonempty(row["type"], "type", i, path)
            if ttype not in ("buy", "sell"):
                raise DataError(f"{path}: row {i}: 'type' must be 'buy' or 'sell' (got {ttype!r})")
            out.append(Transaction(
                date=Date.fromisoformat(row["date"]),
                ticker=_require_nonempty(row["ticker"], "ticker", i, path),
                type=ttype,
                shares=_require_positive(_require_float(row["shares"], "shares", i, path), "shares", i, path),
                price=_require_positive(_require_float(row["price"], "price", i, path), "price", i, path),
                amount=_require_positive(_require_float(row["amount"], "amount", i, path), "amount", i, path),
                currency=_require_nonempty(row["currency"], "currency", i, path),
                note=row.get("note", "") or "",
            ))
    return out


def risk_transaction_exists(data_dir: Path, date: Date, ticker: str, ttype: str, shares: float, price: float) -> bool:
    for t in load_risk_transactions(data_dir):
        if (t.date == date and t.ticker == ticker and t.type == ttype
                and abs(t.shares - shares) < 1e-9 and abs(t.price - price) < 1e-9):
            return True
    return False


def append_risk_transaction(data_dir: Path, txn: Transaction):
    path = data_dir / "risk_transactions.csv"
    _ensure_file(path, TRANSACTIONS_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            txn.date.isoformat(), txn.ticker, txn.type, txn.shares, txn.price,
            txn.amount, txn.currency, txn.note,
        ])


# --- risk_extensions_log.csv -------------------------------------------------------------

def load_risk_extensions_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, ticker, reason), ...] sorted ascending."""
    path = data_dir / "risk_extensions_log.csv"
    _ensure_file(path, RISK_EXTENSIONS_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((Date.fromisoformat(row["date"]), row["ticker"], row["reason"]))
    rows.sort(key=lambda r: r[0])
    return rows


def append_risk_extension_log(data_dir: Path, date: Date, ticker: str, reason: str):
    path = data_dir / "risk_extensions_log.csv"
    _ensure_file(path, RISK_EXTENSIONS_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), ticker, reason])


# --- risk_alerts_log.csv ------------------------------------------------------------------

def load_risk_alerts_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, ticker, check, detail), ...] sorted ascending."""
    path = data_dir / "risk_alerts_log.csv"
    _ensure_file(path, RISK_ALERTS_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((Date.fromisoformat(row["date"]), row["ticker"], row["check"], row["detail"]))
    rows.sort(key=lambda r: r[0])
    return rows


def append_risk_alerts_log(data_dir: Path, findings, run_date: Date):
    """findings: [(ticker, check, detail), ...]."""
    path = data_dir / "risk_alerts_log.csv"
    _ensure_file(path, RISK_ALERTS_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for ticker, check, detail in findings:
            writer.writerow([run_date.isoformat(), ticker, check, detail])


# --- config/risk_config.json ---------------------------------------------------------------

def load_risk_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise DataError(f"{config_path} not found — see config/risk_config.json")
    with config_path.open("r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    # checkpoint_days retired 2026-09-02 (RISK_POT_PLAN.md §4) — the fixed 30-day cycle
    # it gated is gone, replaced by the daily mechanical checks below plus a soft
    # staleness backstop (stale_review_days).
    required = [
        "pot_cap_idr", "stop_loss_pct", "target_monthly_return_pct", "benchmark",
        "breakeven_lock_pct", "trailing_stop_arm_pct", "trailing_stop_pct",
        "min_review_days", "stale_review_days",
    ]
    missing = [k for k in required if k not in raw]
    if missing:
        raise DataError(f"{config_path}: missing required field(s): {', '.join(missing)}")
    return {
        "pot_cap_idr": float(raw["pot_cap_idr"]),
        "stop_loss_pct": float(raw["stop_loss_pct"]),
        "target_monthly_return_pct": float(raw["target_monthly_return_pct"]),
        "benchmark": raw["benchmark"],
        "candidate_universe": raw.get("candidate_universe", "kompas100"),
        "breakeven_lock_pct": float(raw["breakeven_lock_pct"]),
        "trailing_stop_arm_pct": float(raw["trailing_stop_arm_pct"]),
        "trailing_stop_pct": float(raw["trailing_stop_pct"]),
        "min_review_days": int(raw["min_review_days"]),
        "stale_review_days": int(raw["stale_review_days"]),
        # Track B's own exit ladder (RISK_POT_PLAN.md §3.1b, 2026-09-25). Optional: when
        # absent, risk_pot.TRACK_B_DEFAULTS applies.
        "track_b": {k: float(v) for k, v in raw["track_b"].items()} if isinstance(raw.get("track_b"), dict) else None,
    }


def save_risk_config(config_path: Path, pot_cap_idr: float, stop_loss_pct: float,
                      target_monthly_return_pct: float, benchmark: str, candidate_universe: str,
                      breakeven_lock_pct: float, trailing_stop_arm_pct: float, trailing_stop_pct: float,
                      min_review_days: int, stale_review_days: int, track_b: dict = None):
    payload = {
        "pot_cap_idr": pot_cap_idr,
        "stop_loss_pct": stop_loss_pct,
        "target_monthly_return_pct": target_monthly_return_pct,
        "benchmark": benchmark,
        "candidate_universe": candidate_universe,
        "breakeven_lock_pct": breakeven_lock_pct,
        "trailing_stop_arm_pct": trailing_stop_arm_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "min_review_days": min_review_days,
        "stale_review_days": stale_review_days,
    }
    if track_b:
        payload["track_b"] = track_b
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


# --- risk_cap_log.csv ------------------------------------------------------------------
# Audit trail of every `risk-set-cap` change — same role temp_rest_fund_log.csv plays for
# `temp-rest-set-fund`. The cap is meant to move as you add/withdraw risk-pot capital
# (RISK_POT_PLAN.md §2), not stay pinned at its initial seed forever.

def load_risk_cap_log(data_dir: Path) -> list[tuple]:
    """Returns [(date, old_cap_idr, new_cap_idr, reason), ...] sorted ascending."""
    path = data_dir / "risk_cap_log.csv"
    _ensure_file(path, RISK_CAP_LOG_FIELDS)
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((
                Date.fromisoformat(row["date"]),
                float(row["old_cap_idr"]),
                float(row["new_cap_idr"]),
                row["reason"],
            ))
    rows.sort(key=lambda r: r[0])
    return rows


def append_risk_cap_log(data_dir: Path, date: Date, old_cap_idr: float, new_cap_idr: float, reason: str):
    path = data_dir / "risk_cap_log.csv"
    _ensure_file(path, RISK_CAP_LOG_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([date.isoformat(), old_cap_idr, new_cap_idr, reason])


# --- config/risk_watchlist.json ------------------------------------------------------------
# The Risk Pot's candidate universe for `risk-screen` (RISK_POT_PLAN.md §3.1 step 1) — NOT a
# live scan of the actual Kompas100/JKSE index (no reliable API/source for current index
# membership), so this is a hand-maintained list instead, seeded from §3's own "starting
# watchlist" and meant to be expanded with individually-researched names over time, the same
# way KLBF was researched (PLAN.md §8.7) rather than pulled from a scraped index list.

def load_risk_watchlist(config_path: Path) -> list[dict]:
    """Returns [{"ticker": ..., "theme": ...}, ...] in file order."""
    if not config_path.exists():
        raise DataError(f"{config_path} not found — see config/risk_watchlist.json")
    with config_path.open("r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    candidates = raw.get("candidates")
    if not candidates:
        raise DataError(f"{config_path}: 'candidates' is missing or empty")
    out = []
    for i, c in enumerate(candidates):
        ticker = c.get("ticker")
        if not ticker:
            raise DataError(f"{config_path}: candidate {i} is missing 'ticker'")
        out.append({"ticker": ticker, "theme": c.get("theme", "")})
    return out


# --- config/buyback_calendar.json -----------------------------------------------------
# Tracks known IDX buyback programs' end dates so a renewal (the real re-rating trigger
# per the ERAA case study, RISK_POT_PLAN.md §3.10) can be watched for proactively instead
# of discovered after the fact (§3.12's "catch it early" discussion). Hand-maintained,
# same spirit as risk_watchlist.json — dates are only as good as what was verified when
# added, re-check via the ticker's own Corp. Action tab before trusting one.

def load_buyback_calendar(config_path: Path) -> list[dict]:
    """Returns dated programs only: [{"ticker", "max_value_idr", "start_date" (date),
    "end_date" (date), "confirmed", "note"}, ...], in file order. Entries with no
    end_date (undated/unconfirmed) are skipped — nothing to watch a countdown against."""
    if not config_path.exists():
        raise DataError(f"{config_path} not found — see config/buyback_calendar.json")
    with config_path.open("r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    programs = raw.get("programs")
    if not programs:
        raise DataError(f"{config_path}: 'programs' is missing or empty")
    out = []
    for i, p in enumerate(programs):
        ticker = p.get("ticker")
        end_date = p.get("end_date")
        if not ticker or not end_date:
            continue
        out.append({
            "ticker": ticker,
            "max_value_idr": p.get("max_value_idr"),
            "start_date": Date.fromisoformat(p["start_date"]) if p.get("start_date") else None,
            "end_date": Date.fromisoformat(end_date),
            "confirmed": p.get("confirmed", False),
            "note": p.get("note", ""),
        })
    return out
