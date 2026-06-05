# Options Trading Guide — Plain English

> **Created:** 2026-06-06
> **Audience:** Anyone new to options. No jargon assumed.
> **Context:** This is a learning reference. No options code exists in the
> codebase yet. See [TRADE_NEXT_IDEAS.md §B](TRADE_NEXT_IDEAS.md) for the
> research plan if we decide to build an options mode.

---

## What is an Option?

An option is a **contract** that gives you the **right** (but not the
obligation) to buy or sell something at a specific price, before a specific
date.

Think of it like booking a flat:

> You pay Rs.2 lakh "token money" to book a flat worth Rs.50 lakh. You have
> 3 months to decide. If flat prices go up to Rs.55 lakh, you exercise your
> booking — you buy at Rs.50 lakh and are Rs.5 lakh richer (minus your Rs.2
> lakh token). If prices drop to Rs.45 lakh, you just walk away — you lose
> your Rs.2 lakh token but that's it. You're not forced to buy at Rs.50 lakh.

That "token money" is the **premium**. That Rs.50 lakh agreed price is the
**strike price**. That 3-month deadline is the **expiry**.

---

## Two Types of Options

| | Call Option (CE) | Put Option (PE) |
|---|---|---|
| **Right to** | BUY at strike price | SELL at strike price |
| **You buy this when** | You think price will GO UP | You think price will GO DOWN |
| **Example** | NIFTY at 24,000. You buy 24,000 CE. If NIFTY goes to 24,500 → you profit | NIFTY at 24,000. You buy 24,000 PE. If NIFTY drops to 23,500 → you profit |
| **Max loss** | Premium you paid (nothing more) | Premium you paid (nothing more) |
| **Max gain** | Unlimited (as high as price goes) | Limited (price can only go to zero) |

---

## Key Terms

**Strike Price:** The price at which you have the right to buy/sell.

**Premium:** The price you pay to buy the option. This is your maximum risk
when buying.

**Expiry:** The date the option expires. On NSE:

- NIFTY weekly options expire every Thursday
- BANKNIFTY weekly options also expire every Thursday (different expiry day
  now — check current schedule)
- Monthly options expire on last Thursday of month

**Lot Size:** Options trade in fixed lots. NIFTY = 25 units per lot. So if
NIFTY is at 24,000, one lot controls Rs.6,00,000 (25 × 24,000) worth of NIFTY.

**In-the-Money (ITM):** Option already has "real value." Call with strike
23,500 when NIFTY is at 24,000 = ITM by Rs.500.

**At-the-Money (ATM):** Strike price ≈ current price. NIFTY at 24,000,
strike 24,000 = ATM.

**Out-of-the-Money (OTM):** Option has no real value yet. Call with strike
24,500 when NIFTY is at 24,000 = OTM by Rs.500. It only has value if NIFTY
crosses 24,500 before expiry.

---

## How Profit/Loss Works — Option BUYING

### Example: Buying a NIFTY Call Option

```
NIFTY is at 24,000
You buy 1 lot of 24,000 CE (Call) at Rs.200 premium
Lot size = 25
Your cost = 25 × Rs.200 = Rs.5,000 (this is your MAX LOSS, no matter what)

Scenario 1: NIFTY goes to 24,400 (+400 points)
  Your call is now worth ~Rs.400+
  Profit = (400 - 200) × 25 = Rs.5,000
  Return = +100% on your Rs.5,000 investment

Scenario 2: NIFTY stays at 24,000 until expiry
  Your call expires worthless (strike = market price, no value)
  Loss = Rs.5,000 (your entire premium)

Scenario 3: NIFTY drops to 23,600 (-400 points)
  Your call expires worthless
  Loss = Rs.5,000 (still only your premium, NOT Rs.10,000)
```

**Key insight:** When you BUY options, your maximum loss is ALWAYS the premium
paid. You can never lose more. But if you're wrong, you lose 100% of it.

