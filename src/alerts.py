"""Phase 2 alert engine — implements PLAN.md §8.3's triggers, split by real confidence
level (see BUILD_PLAN.md's "Phase 2" section for the full rationale):

  Tier A — automatable from price + dividend history alone, high confidence.
  Tier B — best-effort fundamentals (payout ratio, debt/equity) — yfinance sometimes
           has these for foreign large-caps, sometimes doesn't; every Tier B finding
           is explicitly labeled unverified rather than treated as a clean signal.
  Tier C — credit downgrades, restructuring, M&A news. No data API for this at all —
           it needs an actual web search, which this script cannot do. That part is
           the scheduled Claude Code routine's job, on top of this script's output.

Every check is defensive: yfinance data can be missing/thin for any given ticker on
any given day, and a check that can't get the data it needs skips that holding rather
than guessing or crashing.
"""
from dataclasses import dataclass
from datetime import date as Date, timedelta

from src import storage


@dataclass
class Finding:
    tier: str        # "A" or "B"
    ticker: str       # or "PORTFOLIO" for a portfolio-level finding
    severity: str      # "info" | "watch" | "warning"
    message: str
    check: str = ""    # stable identifier for the check type (e.g. "drift", "dividend_cut") —
                        # used to tell "same signal as last time" apart from "brand new signal"
    repeat: bool = False  # set by annotate_repeats() — True if this same (ticker, check)
                           # also fired on the immediately preceding run


def _dividend_history(ticker):
    import yfinance as yf
    return yf.Ticker(ticker).dividends  # pandas Series: DatetimeIndex -> amount


def _trailing_12mo_dividend(divs, as_of: Date) -> float:
    total = 0.0
    for idx, amt in zip(divs.index, divs.values):
        d = idx.date()
        if 0 <= (as_of - d).days <= 365:
            total += float(amt)
    return total


# --- Tier A ---------------------------------------------------------------------------

def check_allocation_drift(states, band_pp: int) -> list[Finding]:
    findings = []
    for s in states:
        if abs(s.drift_pp) > band_pp:
            severity = "warning" if abs(s.drift_pp) > band_pp * 1.5 else "watch"
            findings.append(Finding("A", s.target.ticker, severity,
                f"Allocation drift {s.drift_pp:+.1f}pp beyond the {band_pp}pp band. New "
                f"contributions already redirect toward this via the buy calculator "
                f"(PLAN.md §3 rule 3).", check="drift"))
    return findings


def check_dividend_cuts(targets) -> list[Finding]:
    findings = []
    for t in targets:
        if t.price_source != "yfinance":
            continue
        try:
            divs = _dividend_history(t.ticker)
        except Exception:
            continue
        if divs.empty or len(divs) < 2:
            continue
        dates = [i.date() for i in divs.index]
        latest_date, latest_amt = dates[-1], float(divs.iloc[-1])
        target_prior = latest_date - timedelta(days=365)
        candidates = [(d, float(a)) for d, a in zip(dates[:-1], divs.iloc[:-1])
                      if abs((d - target_prior).days) <= 45]
        if not candidates:
            continue
        prior_date, prior_amt = min(candidates, key=lambda c: abs((c[0] - target_prior).days))
        if latest_amt < prior_amt - 1e-9:
            pct = (latest_amt / prior_amt - 1) * 100
            findings.append(Finding("A", t.ticker, "warning",
                f"Dividend cut: {latest_amt:.4g} on {latest_date} vs {prior_amt:.4g} on "
                f"{prior_date} (~1yr prior), {pct:.1f}%. Hard trigger, no threshold needed "
                f"(PLAN.md §8.3) — forces a Tier 2 deep review (§8.5), not an automatic sell.",
                check="dividend_cut"))
    return findings


