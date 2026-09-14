# AI Portfolio Manager

A research-led journey from automated trading to clearer investment decisions.

Reviewed: 14 September 2026. The historical experiments below retain their original dates; they are not new September backtests. All money examples on this page are research assumptions, not the owner's balances or personal returns.

## The idea

This project started with a question: can software make a sensible stock-trading decision, carry it out, and tell us honestly whether it worked after paying all the costs?

We built and tried that full journey. The useful outcome was not just a trading bot. It was a reusable system for collecting financial data, applying consistent rules, checking risk, explaining decisions, and learning from results.

**The main focus is now portfolio analysis** (understanding a collection of investments): studying Indian stocks, US stocks and mutual funds, then giving reasoned recommendations for a person to review. **Intraday trading is paused. Options remain research-only.**

The name includes AI (software that generates interpretations from information), but AI is optional. The default analysis uses transparent, repeatable calculations. A recommendation is a decision aid, not a promise of profit or an instruction automatically sent to a broker.

## End-to-end trading

**Intraday** means buying and selling within the same trading day, without carrying the position overnight. The system was implemented and tried from decision-making through actual broker buy and sell orders, monitoring, closing trades and checking the final records.

1. **Collect the facts.** Read prices and volume (how many shares changed hands). A candle (the opening, highest, lowest and closing price over a time interval) summarises the market's movement.
2. **Find candidates.** Scan a universe (the list of stocks being considered). The current retained research profile uses NIFTY100 (a group of 100 large Indian listed companies).
3. **Make a decision.** Apply a strategy (a written set of rules for entering and leaving a trade). Several different ideas were tested instead of assuming that one clever rule must work.
4. **Check the risk.** Choose a position size (how many shares to trade), a stop-loss (a price at which to exit a losing trade), and a target (a planned price for taking profit). Check available funds, trading costs and limits on simultaneous trades.
5. **Send and track orders.** Use Zerodha Kite (the broker's software interface) to submit orders and check fills (the quantity and price actually executed). An order request is not treated as proof that the trade happened.
6. **Manage the trade.** Monitor prices and risk limits. Depending on the selected strategy, close at the stop, target, or square-off time (the deadline for closing intraday positions).
7. **Check the outcome.** Reconcile (compare our records with the broker's records), account for charges, and write reports. Simulated trades and real trades have separate records so practice results do not become personal profits or tax figures.

The reusable engineering includes the scanner, risk checks, order handling, recovery paths, recordkeeping and reporting. **The end-to-end workflow is real; reliable future profitability is not yet established.**

## What the tests taught us

A **backtest** is a rehearsal using historical market data. It asks what a precise set of rules would have done in the past. A **dry run** is a rehearsal using arriving market prices without sending real orders. **Live evidence** comes from actual broker executions. These are different kinds of evidence and must not be mixed.

We used **walk-forward testing** (developing rules on an earlier period, then checking them on a later period). The later **out-of-sample** period (data kept separate from the initial development period) is more informative than the data used to tune the rules. Repeatedly choosing settings after looking at that later period still weakens its independence; a new untouched period and forward trials are needed before promotion.

The work progressed from a blended indicator system, through a 62-gate audit (testing filters that allow or reject trades), to market-condition filters, different time intervals, entry and exit rules, and separate strategy families. Failed experiments were kept as evidence rather than hidden.

**Profit factor** (total gains divided by total losses, using the experiment's after-trading-cost results) is one way to read the following table. Below 1.00 means losses exceeded gains. A value of 1.30 means 1.30 units were gained for every 1 unit lost; it does not mean a 30% portfolio return. Fixed subscriptions are assessed separately.

| Historical experiment | Profit factor | What we learned |
|---|---:|---|
| Legacy blended rules, held-out test, May 2026 | 0.82 | Many indicators and risk filters did not create a profitable entry signal. |
| Legacy rules limited to volatile markets, June 2026 | 1.10 | Filtering market conditions helped, but stayed below the research gate of 1.15. |
| Gap-and-Go v1.2.0, reported TEST cohort, June 2026 | 1.30 | The strongest retained candidate in that comparison; 179 simulated trades and further validation still required. |
| Directional option buying, held-out test | 0.64 | The tested rules did not overcome losses and costs. Option premiums were modelled. |
| Defined-risk iron condor, held-out test | 0.46 | Buying protection did not make the tested option-selling structure profitable. Premium-model limitations still apply. |

These are separate experiments, not a single combined track record or a like-for-like ranking. The option results used estimated premiums (the prices paid for option contracts); a later check against recorded quotes was limited and did not turn those backtests into real executions.

**Gap-and-Go** tries to follow an opening gap (a jump from yesterday's close to today's opening price) when volume and price behaviour support continued movement. Research improved entry timing so a decision did not use a candle that had not finished, added checks that the gap was holding, tested wider stock lists, and adjusted volume rules on days when many stocks gapped together.

For the reported v1.2.0 cohort, maximum drawdown (the largest fall from a previous high in the simulated results) was 11.58%, and Sharpe was 1.29 (a measure of return relative to how variable the results were). These figures are historical research measurements, not a forecast or evidence of current personal performance.

Other recorded work included shorter five-minute entries, opening-range breakouts (trading beyond the first period's high or low), mean reversion (looking for an unusually large price move to partly reverse), previous-day breakouts, momentum (following continuing price strength), and book-inspired configurations. The August 2026 book-configuration retest and late-start fallback audit did not provide a reason to resume live trading.

Source record: the project's Trading Statistics and Trading Roadmap, including the June Gap-and-Go comparisons and the August 2026 options, book-strategy and fallback reviews. This page summarises those archived results; it does not claim they were rerun today.

## Why intraday is paused

**The tested small-capital setup was not compelling after all costs.** Capital means the money available for trading. A small gain before costs is not the same as money an investor can keep.

Each round trip (entering and exiting a trade) can incur brokerage (the broker's fee), exchange and regulatory charges (fees for operating and supervising the market), STT (a tax on certain securities transactions), GST (tax on applicable services), and stamp duty (a transaction levy). Spread (the difference between the available buying and selling prices) and slippage (getting a worse execution price than assumed) also reduce results. A paid data/API subscription is an additional operating cost, separate from per-trade charges.

An archived v1.1.1 sizing example illustrates the problem:

| Research assumption or outcome | Amount |
|---|---:|
| Assumed starting capital | Rs.50,000 |
| Historical annual result after modelled trading charges | Rs.+3,621 |
| Assumed API subscription: Rs.500 per month for 12 months | Rs.-6,000 |
| Result after that subscription assumption | Rs.-2,379 |

This is a specific historical example, not the same cohort as the v1.2.0 table above. Subscription prices and regulatory rates can change and must be checked before a new experiment. It is not a general claim that nobody can make money with Rs.50,000, or that putting in more money would automatically solve the problem.

**We have not demonstrated that the system beats a suitable market benchmark after all costs** (a fair comparison with, for example, an appropriate index over the same dates and with comparable risk). A positive backtest alone does not settle that question. Simply increasing capital is not a validated investment plan.

The decision was to stop live intraday trading and shift the main effort toward investment analysis. The decision logic and engineering remain useful foundations for ongoing research. A stronger cost model, genuinely fresh test data and successful forward validation would be needed before considering a new live pilot.

## The portfolio pivot

Instead of asking only "what should we trade this morning?", the active work asks "what do these investments contain, where are the risks, and what deserves further study?"

**Indian stocks.** The tool reads holdings (shares already owned) and evaluates each business and its market behaviour. A factor scorecard (a structured score made from several different checks) separates the investment rating from the risk grade. It considers trend, momentum, risk-adjusted results (returns considered alongside the risks taken), quality, valuation (price compared with business measures), and position fit (whether the stock makes sense in the existing portfolio). Missing data is shown as missing rather than silently treated as poor performance.

**US stocks.** A separate long-term scorecard considers business quality, valuation against sector peers (similar businesses), growth, momentum, relative strength (performance compared with a reference market), and risk. It supports research actions such as accumulate, hold, watch, review, trim or avoid. It is not presented as an intraday or swing-trading signal. Currency conversion (translating dollar values into rupees) is shown with its source and freshness.

**Mutual funds.** A mutual fund pools investors' money into a basket managed by a fund provider. Its NAV (the price of one fund unit) is usually published once a day, not continuously like a stock price. The tool combines Coin holdings with funds entered from other brokers, tracks SIPs (regular scheduled investments), and studies allocation (how the money is divided), concentration (too much exposure to the same investment), and similar fund behaviour. Correlation (how similarly two funds' prices move) is a clue about similarity, not proof that they own identical stocks.

**The portfolio view.** It looks across sectors (groups of related businesses), market-cap groups (company-size categories), concentrated positions, weak areas and possible additions. A high stock rating need not mean "buy more": an already oversized holding might need trimming (reducing its size). These recommendations support human judgment; this analysis does not automatically place orders.

**Optional AI.** AI can add a plain-language thesis (the case for an investment), risks and context. It does not replace the underlying calculations. A separate prompt-builder can prepare information for a user's chosen AI service; that is a deliberate external-data-sharing decision, not an automatic private conversation.

## What each page represents

| Dashboard page | Its purpose | Current phase |
|---|---|---|
| Portfolio HQ | A combined view of tracked Indian shares, mutual funds and US holdings, plus percentage performance and allocation. | Active. It does not represent assets that have not been entered or connected. |
| Portfolio | Indian-stock study, portfolio gaps, ratings, risk and per-stock explanations. | Active, decision support; no automatic broker orders. |
| Mutual Funds | Coin and externally tracked funds, NAV history, scheduled investments and structural review. | Active; freshness depends on available NAV and broker data. |
| US | Long-term US-stock study, holdings, candidate research and comparisons. | Active; data coverage and provider availability are visible limitations. |
| Swing | Indian stock setups held over days or weeks, watchlists and a local position tracker. | Implemented research/workflow surface, not a claim of validated returns. |
| Intraday | Historical actual-trade results and day-level explanations. | Live execution paused; retained for evidence and research. |
| Dry Run | Separate simulated trade results, including the options mode selector. | Research; these are not real profits or tax records. |
| Tax | Realised gains (results after closing an investment), charges and tax estimates. | Recordkeeping aid; verification and professional review may still be needed. |
| Theory | This project story, plus the detailed strategy and research references. | Public overview and deeper research documentation. |

The Indian swing tracker can describe shares already counted in the Indian holdings. HQ does not add those shares a second time. Unrealised results (gains or losses on investments still held) are also kept distinct from realised results (gains or losses after closing).

## Options: research before risk

An **option** is a time-limited contract linked to an asset or index. The premium is its price. A call gives its buyer the right to buy at a specified price, and a put gives its buyer the right to sell. The expiry is the date that right ends. The price can change because of time remaining and expected market movement, not just the direction of the underlying stock or index.

The project has an options scanner, pricing research, simulated order handling, tracking and reports. **That is infrastructure, not a validated trading strategy.** Directional buying (buying an option because a market move is expected) and a defined-risk iron condor (combining options to benefit from a limited price range while buying protection against larger moves) have been tested and failed the recorded profitability checks.

The roadmap keeps a calendar spread (combining options with the same strike price but different expiry dates) as an unproven next research candidate. Recorded real premiums and broader date coverage are needed. An options model must account for contract size, expiry, spread, execution and charges. We will not jump directly from an attractive theory to real trades. The default remains dry-run, and uncovered option selling (selling without protective positions) is blocked.

## How it is built

The application is a Python program with a local browser dashboard. A CLI (a command typed in a terminal) starts a selected mode. HTML and JavaScript display the dashboard; Python services read data and perform the calculations.

Responsibilities are separated: market-data clients fetch information; analysis engines calculate scores; strategy scanners propose candidates; order engines handle execution rules; trackers and persistence modules maintain records; renderers present results. Shared calculations, logging, risk measurements and the dashboard theme are reused instead of being copied into every page.

SQLite (a small database stored in a local file) holds structured records. Cached data (a saved copy of a previous response) keeps the interface usable when a provider is slow or unavailable. Source and date fields help distinguish fresh information from older snapshots.

Three repositories have different jobs: the application contains code and public documentation; the backtest-data repository contains versioned market-research datasets; the private operational-data repository holds backups of personal records. Market data must not be confused with broker tokens, personal positions or tax ledgers. A configuration fingerprint (a short identifier for the settings used) helps connect an experiment to its exact rules.

## Privacy and limits

Dashboard privacy mode is shared across pages. It keeps percentage performance while masking personal monetary amounts, quantities, account references and personal inputs. Hiding quantities matters because multiplying a quantity by a public share price can reveal a holding's value. Likewise, displaying both an absolute gain and its percentage can reveal the original investment.

Public prices and clearly labelled simulated research examples may remain visible. Personal money charts and controls that expose private editing or copy prompts are restricted while privacy is on.

**This is screen privacy, not access control.** The local application still needs its real data, and that data may remain in browser memory, page source or API responses. It is not safe to publish the running personal dashboard on the internet, share raw exports, or assume a private-data repository is suitable for a public hackathon submission. Public demonstrations should not include broker login screens, terminals, secret files or personal backups.

Data can be stale or incomplete, models can be wrong, historical tests can be biased, and AI can make mistakes. Recommendations are research aids, not regulated financial advice. No return or market outperformance is guaranteed.

## What comes next

The active direction is better Indian and US investment research, clearer explanations, reliable data freshness, improved portfolio and fund review, and reproducible testing. The intraday and options engines remain available for disciplined research, without promoting them as profitable products.

Before any trading strategy is considered for live use again: state the idea clearly, freeze its rules, test on genuinely fresh data, include all trading and operating costs, compare with a suitable benchmark, stress-test adverse conditions, and collect enough separate forward evidence. A limited live pilot would need a new explicit decision, not merely a configuration change made for a demonstration.

The hackathon result is an end-to-end financial research tool that learned from its experiments: build the workflow, measure the result, acknowledge what did not work, and apply the reusable parts to a more useful problem.