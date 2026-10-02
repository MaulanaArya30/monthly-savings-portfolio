"""Risk Pot — a separate, riskier short-horizon trading allocation. See
RISK_POT_PLAN.md §4 (event-driven review + two-stage profit-lock, revised 2026-09-02)
for the full design and reasoning. Fully isolated from the core DCA sleeve on purpose:
this module never reads config/targets.json or data/transactions.csv, and
calculator.py/tracker.py/alerts.py never import from here. cli.py is the only thing
that talks to both.

Reuses calculator.net_shares/avg_cost_basis directly — those are already generic over any
list[Transaction], no risk-pot-specific version needed.
"""
import re
from dataclasses import dataclass
from datetime import date as Date

from src.calculator import avg_cost_basis, net_shares
from src.models import Transaction

_STOP_LOSS_PREFIX = "stop_loss="
_TRACK_PREFIX = "track="
_PIVOT_PREFIX = "pivot="

# Track B (RISK_POT_PLAN.md §3.1b, decided 2026-09-25): early special situations (the
# SINI/STAR sequence) get their own exit ladder. Used when risk_config.json has no
# explicit "track_b" block. Backtest behind these numbers: RISK_POT_PLAN.md §3.22.
TRACK_B_DEFAULTS = {
    "stop_loss_pct": 0.15,          # hard floor below entry
    "pivot_buffer_pct": 0.03,       # thesis is wrong on a close back inside the base
    "breakeven_lock_pct": 0.30,     # the pot's +10% lock cut SINI's leg 2 at -5% on two ARB days
    "trailing_stop_arm_pct": 0.50,
    "trailing_stop_pct": 0.25,      # wide enough for +/-10% limit-bound (FCA) swings
}


def encode_note(stop_loss, note: str, track: str = None, pivot: float = None) -> str:
    """Packs an optional stop-loss price (and, for a Track B entry, the track and the
    base pivot) into the note field (no schema change — same (date, ticker, type, shares,
    price, amount, currency, note) shape as transactions.csv, per RISK_POT_PLAN.md §6).
    Tokens come first, so the extract_* helpers find them regardless of the free text."""
    parts = []
    if stop_loss is not None:
        parts.append(f"{_STOP_LOSS_PREFIX}{stop_loss}")
    if track:
        parts.append(f"{_TRACK_PREFIX}{track}")
    if pivot is not None:
        parts.append(f"{_PIVOT_PREFIX}{pivot}")
    if note:
        parts.append(note)
    return " ".join(parts)


def extract_stop_loss(note: str):
    """Reverses encode_note. Returns None if no stop-loss was recorded."""
    m = re.search(rf"{re.escape(_STOP_LOSS_PREFIX)}([0-9.]+)", note or "")
    return float(m.group(1)) if m else None


def extract_track(note: str) -> str:
    """'B' for a Track B entry, else 'A' (every row logged before 2026-09-25 is Track A)."""
    m = re.search(rf"(?:^|\s){re.escape(_TRACK_PREFIX)}([AB])\b", note or "")
    return m.group(1) if m else "A"


def extract_pivot(note: str):
    m = re.search(rf"(?:^|\s){re.escape(_PIVOT_PREFIX)}([0-9.]+)", note or "")
    return float(m.group(1)) if m else None


def track_b_base_stop(entry_price: float, pivot: float, stop_loss_pct: float,
                      pivot_buffer_pct: float) -> float:
    """Track B's starting floor: the TIGHTER (higher) of 'back inside the base' (pivot
    minus a buffer) and the hard -stop_loss_pct floor below entry. If the breakout is
    already far above the pivot, the hard floor binds; if it's just above, the pivot does."""
    return max(pivot * (1 - pivot_buffer_pct), entry_price * (1 - stop_loss_pct))


def open_tickers(transactions: list[Transaction]) -> list[str]:
    """Tickers with net shares > 0, in the order they first appear."""
    seen = []
    for t in transactions:
        if t.ticker not in seen:
            seen.append(t.ticker)
    return [t for t in seen if net_shares(transactions, t) > 1e-9]


def entry_date_for(transactions: list[Transaction], ticker: str) -> Date:
    """Earliest buy date for this ticker — the position's clock start. Risk-pot
    positions are single-shot entries (buy once, hold, sell once, per the plan), so this
    is unambiguous even if the ledger ever ends up with more than one buy row."""
    buys = sorted((t for t in transactions if t.ticker == ticker and t.type == "buy"), key=lambda t: t.date)
    if not buys:
        raise ValueError(f"No buy transaction on record for {ticker!r}")
    return buys[0].date