def check_sharp_price_moves(targets, data_dir, threshold_pct=-15.0, window_days=30) -> list[Finding]:
    findings = []
    for t in targets:
        hist = storage.load_price_history(data_dir, t.ticker)
        if len(hist) < 2:
            continue
        latest_date, latest_price = hist[-1]
        cutoff = latest_date - timedelta(days=window_days)
        past = [p for d, p in hist if d <= cutoff]
        if not past or past[-1] <= 0:
            continue
        past_price = past[-1]
        change_pct = (latest_price / past_price - 1) * 100
        if change_pct <= threshold_pct:
            findings.append(Finding("A", t.ticker, "warning",
                f"Price down {abs(change_pct):.1f}% over ~{window_days} days ({past_price:.4g} -> "
                f"{latest_price:.4g}). Check this against its benchmark (below) before treating "
                f"it as holding-specific — equity drawdowns of -20% to -55% are normal over a full "
                f"cycle (PLAN.md §8.3).", check="sharp_price_move"))
    return findings


def check_total_return(xirr_rate, xirr_reliable, floor=0.07) -> list[Finding]:
    if xirr_rate is None or not xirr_reliable:
        return []
    if xirr_rate < floor:
        return [Finding("A", "PORTFOLIO", "warning",
            f"Trailing annualized total return (XIRR) {xirr_rate*100:.1f}% is below the "
            f"{floor*100:.0f}% floor from PLAN.md §7.5. Judge against the multi-year trend, "
            f"not this reading alone.", check="total_return")]
    return []


def check_chowder_number(targets, prices_by_ticker) -> list[Finding]:
    """Chowder Number = current yield + trailing 5yr dividend growth (CAGR).
    Thresholds from PLAN.md §8.1: 12% if yield >=3%, 15% if <3% (none of the current
    holdings are utility/REIT-like, so that lower 8% threshold isn't wired up here —
    extend if a future holding needs it). Run at the annual deep review, not as a
    monthly trip-wire — a below-threshold reading means "worth comparing to
    alternatives," not "sell now."
    """
    findings = []
    today = Date.today()
    for t in targets:
        if t.price_source != "yfinance":
            continue
        price = prices_by_ticker.get(t.ticker)
        if not price:
            continue
        try:
            divs = _dividend_history(t.ticker)
        except Exception:
            continue
        if divs.empty:
            continue
        trailing_12mo = _trailing_12mo_dividend(divs, today)
        five_yr_ago = today - timedelta(days=365 * 5)
        past_12mo = sum(
            float(a) for idx, a in zip(divs.index, divs.values)
            if 0 <= (five_yr_ago - idx.date()).days <= 365
        )
        if trailing_12mo <= 0 or past_12mo <= 0:
            continue
        current_yield = trailing_12mo / price
        growth_5yr_cagr = (trailing_12mo / past_12mo) ** (1 / 5) - 1
        chowder = current_yield + growth_5yr_cagr
        threshold = 0.12 if current_yield >= 0.03 else 0.15
        if chowder < threshold:
            findings.append(Finding("A", t.ticker, "watch",
                f"Chowder Number {chowder*100:.1f}% (yield {current_yield*100:.1f}% + 5yr div "
                f"growth {growth_5yr_cagr*100:.1f}%) is below its {threshold*100:.0f}% threshold "
                f"— worth comparing against alternatives at the next annual review (PLAN.md §8.1).",
                check="chowder_number"))
    return findings


def check_yield_trap(targets, prices_by_ticker) -> list[Finding]:
    """Approximation, documented rather than hidden: uses TODAY's trailing-12mo dividend
    rate divided by prices over the last 5 years, rather than the dividend rate that was
    actually current at each historical point (which would need a full dividend-history
    reconstruction). Reasonable proxy for "is the current yield unusually high," not exact.
    """
    import yfinance as yf
    findings = []
    today = Date.today()
    for t in targets:
        if t.price_source != "yfinance":
            continue
        price = prices_by_ticker.get(t.ticker)
        if not price:
            continue
        try:
            divs = _dividend_history(t.ticker)
            hist = yf.Ticker(t.ticker).history(period="5y")
        except Exception:
            continue
        if divs.empty or hist.empty:
            continue
        trailing_12mo = _trailing_12mo_dividend(divs, today)
        if trailing_12mo <= 0:
            continue
        closes = [float(c) for c in hist["Close"] if c and c > 0]
        if not closes:
            continue
        avg_5yr_yield = sum(trailing_12mo / c for c in closes) / len(closes)
        current_yield = trailing_12mo / price
        price_falling = price < closes[0]
        if avg_5yr_yield > 0 and current_yield > avg_5yr_yield * 1.3 and price_falling:
            findings.append(Finding("A", t.ticker, "warning",
                f"Yield-trap pattern: current yield {current_yield*100:.1f}% is well above its "
                f"~5yr average {avg_5yr_yield*100:.1f}%, and price has fallen over that window — "
                f"a rising yield from a falling price is a warning sign, not a bonus "
                f"(PLAN.md §3 rule 4 / §8.3).", check="yield_trap"))
    return findings


