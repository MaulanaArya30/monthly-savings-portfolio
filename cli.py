#!/usr/bin/env python3
"""Monthly Savings Portfolio — CLI entry point.

Running this with no arguments, or with -h/--help, always shows the full command
reference (BUILD_PLAN.md: "I can forget, make this show every time").
"""
import argparse
import json
import sys
from datetime import date as Date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import alerts, calculator, risk_pot, storage, tracker  # noqa: E402
from src.models import Dividend, Transaction  # noqa: E402
from src.prices import (  # noqa: E402
    bootstrap_price_history_if_needed, get_fx_rate_usdidr, get_highest_close_since, get_live_price,
    get_price, get_price_on_or_before, price_volume_signal, trailing_return_and_liquidity,
)
from src.storage import DataError  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "targets.json"
TEMP_REST_CONFIG_PATH = PROJECT_ROOT / "config" / "temp_rest.json"
RISK_CONFIG_PATH = PROJECT_ROOT / "config" / "risk_config.json"
RISK_WATCHLIST_PATH = PROJECT_ROOT / "config" / "risk_watchlist.json"
BUYBACK_CALENDAR_PATH = PROJECT_ROOT / "config" / "buyback_calendar.json"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"

# --- undo -------------------------------------------------------------------------
# Which files each logging command can mutate. `main()` snapshots these before running
# one of these commands and journals the "before" state to UNDO_JOURNAL_PATH if anything
# actually changed; `sav undo` restores that snapshot. Deliberately excludes `buy`,
# `status`, and `alerts` — those touch price_history.csv/alerts_log.csv as an incidental
# side effect of fetching prices, not as a user-entered fact, and journaling them would
# let a routine `status` check silently bury the undo record for an actual mistake.
UNDO_JOURNAL_PATH = DATA_DIR / ".last_action.json"
UNDO_FILES = {
    "log-buy": [DATA_DIR / "transactions.csv"],
    "log-sell": [DATA_DIR / "transactions.csv"],
    "log-dividend": [DATA_DIR / "dividends.csv"],
    "set-price": [DATA_DIR / "manual_prices.csv", DATA_DIR / "price_history.csv"],
    "set-target": [CONFIG_PATH, DATA_DIR / "target_changes_log.csv", DATA_DIR / "deep_review_log.csv"],
    "temp-rest-deposit": [DATA_DIR / "temp_rest_log.csv"],
    "temp-rest-redeem": [DATA_DIR / "temp_rest_log.csv"],
    "temp-rest-set-fund": [TEMP_REST_CONFIG_PATH, DATA_DIR / "temp_rest_fund_log.csv", DATA_DIR / "deep_review_log.csv"],
    "review-log": [DATA_DIR / "deep_review_log.csv"],
    "risk-log-buy": [DATA_DIR / "risk_transactions.csv"],
    "risk-log-sell": [DATA_DIR / "risk_transactions.csv"],
    "risk-extend": [DATA_DIR / "risk_extensions_log.csv"],
    "risk-set-cap": [RISK_CONFIG_PATH, DATA_DIR / "risk_cap_log.csv"],
    # risk-alert excluded, same reasoning as `alerts` above — its file write is incidental
    # history, not a user-entered fact.
}

HELP_EPILOG = """\
Monthly Savings Portfolio — commands

  buy <amount_idr>
      Calculate this month's buy split across holdings.
      Example: python cli.py buy 5000000

  log-buy <ticker> <shares> <price> [--amount] [--date YYYY-MM-DD] [--note "..."]
      Record an actual purchase. `price` is the per-share market price (used for cost
      basis); if the actual cash debited differs (broker fee, rounding), pass --amount
      to log the real figure instead of shares×price. Without --amount, amount = shares×price.
      --amount is ALWAYS in the holding's own currency (IDR for ASII/BBRI, USD for KO) —
      never the IDR your broker actually debited for a USD holding, that gets converted to IDR
      again downstream and would badly overstate what was invested.
      Example: python cli.py log-buy ASII.JK 400 4770
      Example: python cli.py log-buy ASII.JK 300 5100 --amount 1532550   # includes a Rp2,550 fee

  log-sell <ticker> <shares> <price> [--amount] [--date YYYY-MM-DD] [--note "..."]
      Record an actual sale — prints realized gain/loss (average cost basis) before confirming.
      Same --amount override as log-buy, for a sale that nets a different amount than
      shares×price (e.g. the IDX final tax withheld on sells).
      Example: python cli.py log-sell BBRI.JK 100 4200

  undo
      Undo the last logging command (log-buy, log-sell, log-dividend, set-price,
      set-target, temp-rest-deposit, temp-rest-redeem, temp-rest-set-fund, review-log,
      risk-log-buy, risk-log-sell, risk-set-cap, risk-extend).
      Single level only — restores exactly the files that command changed to how they
      were right before it ran, then clears the undo record, so it can't be chained
      further back. Asks for confirmation, showing which command and files it's about
      to revert. `buy`/`status`/`alerts` aren't tracked (nothing to undo there).
      Example: python cli.py undo

  log-dividend <ticker> <amount> [--date YYYY-MM-DD]
      Record a dividend actually received.
      Example: python cli.py log-dividend ASII.JK 45000

  status
      Show current holdings, value, drift, and blended yield (gross + net).
      Example: python cli.py status

  set-price <ticker> <price> [--date YYYY-MM-DD]
      Manually record a price — needed for the gold ETF (no price API yet).
      Example: python cli.py set-price XMES.JK 780

  alerts
      Run the alert checks (drift, dividend cuts, yield-trap, price moves, Chowder
      Number, benchmark-relative). Tier C (credit downgrades/news) needs a web search,
      not covered here — that's the scheduled routine's job on top of this.
      Logs to data/alerts_log.csv, which `buy` reads (see set-target below).
      For any ticker that hits Tier 2 (hard trigger or a repeated flag), also shows
      an illustrative impact preview — NOT a recommendation, just ripple-effect
      sizing (-5pp/-10pp/full exit) so you can judge severity before deciding.
      Once a year (or on any Tier 2 finding), prints a "FOR A LIVE CLAUDE SESSION" block
      at the top — structured for pasting straight into a conversation so the review of
      both the main stocks and the temp-rest fund can happen right away, still plain
      English so it's readable on its own too. Neither can be mechanically re-evaluated
      by this script — no stable data feed for either.
      Example: python cli.py alerts

  set-target <ticker> <percent> [--reason "..." | --preview]
      Change a holding's target weight after your own review (a Tier 3 Trim or the
      first step of a Tier 4 Exit — PLAN.md §8.5). Redistributes the difference across
      the other holdings proportionally. Only affects future `buy` contributions —
      existing shares are untouched; use log-sell separately for a full Exit. Logged
      to data/target_changes_log.csv with your --reason, for an audit trail.
      Use --preview to dry-run any number of your own choosing — shows the result,
      writes nothing, no --reason needed.
      Example: python cli.py set-target ASII.JK 30 --reason "payout ratio 91%, repeat flag 2026-09"
      Example: python cli.py set-target ASII.JK 25 --preview

  temp-rest-status
      Show what's currently parked in the temp-rest money-market fund and its history.
      Example: python cli.py temp-rest-status

  temp-rest-deposit <amount_idr> [--date] [--note]
      Log that you bought the temp-rest fund with leftover cash (`buy` suggests this
      when leftover crosses config/temp_rest.json's significant_threshold_idr).
      Example: python cli.py temp-rest-deposit 421000

  temp-rest-redeem <amount_idr> [--date] [--note]
      Log pulling money back out of temp-rest (T+1/T+2 settlement — do this a few days
      before your buy day, then fold the landed amount into that month's contribution).
      Example: python cli.py temp-rest-redeem 421000

  temp-rest-set-fund <name> --reason "..."
      Change which fund temp-rest uses — a yearly review, same audit pattern as
      set-target. Which fund is a research question, not something this script picks.
      Resets the yearly deep-review clock (see review-log below).
      Example: python cli.py temp-rest-set-fund "Fund Name Here" --reason "switched providers"

  review-log --reason "..."
      Log that a full live-session deep review happened today, even when nothing
      changed — set-target and temp-rest-set-fund already do this automatically when
      actually used; this covers the "reviewed, no changes needed" case so the yearly
      clock (data/deep_review_log.csv) doesn't stay stuck on "due" between real changes.
      Example: python cli.py review-log --reason "2027 annual review: all 3 holdings still pass §8.1, temp-rest fund unchanged"

  risk-log-buy <ticker> <shares> <price> [--amount] [--stop-loss] [--track A|B] [--pivot] [--date] [--note]
      Record a Risk Pot entry — see RISK_POT_PLAN.md. Fully separate ledger
      (data/risk_transactions.csv) from the core sleeve: own capital, own commands,
      never touches transactions.csv/targets.json. Always IDR. --stop-loss records the
      exit trigger price up front (packed into the note field, no schema change).
      --track B marks an early special situation (RISK_POT_PLAN.md §3.1b) with its own
      exit ladder (risk_config.json "track_b": breakeven after +30%, 25% trail after
      +50%); it needs --pivot (the base pivot the breakout cleared), and without
      --stop-loss the base stop is computed as the higher of pivot -3% and entry -15%.
      Example: python cli.py risk-log-buy EMAS.JK 100 6850 --stop-loss 5822.5
      Example: python cli.py risk-log-buy SINI.JK 100 4400 --track B --pivot 4000

  risk-log-sell <ticker> <shares> <price> [--amount] [--date] [--note]
      Record a Risk Pot exit — prints realized gain/loss first, same as log-sell.
      Example: python cli.py risk-log-sell EMAS.JK 100 7200

  risk-set-cap <amount_idr> --reason "..."
      Change the Risk Pot's revolving capital-at-work cap (config/risk_config.json's
      pot_cap_idr) — use this when you add or pull out risk-pot capital. Warns (doesn't
      block) if the new cap would be below what's currently deployed in open positions;
      close/trim a position with risk-log-sell first if that's what you actually meant.
      Logged to data/risk_cap_log.csv with your --reason, for an audit trail.
      Example: python cli.py risk-set-cap 1500000 --reason "topped up another Rp500,000"

  risk-extend <ticker> --reason "..." [--force]
      Log today's live review of a Risk Pot position and keep it open — no fixed
      calendar cycle to renew (RISK_POT_PLAN.md §4, revised 2026-09-02): usable the
      moment `risk-alert` flags something, not gated to a scheduled date. Refuses
      unless the position's return since its last review is positive AND beats the
      configured benchmark (^JKSE) over the same window — pass --force to override
      (still requires --reason). Resets the review anchor to today either way.
      Example: python cli.py risk-extend EMAS.JK --reason "still beating JKSE, thesis intact"

  risk-screen
      Rank config/risk_watchlist.json by trailing 1mo/3mo return vs the benchmark
      (RISK_POT_PLAN.md §3.1 steps 1-2) — mechanical momentum ranking only, excludes
      tickers already in the core sleeve. NOT a live Kompas100/JKSE index scan — that
      file is a hand-maintained candidate list, expand it as you research real names.
      Still needs a catalyst check, sizing consultation, and a thesis/stop-loss (steps
      3-6) before `risk-log-buy` — this just saves the per-ticker legwork of step 2.
      Example: python cli.py risk-screen

  risk-buyback-watch [--days N]
      Flag config/buyback_calendar.json programs ending within N days (default 21) —
      the real re-rating trigger is usually a RENEWAL announced right as a program
      closes (RISK_POT_PLAN.md §3.10's ERAA case study), so this exists to catch that
      early instead of after the fact (§3.12). Mechanical only — an ending program is a
      reason to check for a renewal announcement, not itself a signal to trade. Dates
      are hand-verified when added, not live-fetched — re-check via the ticker's own
      Corp. Action tab before trusting an "ends soon" flag.
      Example: python cli.py risk-buyback-watch --days 14

  risk-status
      Show every open Risk Pot position: days held, unrealized P&L, the base stop-loss
      and the live effective stop (breakeven/trailing lock if one's armed — the
      take-profit mechanism, RISK_POT_PLAN.md §4.1), days since last review, and
      capital-at-work vs. the configured Rp 1,000,000 cap.
      Example: python cli.py risk-status

  risk-alert
      Run every morning — this is the actual daily decision trigger for a
      short-horizon pot, not a countdown to a scheduled date (RISK_POT_PLAN.md §4,
      revised 2026-09-02). Flags, per position: an effective-stop breach (stop-loss or
      take-profit — exit now), a Wyckoff price-volume "danger" day (worth a look, not
      an automatic exit), the benchmark-relative bar failing (once min_review_days has
      passed), or the pot exceeding its cap. A position with no flag needs nothing that
      day — one can flag on day 3, another can run flag-free for weeks. Logs findings
      to data/risk_alerts_log.csv.
      Example: python cli.py risk-alert

Ticker names must match config/targets.json exactly (e.g. ASII.JK, not ASII).
Run any command with -h for its full parameter list.
"""