def stop_loss_for(transactions: list[Transaction], ticker: str):
    """Stop-loss recorded on the most recent buy for this ticker, or None."""
    buys = sorted((t for t in transactions if t.ticker == ticker and t.type == "buy"), key=lambda t: t.date)
    if not buys:
        return None
    return extract_stop_loss(buys[-1].note)


def track_for(transactions: list[Transaction], ticker: str) -> str:
    """Track recorded on the most recent buy for this ticker ('A' if none recorded)."""
    buys = sorted((t for t in transactions if t.ticker == ticker and t.type == "buy"), key=lambda t: t.date)
    return extract_track(buys[-1].note) if buys else "A"


def pivot_for(transactions: list[Transaction], ticker: str):
    buys = sorted((t for t in transactions if t.ticker == ticker and t.type == "buy"), key=lambda t: t.date)
    return extract_pivot(buys[-1].note) if buys else None


def exit_params(config: dict, track: str) -> dict:
    """The profit-lock ladder for a track. Track A = the pot-wide fields; Track B = the
    config's "track_b" block (or TRACK_B_DEFAULTS)."""
    if track == "B":
        return dict(TRACK_B_DEFAULTS, **(config.get("track_b") or {}))
    return {
        "stop_loss_pct": config.get("stop_loss_pct", 0.15),
        "breakeven_lock_pct": config["breakeven_lock_pct"],
        "trailing_stop_arm_pct": config["trailing_stop_arm_pct"],
        "trailing_stop_pct": config["trailing_stop_pct"],
    }


def last_review_date(entry_date: Date, extensions_log: list[tuple], ticker: str) -> Date:
    """The date this position was last explicitly reviewed: its most recent
    `risk-extend` date, or its entry date if it's never been reviewed. Renamed from
    last_checkpoint_date (RISK_POT_PLAN.md §4, revised 2026-09-02) — there's no fixed
    30-day cycle to anchor anymore, this just marks "since when do we measure the
    benchmark-relative return (§4.2) and the staleness backstop (§4.2)." extensions_log
    is storage.load_risk_extensions_log's [(date, ticker, reason), ...]."""
    review_dates = [d for d, t, _reason in extensions_log if t == ticker]
    if not review_dates:
        return entry_date
    return max(entry_date, max(review_dates))


def pct_return(old_price: float, new_price: float) -> float:
    """Percent return, e.g. 15.3 for +15.3%. Raises on a non-positive old_price rather
    than silently dividing by zero or returning a meaningless number."""
    if old_price <= 0:
        raise ValueError(f"old_price must be positive, got {old_price!r}")
    return (new_price / old_price - 1) * 100


def beats_benchmark(position_return_pct: float, benchmark_return_pct: float) -> bool:
    """The mechanical extend/exit bar (RISK_POT_PLAN.md §4.2): positive AND ahead of the
    benchmark over the same window — deliberately not "hit the target% or exit" (see
    RISK_POT_PLAN.md §3's honesty note on why the target isn't this bar). Evaluated
    every `risk-alert` run now (via review_needed below), not just at a fixed 30-day mark."""
    return position_return_pct > 0 and position_return_pct > benchmark_return_pct


def review_needed(position_return_pct: float, benchmark_return_pct: float, days_since_review: int,
                   min_review_days: int) -> bool:
    """The daily benchmark-relative flag (RISK_POT_PLAN.md §4.2) — beats_benchmark
    evaluated every day instead of once at a fixed checkpoint, gated by min_review_days
    so a fresh position's first few days of ordinary noise don't fire it. Mirrors
    TRANSACTION_ANALYSIS_GUIDE.md's own multi-day-not-single-day correction, applied
    here to the benchmark check instead of broker flow."""
    if days_since_review < min_review_days:
        return False
    return not beats_benchmark(position_return_pct, benchmark_return_pct)


def capital_at_work(transactions: list[Transaction]) -> float:
    """Sum of cost basis (avg_cost x shares) across every open position — what the
    Rp 1,000,000 cap (RISK_POT_PLAN.md §2) is actually checked against. A revolving
    figure: closing a position frees its cost basis back up, nothing is "used up" once."""
    total = 0.0
    for ticker in open_tickers(transactions):
        total += avg_cost_basis(transactions, ticker) * net_shares(transactions, ticker)
    return total