### Example: Buying a NIFTY Put Option

```
NIFTY is at 24,000
You buy 1 lot of 24,000 PE (Put) at Rs.180 premium
Your cost = 25 × Rs.180 = Rs.4,500

Scenario 1: NIFTY drops to 23,600 (-400 points)
  Your put is now worth ~Rs.400+
  Profit = (400 - 180) × 25 = Rs.5,500
  Return = +122%

Scenario 2: NIFTY goes up to 24,400
  Your put expires worthless
  Loss = Rs.4,500
```

---

## How Profit/Loss Works — Option SELLING

Selling is the mirror of buying. You COLLECT premium upfront and hope the
option expires worthless.

### Example: Selling a NIFTY Call Option

```
NIFTY is at 24,000
You SELL 1 lot of 24,500 CE (OTM call) at Rs.100 premium
You RECEIVE = 25 × Rs.100 = Rs.2,500 immediately

Scenario 1: NIFTY stays below 24,500 until expiry
  The option expires worthless
  You KEEP the Rs.2,500 premium → PROFIT

Scenario 2: NIFTY goes to 25,000
  The option is now worth Rs.500
  Your loss = (500 - 100) × 25 = Rs.10,000
  You received Rs.2,500 but owe Rs.12,500 → NET LOSS Rs.10,000

Scenario 3: NIFTY goes to 26,000 (black swan)
  The option is now worth Rs.1,500
  Your loss = (1500 - 100) × 25 = Rs.35,000
  CATASTROPHIC — this is why naked selling is dangerous
```

**Key insight — selling options:**

- You win SMALL and often (premium collected)
- You lose BIG and rarely (if market moves sharply against you)
- Your maximum gain is FIXED (the premium collected)
- Your maximum loss is UNLIMITED (for naked calls) or VERY LARGE (for naked
  puts)

---

## The Greeks — What Makes Option Prices Move

Option prices don't just depend on direction. Four forces act on them:

### Delta (Δ) — Direction Sensitivity

How much the option price moves when NIFTY moves Rs.1.

- ATM call has delta ~0.5 → NIFTY up Rs.100 → option up ~Rs.50
- Deep ITM delta ~1.0 → moves like the stock itself
- Deep OTM delta ~0.0 → barely moves

### Theta (θ) — Time Decay

Options LOSE VALUE every day just because time passes.

This is the "token money rotting" — each day closer to expiry, the option is
worth less.

- **Theta accelerates near expiry** — a 2-day-to-expiry option decays MUCH
  faster than a 30-day option