def check_benchmark_relative(targets, threshold_pp=15.0) -> list[Finding]:
    """Price-only total return for both the holding and its benchmark (dividends not
    included on either side) — a known simplification; the point is catching a large,
    sustained relative gap, not a precise total-return-vs-benchmark figure."""
    import yfinance as yf
    findings = []
    for t in targets:
        if not t.benchmark:
            continue
        try:
            stock_hist = yf.Ticker(t.ticker).history(period="1y")
            bench_hist = yf.Ticker(t.benchmark).history(period="1y")
        except Exception:
            continue
        if stock_hist.empty or bench_hist.empty:
            continue
        stock_start, stock_end = float(stock_hist["Close"].iloc[0]), float(stock_hist["Close"].iloc[-1])
        bench_start, bench_end = float(bench_hist["Close"].iloc[0]), float(bench_hist["Close"].iloc[-1])
        if stock_start <= 0 or bench_start <= 0:
            continue
        stock_return = stock_end / stock_start - 1
        bench_return = bench_end / bench_start - 1
        gap_pp = (stock_return - bench_return) * 100
        if gap_pp < -threshold_pp:
            findings.append(Finding("A", t.ticker, "warning",
                f"Trailing 12mo return {stock_return*100:.1f}% trails its benchmark "
                f"{t.benchmark} ({bench_return*100:.1f}%) by {abs(gap_pp):.1f}pp — this is what "
                f"separates 'this holding broke' from 'the market moved' (PLAN.md §8.3).",
                check="benchmark_relative"))
    return findings


# --- Tier B ---------------------------------------------------------------------------

def check_fundamentals_tier_b(targets) -> list[Finding]:
    """Best-effort. yfinance's fundamentals fields for foreign large-caps are known-
    unreliable (BUILD_PLAN.md) — every finding here is explicitly labeled UNVERIFIED,
    never presented as a clean pass/fail the way Tier A findings are."""
    import yfinance as yf
    findings = []
    for t in targets:
        if t.price_source != "yfinance":
            continue
        try:
            info = yf.Ticker(t.ticker).info
        except Exception:
            findings.append(Finding("B", t.ticker, "info",
                "Could not fetch fundamentals data this run — Tier B unavailable, not a signal either way.",
                check="fundamentals"))
            continue
        payout = info.get("payoutRatio")
        debt_to_equity = info.get("debtToEquity")
        parts = []
        if payout is not None:
            parts.append(f"payout ratio {payout*100:.0f}%")
        if debt_to_equity is not None:
            parts.append(f"debt/equity {debt_to_equity:.0f}")
        if not parts:
            findings.append(Finding("B", t.ticker, "info",
                "No fundamentals fields available from yfinance for this ticker — "
                "unverified either way, check manually at the deep review.", check="fundamentals"))
            continue
        severity = "warning" if (payout is not None and payout > 0.90) else "info"
        findings.append(Finding("B", t.ticker, severity,
            "[UNVERIFIED, confirm at deep review — free-API fundamentals for foreign "
            "large-caps are unreliable] " + ", ".join(parts), check="fundamentals"))
    return findings


# --- repeat-tracking ---------------------------------------------------------------------
# PLAN.md §8.5: a flag repeating on 2 consecutive checks forces a Tier 2 deep review,
# not just another "watch." The checks above have no memory of prior runs on their
# own — this is what gives them one, using data/alerts_log.csv.

def annotate_repeats(findings: list[Finding], data_dir) -> list[Finding]:
    log_rows = storage.load_alerts_log(data_dir)
    today = Date.today()
    prior_dates = sorted({r[0] for r in log_rows if r[0] < today})
    if not prior_dates:
        return findings  # first run ever — nothing to compare against
    prev_run_date = prior_dates[-1]
    prev_keys = {(r[2], r[3]) for r in log_rows if r[0] == prev_run_date}  # (ticker, check)
    for f in findings:
        if (f.ticker, f.check) in prev_keys:
            f.repeat = True
    return findings


