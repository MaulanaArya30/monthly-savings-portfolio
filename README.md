# Monthly Savings Portfolio

A personal command-line tool for running a monthly dividend-DCA plan on Indonesian (IDX)
and US stocks: tells you how much to buy of each holding this month, tracks what you
actually own, and flags when something's worth a closer look. It also manages a small,
separately capped "Risk Pot" for shorter-term momentum trades, with stop-loss,
breakeven and trailing-stop rules.

**What this tool never does:** place a trade, log a transaction for you automatically, or
silently change your target allocation. Every number it produces is a suggestion you act
on manually at your broker — the tool computes and remembers, you decide and execute.

> **Sample data.** Everything in `config/` and `data/` is made-up example data, so the
> tool runs out of the box. The example outputs below are illustrative. References like
> `PLAN.md §8.5` or `RISK_POT_PLAN.md §3.10` in comments and output point to the author's
> private planning notes, which aren't included in this repo.

**Stack:** Python 3.11, `yfinance` for prices, dividends and FX, plain CSV/JSON storage,
`pytest` (137 tests).

---

## Contents

- [Setup](#setup)
- [The mental model](#the-mental-model)
- [A normal month, start to finish](#a-normal-month-start-to-finish)
- [The review ladder](#the-review-ladder) — what Tier 1/2/3/4 actually mean
- [Command reference](#command-reference)
  - [`buy`](#buy-amount_idr)
  - [`log-buy` / `log-sell`](#log-buy-ticker-shares-price--date--note--log-sell)
  - [`log-dividend`](#log-dividend-ticker-amount---date)
  - [`status`](#status)
  - [`set-price`](#set-price-ticker-price---date)
  - [`alerts`](#alerts)
  - [`set-target`](#set-target-ticker-percent---reason---or---preview)
  - [`temp-rest-*`](#temp-rest-status--temp-rest-deposit--temp-rest-redeem--temp-rest-set-fund)
  - [`review-log`](#review-log---reason-)
  - [Risk Pot](#risk-pot--risk-log-buy--risk-log-sell--risk-set-cap--risk-extend--risk-screen--risk-buyback-watch--risk-status--risk-alert)
  - [`undo`](#undo)
- [Data files](#data-files)
- [Troubleshooting](#troubleshooting)

---

## Setup

```
pip install -r requirements.txt
```

Optionally, add a `sav` shortcut so you can run it from anywhere instead of typing the
full path, for example `alias sav="python /path/to/cli.py"` (bash/zsh), or a function in
your PowerShell profile:

```
sav status
sav buy 5000000
```

`sav <command>` and `python cli.py <command>` (run from the project folder) are exactly
the same thing — `sav` just forwards to the latter. Every example below uses `sav`, but
either form works. Running `sav` with no arguments, or `sav -h`, always prints the same
full command list you see below, so you never have to come back to this file just to
remember a flag.

---

## The mental model

Four things you should know before anything else makes sense:

1. **`config/targets.json` is your goal** — the 40/30/30 split, the rebalance band, which
   broker each holding is on. You only touch this indirectly, via `set-target`.
2. **`data/transactions.csv` is the only source of truth for what you actually own.**
   Nothing else — not a suggestion `buy` printed, not a report file — counts as a real
   holding until it's a row in this file, via `log-buy`/`log-sell`.
3. **Suggestions are never applied automatically.** `buy` calculates a split, `alerts`
   surfaces a concern, `set-target --preview` shows a hypothetical — none of them write
   anything until you run the corresponding `log-*` / `set-target` (without `--preview`)
   command yourself.
4. **Every command that changes something explains itself in its own output** — you
   shouldn't need to guess what a number means; if it's not obvious, that's a bug in the
   tool's output, not something you're expected to already know.

---

## A normal month, start to finish

```
sav alerts          # check for anything that needs a closer look before buying
sav buy <amount>     # see this month's suggested split
                      #   ...manually place the trades at your broker...
sav log-buy <ticker> <shares> <price>   # once per holding you actually bought
                      #   ...repeat log-buy for each holding...
sav status           # confirm the logged buys landed correctly
```

If `alerts` flagged something serious (see [Tier 2](#the-review-ladder) below) or `buy`
suggested parking a leftover in temp-rest, those are separate, optional follow-ups — not
required every month.

---

## The review ladder

Two completely separate systems in this tool are both called "tiers" — easy to mix up,
so both get the full explanation here, in one place, rather than scattered across
command sections:

- **Tier A / B / C** — how much to *trust* a given finding. Set by `sav alerts`.
- **Tier 1 / 2 / 3 / 4** — what to actually *do* once you trust it. From `PLAN.md` §8.5.

They're independent axes. A Tier A (high-confidence) finding might still only warrant a
Tier 1 Watch — most single findings do, that's the normal case. It takes a specific kind
of Tier A finding (a dividend cut) or a repeated one to actually trigger Tier 2.

### Tier A / B / C — how much to trust a finding

Shown as labeled sections in every `sav alerts` run. Full design: `BUILD_PLAN.md`'s
Phase 2.

| Tier | What it covers | The actual checks | How much to trust it |
|---|---|---|---|
| **A** | Automatable from real price + dividend history via `yfinance`, plus your actual holdings. High confidence — real data, not a guess. | **Allocation drift** — weight vs. target, beyond the 10pp band. **Dividend cuts** — this payout vs. ~1 year prior; any decrease is a hard trigger, no threshold. **Sharp price moves** — down 15%+ over ~30 days. **Total return** — your real XIRR vs. the 7% floor (§7.5). **Chowder Number** — yield + 5yr dividend growth vs. a 12-15% threshold. **Yield-trap** — yield rising *while price falls*, distinguishing a real trap from healthy dividend growth. **Benchmark-relative** — trailing 12mo return vs. `^JKSE`/`^GSPC`, so a market-wide dip doesn't get mistaken for a holding-specific problem. | High confidence the *data* is real. Still only a mechanical signal, though — a Tier A finding tells you *something changed*, not *what to do about it*. That's what the 1-4 ladder below is for. |
| **B** | Best-effort fundamentals — payout ratio, debt-to-equity — pulled from `yfinance`'s `.info` fields. | Whatever fields happen to be populated for that specific ticker; no fixed list, some tickers return more than others. | Low-to-moderate. These fields are known-unreliable for foreign large-caps. Always shown prefixed `[UNVERIFIED, confirm at deep review]` in the output — worth a manual check, never a clean pass/fail on its own. |
| **C** | Credit rating downgrades, M&A, restructuring, regulatory action — qualitative news. | None — `alerts.py` has no mechanical check here at all, by design. | Not computed by this script, period. `alerts` always prints the same disclaimer explaining why: this needs a live web search and real judgment, which is a live Claude session's job (or eventually a scheduled routine), not something a plain Python script can ever do. This is exactly the research a Tier 2 forced review and a Tier 4 replacement search both actually depend on to reach a real verdict. |

### Tier 1 / 2 / 3 / 4 — what to actually do about a finding

Referenced constantly by `alerts`, `buy`'s review banner, `set-target`, and
`review-log`. Full source: `PLAN.md` §8.5.

A single flag is never an automatic sell — a savings portfolio shouldn't churn on
ordinary noise. What happens next depends entirely on which tier a finding lands in:

| Tier | Name | Triggered by | What it means you actually do |
|---|---|---|---|
| **1** | **Watch** | The default for one flag — a single yield-trap reading, payout ratio drifting into the warning band, one `[WATCH]` finding. | Nothing. No trade, no `set-target` call. Just noted, checked again at the next light review. Most flags should die here — this is the normal, expected outcome for most findings, not a near-miss. |
| **2** | **Forced deep review** | Any hard trigger (dividend cut/freeze, credit downgrade, major restructuring/M&A/regulatory news), *or* the same soft flag repeating on 2 consecutive checks (a `[REPEAT]` tag). | Still not sell. Means rerun the full §8.1 scorecard *right now* instead of waiting for the next scheduled review, pulling in real Tier C research, and compare the honest result against alternatives. This is exactly what `alerts`' "FOR A LIVE CLAUDE SESSION" block and `buy`'s "REVIEW NEEDED" banner are reacting to — both exist to make sure this step actually happens instead of getting skipped. |
| **3** | **Trim** | The Tier 2 review comes back weak but not disqualifying — e.g. Chowder Number below threshold with payout ratio still fine, or a one-off freeze on an otherwise healthy balance sheet. | Stop *growing* the position — run `sav set-target <ticker> <lower percent> --reason "..."` to redirect new contributions elsewhere, without forcing a sale. Cheaper than selling, and doesn't lock in a possibly-premature loss on a name that might recover. |
| **4** | **Exit** | The Tier 2 review clearly fails — e.g. payout ratio in Danger tier *and* FCF payout confirmed unsustainable *and* the cut actually materializes — or the holding stopped doing the diversification job it was picked for. | `sav log-sell` the position, and pick a replacement that clears the *same* §8.1 bar the original pick did — not an ad hoc swap for whatever looks appealing that day. A large position can unwind over 1-2 review cycles instead of one rushed sale at a bad price. |

---

## Command reference

### `buy <amount_idr>`

**What it does:** given a Rupiah amount, computes how much of each holding to buy this
month — respecting the rebalance band (drift more than 10pp from target redirects new
money toward whichever holding is most underweight, per `PLAN.md` §3 rule 3), and
flooring every suggestion to whole board lots (100 shares for ASII/BBRI, 1 share for KO).
**Never logs anything** — it's a calculator, not a transaction.

```
sav buy 5000000
```

```
Buy plan — 2026-08-25
Contribution: Rp 5,000,000

REVIEW NEEDED before buying (from `sav alerts` on 2026-08-25, 0d ago) — PLAN.md §8.5 Tier 2:
  - ASII.JK: dividend_cut
  This does NOT change today's split automatically. Tier 2 means rerun the full §8.1 scorecard yourself
  (including the Tier C research this tool can't do) before deciding. If you decide to reduce a position
  afterward: `sav set-target <ticker> <percent> --reason "..."`.

ASII.JK — Astra International (your broker)
  Target    Rp 2,000,000
  Price     Rp 4,850
  Buy       400 @ Rp 4,850
  Spend     Rp 1,940,000
  Leftover  Rp 60,000

BBRI.JK — Bank Rakyat Indonesia (your broker)
  Target    Rp 1,500,000
  Price     Rp 3,160
  Buy       400 @ Rp 3,160
  Spend     Rp 1,264,000
  Leftover  Rp 236,000

KO — Coca-Cola (your broker)
  Target     Rp 1,500,000
  Price      $91.64
  Buy        1 @ $91.64 [leftover top-up]
  Spend      Rp 1,621,936
  Leftover   Rp 0
  Topped up  +Rp 121,936 from shared leftover

Total leftover (carries forward via drift next month): Rp 174,064
USD/IDR used: 17,699.00 — indicative mid-market rate, not what your broker will actually charge (retail FX
carries a spread on top).
Note: KO's own target was Rp 121,936 short of a full lot — topped up from this month's shared leftover
(other holdings' in-lot residue) instead of carrying forward, so 1 lot got bought instead of 0.

Temp-Rest — Example Money Market Fund
  Currently parked       Rp 0
  This month's leftover  Rp 174,064
  Significant threshold  Rp 100,000
  Action                 Above threshold — park it: `sav temp-rest-deposit 174064`

(full report saved: reports/2026-08-25_buy.md)
```

**Reading this output, block by block:**

| Line | Meaning |
|---|---|
| `REVIEW NEEDED...` (only appears sometimes) | Read from your last `sav alerts` run — a hard trigger (like a dividend cut) or a flag that repeated 2 runs in a row. It's informational, not a block: the split below is computed exactly the same either way. See [`alerts`](#alerts) for what counts as Tier 2. |
| `Target` | This holding's ideal Rupiah allocation this month, before rounding to whole lots. |
| `Buy` | The actual whole-lot amount you'd place an order for. `0 (below 1 lot...)` means the target — and every other holding's spare in-lot cash — still wasn't enough to afford one lot; `[leftover top-up]` means it was, see `Topped up` below. |
| `Spend` | `Buy` × `Price`, in Rupiah — what actually leaves your account for this holding. |
| `Leftover` | `Target − Spend`, floored at Rp 0 — money from this holding's own target not deployed to it this month because it didn't reach a full lot. |
| `Topped up` (only appears when this holding's lot was funded from the shared pool) | How much of this lot came from *other* holdings' leftover rather than this holding's own target — see the `Note:` line below the table. Always exactly enough to reach 1 lot, never more, and never at the expense of what another holding already bought. |
| `Total leftover` | Sum of every holding's leftover *after* any top-ups — this doesn't vanish, see the Temp-Rest block, and note that a holding still below minimum shows up more underweight next month automatically (no manual tracking needed). |
| `Temp-Rest — <fund>` block (only appears when there's something to report) | Same shape as a holding block: what's currently parked, this month's leftover against your configured threshold (`config/temp_rest.json`, Rp 100,000 by default), and the resulting `Action` — purely a suggestion, see [`temp-rest-*`](#temp-rest-status--temp-rest-deposit--temp-rest-redeem--temp-rest-set-fund). If something's already parked, an extra `If already redeemed` row reminds you to fold a landed redemption into this month's contribution first. |

The example above shows the two most common real cases at once: ASII/BBRI got bought in
whole lots with a small in-lot leftover each, and KO's Rp 1,500,000 target was Rp 121,936
short of its ~Rp 1.62M lot — but ASII/BBRI's own leftover this month covered that gap, so
KO still got its 1 share instead of carrying the whole target forward. If the pool
*doesn't* cover the gap (a bigger shortfall, or everyone else's leftover already spoken
for), the holding shows `0 (below 1-lot minimum of Rp X)` instead and the shortfall
carries forward via drift next month, exactly as before this feature existed.

---

### `log-buy <ticker> <shares> <price> [--amount] [--date] [--note]` / `log-sell`

**What it does:** records what you *actually* bought or sold — the only thing that makes
a holding real to this tool. `buy`'s suggestion and your real fill can differ slightly
(price moved, you rounded differently); log whatever you actually did.

```
sav log-buy ASII.JK 400 4790
```
```
Logged buy: 400.0 ASII.JK @ Rp 4,790 (Rp 1,916,000.00) on 2026-08-25
```

`log-sell` additionally prints your realized gain/loss (using average cost basis — not
FIFO/LIFO, adequate for tracking health, not tax-grade) *before* logging:

```
sav log-sell BBRI.JK 100 4200
```
```
Average cost basis for BBRI.JK: Rp 3,180.00/share
Realized gain on this sale: Rp 102,000.00
Logged sell: 100.0 BBRI.JK @ Rp 4,200 (Rp 420,000.00) on 2026-08-25
```

**`--amount`:** `price` is the per-share market price — it's what feeds average-cost-basis
math. `amount` defaults to `shares × price`, which is exact when a broker charges 0%
trading fee. If the real cash moved differs (a fee, a withheld tax on a sell), pass
`--amount` to log the true figure without distorting the per-share price:

```
sav log-buy ASII.JK 300 5100 --amount 1532550
```
```
Logged buy: 300.0 ASII.JK @ Rp 5,100 (Rp 1,532,550 [includes Rp 2,550 vs 300 x Rp 5,100]) on 2026-08-25
```

**`--amount` is always in the holding's own currency** — IDR for ASII/BBRI/gold, **USD** for
KO — same as `price`, never the IDR your broker actually debited for a USD holding. `log-buy`
stores `currency=` the holding's configured currency and everything downstream converts
`amount` to IDR by multiplying by the FX rate — feed it an IDR figure for KO and that
conversion happens *twice*, wildly overstating what was invested. There's currently no way
to record "extra IDR spent crossing the FX spread on the funding leg" for a USD holding —
only use `--amount` on a KO row for a genuine USD-denominated fee, if one ever appears on a
trade confirmation; otherwise just leave it at the default `shares × price`.

**Duplicate protection:** if you log the exact same `(date, ticker, type, shares, price)`
twice — an accidental double-run — you'll be asked to confirm before it's logged again,
so a slip doesn't silently double a position.

---

### `log-dividend <ticker> <amount> [--date]`

**What it does:** records a dividend you actually received. This is what lets `status`
show *realized* yield (what you're actually getting), not just the researched projection
from `targets.json` — the two can and do diverge, and that gap is worth noticing.

```
sav log-dividend ASII.JK 45000
```
```
Logged dividend: Rp 45,000.00 from ASII.JK on 2026-08-25
```

---

### `status`

**What it does:** the full current picture — what you hold, what it's worth, how far
each holding has drifted from target, and your real return so far.

```
sav status
```

```
Portfolio status — 2026-08-25

ASII.JK — Astra International
  Shares  0
  Price   Rp 4,790
  Value   Rp 0
  Weight  0.0%  (target 40%)
  Drift   -40.0pp  !

BBRI.JK — Bank Rakyat Indonesia
  Shares  0
  Price   Rp 3,180
  Value   Rp 0
  Weight  0.0%  (target 30%)
  Drift   -30.0pp  !

KO — Coca-Cola
  Shares  0
  Price   $91.99
  Value   Rp 0
  Weight  0.0%  (target 30%)
  Drift   -30.0pp  !

Total value:          Rp 0
Net contributed:      Rp 0
Unrealized gain/loss: Rp 0

Blended dividend yield — gross: 0.00%  |  net of withholding tax: 0.00%
(gross vs. net shown separately on purpose — comparing gross against a net bank rate is
the exact mistake this tool exists to prevent, see PLAN.md §7.3)

Annualized total return (XIRR): not enough data yet.

Drifted beyond the rebalance band: ASII.JK (-40.0pp), BBRI.JK (-30.0pp), KO (-30.0pp)

(full report saved: reports/2026-08-25_status.md)
```

*(This particular run is on an empty portfolio — nothing logged yet — which is why every
`Drift` reads as exactly `-target%`. That's expected: weight is 0% of a $0 portfolio, so
drift is just `0 − target`. It's not a real signal until you've actually logged a buy —
same reason `alerts`' drift warnings are noise right now too.)*

**Reading each field:**

| Field | Meaning |
|---|---|
| `Shares` / `Price` / `Value` | What you hold, its current price, and `Shares × Price` converted to Rupiah. |
| `Weight` vs `(target ...)` | What % of your total portfolio this holding actually is, vs. what it's supposed to be. |
| `Drift` | `Weight − Target`, in percentage points. The `!` flag means it's past the 10pp rebalance band — `buy` will redirect money toward it. |
| `Net contributed` | Sum of every buy minus every sell, in Rupiah — your actual cash in. |
| `Unrealized gain/loss` | `Total value − Net contributed` — paper profit/loss, not yet realized by selling. |
| Blended yield, gross vs. net | Your weighted researched dividend yield, shown two ways on purpose so you never accidentally compare a gross figure against a net bank rate (see the caveat line — this was a real mistake made once while planning this tool). |
| `Realized yield-to-date` (only shown once you have dividends logged) | Actual dividends received ÷ capital invested — the real number, next to the projected one. |
| `Annualized total return (XIRR)` | Your true money-weighted return accounting for *when* each contribution happened, not a naive percentage. Shows `not enough data yet` / a high-variance warning until you have roughly a year of history — judge it against the 7% floor / 10-15% target from `PLAN.md` §7.5 only once it says it's reliable. |
| `Drifted beyond the rebalance band` | Quick list of anything currently outside the 10pp band — the same holdings `buy` will prioritize next. |
| `Temp-rest parked: ...` (only shown once nonzero) | Reminder of what's sitting in the temp-rest fund — see below. |

---

### `set-price <ticker> <price> [--date]`

**What it does:** manually records a price for a holding whose `price_source` in
`targets.json` is `"manual"` — currently unused (all three real holdings pull live prices
via `yfinance`), reserved for something like the gold ETF if that's ever added, since no
free price API covers Indonesia's brand-new gold ETFs yet.

```
sav set-price XMES.JK 780
```
```
Recorded manual price: XMES.JK = Rp 780.00 on 2026-08-25
```

---

### `alerts`

**What it does:** runs every automatable check against your real holdings and each
ticker's price/dividend history, and tells you plainly which tier of confidence each
finding deserves. This is the tool's answer to `PLAN.md` §8's "how do I know if a stock
is straying" question.

```
sav alerts
```

```
Alerts — 2026-08-25

========================================================================
FOR A LIVE CLAUDE SESSION — paste this whole output there.
Why now: yearly deep review due (never done); Tier 2 finding(s) this run: ASII.JK.
Project: C:\...\monthly_savings_portfolio  (see README.md, PLAN.md §8.1 scorecard / §8.5 decision ladder)
Temp-rest right now: Rp 0 in Example Money Market Fund.
Please:
  1. Judge every finding below against §8.1/§8.5 — Watch / Forced deep review / Trim /
     Exit — including the Tier C research (news, credit, restructuring) this script
     structurally can't do on its own.
  2. Re-check the temp-rest fund against your broker's actual live listing (fetch it, compare
     AUM/stability), same approach as the fund already set.
  3. If a change is warranted, apply it with `sav set-target` / `sav temp-rest-set-fund`
     — both log themselves automatically.
  4. Either way, close this out with `sav review-log --reason "..."` so the yearly
     clock resets — even a "no changes needed" verdict counts as a real review.
========================================================================

Tier A (automatable, high confidence):
  [WARNING] ASII.JK: Allocation drift -40.0pp beyond the 10pp band. New contributions already redirect
  toward this via the buy calculator (PLAN.md §3 rule 3).
  [WARNING] BBRI.JK: Allocation drift -30.0pp beyond the 10pp band. ...
  [WARNING] KO: Allocation drift -30.0pp beyond the 10pp band. ...
  [WARNING] ASII.JK: Dividend cut: 292 on 2026-05-05 vs 308 on 2025-05-21 (~1yr prior), -5.2%. Hard
  trigger, no threshold needed (PLAN.md §8.3) — forces a Tier 2 deep review (§8.5), not an automatic sell.
  [WATCH] KO: Chowder Number 6.9% (yield 2.3% + 5yr div growth 4.6%) is below its 15% threshold — worth
  comparing against alternatives at the next annual review (PLAN.md §8.1).

Tier B (best-effort fundamentals):
  [INFO] ASII.JK: [UNVERIFIED, confirm at deep review — free-API fundamentals for foreign large-caps are
  unreliable] payout ratio 53%, debt/equity 41
  [INFO] BBRI.JK: [UNVERIFIED, ...] payout ratio 89%
  [INFO] KO: [UNVERIFIED, ...] payout ratio 62%, debt/equity 116

Impact preview for ASII.JK (illustrative only — NOT a recommendation, nothing applied):
  Currently:  ASII.JK 40.0% | BBRI.JK 30.0% | KO 30.0%
  If -5pp            ASII.JK 35.0% | BBRI.JK 32.5% | KO 32.5%
  If -10pp           ASII.JK 30.0% | BBRI.JK 35.0% | KO 35.0%
  If full exit (0%)  ASII.JK 0.0% | BBRI.JK 50.0% | KO 50.0%
  These step sizes are arbitrary round numbers for comparison, not derived from any formula — the real
  number is your call after the full §8.1 review. Preview your own exact number anytime:
  sav set-target <ticker> <percent> --preview

Tier C (credit downgrades, restructuring, M&A news) isn't covered by this script — that needs a live web
search, which is the scheduled Claude Code routine's job on top of this output, not something a plain
Python script can do.

(full report saved: reports/2026-08-25_alerts.md)
```

**What each part means:**

- **"FOR A LIVE CLAUDE SESSION" block** — appears only when something actually warrants
  a real look: the yearly deep review is due (see below), or a Tier 2 finding fired this
  run. Deliberately dual-purpose: plain enough to read yourself, but structured with an
  explicit task list so pasting the *whole* output into a fresh Claude conversation gets
  you a useful review immediately, no back-and-forth needed to explain what's being
  asked. Covers both the main stocks (§8.1 scorecard) and the temp-rest fund in one pass,
  since neither can be mechanically re-evaluated by this script — picking a stock
  replacement needs the same live Tier C research a credit-downgrade check does, and
  re-checking the fund needs a live fetch of your broker's listing (no stable API exists for
  either, see `temp-rest-set-fund` below). It's a nudge to go have that conversation, not
  an automatic re-pick. Closing it out with `sav review-log --reason "..."` (or a real
  `set-target`/`temp-rest-set-fund` change) resets the yearly clock — data in
  `data/deep_review_log.csv`.
- **Tier A / B / C sections** — see [The review ladder](#the-review-ladder) above for the
  full breakdown of what each tier covers and how much to trust it; this is just where
  they show up in real output. Seven checks feed Tier A, `[UNVERIFIED]`-prefixed fields
  feed Tier B, and Tier C is always the same disclaimer since this script has no check
  for it at all.
- **`[WATCH]` vs `[WARNING]`** — severity within Tier A. `WATCH` is the default response
  to one flag (Tier 1 on the decision ladder — most flags should die here, no action
  needed). `WARNING` means it's more serious on its own (a hard trigger, or drift more
  than 1.5× the band).
- **`[REPEAT]` tag** (not shown above — this was a first occurrence) — appears when the
  exact same `(ticker, check)` also fired on the immediately preceding run. This is what
  escalates an ordinary `WATCH` to Tier 2 on its own, per the decision ladder.
- **Impact preview** — only shown for a ticker that hit **Tier 2** this run (a dividend
  cut, or a flag that repeated). Shows what a hypothetical −5pp / −10pp / full-exit trim
  would ripple to every other holding's weight, purely so you can gauge severity. The
  step sizes are arbitrary and explicitly labeled as such — nothing here is a
  recommended number, and nothing is applied.

Every run also appends to `data/alerts_log.csv`, which is what makes `[REPEAT]` tags and
`buy`'s review banner possible — running `alerts` regularly (roughly monthly, alongside
`buy`) is what keeps that history meaningful.

---

### `set-target <ticker> <percent> [--reason "..." | --preview]`

**What it does:** applies a conscious decision to change a holding's target weight — a
Tier 3 Trim (stop growing a position) or the first step of a Tier 4 Exit (`PLAN.md`
§8.5). The number always comes from you, after your own §8.1 scorecard review — this
command's job is just to do the redistribution math safely and leave a record of why.

```
sav set-target ASII.JK 30 --reason "payout ratio 91%, repeat flag 2026-09"
```
```
Target weight changed: ASII.JK 40.0% -> 30.0%
Reason: payout ratio 91%, repeat flag 2026-09

New targets (redistributed proportionally):
  ASII.JK: 40.0% -> 30.0%
  BBRI.JK: 30.0% -> 35.0%
  KO: 30.0% -> 35.0%

This only changes future contributions via `sav buy` — existing shares are untouched. If
this is a full Tier 4 Exit, log the sale separately with `sav log-sell`.
```

**How the redistribution works:** the weight you free up (or absorb) is split across the
*other* holdings in proportion to their own current weight — not equally. In the example
above, BBRI and KO were tied at 30/30, so they split the freed 10pp evenly (+5pp each).
If they weren't tied, the larger one would absorb proportionally more.

**`--preview`** dry-runs any number without writing anything or requiring `--reason` —
use it to size a change before committing:

```
sav set-target ASII.JK 25 --preview
```
```
PREVIEW ONLY — nothing written. ASII.JK 40.0% -> 25.0%:
  ASII.JK: 40.0% -> 25.0%
  BBRI.JK: 30.0% -> 37.5%
  KO: 30.0% -> 37.5%

Run without --preview (and with --reason) to actually apply this.
```

Every real (non-preview) change is logged to `data/target_changes_log.csv` — a permanent
record of what changed, when, and why. It also resets `alerts`' yearly deep-review clock
(`data/deep_review_log.csv`), since making a real change is itself evidence a review
happened.

---

### `temp-rest-status` / `temp-rest-deposit` / `temp-rest-redeem` / `temp-rest-set-fund`

**The problem this solves:** `buy` almost always leaves some leftover (below-1-lot)
Rupiah sitting unspent. Left alone, that money earns 0% for a month. `temp_rest` gives it
somewhere better to sit: a money-market fund (Reksadana Pasar Uang) at your broker — near-zero
volatility, no board-lot minimum (buyable from Rp 10,000), typical yield ~3-4%/yr. It's
explicitly a "better than zero while parked" vehicle, not a yield play — your ~6% digital
bank savings option still beats it on pure return.

**The full cycle:**

1. `buy` tells you when leftover crosses your threshold (`config/temp_rest.json`, Rp
   100,000 by default) and suggests parking it.
2. You manually buy the fund at your broker, then log it: `sav temp-rest-deposit <amount>`.
   **Found 2026-08-26: at your broker, reksadana purchases are funded separately from your
   stock RDN** — the RDN cash balance (leftover sitting there after a stock buy) can't be
   used directly to buy the fund; it needs its own top-up via bank transfer or e-wallet
   (e.g. Gopay) through the reksadana tab. Not a one-tap move between the two, despite
   both living in the same app — budget for that extra step, or just fold small leftovers
   into next month's contribution instead of parking them every single time.
3. A few days *before* your next buy day (fund redemptions take T+1/T+2 business days —
   not instant), redeem it at your broker, then log it: `sav temp-rest-redeem <amount>`.
4. Once it's actually landed in your account, fold that amount into that month's
   contribution — `sav buy <this month's contribution + the redeemed amount>`. No special
   flag needed: it just flows through the normal rebalance-aware split like any other
   money.

```
sav temp-rest-status
```
```
Temp-rest status
  Current fund:           Example Money Market Fund
  Tracked balance:        Rp 0
  Significant threshold:  Rp 100,000

This is a ledger balance (deposits minus redemptions you've logged), not a live NAV pull
— no price API used here covers Indonesian money-market funds. The real current value on
your broker should be at or above this (the fund accrues quietly); typical money-market yields
ran ~3-4%/yr in 2025-2026 — meaningfully below a typical ~6% digital-bank savings rate, so
treat this purely as a 'better than zero while parked' vehicle, not a yield play.
```

**Important:** the "Tracked balance" is a plain ledger (what you've logged in minus what
you've logged out) — not a live pull from your broker, since no price API covers Indonesian
reksadana. It should be at or slightly below the real value shown in your app (the fund
quietly accrues interest this tool doesn't try to estimate). If the gap looks bigger than
a few weeks of ~3-4%/yr accrual would explain, trust your broker's number, not this one.

`temp-rest-deposit`/`temp-rest-redeem` take an amount and log it — same shape as
`log-buy`. `temp-rest-redeem` warns (but doesn't block) if you try to redeem more than
the tracked balance, since real accrued interest isn't included in the ledger.

**`temp-rest-set-fund`** changes which fund is used — a yearly review, same
audit-with-reason pattern as `set-target`. *Which* fund to use is a research question
this script can't answer on its own: neither your broker nor OJK expose a stable data feed a
script could hit directly (your broker has no public API, and OJK's public NAB data doesn't
know which funds your broker specifically curates into its app), so this stays a live check —
`sav alerts` will remind you once a year since the last change (as part of the same
unified deep-review block that covers the main stocks too, see [`alerts`](#alerts)), but
the actual picking happens in a conversation. Yearly is also the right cadence on its own
merits: chasing a "better" fund more often than that would mostly be reacting to noise
between near-identical low-volatility funds, not a real difference. Using this command
also resets the deep-review clock, same as `set-target`.

```
sav temp-rest-set-fund "Example Money Market Fund" --reason "Largest AUM among RDPU options at your broker"
```

---

### `review-log --reason "..."`

**What it does:** stamps that a full live-session deep review happened today, even when
the conclusion was "everything's still fine, no changes." `set-target` and
`temp-rest-set-fund` already reset the yearly clock automatically *when you actually use
them* — this command covers the other common outcome, where you review and decide
nothing needs to change. Without it, a clean review would leave `alerts` nagging every
run even though you'd already done the work.

```
sav review-log --reason "2027 annual review: all 3 holdings still pass §8.1, temp-rest fund unchanged"
```
```
Logged deep review: 2027-08-25 — 2027 annual review: all 3 holdings still pass §8.1, temp-rest fund unchanged
```

---

### Risk Pot — `risk-log-buy` / `risk-log-sell` / `risk-set-cap` / `risk-extend` / `risk-screen` / `risk-buyback-watch` / `risk-status` / `risk-alert`

**What it is:** a separate, riskier short-horizon trading allocation — completely isolated
from everything above. Own capital (funded by money you bring in specifically for this —
never from your monthly contribution or from temp-rest leftover), own ledger
(`data/risk_transactions.csv`), own commands. `sav buy`/`sav status`/`sav alerts` have zero
code path in common with this pot; it can't corrupt your blended yield, Chowder Number, or
any core-sleeve number. 

**The rules:** up to 100% of the configured cap (Rp 1,000,000 to start — change it with
`risk-set-cap` as you add or pull out capital) on a single position — sizing is a
per-trade call, not a fixed split, so size it deliberately rather than defaulting to "all
of it." Stop-loss and take-profit are a two-stage **profit-lock ladder** (`RISK_POT_PLAN.md`
§4.1, revised 2026-09-02): a hard **-15%** circuit breaker from entry; once the position's
peak gain hits **+10%**, that floor rises to breakeven (worst case becomes "no loss"); once
it hits **+20%**, the floor starts trailing 10 percentage points behind the highest price
since entry — that trailing floor **is** the take-profit trigger, banked when price actually
falls off its own peak, never sold at a predetermined number (the Remora method's own "let
winners run" + asymmetric-risk/reward philosophy ruled out a fixed target on purpose). No
fixed calendar checkpoint either — this pot is short-horizon trading that can resolve in
days, so `sav risk-alert`, run every morning, is the real decision trigger: whatever it flags
that day is what needs a live decision, a quiet day needs nothing. Candidates come from
Kompas100 (or JKSE as a wider net, with a real liquidity check), never a ticker already in
your core three.

```
sav risk-log-buy ANTM.JK 500 1900 --stop-loss 1615
```
```
Logged risk-pot buy: 500.0 ANTM.JK @ Rp 1,900 (Rp 950,000) on 2026-08-27
```

**Track B entries (added 2026-09-25, `RISK_POT_PLAN.md` §3.1b):** an early special situation
(the SINI/STAR pattern) gets its own exit ladder. Pass `--track B` and the base pivot the
breakout cleared (from your own chart analysis). Without
`--stop-loss`, the base stop is computed as the higher of pivot −3% and entry −15%:

```
sav risk-log-buy SINI.JK 100 4400 --track B --pivot 4000
```
```
Track B base stop: Rp 3,880 (the higher of pivot -3% and entry -15%). Ladder: breakeven after +30%, 25% trail after +50%.
Logged risk-pot buy: 100.0 SINI.JK @ Rp 4,400 (Rp 440,000) on …
```

From then on `risk-status` shows the track and pivot, the effective stop follows Track B's
ladder (numbers in `config/risk_config.json` → `"track_b"`), and `risk-alert` reports index lag
or a single heavy down day as information only, not exit signals. `--pivot` without
`--track B`, or `--track B` without `--pivot`, is refused before anything is written.

```
sav risk-status
```
```
Risk Pot status — 2026-09-02

ANTM.JK
  Shares            500
  Avg cost          Rp 1,900
  Price             Rp 2,100
  Unrealized P&L    Rp 100,000 (+10.53%)
  Stop-loss (base)  Rp 1,615
  Effective stop    Rp 1,900  (breakeven lock armed — this is the take-profit trigger, RISK_POT_PLAN.md §4.1)
  Entry             2026-08-27 (6d ago)
  Last reviewed     2026-08-27 (6d ago)

Capital at work: Rp 950,000 / Rp 1,000,000 cap (95%)
```

Six days in, no calendar checkpoint required for any of this — the peak gain (+10.53%)
crossed the +10% breakeven-lock threshold on its own, so the floor rose from the base
Rp 1,615 to breakeven (Rp 1,900) automatically. Not yet past +20%, so the trailing lock isn't
armed — that's what would actually start banking profit as the position (hopefully) keeps
running.

```
sav risk-extend ANTM.JK --reason "still beating JKSE, thesis intact"
```
```
ANTM.JK: 6d since last review (2026-08-27) — return +10.53% vs ^JKSE +1.14% over the same window.
Logged today's review of ANTM.JK — keeping the position open, no fixed cycle to renew. Reason: still beating JKSE, thesis intact
```

```
sav risk-set-cap 1500000 --reason "topped up another Rp500,000"
```
```
Risk Pot cap changed: Rp 1,000,000 -> Rp 1,500,000
Reason: topped up another Rp500,000
Capital at work: Rp 950,000 / Rp 1,500,000 cap (63%)
```

`risk-set-cap` is how you add or withdraw risk-pot capital — it only rewrites
`pot_cap_idr` in `config/risk_config.json` (every other field there is untouched) and logs
the change to `data/risk_cap_log.csv` with your `--reason`. It warns, but doesn't block, if
the new cap would sit below what's already deployed in open positions — trim/close a
position with `risk-log-sell` first if that's what you actually meant.

```
sav risk-screen
```
```
Risk Pot screen — 2026-08-27
Candidate universe: config/risk_watchlist.json (5 names) — a hand-maintained list, NOT a live Kompas100/JKSE index scan.
Benchmark ^JKSE: 1mo +3.64%  3mo +4.62%

 Ticker     Theme                             1mo      3mo  3mo excess    Avg daily value
*MBMA.JK   nickel/EV battery supply chain     8.9%    15.5%      +10.9pp Rp 63,052,091,606/day
*ANTM.JK   nickel/EV battery supply chain     5.7%    13.8%       +9.2pp Rp 383,679,209,901/day
*INCO.JK   nickel/EV battery supply chain     8.5%     7.7%       +3.1pp Rp 74,581,029,622/day
*GOTO.JK   digital economy                  0.0%     0.0%       -4.6pp Rp 8,688,969,206/day
*BUKA.JK   digital economy                -12.4%   -16.5%      -21.2pp Rp 21,857,390,602/day

* = shortlist (top 5, ranked by 3mo excess return vs ^JKSE). Liquidity is a real average, not a pass/fail cutoff — judge it yourself (§3 item 1).
```

`risk-screen` automates §3.1's steps 1-2 only: build the candidate universe (excluding
tickers already in the core sleeve) and rank by trailing return vs the benchmark. **It is
not a live Kompas100/JKSE index scan** — there's no reliable API/source for current index
membership, so it runs against `config/risk_watchlist.json` instead, a hand-maintained list
seeded from `RISK_POT_PLAN.md` §3's own starting watchlist. Expand that file with
individually-researched names as you go, the same way KLBF replaced KO (`PLAN.md` §8.7) —
don't paste in a scraped index list. Steps 3-6 (a real catalyst check, picking or skipping
the slot, sizing by consultation, writing the thesis + stop-loss) still need actual
judgment before `risk-log-buy` — this command only saves the per-ticker legwork of pulling
and ranking the numbers.

```
sav risk-buyback-watch
```
```
Buyback expiry watch — 2026-09-11 (window: next 21 days)
Mechanical only — a program ending soon is a reason to check for a renewal announcement (RISK_POT_PLAN.md §3.10), not itself a signal to trade.

  PRDA.JK     Rp150,000,000,000  ends 2026-11-19  (69d left)
             Newest program found in the 2026-09-11 screen, and the nearest confirmed expiry on this list — worth a real watch in the weeks leading up to 2026-11-19 for a renewal/escalation announcement.
  ERAA.JK     Rp500,000,000,000  ends 2026-12-04  (84d left)
             Renewal of a completed Rp100B program (2026-06-02 to 2026-09-02) — the renewal pattern this command exists to catch. Watch ~2 weeks before 2026-12-04 for a further renewal.

Nothing inside the 21-day watch window right now.

Dates here are only as good as what was verified when added — re-check the ticker's own Stockbit Corp. Action tab (or IDX's Keterbukaan Informasi portal) before treating an 'ends soon' flag as certain.
```

`risk-buyback-watch` exists because the real re-rating trigger, per ERAA's own case study
(`RISK_POT_PLAN.md` §3.10), was a **renewal** announced the instant the original program closed —
not the program itself. Flags any `config/buyback_calendar.json` program ending within `--days`
(default 21) so that renewal can be watched for proactively instead of discovered after the market's
already reacted (§3.12/§3.13's "catch it early" discussion). Mechanical and hand-maintained, same
spirit as `risk-screen`'s own watchlist — dates are only as good as what was verified when added,
re-check via the ticker's own Corp. Action tab before trusting an "ends soon" flag.

`risk-extend` **refuses** if the position doesn't clear the mechanical bar (positive return
*and* beating the benchmark since the last review) — pass `--force` to override anyway,
which still requires `--reason` and is logged with a "(bar not met — forced)" note so the
ledger is honest about it. `risk-alert`, run every morning, is the actual daily trigger
(`RISK_POT_PLAN.md` §4.2): it flags an effective-stop breach (stop-loss *or* take-profit —
exit now, either way), the benchmark bar failing (once the position has at least 5 days of
history, so day-1 noise doesn't fire it), a Wyckoff "danger" price-volume day (down price,
up volume — worth a look, not an automatic exit), 21 days with zero flags at all (a soft
sanity-check nudge, not a forced decision), or the pot exceeding its cap — and logs whatever
it finds to `data/risk_alerts_log.csv`. `risk-log-sell` mirrors the core `log-sell` —
prints the realized gain/loss before logging the exit.

**Currency is always IDR** — the candidate universe (Kompas100/JKSE) is IDX-only, so unlike
core `log-buy`, there's no `--amount`-currency decision to make here.

---

### `undo`

**What it does:** undoes the last logging command — `log-buy`, `log-sell`, `log-dividend`,
`set-price`, `set-target`, `temp-rest-deposit`, `temp-rest-redeem`, `temp-rest-set-fund`,
`review-log`, or a Risk Pot `risk-log-buy`/`risk-log-sell`/`risk-set-cap`/`risk-extend`. Every one of those, right before it writes anything, snapshots the
file(s) it's about to touch; `undo` restores that snapshot and shows you what it's about
to revert before doing it.

```
sav undo
```
```
Undo `sav log-buy ASII.JK 300 51000 --amount 1532550`? This restores: transactions.csv [y/N] y
Undone: sav log-buy ASII.JK 300 51000 --amount 1532550
Restored: transactions.csv
```

**Single level only** — it undoes the one most recent logging command, then clears the
record, so running it twice in a row does nothing the second time ("Nothing to undo.").
It also only tracks the commands above: `buy`, `status`, `alerts`, `risk-status`, and
`risk-alert` aren't logging actions (their file writes, where they have any, are incidental
— fetched prices, alert history) and don't push an undo record, so running one of those
between a mistake and `sav undo` won't bury your ability to undo the mistake.

---

## Data files

Everything under `data/` and `config/` is plain CSV/JSON — inspect or back up with any
text editor, no database involved.

| File | What it holds |
|---|---|
| `config/targets.json` | Target weights, tickers, rebalance band, lot sizes, tax rates. Edited only via `set-target`, never by hand once real money is in. |
| `config/temp_rest.json` | The temp-rest threshold and current fund name. |
| `config/risk_config.json` | Risk Pot settings — cap, stop-loss %, the profit-lock ladder (`breakeven_lock_pct`/`trailing_stop_arm_pct`/`trailing_stop_pct`), review gating (`min_review_days`/`stale_review_days`), benchmark, candidate universe. The cap (`pot_cap_idr`) is edited via `risk-set-cap`; every other field is still hand-edited. |
| `config/risk_watchlist.json` | `risk-screen`'s candidate list — hand-maintained, not a live index feed. Add individually-researched tickers as you find them. |
| `config/buyback_calendar.json` | `risk-buyback-watch`'s hand-maintained list of known buyback programs' end dates — flags an approaching expiry so a renewal announcement (the real re-rating trigger, `RISK_POT_PLAN.md` §3.10) can be caught early. |
| `data/risk_transactions.csv` | The Risk Pot's own buy/sell ledger — completely separate from `transactions.csv` above, on purpose. |
| `data/risk_extensions_log.csv` | Audit trail of every `risk-extend` decision and its reason. |
| `data/risk_cap_log.csv` | Audit trail of every `risk-set-cap` change and its reason. |
| `data/risk_alerts_log.csv` | Every `risk-alert` run's findings — effective-stop breaches, benchmark-bar failures, price-volume danger days, staleness nudges, cap warnings. |
| `data/risk_screen_log.csv` | Stamped automatically every time `risk-screen` runs — the recurring-cadence clock the daily routine reads to decide whether a fresh wide screen is due (`RISK_POT_PLAN.md` §3.12/§3.13). |
| `data/transactions.csv` | Every real buy/sell — the only source of truth for what you own. |
| `data/dividends.csv` | Every dividend actually received. |
| `data/manual_prices.csv` / `price_history.csv` | Manual price entries and the daily price history `alerts` checks against. |
| `data/alerts_log.csv` | Every `alerts` run's findings — powers `[REPEAT]` tags and `buy`'s review banner. |
| `data/target_changes_log.csv` | Audit trail of every `set-target` change and its reason. |
| `data/temp_rest_log.csv` | Deposit/redemption ledger for the temp-rest fund. |
| `data/temp_rest_fund_log.csv` | Audit trail of every `temp-rest-set-fund` change and its reason. |
| `data/deep_review_log.csv` | The unified yearly review clock — `set-target`, `temp-rest-set-fund`, and `review-log` all append here; `alerts` reads it to decide when to show the "FOR A LIVE CLAUDE SESSION" block. |
| `reports/YYYY-MM-DD_<command>.md` | A saved Markdown copy of every `buy`/`status`/`alerts` run — gitignored, purely a local scratch record. |
| `data/.last_action.json` | Internal — the single-level `undo` snapshot of whatever the last logging command changed. Not tracked in git; don't hand-edit it. |

---

## Troubleshooting

**`python` / `python -h` doesn't work at all** — this project pins its Python version via
`.python-version` (pyenv). If `python` is broken outside this folder too, that's a
separate machine-wide pyenv setup issue, not this project.

**`sav <command>` isn't recognized** — `sav` is an optional shell alias/function you set
up yourself (see [Setup](#setup)). Open a new terminal after adding it, or just use
`python cli.py <command>` from the project folder.

**A command errors with "Config error" or "Data error"** — these always name the specific
bad file/row rather than crashing blind. Fix the named issue and rerun; nothing partially
writes on a validation failure.

**Ticker not recognized** — must match `config/targets.json` exactly, including the
exchange suffix (`ASII.JK`, not `ASII`).
