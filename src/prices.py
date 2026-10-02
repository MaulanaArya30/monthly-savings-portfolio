"""Price fetching: yfinance for ASII.JK/BBRI.JK/KO, manual entry fallback for anything
without API coverage (the gold ETF today), plus the USD/IDR FX lookup and the
price-history bootstrap described in BUILD_PLAN.md.
"""
from datetime import date as Date, timedelta

from src.models import Target
from src.storage import DataError, bulk_append_price_history, has_price_history, load_latest_manual_price

_fx_cache = {}


def _yfinance_price(ticker: str) -> float:
    import yfinance as yf
    info = yf.Ticker(ticker).fast_info
    price = info.get("lastPrice") or info.get("last_price")
    if not price:
        raise DataError(f"yfinance returned no price for {ticker} — check the ticker is correct/market is open")
    return float(price)


def get_price(target: Target, data_dir) -> float:
    """Current price in the holding's own currency."""
    if target.price_source == "yfinance":
        return _yfinance_price(target.ticker)
    if target.price_source == "manual":
        latest = load_latest_manual_price(data_dir, target.ticker)
        if latest is None:
            raise DataError(
                f"No manual price on file for {target.ticker}. Run "
                f"`python cli.py set-price {target.ticker} <price>` before buy/status."
            )
        entry_date, price, _currency = latest
        staleness_days = (Date.today() - entry_date).days
        if staleness_days > 45:
            raise DataError(
                f"Manual price for {target.ticker} is {staleness_days} days old (from {entry_date}). "
                f"Run `python cli.py set-price {target.ticker} <price>` with a current number before continuing."
            )
        return price
    raise DataError(f"Unknown price_source {target.price_source!r} for {target.ticker}")


def get_live_price(ticker: str) -> float:
    """Current price for any yfinance ticker, no Target/config lookup needed — for the
    Risk Pot, whose tickers rotate monthly and were never pre-configured in
    config/targets.json the way the core sleeve's holdings are."""
    return _yfinance_price(ticker)


def get_price_on_or_before(ticker: str, target_date: Date) -> float:
    """Closing price on target_date, or the most recent trading day at/before it (handles
    weekends/holidays for free — yfinance simply has no row on a non-trading day). Used
    for the Risk Pot's benchmark-relative return check (RISK_POT_PLAN.md §4):
    comparing a position's return since its last checkpoint against ^JKSE's return over
    the same window needs both tickers' price on that exact historical date, not just
    today's.
    """
    import yfinance as yf
    hist = yf.Ticker(ticker).history(start=target_date - timedelta(days=10), end=target_date + timedelta(days=1))
    closes = hist["Close"].dropna()
    if closes.empty:
        raise DataError(f"No price history for {ticker} on or before {target_date.isoformat()}")
    return float(closes.iloc[-1])


def get_fx_rate_usdidr() -> float:
    """Indicative mid-market USD/IDR rate — NOT what a broker will actually charge,
    retail FX always carries a spread on top (BUILD_PLAN.md's calculator step 7 note).
    """
    if "USDIDR" in _fx_cache:
        return _fx_cache["USDIDR"]
    import yfinance as yf
    info = yf.Ticker("USDIDR=X").fast_info
    rate = info.get("lastPrice") or info.get("last_price")
    if not rate:
        raise DataError("yfinance returned no USDIDR=X rate — check network/market hours")
    _fx_cache["USDIDR"] = float(rate)
    return float(rate)


def get_highest_close_since(ticker: str, start_date: Date) -> float:
    """Highest daily close from start_date (inclusive) through today — feeds the Risk
    Pot's trailing-stop take-profit mechanic (RISK_POT_PLAN.md §4.1): the effective stop
    trails behind the position's own peak, not just today's price."""
    import yfinance as yf
    hist = yf.Ticker(ticker).history(start=start_date - timedelta(days=5), end=Date.today() + timedelta(days=1))
    closes = hist["Close"].dropna()
    closes = closes[closes.index.date >= start_date]
    if closes.empty:
        raise DataError(f"No price history for {ticker} on/after {start_date.isoformat()}")
    return float(closes.max())


def price_volume_signal(ticker: str) -> dict:
    """Yesterday-vs-today price % change and volume % change, classified against the
    Wyckoff price-volume matrix (TRANSACTION_ANALYSIS_GUIDE.md §2): up price + up volume
    is healthy, down+down is a healthy correction, up+down is a warning (unconfirmed
    move), down+up is "danger" (possible real distribution). An aggregate signal only —
    it can't tell foreign flow from domestic on its own (the guide's own documented
    limitation) — RISK_POT_PLAN.md §4.2 treats a "danger" reading as a flag to look
    closer via a live broker-flow check, never an automatic exit.
    """
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="10d")
    closes = hist["Close"].dropna()
    volumes = hist["Volume"].dropna()
    if len(closes) < 2 or len(volumes) < 2:
        raise DataError(f"Not enough recent price history for {ticker} to compute a price-volume signal")
    price_pct = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100
    volume_pct = (float(volumes.iloc[-1]) / float(volumes.iloc[-2]) - 1) * 100
    price_up, volume_up = price_pct > 0, volume_pct > 0
    # an unchanged close is neither up nor down: it falls through to "healthy" rather than
    # reading as "down price, up volume" (ERAA 2026-10-01, flat at 595, was flagged as danger)
    if price_pct < 0 and volume_up:
        signal = "danger"
    elif price_up and not volume_up:
        signal = "warning"
    else:
        signal = "healthy"
    return {"price_pct": price_pct, "volume_pct": volume_pct, "signal": signal}


def trailing_return_and_liquidity(ticker: str, months: int) -> tuple[float, float]:
    """Trailing total return (%, e.g. -35.0 for -35%) and average daily traded value
    (price x volume, in the ticker's own currency) over the trailing ~months*30 days.
    Backs the Risk Pot's momentum screen (RISK_POT_PLAN.md §3.1 step 2): the return
    ranks candidates against the benchmark, the traded value is a real liquidity check
    (§3 item 1) that has to actually be computed, not inferred from being on a list.
    """
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period=f"{months}mo")
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        raise DataError(f"Not enough price history for {ticker} over {months}mo")
    return_pct = (float(closes.iloc[-1]) / float(closes.iloc[0]) - 1) * 100
    traded_value = (hist["Close"] * hist["Volume"]).dropna()
    avg_daily_value = float(traded_value.mean()) if not traded_value.empty else 0.0
    return return_pct, avg_daily_value


def bootstrap_price_history_if_needed(target: Target, data_dir):
    """Seeds price_history.csv with ~90 days of real history the first time a
    yfinance-backed holding is seen, so alerts (Phase 2) aren't blind for weeks.
    No-op for manual-price holdings (nothing to backfill) or if history already exists.
    """
    if target.price_source != "yfinance":
        return
    if has_price_history(data_dir, target.ticker):
        return
    import yfinance as yf
    hist = yf.Ticker(target.ticker).history(period="90d")
    if hist.empty:
        return
    rows = [(idx.date(), float(row["Close"])) for idx, row in hist.iterrows()]
    bulk_append_price_history(data_dir, target.ticker, rows, target.currency)