# --- formatting helpers ---------------------------------------------------------------

def fmt_idr(x: float) -> str:
    return f"Rp {x:,.0f}"


def fmt_money(x: float, currency: str) -> str:
    if currency == "IDR":
        return fmt_idr(x)
    return f"${x:,.2f}"


def fmt_pct(x, digits=2) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.{digits}f}%"


def fmt_pp(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.1f}pp"


def fmt_signed_pct(x: float) -> str:
    """A plain percent return (e.g. Risk Pot unrealized P&L%), not a drift-in-
    percentage-points figure — that's fmt_pp above."""
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}%"


def render_blocks(blocks) -> str:
    """blocks: [(title, [(label, value), ...]), ...] — a narrow, vertical layout for
    the console that never depends on terminal width. A wide table breaks in a
    terminal that can't scroll horizontally (it just wraps and misaligns); this can't."""
    lines = []
    for title, fields in blocks:
        lines.append(title)
        label_width = max((len(label) for label, _ in fields), default=0)
        for label, value in fields:
            lines.append(f"  {label.ljust(label_width)}  {value}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def render_markdown_table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown_block(title, fields) -> str:
    """Markdown counterpart to render_blocks — same (title, [(label, value), ...]) shape,
    rendered as a labeled two-column table instead of a plain paragraph."""
    lines = [f"**{title}**", "", "| Field | Value |", "|---|---|"]
    lines.extend(f"| {label} | {value} |" for label, value in fields)
    return "\n".join(lines)


def render_review_banner(review) -> str:
    """The full extent of `sav alerts`' weigh-in on `sav buy`: an unmissable nudge read
    from the last logged alerts run, never a silent ratio change. See
    alerts.pending_review_from_log for why a computed reallocation number isn't here.
    """
    if review.last_run_date is None:
        return ("Note: `sav alerts` has never been run — run it before buying so you're not "
                "flying blind on dividend cuts, drift, etc.")
    if review.tier2:
        lines = [
            f"REVIEW NEEDED before buying (from `sav alerts` on {review.last_run_date.isoformat()}, "
            f"{review.days_since}d ago) — PLAN.md §8.5 Tier 2:",
        ]
        for ticker, check in review.tier2:
            lines.append(f"  - {ticker}: {check}")
        lines.append(
            "  This does NOT change today's split automatically. Tier 2 means rerun the full "
            "§8.1 scorecard yourself (including the Tier C research this tool can't do) before "
            "deciding. If you decide to reduce a position afterward: "
            "`sav set-target <ticker> <percent> --reason \"...\"`."
        )
        return "\n".join(lines)
    if review.stale:
        return (f"Note: `sav alerts` hasn't been run in {review.days_since} days — worth a quick "
                f"check before this buy.")
    return (f"Reviewed clean: last `sav alerts` run {review.days_since}d ago "
            f"({review.last_run_date.isoformat()}), no Tier 2 flags.")


def render_impact_preview(ticker, targets, scenarios) -> str:
    """Illustrative-only ripple-effect preview shown by `sav alerts` for a Tier 2
    ticker, and by `sav set-target --preview`. Never a suggestion of the "correct"
    size — see calculator.preview_reallocation_scenarios' docstring."""
    if not scenarios:
        return ""
    current = {t.ticker: t.target_weight for t in targets}
    lines = [
        f"Impact preview for {ticker} (illustrative only — NOT a recommendation, nothing applied):",
        "  Currently:  " + " | ".join(f"{tk} {fmt_pct(w, 1)}" for tk, w in current.items()),
    ]
    label_width = max(len(label) for label, _ in scenarios)
    for label, scenario_targets in scenarios:
        weights = {t.ticker: t.target_weight for t in scenario_targets}
        lines.append(f"  If {label.ljust(label_width)}  " + " | ".join(f"{tk} {fmt_pct(w, 1)}" for tk, w in weights.items()))
    lines.append(
        "  These step sizes are arbitrary round numbers for comparison, not derived from any "
        "formula — the real number is your call after the full §8.1 review. Preview your own "
        "exact number anytime: sav set-target <ticker> <percent> --preview"
    )
    return "\n".join(lines)


def build_temp_rest_block(temp_rest_balance, temp_rest_config, total_leftover):
    """`sav buy`'s temp-rest section, in the same (title, [(label, value), ...]) shape as
    a holding block (see render_blocks) — reports what's currently parked (if anything)
    and, only when this month's leftover meets your own configured threshold, suggests
    parking it. Never applies anything itself. See calculator.temp_rest_balance and
    cmd_temp_rest_deposit/redeem. Returns None when there's nothing to report (nothing
    parked and no leftover this month).
    """
    if temp_rest_balance <= 1e-6 and total_leftover <= 1e-6:
        return None

    fund = temp_rest_config["current_fund_name"] or "(not set — see `sav temp-rest-set-fund`)"
    threshold = temp_rest_config["significant_threshold_idr"]

    fields = [("Currently parked", fmt_idr(temp_rest_balance))]
    if temp_rest_balance > 1e-6:
        fields.append((
            "If already redeemed",
            "fold the landed amount into this contribution, then `sav temp-rest-redeem <amount>`",
        ))
    fields.append(("This month's leftover", fmt_idr(total_leftover)))
    fields.append(("Significant threshold", fmt_idr(threshold)))
    if total_leftover >= threshold and threshold > 0:
        action = f"Above threshold — park it: `sav temp-rest-deposit {total_leftover:.0f}`"
    elif total_leftover > 0:
        action = "Below threshold — leave as cash as usual"
    else:
        action = "No leftover this month"
    fields.append(("Action", action))

    return (f"Temp-Rest — {fund}", fields)


def write_report(cmd: str, content: str):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{Date.today().isoformat()}_{cmd}.md"
    path.write_text(content, encoding="utf-8")
    return path


# --- shared setup ------------------------------------------------------------------

def load_targets_or_die():
    try:
        return storage.load_targets(CONFIG_PATH)
    except (DataError, FileNotFoundError, KeyError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)


def load_temp_rest_config_or_die():
    try:
        return storage.load_temp_rest_config(TEMP_REST_CONFIG_PATH)
    except (DataError, FileNotFoundError, KeyError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)


def load_risk_config_or_die():
    try:
        return storage.load_risk_config(RISK_CONFIG_PATH)
    except (DataError, FileNotFoundError, KeyError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)


def find_target(targets, ticker):
    for t in targets:
        if t.ticker == ticker:
            return t
    valid = ", ".join(t.ticker for t in targets)
    print(f"Unknown ticker {ticker!r}. Must match config/targets.json exactly: {valid}", file=sys.stderr)
    sys.exit(1)


def fetch_all_prices(targets):
    """Fetches current prices for every holding, bootstraps price history for
    yfinance-backed holdings on first run, and appends today's price to
    price_history.csv for every holding (one row per ticker per day the tool runs).
    """
    prices = {}
    for t in targets:
        try:
            bootstrap_price_history_if_needed(t, DATA_DIR)
            price = get_price(t, DATA_DIR)
        except DataError as e:
            print(f"Price error: {e}", file=sys.stderr)
            sys.exit(1)
        prices[t.ticker] = price
        storage.append_price_history(DATA_DIR, t.ticker, price, t.currency, Date.today())
    return prices


def needs_fx(targets) -> bool:
    return any(t.currency == "USD" for t in targets)


def get_fx_or_one(targets) -> float:
    if not needs_fx(targets):
        return 1.0
    try:
        return get_fx_rate_usdidr()
    except DataError as e:
        print(f"FX error: {e}", file=sys.stderr)
        sys.exit(1)


def parse_date(s):
    return Date.fromisoformat(s) if s else Date.today()


# --- commands -----------------------------------------------------------------------

def cmd_buy(args):
    band, targets = load_targets_or_die()
    transactions = storage.load_transactions(DATA_DIR)
    prices = fetch_all_prices(targets)
    fx = get_fx_or_one(targets)
    review = alerts.pending_review_from_log(DATA_DIR)

    lines, states = calculator.calculate_buy(targets, band, transactions, args.amount_idr, prices, fx)

    headers = ["Holding", "Broker", "Target", "Buy", "Price", "Spend", "Leftover"]
    rows = []
    below_min_notes = []
    topup_notes = []
    for ln in lines:
        if ln.below_min_lot:
            buy_str = f"0 (below 1 lot, {fmt_idr(ln.lot_value_idr)} min)"
            below_min_notes.append(
                f"Note: {ln.ticker} got {fmt_idr(ln.raw_target_idr)} this month but that's below its "
                f"1-lot minimum — nothing bought, the gap carries forward and will be prioritized "
                f"next month via the redirect-to-underweight rule."
            )
        elif ln.topped_up:
            buy_str = f"{ln.units_to_buy:.0f} @ {fmt_money(ln.price_native, ln.currency)} [leftover top-up]"
            topup_notes.append(
                f"Note: {ln.ticker}'s own target was {fmt_idr(ln.topup_amount_idr)} short of a full lot — "
                f"topped up from this month's shared leftover (other holdings' in-lot residue) instead of "
                f"carrying forward, so 1 lot got bought instead of 0."
            )
        else:
            buy_str = f"{ln.units_to_buy:.0f} @ {fmt_money(ln.price_native, ln.currency)}"
        rows.append([
            f"{ln.name} ({ln.ticker})", ln.broker, fmt_idr(ln.raw_target_idr), buy_str,
            fmt_money(ln.price_native, ln.currency), fmt_idr(ln.spend_idr), fmt_idr(max(0.0, ln.leftover_idr)),
        ])
    total_leftover = sum(ln.leftover_idr for ln in lines)
    temp_rest_config = load_temp_rest_config_or_die()
    temp_rest_bal = calculator.temp_rest_balance(storage.load_temp_rest_log(DATA_DIR))
    temp_rest_block = build_temp_rest_block(temp_rest_bal, temp_rest_config, total_leftover)
    fx_note = (
        f"USD/IDR used: {fx:,.2f} — indicative mid-market rate, not what your broker will actually "
        f"charge (retail FX carries a spread on top)."
    ) if needs_fx(targets) else None

    # --- console: narrow vertical blocks, never depends on terminal width ---
    blocks = []
    for ln in lines:
        if ln.below_min_lot:
            buy_str = f"0 (below 1-lot minimum of {fmt_idr(ln.lot_value_idr)})"
        elif ln.topped_up:
            buy_str = f"{ln.units_to_buy:.0f} @ {fmt_money(ln.price_native, ln.currency)} [leftover top-up]"
        else:
            buy_str = f"{ln.units_to_buy:.0f} @ {fmt_money(ln.price_native, ln.currency)}"
        fields = [
            ("Target", fmt_idr(ln.raw_target_idr)),
            ("Price", fmt_money(ln.price_native, ln.currency)),
            ("Buy", buy_str),
            ("Spend", fmt_idr(ln.spend_idr)),
            ("Leftover", fmt_idr(max(0.0, ln.leftover_idr))),
        ]
        if ln.topped_up:
            fields.append(("Topped up", f"+{fmt_idr(ln.topup_amount_idr)} from shared leftover"))
        blocks.append((f"{ln.ticker} — {ln.name} ({ln.broker})", fields))

    console = [f"Buy plan — {Date.today().isoformat()}", f"Contribution: {fmt_idr(args.amount_idr)}", ""]
    console.append(render_review_banner(review))
    console.append("")
    console.append(render_blocks(blocks))
    console.append("")
    console.append(f"Total leftover (carries forward via drift next month): {fmt_idr(total_leftover)}")
    if fx_note:
        console.append(fx_note)
    console.extend(below_min_notes)
    console.extend(topup_notes)
    if temp_rest_block:
        console.append("")
        console.append(render_blocks([temp_rest_block]))
    print("\n".join(console))

    # --- report file: markdown, for opening in an editor/viewer later ---
    md = [f"# Buy plan — {Date.today().isoformat()}", "", f"Contribution: {fmt_idr(args.amount_idr)}", ""]
    md.append(render_review_banner(review))
    md.append("")
    md.append(render_markdown_table(headers, rows))
    md.append("")
    md.append(f"Total leftover (not spent this month, carries forward via drift next month): {fmt_idr(total_leftover)}")
    if fx_note:
        md.append("")
        md.append(fx_note.replace("indicative mid-market rate", "**indicative mid-market rate**"))
    for note in below_min_notes:
        md.append("")
        md.append(note)
    for note in topup_notes:
        md.append("")
        md.append(note)
    if temp_rest_block:
        md.append("")
        md.append(render_markdown_block(*temp_rest_block))
    path = write_report("buy", "\n".join(md))
    print(f"\n(full report saved: {path})")


def cmd_log_buy(args):
    _log_transaction(args, "buy")


def cmd_log_sell(args):
    band, targets = load_targets_or_die()
    target = find_target(targets, args.ticker)
    transactions = storage.load_transactions(DATA_DIR)
    held = calculator.net_shares(transactions, args.ticker)
    if args.shares > held + 1e-9:
        print(f"Warning: selling {args.shares} but only {held} of {args.ticker} are on record.", file=sys.stderr)

    avg_cost = calculator.avg_cost_basis(transactions, args.ticker)
    gain_per_share = args.price - avg_cost
    total_gain = gain_per_share * args.shares
    print(f"Average cost basis for {args.ticker}: {fmt_money(avg_cost, target.currency)}/share")
    print(f"Realized {'gain' if total_gain >= 0 else 'loss'} on this sale: {fmt_money(total_gain, target.currency)}")

    _log_transaction(args, "sell")


def _log_transaction(args, ttype):
    band, targets = load_targets_or_die()
    target = find_target(targets, args.ticker)
    date = parse_date(args.date)
    computed_amount = args.shares * args.price
    amount = args.amount if getattr(args, "amount", None) is not None else computed_amount

    if storage.transaction_exists(DATA_DIR, date, args.ticker, ttype, args.shares, args.price):
        answer = input(
            f"An identical {ttype} row already exists for {args.ticker} on {date} "
            f"({args.shares} @ {args.price}). Log it again anyway? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Not logged.")
            return

    txn = Transaction(date=date, ticker=args.ticker, type=ttype, shares=args.shares,
                       price=args.price, amount=amount, currency=target.currency, note=args.note or "")
    storage.append_transaction(DATA_DIR, txn)
    diff = amount - computed_amount
    diff_note = ""
    if abs(diff) > 1e-6:
        diff_note = (f" [{'includes' if diff > 0 else 'net of'} {fmt_money(abs(diff), target.currency)} "
                     f"vs {args.shares:.0f} x {fmt_money(args.price, target.currency)}]")
    print(f"Logged {ttype}: {args.shares} {args.ticker} @ {fmt_money(args.price, target.currency)} "
          f"({fmt_money(amount, target.currency)}{diff_note}) on {date}")


def cmd_log_dividend(args):
    band, targets = load_targets_or_die()
    target = find_target(targets, args.ticker)
    date = parse_date(args.date)
    div = Dividend(date=date, ticker=args.ticker, amount=args.amount, currency=target.currency)
    storage.append_dividend(DATA_DIR, div)
    print(f"Logged dividend: {fmt_money(args.amount, target.currency)} from {args.ticker} on {date}")


def cmd_set_price(args):
    band, targets = load_targets_or_die()
    target = find_target(targets, args.ticker)
    date = parse_date(args.date)
    storage.append_manual_price(DATA_DIR, args.ticker, args.price, target.currency, date)
    storage.append_price_history(DATA_DIR, args.ticker, args.price, target.currency, date)
    print(f"Recorded manual price: {args.ticker} = {fmt_money(args.price, target.currency)} on {date}")


def cmd_set_target(args):
    """Applies a conscious Tier 3/4 reallocation (PLAN.md §8.5) — the number always
    comes from you, after your own §8.1 scorecard review; this command's job is just
    to do the redistribution math safely and leave an audit trail. See
    calculator.redistribute_target_weight and alerts.pending_review_from_log (the
    `sav buy` banner that points here). --preview dry-runs the same math and writes
    nothing, for sizing your own number before committing.
    """
    if not args.preview and not args.reason:
        print("Cannot apply: --reason is required (unless using --preview).", file=sys.stderr)
        sys.exit(1)

    band, targets = load_targets_or_die()
    old_target = find_target(targets, args.ticker)
    old_weight = old_target.target_weight
    new_weight = args.percent / 100.0

    try:
        new_targets = calculator.redistribute_target_weight(targets, args.ticker, new_weight)
    except ValueError as e:
        print(f"Cannot apply: {e}", file=sys.stderr)
        sys.exit(1)

    old_by_ticker = {t.ticker: t.target_weight for t in targets}

    if args.preview:
        print(f"PREVIEW ONLY — nothing written. {args.ticker} {fmt_pct(old_weight, 1)} -> {fmt_pct(new_weight, 1)}:")
        for t in new_targets:
            print(f"  {t.ticker}: {fmt_pct(old_by_ticker[t.ticker], 1)} -> {fmt_pct(t.target_weight, 1)}")
        print("\nRun without --preview (and with --reason) to actually apply this.")
        return

    storage.save_targets(CONFIG_PATH, band, new_targets)
    storage.append_target_change_log(DATA_DIR, args.ticker, old_weight, new_weight, args.reason, Date.today())
    storage.append_deep_review_log(
        DATA_DIR, Date.today(),
        f"set-target {args.ticker}: {fmt_pct(old_weight, 1)} -> {fmt_pct(new_weight, 1)} — {args.reason}",
    )

    print(f"Target weight changed: {args.ticker} {fmt_pct(old_weight, 1)} -> {fmt_pct(new_weight, 1)}")
    print(f"Reason: {args.reason}")
    print("\nNew targets (redistributed proportionally):")
    for t in new_targets:
        print(f"  {t.ticker}: {fmt_pct(old_by_ticker[t.ticker], 1)} -> {fmt_pct(t.target_weight, 1)}")
    print(
        "\nThis only changes future contributions via `sav buy` — existing shares are untouched. "
        "If this is a full Tier 4 Exit, log the sale separately with `sav log-sell`."
    )


def cmd_temp_rest_status(args):
    config = load_temp_rest_config_or_die()
    log_rows = storage.load_temp_rest_log(DATA_DIR)
    balance = calculator.temp_rest_balance(log_rows)
    fund = config["current_fund_name"] or "(not set — see `sav temp-rest-set-fund`)"

    print("Temp-rest status")
    print(f"  Current fund:           {fund}")
    print(f"  Tracked balance:        {fmt_idr(balance)}")
    print(f"  Significant threshold:  {fmt_idr(config['significant_threshold_idr'])}")
    print(
        "\nThis is a ledger balance (deposits minus redemptions you've logged), not a live "
        "NAV pull — no price API used here covers Indonesian money-market funds. The real "
        "current value at your broker should be at or above this (the fund accrues quietly); "
        "typical money-market yields ran ~3-4%/yr in 2025-2026 — meaningfully below a typical "
        "~6% digital-bank savings rate, so treat this purely as a 'better than zero while "
        "parked' vehicle, not a yield play."
    )
    if log_rows:
        print("\nHistory:")
        for d, action, amount, note in log_rows:
            note_str = f" — {note}" if note else ""
            print(f"  {d.isoformat()}  {action:<7} {fmt_idr(amount)}{note_str}")


def cmd_temp_rest_deposit(args):
    date = parse_date(args.date)
    storage.append_temp_rest_log(DATA_DIR, date, "deposit", args.amount_idr, args.note or "")
    print(f"Logged temp-rest deposit: {fmt_idr(args.amount_idr)} on {date}")


def cmd_temp_rest_redeem(args):
    date = parse_date(args.date)
    log_rows = storage.load_temp_rest_log(DATA_DIR)
    balance = calculator.temp_rest_balance(log_rows)
    if args.amount_idr > balance + 1e-6:
        print(
            f"Warning: redeeming {fmt_idr(args.amount_idr)} but the tracked balance is only "
            f"{fmt_idr(balance)}. Logging anyway — the ledger doesn't include accrued interest, "
            f"so a real redemption can be a bit higher than this. Check your broker if the gap looks "
            f"too large to be just accrual.",
            file=sys.stderr,
        )
    storage.append_temp_rest_log(DATA_DIR, date, "redeem", args.amount_idr, args.note or "")
    print(f"Logged temp-rest redemption: {fmt_idr(args.amount_idr)} on {date}")
    print(
        "Fold this amount into your next `sav buy <amount>` contribution once it's actually "
        "landed in your account (T+1/T+2 settlement) — it isn't added automatically."
    )


def cmd_temp_rest_set_fund(args):
    """A yearly review-and-possibly-change action, same spirit as `sav set-target`:
    which specific fund to use is a research question this script can't answer on its
    own (no stable API to check your broker's listing against), so the name and the
    reasoning both come from you.
    """
    config = load_temp_rest_config_or_die()
    old_name = config["current_fund_name"]
    storage.save_temp_rest_config(
        TEMP_REST_CONFIG_PATH, config["significant_threshold_idr"], args.name, args.reason
    )
    storage.append_temp_rest_fund_log(DATA_DIR, Date.today(), old_name, args.name, args.reason)
    storage.append_deep_review_log(
        DATA_DIR, Date.today(), f"temp-rest-set-fund: {old_name or '(none)'} -> {args.name} — {args.reason}"
    )
    print(f"Temp-rest fund changed: {old_name or '(none)'} -> {args.name}")
    print(f"Reason: {args.reason}")


def cmd_review_log(args):
    """Stamps that a full live-session deep review happened today, even when the
    conclusion was "no changes needed". set-target and temp-rest-set-fund already log
    automatically when actually used — this covers the rest, so the yearly clock
    (alerts.deep_review_status) doesn't just sit permanently "due" between changes.
    """
    storage.append_deep_review_log(DATA_DIR, Date.today(), args.reason)
    print(f"Logged deep review: {Date.today().isoformat()} — {args.reason}")


# --- Risk Pot ------------------------------------------------------------------------
# See RISK_POT_PLAN.md for the full design. Fully separate from everything above:
# own ledger (risk_transactions.csv), own config (risk_config.json), own commands, no
# shared entry point with `sav buy`. Always IDR — the candidate universe (Kompas100/JKSE)
# is IDX-only, so there's no per-row currency to choose the way core log-buy has to.

def cmd_risk_log_buy(args):
    _risk_log_transaction(args, "buy")


def cmd_risk_log_sell(args):
    transactions = storage.load_risk_transactions(DATA_DIR)
    held = calculator.net_shares(transactions, args.ticker)
    if args.shares > held + 1e-9:
        print(f"Warning: selling {args.shares} but only {held} of {args.ticker} are on record "
              f"in the risk pot.", file=sys.stderr)

    avg_cost = calculator.avg_cost_basis(transactions, args.ticker)
    gain_per_share = args.price - avg_cost
    total_gain = gain_per_share * args.shares
    print(f"Average cost basis for {args.ticker}: {fmt_idr(avg_cost)}/share")
    print(f"Realized {'gain' if total_gain >= 0 else 'loss'} on this risk-pot sale: {fmt_idr(total_gain)}")

    _risk_log_transaction(args, "sell")


def _risk_log_transaction(args, ttype):
    date = parse_date(args.date)
    amount = args.amount if getattr(args, "amount", None) is not None else args.shares * args.price

    track = getattr(args, "track", "A") or "A"
    pivot = getattr(args, "pivot", None)
    if ttype == "buy" and track == "B" and pivot is None:
        print("Track B needs --pivot (the base pivot the breakout cleared) so its exit rule can be "
              "computed (RISK_POT_PLAN.md §3.1b).", file=sys.stderr)
        sys.exit(1)
    if ttype == "buy" and track == "A" and pivot is not None:
        print("--pivot only applies to --track B.", file=sys.stderr)
        sys.exit(1)
    if ttype == "buy" and track == "B" and getattr(args, "stop_loss", None) is None:
        ladder = risk_pot.exit_params(load_risk_config_or_die(), "B")
        args.stop_loss = round(risk_pot.track_b_base_stop(
            args.price, pivot, ladder["stop_loss_pct"], ladder["pivot_buffer_pct"]), 2)
        print(f"Track B base stop: {fmt_idr(args.stop_loss)} (the higher of pivot "
              f"-{ladder['pivot_buffer_pct'] * 100:.0f}% and entry -{ladder['stop_loss_pct'] * 100:.0f}%). "
              f"Ladder: breakeven after +{ladder['breakeven_lock_pct'] * 100:.0f}%, "
              f"{ladder['trailing_stop_pct'] * 100:.0f}% trail after +{ladder['trailing_stop_arm_pct'] * 100:.0f}%.")

    if storage.risk_transaction_exists(DATA_DIR, date, args.ticker, ttype, args.shares, args.price):
        answer = input(
            f"An identical risk-pot {ttype} row already exists for {args.ticker} on {date} "
            f"({args.shares} @ {args.price}). Log it again anyway? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Not logged.")
            return

    note = args.note or ""
    if ttype == "buy" and (getattr(args, "stop_loss", None) is not None or track == "B"):
        note = risk_pot.encode_note(getattr(args, "stop_loss", None), note,
                                    track="B" if track == "B" else None, pivot=pivot)

    txn = Transaction(date=date, ticker=args.ticker, type=ttype, shares=args.shares,
                       price=args.price, amount=amount, currency="IDR", note=note)
    storage.append_risk_transaction(DATA_DIR, txn)

    diff = amount - args.shares * args.price
    diff_note = ""
    if abs(diff) > 1e-6:
        diff_note = (f" [{'includes' if diff > 0 else 'net of'} {fmt_idr(abs(diff))} "
                     f"vs {args.shares:.0f} x {fmt_idr(args.price)}]")
    print(f"Logged risk-pot {ttype}: {args.shares} {args.ticker} @ {fmt_idr(args.price)} "
          f"({fmt_idr(amount)}{diff_note}) on {date}")

    if ttype == "buy":
        config = load_risk_config_or_die()
        at_work = risk_pot.capital_at_work(storage.load_risk_transactions(DATA_DIR))
        cap = config["pot_cap_idr"]
        if at_work > cap + 1e-6:
            print(f"Warning: risk-pot capital-at-work is now {fmt_idr(at_work)}, over the "
                  f"configured {fmt_idr(cap)} cap. Logging anyway — this is a nudge, not a block; "
                  f"trim an existing position or treat this as a deliberate one-off.", file=sys.stderr)


def cmd_risk_set_cap(args):
    """Changes config/risk_config.json's pot_cap_idr — the one number that moves when
    you add or pull out risk-pot capital (RISK_POT_PLAN.md §2 calls this a revolving cap
    on capital-at-work, not a lifetime total, but the cap itself was never meant to be
    frozen at its initial Rp 1,000,000 seed forever). Only touches pot_cap_idr; every
    other risk_config.json field (stop-loss %, profit-lock thresholds, benchmark,
    candidate universe) round-trips unchanged.
    """
    if args.amount_idr < 0:
        print("Cannot apply: cap must not be negative.", file=sys.stderr)
        sys.exit(1)

    config = load_risk_config_or_die()
    old_cap = config["pot_cap_idr"]

    at_work = risk_pot.capital_at_work(storage.load_risk_transactions(DATA_DIR))
    if args.amount_idr < at_work - 1e-6:
        print(
            f"Warning: new cap {fmt_idr(args.amount_idr)} is below the {fmt_idr(at_work)} "
            f"already deployed in open positions. Applying anyway — this is a nudge, not a "
            f"block; trim/close a position with `sav risk-log-sell` if that's what you meant.",
            file=sys.stderr,
        )

    storage.save_risk_config(
        RISK_CONFIG_PATH, args.amount_idr, config["stop_loss_pct"],
        config["target_monthly_return_pct"], config["benchmark"], config["candidate_universe"],
        config["breakeven_lock_pct"], config["trailing_stop_arm_pct"], config["trailing_stop_pct"],
        config["min_review_days"], config["stale_review_days"], track_b=config.get("track_b"),
    )
    storage.append_risk_cap_log(DATA_DIR, Date.today(), old_cap, args.amount_idr, args.reason)

    print(f"Risk Pot cap changed: {fmt_idr(old_cap)} -> {fmt_idr(args.amount_idr)}")
    print(f"Reason: {args.reason}")
    print(f"Capital at work: {fmt_idr(at_work)} / {fmt_idr(args.amount_idr)} cap "
          f"({(at_work / args.amount_idr * 100) if args.amount_idr > 0 else 0:.0f}%)")


def _benchmark_return_since(ticker: str, bench: str, anchor: Date) -> tuple[float, float]:
    """Position's return and the benchmark's return over the same window, both measured
    from `anchor` to now — the mechanical bar RISK_POT_PLAN.md §4.2 checks daily and
    §4.3's `risk-extend` checks on demand. Both legs use closing prices (anchor via
    get_price_on_or_before, "now" via get_live_price) so they're apples-to-apples with
    the benchmark, which only has close data."""
    current_price = get_live_price(ticker)
    anchor_price = get_price_on_or_before(ticker, anchor)
    position_return = risk_pot.pct_return(anchor_price, current_price)

    bench_now = get_live_price(bench)
    bench_then = get_price_on_or_before(bench, anchor)
    bench_return = risk_pot.pct_return(bench_then, bench_now)
    return position_return, bench_return


def cmd_risk_extend(args):
    config = load_risk_config_or_die()
    transactions = storage.load_risk_transactions(DATA_DIR)
    if calculator.net_shares(transactions, args.ticker) <= 1e-9:
        print(f"No open risk-pot position for {args.ticker}.", file=sys.stderr)
        sys.exit(1)

    extensions = storage.load_risk_extensions_log(DATA_DIR)
    entry = risk_pot.entry_date_for(transactions, args.ticker)
    anchor = risk_pot.last_review_date(entry, extensions, args.ticker)
    bench = config["benchmark"]

    position_return, bench_return = _benchmark_return_since(args.ticker, bench, anchor)
    ok = risk_pot.beats_benchmark(position_return, bench_return)
    days_since = (Date.today() - anchor).days

    print(f"{args.ticker}: {days_since}d since last review ({anchor.isoformat()}) — "
          f"return {fmt_signed_pct(position_return)} vs {bench} {fmt_signed_pct(bench_return)} "
          f"over the same window.")

    track_b = risk_pot.track_for(transactions, args.ticker) == "B"
    if not ok and track_b:
        print("Track B position: the benchmark bar isn't its exit rule (the stop ladder is, "
              "RISK_POT_PLAN.md §3.1b), so the review is logged without --force.")
    elif not ok and not args.force:
        print(f"Doesn't clear the mechanical bar (needs a positive return AND beating {bench}) — "
              f"`sav risk-log-sell` instead, or re-run with --force if you're deliberately "
              f"overriding (still requires --reason).", file=sys.stderr)
        sys.exit(1)

    storage.append_risk_extension_log(DATA_DIR, Date.today(), args.ticker, args.reason)
    override_note = " (bar not met — forced)" if not ok and not track_b else ""
    print(f"Logged today's review of {args.ticker}{override_note} — keeping the position open, "
          f"no fixed cycle to renew. Reason: {args.reason}")


def cmd_risk_screen(args):
    """Automates RISK_POT_PLAN.md §3.1 step 1 (candidate universe, minus core holdings)
    and step 2 (rank by trailing momentum vs the benchmark) — mechanical, not a pick.
    Steps 3-6 (why it's actually moving, pick-or-skip, sizing by consultation, thesis +
    stop-loss) still need real judgment before `risk-log-buy`; this only saves the
    manual per-ticker legwork of step 2.

    NOT a live scan of the actual Kompas100/JKSE index — there's no reliable API/source
    for current index membership available here, so this runs against
    config/risk_watchlist.json, a hand-maintained list seeded from §3's own starting
    watchlist. Expand it with individually-researched names as you go (see that file's
    own note) — don't treat this output as "the market," only as "this configured list."
    """
    config = load_risk_config_or_die()
    bench = config["benchmark"]
    try:
        candidates = storage.load_risk_watchlist(RISK_WATCHLIST_PATH)
    except DataError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    _band, targets = load_targets_or_die()
    core_tickers = {t.ticker for t in targets}
    skipped_core = [c["ticker"] for c in candidates if c["ticker"] in core_tickers]
    candidates = [c for c in candidates if c["ticker"] not in core_tickers]

    print(f"Risk Pot screen — {Date.today().isoformat()}")
    print(f"Candidate universe: config/risk_watchlist.json ({len(candidates)} names) — "
          f"a hand-maintained list, NOT a live Kompas100/JKSE index scan.")
    if skipped_core:
        print(f"Excluded (already core holdings): {', '.join(skipped_core)}")

    try:
        bench_1mo, _ = trailing_return_and_liquidity(bench, 1)
        bench_3mo, _ = trailing_return_and_liquidity(bench, 3)
    except DataError as e:
        print(f"Cannot fetch benchmark {bench}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Benchmark {bench}: 1mo {fmt_signed_pct(bench_1mo)}  3mo {fmt_signed_pct(bench_3mo)}\n")

    rows = []
    skipped = []
    for c in candidates:
        ticker = c["ticker"]
        try:
            r1, _ = trailing_return_and_liquidity(ticker, 1)
            r3, liq = trailing_return_and_liquidity(ticker, 3)
        except Exception as e:
            skipped.append((ticker, str(e)))
            continue
        rows.append({"ticker": ticker, "theme": c["theme"], "r1": r1, "r3": r3,
                      "excess3": r3 - bench_3mo, "liquidity": liq})

    rows.sort(key=lambda r: r["excess3"], reverse=True)
    shortlist_n = min(5, len(rows))

    if not rows:
        print("No candidates could be fetched — check the tickers in config/risk_watchlist.json.")
    else:
        print(f"{'':1}{'Ticker':<10} {'Theme':<28} {'1mo':>8} {'3mo':>8} {'3mo excess':>11} {'Avg daily value':>18}")
        for i, r in enumerate(rows):
            marker = "*" if i < shortlist_n else " "
            print(f"{marker}{r['ticker']:<9} {r['theme']:<28} {r['r1']:>7.1f}% {r['r3']:>7.1f}% "
                  f"{r['excess3']:>+10.1f}pp {fmt_idr(r['liquidity']):>15}/day")
        print(f"\n* = shortlist (top {shortlist_n}, ranked by 3mo excess return vs {bench}). "
              f"Liquidity is a real average, not a pass/fail cutoff — judge it yourself (§3 item 1).")

    if skipped:
        print(f"\nSkipped (no usable data): {', '.join(t for t, _ in skipped)}")

    print(
        "\nThis is only §3.1 steps 1-2 (universe + momentum ranking) — mechanical, not a pick. "
        "Still needed before `risk-log-buy`: step 3 (why is it actually moving — a real "
        "news/catalyst check, reject unexplained movers), step 4 (pick the strongest survivor, "
        "or skip the slot if nothing clears the bar), step 5 (size by consultation, not a "
        "formula), and step 6 (write the thesis + stop-loss). See RISK_POT_PLAN.md §3.1."
    )

    shortlist_str = ", ".join(r["ticker"] for r in rows[:shortlist_n]) if rows else "(none)"
    storage.append_risk_screen_log(
        DATA_DIR, Date.today(),
        f"{len(candidates)} candidates screened, shortlist: {shortlist_str}")


def cmd_risk_buyback_watch(args):
    """RISK_POT_PLAN.md §3.12's 'catch it early' discussion — the ERAA case study (§3.10)
    showed the real re-rating trigger is usually a buyback RENEWAL announced right as a
    program closes, not the original program. This flags config/buyback_calendar.json's
    dated programs approaching their end date so a renewal can be watched for proactively
    instead of found after the market's already reacted."""
    try:
        programs = storage.load_buyback_calendar(BUYBACK_CALENDAR_PATH)
    except DataError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    today = Date.today()
    programs.sort(key=lambda p: p["end_date"])
    window_days = args.days

    print(f"Buyback expiry watch — {today.isoformat()} (window: next {window_days} days)")
    print(
        "Mechanical only — a program ending soon is a reason to check for a renewal "
        "announcement (RISK_POT_PLAN.md §3.10), not itself a signal to trade.\n"
    )

    due = []
    for p in programs:
        days_left = (p["end_date"] - today).days
        confirmed_tag = "" if p["confirmed"] else " [UNCONFIRMED DATE — verify before trusting]"
        if days_left < 0:
            status = f"ended {-days_left}d ago — check whether a renewal was already announced"
        elif days_left <= window_days:
            status = f"{days_left}d left — WATCH for a renewal announcement"
            due.append(p["ticker"])
        else:
            status = f"{days_left}d left"
        max_val = f"Rp{p['max_value_idr']:,.0f}" if p["max_value_idr"] else "?"
        print(f"  {p['ticker']:<10} {max_val:>18}  ends {p['end_date'].isoformat()}  ({status}){confirmed_tag}")
        if p["note"]:
            print(f"             {p['note']}")

    if due:
        print(f"\n{len(due)} program(s) inside the watch window: {', '.join(due)}")
    else:
        print(f"\nNothing inside the {window_days}-day watch window right now.")

    print(
        "\nDates here are only as good as what was verified when added — re-check the "
        "ticker's own Stockbit Corp. Action tab (or IDX's Keterbukaan Informasi portal) "
        "before treating an 'ends soon' flag as certain."
    )


def cmd_risk_status(args):
    config = load_risk_config_or_die()
    transactions = storage.load_risk_transactions(DATA_DIR)
    extensions = storage.load_risk_extensions_log(DATA_DIR)
    tickers = risk_pot.open_tickers(transactions)
    today = Date.today()

    print(f"Risk Pot status — {today.isoformat()}")
    if not tickers:
        print("  No open positions.")

    total_cost = 0.0
    for ticker in tickers:
        price = get_live_price(ticker)
        entry = risk_pot.entry_date_for(transactions, ticker)
        try:
            highest = get_highest_close_since(ticker, entry)
        except DataError:
            highest = price  # no history yet (e.g. bought today) — falls back to current price
        pos = risk_pot.build_position(transactions, extensions, ticker, price, highest, config)
        total_cost += pos.cost_basis
        stop_str = fmt_idr(pos.stop_loss) if pos.stop_loss is not None else "not set"
        eff_stop = pos.effective_stop

        print(f"\n{ticker}")
        print(f"  Shares            {pos.shares:.0f}")
        print(f"  Avg cost          {fmt_idr(pos.avg_cost)}")
        print(f"  Price             {fmt_idr(price)}")
        print(f"  Unrealized P&L    {fmt_idr(pos.unrealized_pl)} ({fmt_signed_pct(pos.unrealized_pl_pct)})")
        if pos.track == "B":
            pivot_str = fmt_idr(pos.pivot) if pos.pivot is not None else "not recorded"
            print(f"  Track             B, own exit rule (RISK_POT_PLAN.md §3.1b): base pivot {pivot_str}; "
                  f"breakeven after +{pos.breakeven_lock_pct * 100:.0f}%, "
                  f"{pos.trailing_stop_pct * 100:.0f}% trail after +{pos.trailing_stop_arm_pct * 100:.0f}%")
        print(f"  Stop-loss (base)  {stop_str}")
        if eff_stop is not None and eff_stop > pos.stop_loss + 1e-6:
            peak_pl_pct = risk_pot.pct_return(pos.avg_cost, max(highest, price))
            lock = "trailing" if peak_pl_pct >= pos.trailing_stop_arm_pct * 100 else "breakeven"
            print(f"  Effective stop    {fmt_idr(eff_stop)}  ({lock} lock armed — this is the "
                  f"take-profit trigger, RISK_POT_PLAN.md §4.1)")
        print(f"  Entry             {pos.entry_date.isoformat()} ({pos.days_since_entry(today)}d ago)")
        print(f"  Last reviewed     {pos.review_anchor.isoformat()} ({pos.days_since_review(today)}d ago)")
        if pos.stop_loss_breached():
            print(f"  !! At/below the effective stop — exit now, don't wait for a review.")
        elif pos.stale(today, config["stale_review_days"]):
            print(f"  ** {pos.days_since_review(today)}d since last review with no flag — worth a "
                  f"sanity check regardless (soft backstop, RISK_POT_PLAN.md §4.2). **")

    cap = config["pot_cap_idr"]
    pct = (total_cost / cap * 100) if cap > 0 else 0.0
    print(f"\nCapital at work: {fmt_idr(total_cost)} / {fmt_idr(cap)} cap ({pct:.0f}%)")
    if total_cost > cap + 1e-6:
        print("  Over the configured cap.")


def cmd_risk_alert(args):
    """RISK_POT_PLAN.md §4.2's daily mechanical checks — the actual decision trigger for
    a short-horizon pot, not a countdown to a scheduled date. Order matters: an
    effective-stop breach (stop-loss or take-profit) short-circuits the rest, since
    nothing else matters once that's hit."""
    config = load_risk_config_or_die()
    transactions = storage.load_risk_transactions(DATA_DIR)
    extensions = storage.load_risk_extensions_log(DATA_DIR)
    tickers = risk_pot.open_tickers(transactions)
    today = Date.today()
    bench = config["benchmark"]

    print(f"Risk Pot alerts — {today.isoformat()}")
    if not tickers:
        print("  No open positions.")

    findings = []
    for ticker in tickers:
        price = get_live_price(ticker)
        entry = risk_pot.entry_date_for(transactions, ticker)
        try:
            highest = get_highest_close_since(ticker, entry)
        except DataError:
            highest = price
        pos = risk_pot.build_position(transactions, extensions, ticker, price, highest, config)

        if pos.stop_loss_breached():
            eff = pos.effective_stop
            took_profit = eff is not None and pos.stop_loss is not None and eff > pos.stop_loss + 1e-6
            check, verb = ("take_profit_breach", "take-profit") if took_profit else ("stop_loss_breach", "stop-loss")
            detail = f"price {fmt_idr(price)} at/below the {verb} level {fmt_idr(eff)} — exit now."
            print(f"  !! {ticker}: {detail}")
            findings.append((ticker, check, detail))
            continue

        flagged = False

        try:
            pos_ret, bench_ret = _benchmark_return_since(ticker, bench, pos.review_anchor)
            needs_review = risk_pot.review_needed(
                pos_ret, bench_ret, pos.days_since_review(today), config["min_review_days"])
        except DataError as e:
            needs_review = False
            print(f"  {ticker}: benchmark check skipped — {e}", file=sys.stderr)
        if needs_review and pos.track == "B":
            detail = (f"return since last review {fmt_signed_pct(pos_ret)} vs {bench} "
                      f"{fmt_signed_pct(bench_ret)}. Track B: lagging the index is NOT an exit reason "
                      f"(a base can go sideways for weeks); it exits only via its own stop ladder "
                      f"(RISK_POT_PLAN.md §3.1b). Informational.")
            print(f"  {ticker}: {detail}")
            findings.append((ticker, "benchmark_lag_track_b", detail))
            flagged = True
        elif needs_review:
            detail = (f"return since last review {fmt_signed_pct(pos_ret)} vs {bench} "
                      f"{fmt_signed_pct(bench_ret)} — no longer clearing the mechanical bar, "
                      f"worth a live review (RISK_POT_PLAN.md §4.3): `sav risk-extend` to keep "
                      f"holding with a reason, or `sav risk-log-sell` to exit.")
            print(f"  {ticker}: {detail}")
            findings.append((ticker, "benchmark_fail", detail))
            flagged = True

        try:
            pv = price_volume_signal(ticker)
        except DataError:
            pv = None
        if pv is not None and pv["signal"] == "danger" and pos.track == "B":
            detail = (f"price {fmt_signed_pct(pv['price_pct'])} on volume {fmt_signed_pct(pv['volume_pct'])}. "
                      f"Track B: one heavy down day is not distribution (SINI fell -9.9% on 6.5x volume "
                      f"mid-run); it only matters if it repeats within 2 sessions or closes below the "
                      f"base. Check with the skill's pv.py; the stop ladder is the exit.")
            print(f"  {ticker}: {detail}")
            findings.append((ticker, "price_volume_heavy_down_track_b", detail))
            flagged = True
        elif pv is not None and pv["signal"] == "danger":
            detail = (f"price {fmt_signed_pct(pv['price_pct'])} on volume {fmt_signed_pct(pv['volume_pct'])} "
                      f"— Wyckoff 'danger' reading (down price, up volume). Aggregate signal only, not "
                      f"an automatic exit — worth checking broker/foreign flow before reading into it "
                      f"(TRANSACTION_ANALYSIS_GUIDE.md §2).")
            print(f"  {ticker}: {detail}")
            findings.append((ticker, "price_volume_danger", detail))
            flagged = True

        if not flagged and pos.stale(today, config["stale_review_days"]):
            detail = (f"{pos.days_since_review(today)}d since last review with no flag — worth a "
                      f"sanity check regardless (soft backstop, RISK_POT_PLAN.md §4.2).")
            print(f"  {ticker}: {detail}")
            findings.append((ticker, "stale_review", detail))
            flagged = True

        if not flagged:
            print(f"  {ticker}: OK ({pos.days_since_entry(today)}d in, "
                  f"{fmt_signed_pct(pos.unrealized_pl_pct)} lifetime)")

    at_work = risk_pot.capital_at_work(transactions)
    cap = config["pot_cap_idr"]
    if at_work > cap + 1e-6:
        detail = f"capital-at-work {fmt_idr(at_work)} is over the {fmt_idr(cap)} cap."
        print(f"  !! POT: {detail}")
        findings.append(("POT", "cap_breach", detail))

    if findings:
        storage.append_risk_alerts_log(DATA_DIR, findings, today)


def _snapshot_files(paths):
    """{path: file content, or None if the file doesn't exist yet}."""
    return {str(p): (p.read_text(encoding="utf-8") if p.exists() else None) for p in paths}


def cmd_undo(args):
    if not UNDO_JOURNAL_PATH.exists():
        print("Nothing to undo.")
        return

    journal = json.loads(UNDO_JOURNAL_PATH.read_text(encoding="utf-8"))
    command, before = journal["command"], journal["files"]
    file_names = ", ".join(Path(p).name for p in before)

    answer = input(f"Undo `sav {command}`? This restores: {file_names} [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("Not undone.")
        return

    for path_str, prior_content in before.items():
        p = Path(path_str)
        if prior_content is None:
            if p.exists():
                p.unlink()
        else:
            p.write_text(prior_content, encoding="utf-8")

    UNDO_JOURNAL_PATH.unlink()
    print(f"Undone: sav {command}")
    print(f"Restored: {file_names}")


def cmd_status(args):
    band, targets = load_targets_or_die()
    transactions = storage.load_transactions(DATA_DIR)
    dividends = storage.load_dividends(DATA_DIR)
    prices = fetch_all_prices(targets)
    fx = get_fx_or_one(targets)

    report = tracker.build_status_report(targets, band, transactions, dividends, prices, fx)
    temp_rest_config = load_temp_rest_config_or_die()
    temp_rest_bal = calculator.temp_rest_balance(storage.load_temp_rest_log(DATA_DIR))
    temp_rest_fund = temp_rest_config["current_fund_name"] or "(not set)"

    headers = ["Holding", "Shares", "Price", "Value", "Weight", "Target", "Drift"]
    rows = []
    for s in report.states:
        flag = " !" if abs(s.drift_pp) > band else ""
        rows.append([
            f"{s.target.name} ({s.target.ticker})", f"{s.shares:.0f}", fmt_money(s.price, s.target.currency),
            fmt_idr(s.value_idr), fmt_pct(s.weight, 1), fmt_pct(s.target.target_weight, 0),
            f"{fmt_pp(s.drift_pp)}{flag}",
        ])

    yield_line = f"Blended dividend yield — gross: {fmt_pct(report.yield_gross)}  |  net of withholding tax: {fmt_pct(report.yield_net)}"
    yield_caveat = "(gross vs. net shown separately on purpose — comparing gross against a net bank rate is the exact mistake this tool exists to prevent, see PLAN.md §7.3)"
    realized_line = (
        f"Realized yield-to-date (dividends received / capital contributed): {fmt_pct(report.realized_yield)}"
        if report.realized_yield is not None else None
    )
    if report.total_return_xirr is not None:
        reliability = "" if report.total_return_reliable else "  [high variance, not yet meaningful — need ~12 months of history]"
        xirr_line = f"Annualized total return (XIRR): {fmt_pct(report.total_return_xirr)}{reliability}"
        xirr_caveat = "Target from PLAN.md §7.5: 7% floor, 10-15% aspiration — judge against the trailing multi-year trend, not any single reading."
    else:
        xirr_line, xirr_caveat = "Annualized total return (XIRR): not enough data yet.", None
    drift_line = (
        "Drifted beyond the rebalance band: " + ", ".join(f"{s.target.ticker} ({fmt_pp(s.drift_pp)})" for s in report.drifted)
        if report.drifted else None
    )

    # --- console: narrow vertical blocks, never depends on terminal width ---
    blocks = []
    for s in report.states:
        flag = "  !" if abs(s.drift_pp) > band else ""
        blocks.append((f"{s.target.ticker} — {s.target.name}", [
            ("Shares", f"{s.shares:.0f}"),
            ("Price", fmt_money(s.price, s.target.currency)),
            ("Value", fmt_idr(s.value_idr)),
            ("Weight", f"{fmt_pct(s.weight, 1)}  (target {fmt_pct(s.target.target_weight, 0)})"),
            ("Drift", f"{fmt_pp(s.drift_pp)}{flag}"),
        ]))

    console = [f"Portfolio status — {Date.today().isoformat()}", ""]
    console.append(render_blocks(blocks))
    console.append("")
    console.append(f"Total value:          {fmt_idr(report.total_value_idr)}")
    console.append(f"Net contributed:      {fmt_idr(report.net_contributed_idr)}")
    console.append(f"Unrealized gain/loss: {fmt_idr(report.unrealized_gain_idr)}")
    console.append("")
    console.append(yield_line)
    console.append(yield_caveat)
    if realized_line:
        console.append(realized_line)
    console.append("")
    console.append(xirr_line)
    if xirr_caveat:
        console.append(xirr_caveat)
    if drift_line:
        console.append("")
        console.append(drift_line)
    if temp_rest_bal > 1e-6:
        console.append("")
        console.append(f"Temp-rest parked: {fmt_idr(temp_rest_bal)} in {temp_rest_fund} (see `sav temp-rest-status`)")
    print("\n".join(console))

    # --- report file: markdown ---
    md = [f"# Portfolio status — {Date.today().isoformat()}", ""]
    md.append(render_markdown_table(headers, rows))
    md.append("")
    md.append(f"**Total value:** {fmt_idr(report.total_value_idr)}")
    md.append(f"**Net contributed:** {fmt_idr(report.net_contributed_idr)}")
    md.append(f"**Unrealized gain/loss:** {fmt_idr(report.unrealized_gain_idr)}")
    if temp_rest_bal > 1e-6:
        md.append(f"**Temp-rest parked:** {fmt_idr(temp_rest_bal)} in {temp_rest_fund}")
    md.append("")
    md.append(f"**{yield_line}**")
    md.append(yield_caveat)
    if realized_line:
        md.append(f"**{realized_line}**")
    md.append("")
    md.append(f"**{xirr_line}**")
    if xirr_caveat:
        md.append(xirr_caveat)
    if drift_line:
        md.append("")
        md.append(f"**{drift_line}**")
    path = write_report("status", "\n".join(md))
    print(f"\n(full report saved: {path})")


def cmd_alerts(args):
    band, targets = load_targets_or_die()
    transactions = storage.load_transactions(DATA_DIR)
    dividends = storage.load_dividends(DATA_DIR)
    prices = fetch_all_prices(targets)
    fx = get_fx_or_one(targets)

    report = tracker.build_status_report(targets, band, transactions, dividends, prices, fx)
    findings = alerts.run_all(
        targets, band, report.states, prices, report.total_return_xirr, report.total_return_reliable, DATA_DIR
    )

    # Tier 2 this run (hard trigger, or a flag that repeated) — computed once up front so
    # both the live-session handoff block and the impact preview below can reuse it.
    tier2_tickers = sorted({
        f.ticker for f in findings
        if f.tier == "A" and f.ticker != "PORTFOLIO" and (f.check == "dividend_cut" or f.repeat)
    })
    review_due, review_days_since = alerts.deep_review_status(DATA_DIR)
    live_session_needed = review_due or bool(tier2_tickers)

    out = [f"Alerts — {Date.today().isoformat()}", ""]

    if live_session_needed:
        temp_rest_config = load_temp_rest_config_or_die()
        temp_rest_bal = calculator.temp_rest_balance(storage.load_temp_rest_log(DATA_DIR))
        temp_rest_fund = temp_rest_config["current_fund_name"] or "(not set)"
        why = []
        if review_due:
            when = f"last: {review_days_since}d ago" if review_days_since is not None else "never done"
            why.append(f"yearly deep review due ({when})")
        if tier2_tickers:
            why.append(f"Tier 2 finding(s) this run: {', '.join(tier2_tickers)}")
        out.append("=" * 72)
        out.append("FOR A LIVE CLAUDE SESSION — paste this whole output there.")
        out.append(f"Why now: {'; '.join(why)}.")
        out.append(f"Project: {PROJECT_ROOT}  (see README.md, PLAN.md §8.1 scorecard / §8.5 decision ladder)")
        out.append(f"Temp-rest right now: {fmt_idr(temp_rest_bal)} in {temp_rest_fund}.")
        out.append("Please:")
        out.append("  1. Judge every finding below against §8.1/§8.5 — Watch / Forced deep review / Trim /")
        out.append("     Exit — including the Tier C research (news, credit, restructuring) this script")
        out.append("     structurally can't do on its own.")
        out.append("  2. Re-check the temp-rest fund against your broker's actual live listing (fetch it, compare")
        out.append("     AUM/stability), same approach as the fund already set.")
        out.append("  3. If a change is warranted, apply it with `sav set-target` / `sav temp-rest-set-fund`")
        out.append("     — both log themselves automatically.")
        out.append("  4. Either way, close this out with `sav review-log --reason \"...\"` so the yearly")
        out.append("     clock resets — even a \"no changes needed\" verdict counts as a real review.")
        out.append("=" * 72)
        out.append("")

    if not findings:
        out.append("Nothing flagged (Tier A + Tier B checked).")
    else:
        by_tier = {"A": [], "B": []}
        for f in findings:
            by_tier[f.tier].append(f)
        if by_tier["A"]:
            out.append("Tier A (automatable, high confidence):")
            for f in by_tier["A"]:
                repeat_tag = "  [REPEAT — 2nd check in a row, forces Tier 2 deep review per PLAN.md §8.5]" if f.repeat else ""
                out.append(f"  [{f.severity.upper()}] {f.ticker}: {f.message}{repeat_tag}")
            out.append("")
        if by_tier["B"]:
            out.append("Tier B (best-effort fundamentals):")
            for f in by_tier["B"]:
                repeat_tag = "  [REPEAT — 2nd check in a row]" if f.repeat else ""
                out.append(f"  [{f.severity.upper()}] {f.ticker}: {f.message}{repeat_tag}")
    # Impact preview: only for tickers that hit Tier 2 THIS run (hard trigger, or a flag
    # that repeated) — a single Tier 1 watch is supposed to die there per PLAN.md §8.5,
    # so previewing a trim for it would overstate its importance.
    if tier2_tickers:
        out.append("")
        for ticker in tier2_tickers:
            scenarios = calculator.preview_reallocation_scenarios(targets, ticker)
            out.append(render_impact_preview(ticker, targets, scenarios))
            out.append("")

    out.append(
        "Tier C (credit downgrades, restructuring, M&A news) isn't covered by this script — "
        "that needs a live web search, which is the scheduled Claude Code routine's job on "
        "top of this output, not something a plain Python script can do."
    )

    text = "\n".join(out)
    print(text)
    path = write_report("alerts", text)
    print(f"\n(full report saved: {path})")


# --- argument parsing -----------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Monthly Savings Portfolio — buy calculator and tracker.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("buy", help="Calculate this month's buy split.")
    p.add_argument("amount_idr", type=float)
    p.set_defaults(func=cmd_buy)

    p = sub.add_parser("log-buy", help="Record an actual purchase.")
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("price", type=float)
    p.add_argument("--amount", type=float,
                   help="Actual cash debited, if different from shares×price (e.g. a broker fee). "
                        "Same currency as `price` (never IDR for a USD holding like KO). "
                        "Defaults to shares×price.")
    p.add_argument("--date")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_log_buy)

    p = sub.add_parser("log-sell", help="Record an actual sale.")
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("price", type=float)
    p.add_argument("--amount", type=float,
                   help="Actual cash received, if different from shares×price (e.g. withheld tax/fee). "
                        "Same currency as `price` (never IDR for a USD holding like KO). "
                        "Defaults to shares×price.")
    p.add_argument("--date")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_log_sell)

    p = sub.add_parser("log-dividend", help="Record a dividend received.")
    p.add_argument("ticker")
    p.add_argument("amount", type=float)
    p.add_argument("--date")
    p.set_defaults(func=cmd_log_dividend)

    p = sub.add_parser("status", help="Show current portfolio status.")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("set-price", help="Manually record a price (e.g. gold ETF).")
    p.add_argument("ticker")
    p.add_argument("price", type=float)
    p.add_argument("--date")
    p.set_defaults(func=cmd_set_price)

    p = sub.add_parser("alerts", help="Run the alert checks (Tier A + Tier B).")
    p.set_defaults(func=cmd_alerts)

    p = sub.add_parser("set-target", help="Change a holding's target weight after your own review (Tier 3/4).")
    p.add_argument("ticker")
    p.add_argument("percent", type=float, help="New target weight as a percentage, e.g. 30 for 30%%")
    p.add_argument("--reason", help="Why — logged to data/target_changes_log.csv (required unless --preview)")
    p.add_argument("--preview", action="store_true", help="Dry-run only — show the result, write nothing")
    p.set_defaults(func=cmd_set_target)

    p = sub.add_parser("temp-rest-status", help="Show the temp-rest parked balance and history.")
    p.set_defaults(func=cmd_temp_rest_status)

    p = sub.add_parser("temp-rest-deposit", help="Log parking leftover cash into the temp-rest fund.")
    p.add_argument("amount_idr", type=float)
    p.add_argument("--date")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_temp_rest_deposit)

    p = sub.add_parser("temp-rest-redeem", help="Log pulling money back out of the temp-rest fund.")
    p.add_argument("amount_idr", type=float)
    p.add_argument("--date")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_temp_rest_redeem)

    p = sub.add_parser("temp-rest-set-fund", help="Change which fund temp-rest uses (yearly review).")
    p.add_argument("name")
    p.add_argument("--reason", required=True, help="Why — logged to data/temp_rest_fund_log.csv")
    p.set_defaults(func=cmd_temp_rest_set_fund)

    p = sub.add_parser("review-log", help="Log that a full deep review happened (even with no changes).")
    p.add_argument("--reason", required=True, help="What you checked / concluded — logged to data/deep_review_log.csv")
    p.set_defaults(func=cmd_review_log)

    p = sub.add_parser("risk-log-buy", help="Record a Risk Pot entry (separate from the core sleeve).")
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("price", type=float)
    p.add_argument("--amount", type=float, help="Actual cash debited, if different from shares×price.")
    p.add_argument("--stop-loss", type=float, help="Exit trigger price, recorded up front.")
    p.add_argument("--track", choices=["A", "B"], default="A",
                   help="A = momentum/story (pot defaults); B = early special situation with its own "
                        "exit ladder (RISK_POT_PLAN.md §3.1b). B requires --pivot.")
    p.add_argument("--pivot", type=float,
                   help="Track B only: the base pivot the breakout cleared (prior 40-day high close, "
                        "printed by the skill's pv.py/early_scan.py).")
    p.add_argument("--date")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_risk_log_buy)

    p = sub.add_parser("risk-log-sell", help="Record a Risk Pot exit.")
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("price", type=float)
    p.add_argument("--amount", type=float, help="Actual cash received, if different from shares×price.")
    p.add_argument("--date")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_risk_log_sell)

    p = sub.add_parser("risk-set-cap", help="Change the Risk Pot's capital-at-work cap (add/withdraw capital).")
    p.add_argument("amount_idr", type=float)
    p.add_argument("--reason", required=True, help="Why — logged to data/risk_cap_log.csv")
    p.set_defaults(func=cmd_risk_set_cap)

    p = sub.add_parser("risk-extend", help="Log today's live review of a Risk Pot position and keep it open.")
    p.add_argument("ticker")
    p.add_argument("--reason", required=True, help="Why — logged to data/risk_extensions_log.csv")
    p.add_argument("--force", action="store_true",
                   help="Extend even if the mechanical bar (positive return, beating the benchmark) isn't met.")
    p.set_defaults(func=cmd_risk_extend)

    p = sub.add_parser("risk-screen", help="Rank config/risk_watchlist.json by trailing momentum vs the benchmark.")
    p.set_defaults(func=cmd_risk_screen)

    p = sub.add_parser("risk-buyback-watch", help="Flag config/buyback_calendar.json programs nearing their end date (watch for a renewal).")
    p.add_argument("--days", type=int, default=21, help="Watch window in days (default 21).")
    p.set_defaults(func=cmd_risk_buyback_watch)

    p = sub.add_parser("risk-status", help="Show current Risk Pot positions.")
    p.set_defaults(func=cmd_risk_status)

    p = sub.add_parser("risk-alert", help="Run the Risk Pot's daily checks (stop/take-profit, price-volume, benchmark, cap).")
    p.set_defaults(func=cmd_risk_alert)

    p = sub.add_parser("undo", help="Undo the last logging command (single level).")
    p.set_defaults(func=cmd_undo)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(0)

    undo_paths = UNDO_FILES.get(args.command, [])
    before = _snapshot_files(undo_paths) if undo_paths else None

    try:
        args.func(args)
    except DataError as e:
        print(f"Data error: {e}", file=sys.stderr)
        sys.exit(1)

    if before is not None and _snapshot_files(undo_paths) != before:
        UNDO_JOURNAL_PATH.write_text(
            json.dumps({"command": " ".join(sys.argv[1:]), "files": before}, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
