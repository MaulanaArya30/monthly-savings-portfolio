"""Shared data models for the monthly savings portfolio tool.

See BUILD_PLAN.md's "Data Model" section for the full rationale behind each field.
"""
from dataclasses import dataclass
from datetime import date as Date


@dataclass
class Target:
    """A single holding's target configuration, loaded from config/targets.json."""
    ticker: str
    name: str
    target_weight: float
    currency: str            # "IDR" or "USD"
    price_source: str        # "yfinance" or "manual"
    researched_yield: float  # gross trailing dividend yield, from PLAN.md's own research
    broker: str
    lot_size: int             # 100 for IDX board lots, 1 for a true per-share market like KO
    dividend_tax_rate: float  # e.g. 0.10 domestic IDR, 0.15 working assumption for KO (unverified)
    benchmark: str = ""       # index ticker for relative-performance alerts, e.g. ^JKSE, ^GSPC


@dataclass
class Transaction:
    """One row of data/transactions.csv — the only source of truth for current holdings."""
    date: Date
    ticker: str
    type: str  # "buy" or "sell"
    shares: float
    price: float
    amount: float
    currency: str
    note: str = ""


@dataclass
class Dividend:
    """One row of data/dividends.csv."""
    date: Date
    ticker: str
    amount: float
    currency: str
    note: str = ""


@dataclass
class HoldingState:
    """Computed current state of a holding — never persisted, recomputed fresh every run
    from transactions.csv + a live/manual price. See BUILD_PLAN.md: 'the calculator's
    suggestion is never itself persisted or reconciled against.'
    """
    target: Target
    shares: float
    avg_cost: float       # weighted average cost basis per share, native currency
    price: float           # current price, native currency
    value_native: float    # shares * price, native currency
    value_idr: float       # value converted to IDR
    weight: float           # value_idr / total portfolio value_idr (0 if portfolio is empty)
    drift_pp: float         # (weight - target_weight) * 100, in percentage points