def effective_stop_loss(avg_cost: float, base_stop_loss, highest_price_since_entry: float,
                         current_price: float, breakeven_lock_pct: float,
                         trailing_stop_arm_pct: float, trailing_stop_pct: float):
    """The two-stage profit-lock ladder (RISK_POT_PLAN.md §4.1) — this IS the take-profit
    mechanism for the Risk Pot: there's no fixed target price, the stop just ratchets up
    as the position runs, and an exit happens exactly when price falls off its own peak,
    never sold at a predetermined number. Returns None if base_stop_loss was never set
    (nothing to compute a floor from).

    Both stages arm off the position's *peak* return (highest price since entry vs.
    avg_cost), not today's return — a trailing stop that disarms on an ordinary pullback
    isn't a trailing stop. `current_price` only guards against the peak lagging a fresh
    intraday high that hasn't landed in the daily close history yet.

    Stage 1 (breakeven lock): once the peak gain >= breakeven_lock_pct, the floor rises
    from base_stop_loss to avg_cost — the worst case becomes "no loss," permanently.
    Stage 2 (trailing lock): once the peak gain >= trailing_stop_arm_pct, the floor
    becomes the peak price minus trailing_stop_pct — only ever rises as new highs print,
    this is where real profit actually gets banked.
    """
    if base_stop_loss is None:
        return None
    peak = max(highest_price_since_entry, current_price)
    peak_pl_pct = pct_return(avg_cost, peak)
    stop = base_stop_loss
    if peak_pl_pct >= breakeven_lock_pct * 100:
        stop = max(stop, avg_cost)
    if peak_pl_pct >= trailing_stop_arm_pct * 100:
        trailing = peak * (1 - trailing_stop_pct)
        stop = max(stop, trailing)
    return stop


@dataclass
class RiskPosition:
    ticker: str
    shares: float
    avg_cost: float
    current_price: float
    entry_date: Date
    review_anchor: Date              # since when we measure benchmark return / staleness
    stop_loss: float                 # base -15% level from risk-log-buy; None if never set
    highest_price_since_entry: float
    breakeven_lock_pct: float
    trailing_stop_arm_pct: float
    trailing_stop_pct: float
    track: str = "A"                 # "B" = early special situation, own exit ladder (§3.1b)
    pivot: float = None              # Track B: the base pivot the breakout cleared

    @property
    def value_idr(self) -> float:
        return self.shares * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_cost

    @property
    def unrealized_pl(self) -> float:
        return self.value_idr - self.cost_basis

    @property
    def unrealized_pl_pct(self) -> float:
        return pct_return(self.avg_cost, self.current_price)

    @property
    def effective_stop(self):
        """The live stop-loss/take-profit floor (RISK_POT_PLAN.md §4.1) — base stop,
        or a breakeven/trailing lock if the position has ever run far enough in profit
        to arm one (keyed off the peak, so a pullback can't silently disarm it). None if
        no base stop-loss was ever recorded."""
        return effective_stop_loss(
            self.avg_cost, self.stop_loss, self.highest_price_since_entry, self.current_price,
            self.breakeven_lock_pct, self.trailing_stop_arm_pct, self.trailing_stop_pct,
        )

    def days_since_entry(self, today: Date) -> int:
        return (today - self.entry_date).days

    def days_since_review(self, today: Date) -> int:
        return (today - self.review_anchor).days

    def stop_loss_breached(self) -> bool:
        stop = self.effective_stop
        return stop is not None and self.current_price <= stop

    def stale(self, today: Date, stale_review_days: int) -> bool:
        """Soft backstop only (RISK_POT_PLAN.md §4.2) — not a forced decision, just a
        nudge that this position has gone a while with no flag having fired at all."""
        return self.days_since_review(today) >= stale_review_days


def build_position(transactions: list[Transaction], extensions_log: list[tuple], ticker: str,
                    current_price: float, highest_price_since_entry: float, config: dict) -> RiskPosition:
    entry = entry_date_for(transactions, ticker)
    anchor = last_review_date(entry, extensions_log, ticker)
    track = track_for(transactions, ticker)
    ladder = exit_params(config, track)
    return RiskPosition(
        ticker=ticker,
        shares=net_shares(transactions, ticker),
        avg_cost=avg_cost_basis(transactions, ticker),
        current_price=current_price,
        entry_date=entry,
        review_anchor=anchor,
        stop_loss=stop_loss_for(transactions, ticker),
        highest_price_since_entry=highest_price_since_entry,
        breakeven_lock_pct=ladder["breakeven_lock_pct"],
        trailing_stop_arm_pct=ladder["trailing_stop_arm_pct"],
        trailing_stop_pct=ladder["trailing_stop_pct"],
        track=track,
        pivot=pivot_for(transactions, ticker),
    )
