# Intraday Trading — Tax Guide (India)

> **Applies to:** Indian residents doing equity intraday (MIS) trading on NSE.
> **Last updated:** March 2026. Verify rates each year before filing ITR.

---

## 1. Classification of Intraday Income

Intraday equity trading (buy & sell on the same day) is classified as
**speculative business income** under the Income Tax Act, 1961 (Section 43(5)).

- It is **NOT** capital gains — it's **business income**.
- Taxed at your **income tax slab rate** (not the flat 15% STCG rate).
- Must be reported under **Schedule BP → Speculative Business Income** in ITR.

---

## 2. Tax Rates (Slab-Based)

Since intraday profits are speculative business income, they are **added to your
total income** (salary + other sources) and taxed per your applicable slab.

### New Tax Regime (Default from FY 2023-24 onwards)

| Taxable Income (₹)    | Tax Rate |
|------------------------|----------|
| Up to 3,00,000         | Nil      |
| 3,00,001 – 7,00,000   | 5%       |
| 7,00,001 – 10,00,000  | 10%      |
| 10,00,001 – 12,00,000 | 15%      |
| 12,00,001 – 15,00,000 | 20%      |
| Above 15,00,000        | 30%      |

Plus **4% Health & Education Cess** on total tax.

> **Tip:** If your salary already puts you in the 30% bracket, every rupee
> of intraday profit is taxed at ~31.2% (30% + 4% cess).

---

## 3. Which ITR Form to Use

| Situation | ITR Form |
|-----------|----------|
| Salary + delivery equity only (STCG/LTCG) | ITR-2 |
| Salary + intraday equity trading | **ITR-3** |
| Salary + F&O trading | **ITR-3** |
| Salary + intraday + foreign stocks (RSUs/ESPP) | **ITR-3** |

### If you're a salaried employee with foreign stocks (RSUs)

If you were filing **ITR-2** only because of foreign stocks (Schedule FA),
you'll need to switch to **ITR-3** once you start intraday trading.
ITR-3 also supports Schedule FA for foreign assets, so everything fits.

**ITR-3 additionally requires:**
- Schedule BP (Business/Profession income — your speculative income goes here)
- Schedule P&L (Profit & Loss statement — can use "No Account Case" if turnover < ₹3 Cr)
- Schedule BS (Balance Sheet — simplified for "No Account Case")

---

## 4. Does the Government Auto-Pick Trading Data?

**Partially.** The IT department receives data via:

| Source | What it shows |
|--------|---------------|
| **AIS (Annual Information Statement)** | Your trading transactions, STT paid, buy/sell values reported by exchanges |
| **Form 26AS** | TDS deducted (salary, bank interest, etc.), STT paid |
| **SFT (Statement of Financial Transactions)** | Brokers report high-value transactions to IT department |

**However, you still need to manually file ITR-3** declaring your speculative
income/loss. The government does NOT auto-compute your trading P&L for you.

### What Zerodha provides for filing

