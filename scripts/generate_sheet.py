"""
Generate a TSV spreadsheet from a portfolio analysis JSON report.

Makes ONE Claude API call to extract structured fields (action detail,
stock counts, trigger prices) from the free-text NEXT_STEPS.

Usage:
    python scripts/generate_sheet.py                     # today's portfolio report
    python scripts/generate_sheet.py 2026-03-16          # specific date
    python scripts/generate_sheet.py --list              # list all available report dates
    python scripts/generate_sheet.py --output out.tsv    # custom output path
"""

import argparse
import datetime
import glob
import os
import re
import sys
import json

from dotenv import load_dotenv
import anthropic

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def parse_target_range(target_str: str) -> tuple[str, str]:
    cleaned = target_str.replace("₹", "").replace(",", "")
    numbers = re.findall(r"[\d]+(?:\.[\d]+)?", cleaned)
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    elif len(numbers) == 1:
        return numbers[0], numbers[0]
    return "", ""


def build_claude_prompt(analyses: list[dict]) -> str:
    """Build a single prompt for Claude to extract structured fields from all stocks."""
    lines = [
        "Extract structured trading action data from each stock analysis below.",
        "For EACH stock, return a JSON object with these fields:",
        "  action_detail: short description (e.g. 'Sell 25 shares (50%)', 'Buy 10 shares at ₹840', 'No action')",
        "  num_stocks: integer — shares to buy/sell NOW (0 if HOLD or waiting)",
        "  trigger_price: single price number to watch for next action (0 if none)",
        "  trigger_action: BUY or SELL at trigger, or NONE",
        "  trigger_num_stocks: integer — shares to trade at trigger (0 if none)",
        "",
        "Return ONLY a JSON array of objects in the same order, one per stock. No markdown, no explanation.",
        "",
    ]

    for a in analyses:
        p = a["parsed"]
        stock = a.get("stock", {})
        qty = stock.get("quantity", "?") if stock else "?"
        lines.append(f"--- {a['symbol']} (holds {qty} shares) ---")
        lines.append(f"ACTION: {p.get('ACTION', 'N/A')}")
        lines.append(f"NEXT_STEPS: {p.get('NEXT_STEPS', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def list_available_dates():
    """List all portfolio report dates that have JSON data files."""
    pattern = os.path.join(PROJECT_ROOT, "reports", "portfolio", "*", "*", "portfolio_data_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print("No portfolio report files found.")
        return
    print(f"\n  Available portfolio report dates:\n")
    for f in files:
        # Extract date from path: reports/portfolio/YYYY/MM/portfolio_data_DD.json
        parts = f.replace("\\", "/").split("/")
        year, month = parts[2], parts[3]
        day = re.search(r"portfolio_data_(\d+)\.json", parts[4])
        if day:
            print(f"    {year}-{month}-{int(day.group(1)):02d}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Generate a TSV spreadsheet from a portfolio analysis report.",
        epilog="Examples:\n"
               "  python scripts/generate_sheet.py                  # today's report\n"
               "  python scripts/generate_sheet.py 2026-03-16       # specific date\n"
               "  python scripts/generate_sheet.py --list           # show available dates\n"
               "  python scripts/generate_sheet.py 2026-03-16 -o ~/Desktop/sheet.tsv\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("date", nargs="?", default=None,
                        help="Report date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--list", action="store_true",
                        help="List all available report dates and exit")
    parser.add_argument("-o", "--output", default=None,
                        help="Custom output file path (default: alongside the JSON)")

    args = parser.parse_args()

    if args.list:
        list_available_dates()
        return

    date_str = args.date or str(datetime.date.today())
    try:
        date_obj = datetime.date.fromisoformat(date_str)
    except ValueError:
        print(f"Invalid date format: {date_str}  (expected YYYY-MM-DD)")
        sys.exit(1)

    base_dir  = os.path.join(PROJECT_ROOT, "reports", "portfolio", str(date_obj.year), f"{date_obj.month:02d}")
    json_path = os.path.join(base_dir, f"portfolio_data_{date_obj.day:02d}.json")
    tsv_path  = args.output or os.path.join(base_dir, f"portfolio_sheet_{date_obj.day:02d}.tsv")

    if not os.path.exists(json_path):
        print(f"❌ File not found: {json_path}")
        sys.exit(1)

    with open(json_path, "r") as f:
        data = json.load(f)

    analyses = data.get("analyses", [])
    portfolio = data.get("portfolio", [])

    # Build a lookup for stock data by symbol
    stock_lookup = {s["symbol"]: s for s in portfolio}

    # Attach stock data to each analysis (JSON may not have it nested)
    for a in analyses:
        if "stock" not in a:
            a["stock"] = stock_lookup.get(a["symbol"], {})

    print(f"📊 Found {len(analyses)} stocks in {json_path}")
    print(f"🤖 Calling Claude to extract structured fields (1 API call)...")

    # Single Claude API call
    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
    prompt = build_claude_prompt(analyses)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_response = response.content[0].text.strip()

    # Parse JSON response
    try:
        structured = json.loads(raw_response)
    except json.JSONDecodeError:
        # Try extracting JSON array from response
        match = re.search(r"\[.*\]", raw_response, re.DOTALL)
        if match:
            structured = json.loads(match.group())
        else:
            print(f"❌ Could not parse Claude response. Raw:\n{raw_response[:500]}")
            sys.exit(1)

    if len(structured) != len(analyses):
        print(f"⚠️  Claude returned {len(structured)} items but expected {len(analyses)}")

    # Build TSV
    headers = [
        "Ticker", "Horizon", "Action Detail", "Buy/Sell",
        "No of Stocks", "Value", "My Average", "Current Price",
        "Target Low", "Target High", "Next Steps",
        "Trigger Price", "Action at Trigger",
        "Stocks at Trigger", "Value at Trigger",
    ]

    rows = []
    for i, a in enumerate(analyses):
        p = a["parsed"]
        stock = a.get("stock", {})
        action = p.get("ACTION", "")

        # Get Claude's structured extraction
        s = structured[i] if i < len(structured) else {}

        # Buy/Sell
        if action in ("AVERAGE DOWN", "ADD MORE"):
            buy_sell = "BUY"
        elif action in ("PARTIAL EXIT", "FULL EXIT"):
            buy_sell = "SELL"
        else:
            buy_sell = ""

        # Num stocks
        num_stocks = str(s.get("num_stocks", 0))
        if num_stocks == "0":
            num_stocks = ""

        # Value
        current_price = float(stock.get("current_price", 0))
        try:
            value = str(round(int(num_stocks) * current_price, 2)) if num_stocks else ""
        except (ValueError, TypeError):
            value = ""

        # Target range
        target_low, target_high = parse_target_range(p.get("TARGET_PRICE", ""))

        # Next steps
        next_steps = p.get("NEXT_STEPS", "").replace("\n", " ").replace("\t", " ").strip()

        # Trigger fields
        trigger_price = str(s.get("trigger_price", 0))
        if trigger_price == "0":
            trigger_price = ""

        trigger_action = str(s.get("trigger_action", "NONE")).upper()
        if trigger_action in ("NONE", "0"):
            trigger_action = ""

        trigger_num = str(s.get("trigger_num_stocks", 0))
        if trigger_num == "0":
            trigger_num = ""

        # Value at trigger
        try:
            val_at_trigger = str(round(int(trigger_num) * float(trigger_price), 2)) if trigger_num and trigger_price else ""
        except (ValueError, TypeError):
            val_at_trigger = ""

        row = [
            a["symbol"],
            p.get("HORIZON", ""),
            s.get("action_detail", action),
            buy_sell,
            num_stocks,
            value,
            str(stock.get("avg_buy_price", "")),
            str(stock.get("current_price", "")),
            target_low,
            target_high,
            next_steps,
            trigger_price,
            trigger_action,
            trigger_num,
            val_at_trigger,
        ]
        rows.append(row)

    # ── New stock recommendations from portfolio review ───────────
    new_recs = data.get("new_stock_recommendations", [])
    portfolio_review = data.get("portfolio_review", "")

    # If pre-parsed recommendations exist in JSON, use them directly
    if new_recs:
        print(f"📋 Found {len(new_recs)} new stock recommendations in report data")
    elif portfolio_review:
        # Old report without pre-parsed recs — extract via Claude
        print(f"🤖 Extracting new stock recommendations from portfolio review (1 API call)...")
        rec_prompt = (
            "Extract all specific stock recommendations from this portfolio review text.\n"
            "For each recommended stock, return a JSON object with:\n"
            '  {"symbol": "NSE_TICKER", "sector": "Sector", "action": "BUY", '
            '"horizon": "Short/Medium/Long-term", "target_price": "₹XXX-₹YYY", '
            '"rationale": "One-line reason"}\n\n'
            "Return ONLY a JSON array. No markdown, no explanation. "
            "If no specific stocks are recommended, return [].\n\n"
            f"REVIEW TEXT:\n{portfolio_review}"
        )
        rec_response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": rec_prompt}],
        )
        rec_raw = rec_response.content[0].text.strip()
        try:
            new_recs = json.loads(rec_raw)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", rec_raw, re.DOTALL)
            if match:
                try:
                    new_recs = json.loads(match.group())
                except json.JSONDecodeError:
                    new_recs = []
        if new_recs:
            print(f"📋 Extracted {len(new_recs)} new stock recommendations")

    for rec in new_recs:
        symbol = rec.get("symbol", "")
        target_low, target_high = parse_target_range(rec.get("target_price", ""))
        row = [
            symbol,
            rec.get("horizon", ""),
            f"NEW BUY — {rec.get('rationale', '')}",
            "BUY",
            "",   # num_stocks
            "",   # value
            "",   # avg_buy_price
            "",   # current_price
            target_low,
            target_high,
            rec.get("rationale", ""),
            "",   # trigger_price
            "",   # trigger_action
            "",   # trigger_num
            "",   # val_at_trigger
        ]
        rows.append(row)

    with open(tsv_path, "w", encoding="utf-8") as f:
        f.write("\t".join(headers) + "\n")
        for row in rows:
            f.write("\t".join(row) + "\n")

    print(f"✅ Spreadsheet saved: {tsv_path}")
    print(f"   Open in Excel/Sheets or copy-paste directly.")


if __name__ == "__main__":
    main()