def log_run(findings: list[Finding], data_dir):
    """Persist this run's findings so the *next* run can tell what's new vs. repeating."""
    storage.append_alerts_log(data_dir, findings, Date.today())


# --- alerts' weigh-in on `sav buy` -------------------------------------------------------
# The full extent of alerts' influence on buying: `sav buy` reads this and shows an
# unmissable banner, never a silent ratio change. PLAN.md §8.5 requires the full §8.1
# scorecard — including Tier C qualitative research this script cannot do — before any
# Trim/Exit decision, so Tier A/B signals alone are deliberately never turned into a
# computed reallocation number. The human decision, once made, goes through
# `sav set-target` instead (see calculator.redistribute_target_weight).

@dataclass
class ReviewStatus:
    last_run_date: Date | None
    days_since: int | None
    tier2: list        # [(ticker, check), ...] — hard triggers or 2x-repeated flags, right now
    stale: bool          # no alerts run recently (or ever) — worth running before this buy


def deep_review_status(data_dir, review_after_days: int = 365):
    """Whether a full live-session deep review is due — covers BOTH the main stocks
    (PLAN.md §8.1 scorecard / §8.5 decision ladder) and the temp_rest fund pick,
    since neither can be mechanically re-evaluated by a plain script: judging a stock
    needs the qualitative Tier C research (news/credit/restructuring) this script
    can't do, and re-checking the fund needs a live fetch of your broker's actual listing —
    neither your broker nor OJK expose a stable data feed a script could query directly.

    Reads data/deep_review_log.csv, which `sav set-target` and `sav temp-rest-set-fund`
    both append to automatically when actually used, and which `sav review-log` covers
    for the "reviewed, nothing needed to change" case — any of the three resets the clock.

    Returns (due: bool, days_since_last_review: int | None). days_since is None if no
    review has ever been logged at all.
    """
    log_rows = storage.load_deep_review_log(data_dir)
    if not log_rows:
        return True, None
    days_since = (Date.today() - log_rows[-1][0]).days
    return days_since > review_after_days, days_since


def pending_review_from_log(data_dir, stale_after_days: int = 35) -> ReviewStatus:
    """Reads alerts_log.csv only — never re-runs the checks themselves (that would mean
    another round of yfinance calls on every `sav buy`, and would defeat the point of
    the log: this reflects what you've actually reviewed, not a fresh guess).
    """
    rows = storage.load_alerts_log(data_dir)
    if not rows:
        return ReviewStatus(last_run_date=None, days_since=None, tier2=[], stale=True)

    dates = sorted({r[0] for r in rows})
    last_date = dates[-1]
    days_since = (Date.today() - last_date).days
    last_rows = [r for r in rows if r[0] == last_date]

    hard_triggers = {(r[2], r[3]) for r in last_rows if r[3] == "dividend_cut"}
    repeats = set()
    if len(dates) >= 2:
        prev_keys = {(r[2], r[3]) for r in rows if r[0] == dates[-2]}
        repeats = {(r[2], r[3]) for r in last_rows if (r[2], r[3]) in prev_keys}

    tier2 = sorted(hard_triggers | repeats)
    return ReviewStatus(last_run_date=last_date, days_since=days_since, tier2=tier2,
                         stale=days_since > stale_after_days)


# --- entry point -----------------------------------------------------------------------

def run_all(targets, band_pp, states, prices_by_ticker, xirr_rate, xirr_reliable, data_dir) -> list[Finding]:
    findings = []
    findings += check_allocation_drift(states, band_pp)
    findings += check_dividend_cuts(targets)
    findings += check_sharp_price_moves(targets, data_dir)
    findings += check_total_return(xirr_rate, xirr_reliable)
    findings += check_chowder_number(targets, prices_by_ticker)
    findings += check_yield_trap(targets, prices_by_ticker)
    findings += check_benchmark_relative(targets)
    findings += check_fundamentals_tier_b(targets)
    findings = annotate_repeats(findings, data_dir)
    log_run(findings, data_dir)
    return findings