Download from [Console](https://console.zerodha.com) → **Tax P&L**:
- **Tax P&L Statement** — Intraday/speculative profit, short-term profit, long-term profit, turnover, charges breakdown
- **Tradebook** — Every order with timestamp, price, qty
- **P&L Report** — Scrip-wise profit/loss
- **Ledger** — Cash flow in/out of trading account

> **Important:** Cross-check the Zerodha Tax P&L with your AIS on the
> income tax portal. Flag mismatches via the AIS feedback mechanism.

---

## 5. Expenses You Can Claim as Deductions

Since intraday trading is **business income**, you can deduct legitimate
business expenses from your gross profit:

| Expense | Deductible? | Notes |
|---------|-------------|-------|
| **Brokerage** | ✅ Yes | Zerodha's ₹20/order or 0.03% |
| **STT (Securities Transaction Tax)** | ✅ Yes | 0.025% on sell side |
| **Exchange transaction charges** | ✅ Yes | NSE 0.00307% |
| **GST on brokerage** | ✅ Yes | 18% on brokerage + SEBI + exchange |
| **SEBI charges** | ✅ Yes | ₹10 per crore |
| **Stamp duty** | ✅ Yes | 0.003% on buy side |
| **Internet bill** (proportion for trading) | ✅ Yes | Keep bills as proof |
| **Computer/laptop depreciation** | ✅ Yes | Proportion used for trading |
| **Trading software/subscriptions** | ✅ Yes | Zerodha Connect ₹500/mo, data feeds |
| **Claude AI API costs** | ✅ Yes | Directly used for trade decisions |
| **Advisory/research subscriptions** | ✅ Yes | If used for trading |
| **Electricity** (proportion) | ⚠️ Arguable | Keep records if claiming |
| **STT for capital gains** | ❌ No | Cannot deduct STT when computing STCG/LTCG |

> **Our bot automatically tracks:** brokerage, STT, exchange charges, GST,
> SEBI charges, stamp duty, and Claude API costs in every daily report.

---

## 6. Loss Handling

### Speculative loss offset rules

| Loss type | Can offset against | Carry forward |
|-----------|--------------------|---------------|
| Speculative (intraday equity) loss | **Only** speculative (intraday) profit | 4 years |
| Non-speculative (F&O) loss | Any business income except speculative | 8 years |
| Short-term capital loss | Any capital gain (STCG or LTCG) | 8 years |

**Critical:** Speculative loss CANNOT be set off against:
- Salary income
- F&O profits
- Short-term capital gains
- Any other head of income

To carry forward losses, you **must file ITR before the due date** (July 31).

---

## 7. Tax Audit Requirement

| Condition | Audit Required? |
|-----------|-----------------|
| Turnover > ₹10 Cr (digital transactions) | **Yes** — mandatory CA audit |
| Turnover < ₹10 Cr AND profit ≥ 6% of turnover | No |
| Turnover < ₹10 Cr AND profit < 6% AND total income > ₹2.5L | **Yes** |
| Turnover < ₹10 Cr AND total income < ₹2.5L | No |

**Turnover for intraday** = Absolute sum of settlement profits and losses
(NOT buy+sell value). For small accounts like ours (₹10K budget), turnover
will be well under ₹10 Cr — audit is almost certainly NOT required.

### Presumptive taxation (Section 44AD)

If turnover < ₹3 Cr (digital), you can opt for **presumptive taxation**
and declare 6% of turnover as income. However:
- This only makes sense if your actual profit is higher than 6%
- You pay more tax than necessary if you had losses
- **Not recommended** for algo trading where exact P&L is tracked

---

## 8. Advance Tax

Since trading income is business income, you must pay **advance tax** quarterly
if your total tax liability exceeds ₹10,000 for the year:

| Due Date | Cumulative % of Tax |
|----------|---------------------|
| June 15  | 15% |
| September 15 | 45% |
| December 15 | 75% |
| March 15 | 100% |

Interest under Section 234C applies if you miss these deadlines.

> **Tip:** If your intraday profits are small relative to your salary
> (where TDS is already deducted), advance tax may not apply.

---

## 9. Practical Filing Checklist

1. **Download Tax P&L** from Zerodha Console at financial year end
2. **Run our tax ledger script:** `python scripts/generate_tax_ledger.py`
   — generates a clean per-trade table with all charges for ITR filing
3. **Check AIS** on income tax portal — verify transactions match
4. **File ITR-3** — declare intraday under Schedule BP → Speculative Business Income
5. **Declare expenses** — brokerage, STT, exchange charges, Claude API costs, etc.
6. **Pay advance tax** quarterly if tax liability > ₹10,000/year
7. **Consult a CA** — ITR-3 with balance sheet/P&L is more complex than ITR-1/2

---

## 10. Key Numbers for Our Bot

These are tracked automatically in every daily trading report:

| Item | Where to find |
|------|---------------|
| Date-wise P&L | `reports/trading/YYYY/MM/trading_data_DD.json` |
| Per-trade entry/exit/P&L | Same JSON → `positions` array |
| Brokerage & charges breakdown | Same JSON → `pnl.charges` |
| Cumulative FY ledger | `python scripts/generate_tax_ledger.py` |
| Trade database | `data/trades.db` → `trades` table |

---

## References

- [Zerodha Varsity — Taxation for Traders](https://zerodha.com/varsity/chapter/taxation-for-traders/)
- [Zerodha Charges](https://zerodha.com/charges)
- [Income Tax India — ITR Forms](https://www.incometaxindia.gov.in)
- [Zerodha Console — Tax P&L](https://console.zerodha.com)

---

*Disclaimer: This document is for informational purposes only. Consult a
Chartered Accountant (CA) before filing your returns. Tax laws change
frequently — verify rates and rules for the current assessment year.*