- **Buyers hate theta** (their option is melting)
- **Sellers love theta** (they collected premium that's melting to zero)
- On expiry day (Thursday for NIFTY weeklies), theta is EXTREME — OTM options
  can lose 80% of their value in one day if NIFTY stays flat

### Vega (ν) — Volatility Sensitivity

When INDIA VIX goes UP, ALL option premiums go UP (both calls and puts). When
INDIA VIX goes DOWN, all premiums SHRINK.

- **Buying before an event (budget, RBI, results):** VIX is high, premiums are
  expensive. After the event, VIX drops ("volatility crush") and your option
  loses value EVEN IF direction is correct
- This is why many option buyers get the direction right but still lose money

### Gamma (γ) — Speed of Delta Change

How fast delta changes when NIFTY moves.

- High gamma near ATM + near expiry = explosive moves
- This is what makes expiry-day options so volatile — small NIFTY moves cause
  huge option price swings
- Sellers FEAR gamma on expiry day
- Buyers LOVE it

---

## Costs of Options on NSE (Zerodha)

| Cost Component | Option Buy | Option Sell |
|---|---|---|
| Brokerage | Rs.20 per order (flat) | Rs.20 per order (flat) |
| STT | 0.0625% on premium (sell side only) | 0.0625% on premium (sell side only) |
| Exchange txn | ~0.05% on premium | ~0.05% on premium |
| GST | 18% on (brokerage + exchange + SEBI) | 18% on (brokerage + exchange + SEBI) |
| SEBI | Rs.10 per crore | Rs.10 per crore |
| Stamp duty | 0.003% (buy side) | 0.003% (buy side) |

### Worked Example: Buy and Sell 1 Lot NIFTY CE

```
Buy 1 lot NIFTY 24000 CE at Rs.200 premium
Sell at Rs.280 premium
Lot size = 25

Gross profit = (280 - 200) × 25 = Rs.2,000

Costs:
  Brokerage:    Rs.20 × 2 = Rs.40
  STT:          0.0625% × 280 × 25 = Rs.4.38 (on sell premium)
  Exchange txn: ~0.05% × (200+280) × 25 / 2 = ~Rs.3
  GST:          18% of ~Rs.43 = ~Rs.8
  Stamp:        ~Rs.0.15
  TOTAL COST:   ~Rs.55

Net profit = Rs.2,000 - Rs.55 = Rs.1,945
Cost as % of gross = ~2.75%
```

### Options vs Equity Cost Comparison

| | Equity MIS (Buy+Sell) | Option Buy (Buy+Sell) | Option Sell (Buy+Sell) |
|---|---|---|---|
| Brokerage | Rs.0 (Zerodha) | Rs.40 (Rs.20/order) | Rs.40 (Rs.20/order) |
| STT | 0.025% × turnover (both sides) | 0.0625% × premium (sell only) | 0.0625% × premium (sell only) |
| Exchange txn | 0.00345% × turnover | 0.05% × premium | 0.05% × premium |
| Typical total on Rs.15K | ~Rs.10-15 | ~Rs.45-55 | ~Rs.45-55 |

**Key insight:** Option brokerage is flat Rs.20/order (not %). So for LARGE
premium trades, cost % is low. For SMALL premium trades (OTM), cost % is high.
The advantage of options is NOT lower absolute cost — it's **defined risk**
and **leverage** (control larger notional with smaller capital).

---

## Margin Requirements

| What you do | Margin needed (NIFTY, approximate) |
|---|---|
| **Buy option** | Just the premium (Rs.3,000-25,000 per lot) |
| **Sell naked option** | Rs.1,00,000-1,50,000 per lot (SPAN + exposure) |
| **Iron condor** (sell + buy protection) | Rs.25,000-50,000 per lot |
| **Spread** (buy one strike, sell another) | Rs.15,000-40,000 per spread |

**For Rs.50K budget:**

- You CAN buy options (1-2 lots comfortably)
- You CANNOT sell naked options (need Rs.1L+ margin)
- You CAN do iron condors / spreads (protected positions, lower margin)

---

## When Option Buyers Win vs Lose

| Buyer Wins When | Buyer Loses When |
|---|---|
| Big directional move + quickly | Market stays flat (theta eats premium) |
| Volatility increases after buying | Volatility drops after buying (VIX crush) |
| Expiry far away (time on your side) | Close to expiry (time decay accelerates) |
| You bought ATM/slightly OTM | You bought far OTM (lottery ticket, almost always loses) |

## When Option Sellers Win vs Lose

| Seller Wins When | Seller Loses When |
|---|---|
| Market stays in a range | Big directional move (black swan) |
| Time passes (theta is your friend) | Gamma explosion near expiry |
| Volatility drops | Volatility spikes |
| You sold far OTM (high prob of expiry worthless) | You sold ATM (high gamma, high risk) |

---

## Common Option Strategies (Simple Ones)

### 1. Long Call / Long Put (Directional Bet)

Buy a call if bullish. Buy a put if bearish. Maximum loss = premium.

**Best when:** You have a strong directional view and expect the move to happen
quickly (before theta eats the premium).

### 2. Straddle (Buy Both Sides)

Buy ATM call + ATM put on same expiry.

**Profits when:** NIFTY makes a BIG move in EITHER direction. You don't need
to know which way — just that it will move a lot.

**Loses when:** NIFTY stays flat. Both options decay. You paid two premiums.

### 3. Strangle (Sell Both Sides — OTM)

Sell OTM call + OTM put on same expiry.

**Profits when:** NIFTY stays within a range (between the two strikes). Both
options expire worthless and you keep the premium.

**Loses when:** NIFTY makes a big move and breaks past one of your strikes.
Loss can be large if unprotected.

### 4. Iron Condor (Protected Strangle)

Sell OTM call + sell OTM put (collect premium). Buy further OTM call + buy
further OTM put (protection). Four legs total.

**Profits when:** NIFTY stays in the range. Same as strangle but risk is
CAPPED — your bought options protect against extreme moves.

**Max loss:** Difference between strikes minus premium collected. Defined.

**This is the safest selling strategy for small capital.**

### 5. Bull Call Spread / Bear Put Spread

Buy one call, sell a higher call (or buy one put, sell a lower put).

**Profits when:** NIFTY moves in your expected direction but you want to cap
both risk AND reward.

**Advantage:** Lower cost than buying a call outright (the sold option
subsidises the bought one).

---

## The Indian Options Market — Key Facts

- **India is #1 globally in options volume** — NIFTY and BANKNIFTY weekly
  options trade billions of contracts
- **Weekly expiry every Thursday** — most volume is on expiry day itself
- **~90% of option buyers lose money** (SEBI study 2023) — because they buy
  far OTM, hold too long (theta), and don't manage risk
- **Option sellers have structural edge** (collect theta) but need more
  capital and strict risk management
- **VIX (India Volatility Index)** typically ranges 10-25. Low VIX = cheap
  options. High VIX = expensive options

---

## How Our System Could Use Options

Our intraday equity bot has a regime classifier that identifies three types of
market days:

| Regime | % of Days | Equity PF (OOS) | Options Play |
|---|---|---|---|
| **RANGE** | 39% | 0.62 (worst) | **SELL premium** — price stays in band, theta decays to zero |
| **VOLATILE** | 33.5% | 1.10 (best) | **BUY directional** — big moves justify premium cost |
| **TREND** | 27.5% | 0.88 (middle) | **BUY directional** or **skip** |

The regime classifier is our most valuable asset. On RANGE days we currently
do nothing useful (equity bleeds). Options selling turns those days into a
profit source. On VOLATILE days where equity almost works, options buying with
leverage could amplify the small edge.

---

## Key Risks to Understand Before Starting

1. **Theta is relentless** — if you buy options and the move doesn't happen
   fast, your position melts every day
2. **Volatility crush** — buying before events (budget, RBI) means you pay for
   high VIX. After the event, VIX drops and your option loses value even if
   direction is correct
3. **Far OTM is a lottery ticket** — ~90% of far OTM options expire worthless.
   The odds are stacked against you
4. **Selling has unlimited risk** — always use defined-risk strategies (iron
   condors, spreads) unless you have Rs.2L+ capital and strict risk management
5. **Liquidity varies by strike** — ATM options are liquid. Far OTM can have
   wide bid-ask spreads (you lose on entry and exit)
6. **Expiry day is volatile** — gamma makes option prices swing wildly. Great
   for experienced traders, dangerous for beginners

---

## Learning Path (Before Writing Any Code)

1. **Paper trade on Sensibull** (free tier) — place 20+ paper trades to build
   intuition for how premiums move
2. **Watch premium decay in real-time** — on a Thursday, watch how OTM option
   prices melt through the day
3. **Use Zerodha's brokerage calculator** to verify actual costs per trade
4. **Read Zerodha Varsity Module 5 (Options Theory)** — free, excellent,
   India-specific
5. **Track your paper trades** — win rate, avg win, avg loss, net after costs
6. **Only after 30+ paper trades with positive results** should any code be
   written
