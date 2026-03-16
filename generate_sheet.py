"""
One-time script to generate the spreadsheet TSV from an existing
portfolio_data JSON file.

Makes ONE Claude API call to extract structured fields (action detail,
stock counts, trigger prices) from the free-text NEXT_STEPS.

Usage:
    python generate_sheet.py                              # uses today's date
    python generate_sheet.py 2026-03-16                   # specific date
"""

import os
import re
import sys
import json

from dotenv import load_dotenv
import anthropic

load_dotenv()


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


def main():
    import datetime

    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = str(datetime.date.today())

    date_obj  = datetime.date.fromisoformat(date_str)
    base_dir  = f"reports/portfolio/{date_obj.year}/{date_obj.month:02d}"
    json_path = f"{base_dir}/portfolio_data_{date_obj.day:02d}.json"
    tsv_path  = f"{base_dir}/portfolio_sheet_{date_obj.day:02d}.tsv"

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

    with open(tsv_path, "w", encoding="utf-8") as f:
        f.write("\t".join(headers) + "\n")
        for row in rows:
            f.write("\t".join(row) + "\n")

    print(f"✅ Spreadsheet saved: {tsv_path}")
    print(f"   Open in Excel/Sheets or copy-paste directly.")


if __name__ == "__main__":
    main()
